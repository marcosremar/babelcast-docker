#!/bin/bash
# BabelCast Groq — Boot script (no local LLM needed)
# Uses Groq Cloud API for translation instead of llama.cpp

echo "=== BabelCast Translation Pipeline (Groq Cloud) ==="
echo "Python: $(python3 --version 2>&1)"
echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"
echo ""

# Hot-update API code from GitHub (avoids full image rebuild)
if [ "${BABELCAST_HOT_UPDATE:-1}" = "1" ]; then
    PINNED_COMMIT="${BABELCAST_PIN_COMMIT:-}"
    echo "[0/3] Updating API code from GitHub..."
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1 || true
    if command -v git &>/dev/null; then
        UPDATE_TMPDIR=$(mktemp -d)
        CLONE_OK=0
        if [ -n "$PINNED_COMMIT" ]; then
            if git clone --filter=blob:none --sparse https://github.com/marcosremar/babelcast.git "$UPDATE_TMPDIR" 2>/dev/null; then
                cd "$UPDATE_TMPDIR" && git checkout "$PINNED_COMMIT" 2>/dev/null && git sparse-checkout set docker/api docker/start-groq.sh 2>/dev/null && CLONE_OK=1
            fi
        else
            if git clone --depth 1 --filter=blob:none --sparse https://github.com/marcosremar/babelcast.git "$UPDATE_TMPDIR" 2>/dev/null; then
                cd "$UPDATE_TMPDIR" && git sparse-checkout set docker/api docker/start-groq.sh 2>/dev/null && CLONE_OK=1
            fi
        fi
        if [ "$CLONE_OK" = "1" ] && [ -d "$UPDATE_TMPDIR/docker/api" ]; then
            cp -r "$UPDATE_TMPDIR/docker/api/"* /app/api/
            if [ -f "$UPDATE_TMPDIR/docker/start-groq.sh" ]; then
                if ! diff -q "$UPDATE_TMPDIR/docker/start-groq.sh" /app/start-groq.sh >/dev/null 2>&1; then
                    cp "$UPDATE_TMPDIR/docker/start-groq.sh" /app/start-groq.sh
                    chmod +x /app/start-groq.sh
                    echo "  start-groq.sh updated — re-executing..."
                    rm -rf "$UPDATE_TMPDIR"
                    export BABELCAST_HOT_UPDATE=0
                    exec /app/start-groq.sh
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
echo "[0.5/3] Ensuring TTS dependencies..."
pip install --no-cache-dir -q "transformers==4.57.3" "accelerate>=1.12.0" librosa einops onnxruntime sox 2>&1 | tail -3
pip install --no-cache-dir -q --no-deps "qwen-tts>=0.1.1" "faster-qwen3-tts>=0.2.1" 2>&1 | tail -3
pip install --no-cache-dir -q --upgrade torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -3

# Download Whisper model
echo "[1/3] Downloading Whisper large-v3-turbo..."
python3 -c "
from faster_whisper import WhisperModel
WhisperModel('large-v3-turbo', device='cpu')
print('  Whisper OK')
" || echo "  WARNING: Whisper failed"

# No GGUF download needed — using Groq Cloud API for translation
echo "[2/3] LLM: Groq Cloud API (no local model needed)"
if [ -z "$CONF_GROQ_API_KEY" ]; then
    echo "  WARNING: CONF_GROQ_API_KEY not set — translation will fail!"
else
    echo "  Groq API key configured (${CONF_GROQ_API_KEY:0:8}...)"
fi

echo "[3/3] Pre-downloading TTS model weights (no CUDA init)..."
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base')
print('  TTS Base model weights cached (voice cloning support)')
" 2>&1 || echo "  TTS download skipped (will download on first request)"

echo "[3.5/3] Pre-downloading speaker embedding model (pyannote)..."
python3 -c "
from pyannote.audio import Model
import os
token = os.environ.get('CONF_HF_TOKEN') or os.environ.get('HF_TOKEN', '')
Model.from_pretrained('pyannote/embedding', use_auth_token=token or None)
print('  pyannote/embedding OK (speaker verification ready)')
" 2>&1 || echo "  Speaker embedding model skipped (set HF_TOKEN and accept license at hf.co/pyannote/embedding)"

# No llama.cpp server needed — start API gateway directly
echo "Starting API on port 8000..."
cd /app/api
exec python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
