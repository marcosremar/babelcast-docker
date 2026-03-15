#!/bin/bash
# BabelCast — RunPod boot script
# Boot order: TTS first → Whisper → LLM → API server

echo "=== BabelCast Translation Pipeline ==="
echo "Python: $(python3 --version 2>&1)"
echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"
echo ""

# Hot-update API code from GitHub (avoids full image rebuild)
# Set BABELCAST_HOT_UPDATE=0 to disable, or pin a commit with BABELCAST_PIN_COMMIT
if [ "${BABELCAST_HOT_UPDATE:-1}" = "1" ]; then
    PINNED_COMMIT="${BABELCAST_PIN_COMMIT:-}"
    echo "[0/4] Updating API code from GitHub..."
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1 || true
    if command -v git &>/dev/null; then
        UPDATE_TMPDIR=$(mktemp -d)
        CLONE_OK=0
        if [ -n "$PINNED_COMMIT" ]; then
            # Pinned commit: clone and checkout specific revision for integrity
            if git clone --filter=blob:none --sparse https://github.com/marcosremar/babelcast.git "$UPDATE_TMPDIR" 2>/dev/null; then
                cd "$UPDATE_TMPDIR" && git checkout "$PINNED_COMMIT" 2>/dev/null && git sparse-checkout set docker/api docker/start.sh 2>/dev/null && CLONE_OK=1
            fi
        else
            # Latest: shallow clone (accepts risk of unpinned code)
            if git clone --depth 1 --filter=blob:none --sparse https://github.com/marcosremar/babelcast.git "$UPDATE_TMPDIR" 2>/dev/null; then
                cd "$UPDATE_TMPDIR" && git sparse-checkout set docker/api docker/start.sh 2>/dev/null && CLONE_OK=1
            fi
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
            echo "  API code updated from GitHub${PINNED_COMMIT:+ (commit: ${PINNED_COMMIT:0:12})}"
        fi
        rm -rf "$UPDATE_TMPDIR"
        cd /app
    else
        echo "  git not available, using baked-in code"
    fi
fi

export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# Ensure TTS dependencies are up to date
# Use transformers 4.57.3 (qwen-tts pin) + backport 5.x symbols in tts.py
echo "[0.5/4] Ensuring TTS dependencies..."
pip install --no-cache-dir -q "transformers==4.57.3" "accelerate>=1.12.0" librosa einops onnxruntime sox 2>&1 | tail -3
pip install --no-cache-dir -q --no-deps "qwen-tts>=0.1.1" "faster-qwen3-tts>=0.2.1" 2>&1 | tail -3
# Fix: pyannote.audio installs CPU-only torchvision from PyPI, breaking CUDA.
# Re-install CUDA-enabled torchvision from the cu124 index.
pip install --no-cache-dir -q --upgrade torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -3

# === Download models (TTS first for fast startup) ===

echo "[1/4] Pre-downloading TTS model weights (no CUDA init)..."
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base')
print('  TTS Base model weights cached (voice cloning support)')
" 2>&1 || echo "  TTS download skipped (will download on first request)"

echo "[1.5/4] Pre-downloading speaker embedding model (pyannote)..."
python3 -c "
from pyannote.audio import Model
import os
token = os.environ.get('CONF_HF_TOKEN') or os.environ.get('HF_TOKEN', '')
Model.from_pretrained('pyannote/embedding', use_auth_token=token or None)
print('  pyannote/embedding OK (speaker verification ready)')
" 2>&1 || echo "  Speaker embedding model skipped (set HF_TOKEN and accept license at hf.co/pyannote/embedding)"

echo "[2/4] Downloading Whisper large-v3-turbo..."
python3 -c "
from faster_whisper import WhisperModel
WhisperModel('large-v3-turbo', device='cpu')
print('  Whisper OK')
" || echo "  WARNING: Whisper failed"

LLM_MODEL="${CONF_LLM_MODEL:-translategemma}"
echo "[3/4] Downloading LLM ($LLM_MODEL)..."
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
if [ -n "$GGUF_PATH" ]; then
    echo "  GGUF: $GGUF_PATH"
fi

# Start llama.cpp server (background — wait for it before starting API)
if [ -f "$GGUF_PATH" ]; then
    echo "[4/4] Starting llama.cpp on port 8002..."
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
        echo "Last log lines:"
        tail -20 /tmp/llama.log 2>/dev/null
        exit 1
    fi
fi

# Start API gateway
echo "Starting API on port 8000..."
cd /app/api
exec python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
