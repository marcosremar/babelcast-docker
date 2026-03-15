# BabelCast — Unified Dockerfile (standard + Blackwell)
# Standard (default): CUDA 12.4, sm_89/sm_90 (RTX 4090, H100, A100, etc.)
# Blackwell:          CUDA 12.8, sm_120 (RTX 5090/5080)
#
# Build standard:  docker build -t babelcast-mistral .
# Build Blackwell: docker build -t babelcast-blackwell-mistral \
#   --build-arg BASE_IMAGE=runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04 \
#   --build-arg CUDA_INDEX=cu128 .

ARG BASE_IMAGE=runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04
FROM ${BASE_IMAGE}

ARG CUDA_INDEX=cu124
ARG CONF_LLM_MODEL=translategemma
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CONF_LLM_MODEL=${CONF_LLM_MODEL}

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 sox libsox-dev curl \
    && rm -rf /var/lib/apt/lists/*

# PyTorch (CUDA version determined by CUDA_INDEX)
RUN pip install --no-cache-dir --upgrade torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/${CUDA_INDEX}

# Python dependencies
RUN pip install --no-cache-dir \
    "faster-whisper>=1.1.0" \
    "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" \
    python-multipart httpx soundfile numpy \
    "huggingface-hub>=0.26.0" hf_transfer \
    "pydantic-settings>=2.0" websockets

# Install llama-cpp-python with CUDA support
RUN pip install --no-cache-dir llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/${CUDA_INDEX} && \
    pip install --no-cache-dir "llama-cpp-python[server]"

# Install qwen-tts (official, WITH deps) + transformers 4.57.3
# DO NOT install faster-qwen3-tts (CUDA graphs corrupt audio output)
RUN pip install --no-cache-dir "transformers==4.57.3" "accelerate>=1.12.0" \
    librosa einops onnxruntime sox && \
    pip install --no-cache-dir "qwen-tts>=0.1.1" && \
    find / -path '*/qwen_tts/*.py' -exec sed -i 's/fix_mistral_regex=True,//' {} + 2>/dev/null

# Speaker verification for voice cloning (ECAPA-TDNN, Apache 2.0, no auth token)
RUN pip install --no-cache-dir speechbrain

# Cleanup
RUN rm -rf /root/.cache/pip /tmp/pip-*

# ── Pre-cache models at build time (saves 60-90s on first boot) ──────────
# Whisper large-v3-turbo (~300MB), TTS model, speaker embedding
# LLM GGUF is too large (~7GB) — downloaded at runtime by start.sh
ENV HF_HOME=/app/.cache/huggingface
ENV HF_HUB_ENABLE_HF_TRANSFER=1
RUN python3 -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('Systran/faster-whisper-large-v3-turbo'); \
print('Whisper cached')" && \
    python3 -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base'); \
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice'); \
print('TTS models cached (Base + CustomVoice)')" && \
    python3 -c "\
from speechbrain.inference.speaker import EncoderClassifier; \
EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', run_opts={'device': 'cpu'}); \
print('ECAPA-TDNN cached')" || echo "WARNING: model pre-cache failed (will download at boot)"

# Copy application code
COPY api/ /app/api/
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Run as non-root user for security
RUN useradd -m -s /bin/bash -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["/app/start.sh"]
