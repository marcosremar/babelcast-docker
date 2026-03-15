#!/bin/bash
set -e

echo "=== Detecting GPU architecture ==="
COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
MAJOR="${COMPUTE_CAP%%.*}"
if [ "${MAJOR:-0}" -ge "12" ] 2>/dev/null; then
  CUDA_SUFFIX="cu128"
  TORCH_INDEX="https://download.pytorch.org/whl/cu128"
  echo "Blackwell GPU detected (compute cap ${COMPUTE_CAP}) — using CUDA 12.8 wheels"
else
  CUDA_SUFFIX="cu124"
  TORCH_INDEX="https://download.pytorch.org/whl/cu124"
  echo "Standard GPU detected (compute cap ${COMPUTE_CAP:-unknown}) — using CUDA 12.4 wheels"
fi

echo "=== Installing system deps ==="
apt-get update -qq && apt-get install -y -qq ffmpeg libsndfile1 sox libsox-dev > /dev/null 2>&1
echo "Done."

echo "=== Installing PyTorch ($CUDA_SUFFIX) ==="
pip install -q torch torchaudio --index-url "$TORCH_INDEX" 2>&1 | tail -1
echo "Done."

echo "=== Installing Python dependencies ==="
pip install -q 'faster-whisper>=1.1.0' 'fastapi>=0.115.0' 'uvicorn>=0.32.0' \
  python-multipart httpx soundfile numpy huggingface-hub hf_transfer websockets \
  'pydantic-settings>=2.0' 2>&1 | tail -1
echo "Done."

echo "=== Installing llama-cpp-python ($CUDA_SUFFIX) ==="
pip install -q llama-cpp-python --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/$CUDA_SUFFIX" 2>&1 | tail -1
pip install -q 'llama-cpp-python[server]' 2>&1 | tail -1
echo "Done."

echo "=== Installing faster-qwen3-tts (CUDA graphs, real-time) ==="
pip install -q faster-qwen3-tts 2>&1 | tail -1
echo "Done."

echo "=== Downloading Whisper large-v3-turbo ==="
python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu'); print('Whisper OK')" 2>&1 | grep -E 'OK|error'
echo "Done."

LLM_MODEL="${CONF_LLM_MODEL:-translategemma}"
echo "=== Downloading LLM ($LLM_MODEL) ==="
export HF_HUB_ENABLE_HF_TRANSFER=1
if [ "$LLM_MODEL" = "mistral" ]; then
    GGUF_PATH=$(python3 -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('bartowski/Mistral-7B-Instruct-v0.3-GGUF', filename='Mistral-7B-Instruct-v0.3-Q5_K_M.gguf'))")
else
    GGUF_PATH=$(python3 -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('bullerwins/translategemma-12b-it-GGUF', filename='translategemma-12b-it-Q5_K_M.gguf'))")
fi
echo "Model at: $GGUF_PATH"
echo "Done."

echo "=== Pre-downloading Qwen3-TTS 0.6B-CustomVoice model ==="
python3 -c "
from faster_qwen3_tts import FasterQwen3TTS
model = FasterQwen3TTS.from_pretrained('Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice')
print('faster-qwen3-tts 0.6B-CustomVoice loaded OK')
del model
" 2>&1 | tail -3
echo "Done."

# ── VRAM budget ──────────────────────────────────────────────────────────────
# GPU VRAM is shared between llama.cpp (LLM), Whisper (STT), and Qwen3-TTS.
# Typical split on 24-48GB GPU:
#   • llama.cpp translategemma 12B Q5  → ~11GB  (70 layers)
#   • faster-whisper large-v3-turbo    → ~3GB
#   • Qwen3-TTS 0.6B                   → ~1.5GB
# --n_gpu_layers 70 leaves ~10GB headroom — raise if you have ≥40GB VRAM.
# For Blackwell RTX 5080 (16GB) use 40; for RTX 5090 (32GB) or larger use 70.
# ─────────────────────────────────────────────────────────────────────────────
if [ -z "${CONF_N_GPU_LAYERS}" ]; then
  VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
  if [ "${VRAM_MB:-0}" -le "18000" ] 2>/dev/null; then
    N_GPU_LAYERS=40
    echo "Small GPU (${VRAM_MB}MB VRAM) — using n_gpu_layers=40"
  else
    N_GPU_LAYERS=70
  fi
else
  N_GPU_LAYERS="${CONF_N_GPU_LAYERS}"
fi

echo "=== Starting llama.cpp server on port 8002 (n_gpu_layers=$N_GPU_LAYERS) ==="
python3 -m llama_cpp.server \
  --host 127.0.0.1 --port 8002 \
  --model "$GGUF_PATH" \
  --n_gpu_layers "$N_GPU_LAYERS" \
  --n_ctx 4096 \
  > /workspace/llama_server.log 2>&1 &
LLAMA_PID=$!
echo "llama.cpp PID: $LLAMA_PID"

echo "Waiting for llama.cpp (up to 270s)..."
for i in $(seq 1 90); do
  if curl -s http://127.0.0.1:8002/v1/models > /dev/null 2>&1; then
    echo "llama.cpp ready after $((i * 3))s!"
    break
  fi
  sleep 3
done

echo "=== Starting API gateway on port 8000 ==="
cd /workspace
exec python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
