#!/bin/bash
# BabelCast Qwen3-TTS — standalone TTS boot script
# Downloads model weights on first boot, then starts FastAPI

echo "=== BabelCast Qwen3-TTS ==="
echo "Python: $(python3 --version 2>&1)"
echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"
echo ""

export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# Ensure TTS dependencies are correct
echo "[1/2] Ensuring TTS dependencies..."
pip install --no-cache-dir -q "transformers>=5.0" "accelerate>=1.12.0" 2>&1 | tail -3
pip install --no-cache-dir -q --no-deps "qwen-tts>=0.1.1" "faster-qwen3-tts>=0.2.1" 2>&1 | tail -3
python3 -c "from qwen_tts import Qwen3TTSModel; print('  qwen_tts import OK')" 2>&1 || echo "  WARNING: qwen_tts import failed"
python3 -c "from transformers import AutoProcessor; print('  AutoProcessor OK')" 2>&1 || echo "  WARNING: AutoProcessor import failed"

# Download TTS model weights (no CUDA init — lazy-load on first request)
echo "[2/2] Pre-downloading TTS model weights..."
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base')
print('  TTS Base model cached')
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice')
print('  TTS CustomVoice model cached')
" 2>&1 || echo "  TTS download skipped (will download on first request)"

# Start TTS API server
echo "Starting TTS API on port 8000..."
cd /app/api
exec python3 -m uvicorn server_tts:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
