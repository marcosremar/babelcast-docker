#!/bin/bash
# BabelCast — GPU pod boot script
# Sequentially downloads and starts all local models:
# 1. Whisper STT (blocking download)
# 2. LLM via llama.cpp (blocking download + background server start)
# 3. TTS model + speaker embedding
# 4. FastAPI server
#
# Cloud fallback routing is handled by the AI Gateway, not by this pod.

echo "=== BabelCast Translation Pipeline ==="
echo "Python: $(python3 --version 2>&1)"
echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"
echo ""

# Hot-update API code from GitHub (avoids full image rebuild)
# Requires BABELCAST_PIN_COMMIT to be set (specific commit hash for integrity).
# Without a pinned commit, hot update is skipped and baked-in code is used.
# Set BABELCAST_HOT_UPDATE=0 to disable entirely.
if [ "${BABELCAST_HOT_UPDATE:-1}" = "1" ]; then
    if [ -z "${BABELCAST_PIN_COMMIT:-}" ]; then
        echo "[0] WARNING: BABELCAST_PIN_COMMIT not set — skipping hot update (set it to enable)"
    else
        PINNED_COMMIT="$BABELCAST_PIN_COMMIT"
        echo "[0] Updating API code from GitHub (commit: ${PINNED_COMMIT:0:12})..."
        apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1 || true
        if command -v git &>/dev/null; then
            UPDATE_TMPDIR=$(mktemp -d)
            CLONE_OK=0
            if git clone --depth 50 --filter=blob:none --sparse https://github.com/marcosremar/babelcast.git "$UPDATE_TMPDIR" 2>/dev/null; then
                cd "$UPDATE_TMPDIR" && git checkout "$PINNED_COMMIT" 2>/dev/null && git sparse-checkout set docker/api docker/start.sh 2>/dev/null && CLONE_OK=1
            fi
            if [ "$CLONE_OK" = "1" ] && [ -d "$UPDATE_TMPDIR/docker/api" ]; then
                cp -r "$UPDATE_TMPDIR/docker/api/"* /app/api/
                # Also update start.sh itself if changed
                if [ -f "$UPDATE_TMPDIR/docker/start.sh" ]; then
                    if ! diff -q "$UPDATE_TMPDIR/docker/start.sh" /app/start.sh >/dev/null 2>&1; then
                        cp "$UPDATE_TMPDIR/docker/start.sh" /app/start.sh
                        chmod +x /app/start.sh
                        echo "  start.sh updated — re-executing..."
                        rm -rf "$UPDATE_TMPDIR"
                        export BABELCAST_HOT_UPDATE=0  # prevent infinite loop
                        exec /app/start.sh
                    fi
                fi
                echo "  API code updated from GitHub (commit: ${PINNED_COMMIT:0:12})"
            fi
            rm -rf "$UPDATE_TMPDIR"
            cd /app
        else
            echo "  git not available, using baked-in code"
        fi
    fi
fi

export HF_HOME="${HF_HOME:-/app/.cache/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# Ensure qwen-tts is installed (image may only have faster-qwen3-tts)
if ! python3 -c "from qwen_tts import Qwen3TTSModel" 2>/dev/null; then
    echo "[0] Installing qwen-tts (required for voice cloning)..."
    pip install --no-cache-dir qwen-tts 2>/dev/null
    # Fix mistral_regex kwarg conflict
    find / -path '*/qwen_tts/*.py' -exec sed -i 's/fix_mistral_regex=True,//' {} + 2>/dev/null
    echo "  qwen-tts installed"
fi

# Detect GPU architecture for correct CUDA wheel selection.
# Blackwell (sm_120, RTX 5090/5080) requires CUDA 12.8 wheels; older GPUs use CUDA 12.4.
COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
CUDA_MAJOR="${COMPUTE_CAP%%.*}"
if [ "${CUDA_MAJOR:-0}" -ge "12" ] 2>/dev/null; then
  TORCH_INDEX="https://download.pytorch.org/whl/cu128"
  echo "Detected Blackwell GPU (sm_${COMPUTE_CAP}) — using CUDA 12.8 wheels"
else
  TORCH_INDEX="https://download.pytorch.org/whl/cu124"
  echo "Detected standard GPU (sm_${COMPUTE_CAP:-unknown}) — using CUDA 12.4 wheels"
fi

# Ensure TTS dependencies are up to date
echo "[0.5] Ensuring TTS dependencies..."
pip install --no-cache-dir -q "transformers==4.57.3" "accelerate>=1.12.0" librosa einops onnxruntime sox 2>&1 | tail -3
pip install --no-cache-dir -q --no-deps "qwen-tts>=0.1.1" "faster-qwen3-tts>=0.2.1" 2>&1 | tail -3
# Fix: some deps install CPU-only torchvision from PyPI, breaking CUDA.
# Re-install CUDA-enabled torchvision from the arch-appropriate index.
pip install --no-cache-dir -q --upgrade torchvision torchaudio --index-url "$TORCH_INDEX" 2>&1 | tail -3

LLM_MODEL="${CONF_LLM_MODEL:-translategemma}"

echo ""

if python3 -c "from huggingface_hub import try_to_load_from_cache; assert try_to_load_from_cache('Systran/faster-whisper-large-v3-turbo', 'config.json') is not None" 2>/dev/null; then
    echo "[1/4] Whisper already cached in image"
else
    echo "[1/4] Downloading Whisper large-v3-turbo..."
    python3 -c "
from faster_whisper import WhisperModel
WhisperModel('large-v3-turbo', device='cpu')
print('  Whisper OK')
" || echo "  WARNING: Whisper failed"
fi

echo "[2/4] Downloading LLM ($LLM_MODEL)..."
GGUF_PATH=""
if [ "$LLM_MODEL" = "mistral" ]; then
    GGUF_PATH=$(python3 -c "
from huggingface_hub import hf_hub_download
p = hf_hub_download('bartowski/Mistral-7B-Instruct-v0.3-GGUF', filename='Mistral-7B-Instruct-v0.3-Q5_K_M.gguf')
print(p)
" 2>&1 | tail -1)
else
    GGUF_PATH=$(python3 -c "
from huggingface_hub import hf_hub_download
p = hf_hub_download('bullerwins/translategemma-12b-it-GGUF', filename='translategemma-12b-it-Q5_K_M.gguf')
print(p)
" 2>&1 | tail -1)
fi

if [ -f "$GGUF_PATH" ]; then
    echo "  GGUF: $GGUF_PATH"
    echo "[3/4] Starting llama.cpp on port 8002..."
    python3 -m llama_cpp.server --host 127.0.0.1 --port 8002 \
        --model "$GGUF_PATH" --n_gpu_layers 99 --n_ctx 2048 \
        > /tmp/llama.log 2>&1 &

    LLAMA_READY=0
    for i in $(seq 1 36); do
        if curl -s http://127.0.0.1:8002/v1/models > /dev/null 2>&1; then
            LLAMA_READY=1
            break
        fi
        sleep 5
    done
    if [ "$LLAMA_READY" = "1" ]; then
        echo "llama.cpp ready."
    else
        echo "FATAL: llama.cpp failed to start after 180s"
        tail -20 /tmp/llama.log 2>/dev/null
        exit 1
    fi
else
    echo "  WARNING: GGUF download failed"
fi

if python3 -c "from huggingface_hub import try_to_load_from_cache; assert try_to_load_from_cache('Qwen/Qwen3-TTS-12Hz-0.6B-Base', 'config.json') is not None" 2>/dev/null; then
    echo "[3.5/4] TTS model already cached in image"
else
    echo "[3.5/4] Downloading TTS model..."
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base')
print('  TTS model cached')
" 2>&1 || echo "  TTS download skipped"
fi

if python3 -c "import os; assert os.path.exists(os.path.join(os.environ.get('HF_HOME', '/app/.cache/huggingface'), 'hub/models--speechbrain--spkrec-ecapa-voxceleb'))" 2>/dev/null; then
    echo "[4/4] ECAPA-TDNN already cached in image"
else
    echo "[4/4] Downloading speaker embedding (ECAPA-TDNN)..."
    python3 -c "
from speechbrain.inference.speaker import EncoderClassifier
EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', run_opts={'device': 'cpu'})
print('  ECAPA-TDNN OK')
" 2>&1 || echo "  Speaker embedding skipped"
fi

echo "Starting API on port 8000..."
cd /app/api
exec python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
