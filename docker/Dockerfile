# Use RunPod's PyTorch image (Python 3.11, CUDA 12.4)
# PyTorch 2.6 stable supports sm_89 (RTX 4090) and sm_90 (H100)
# For RTX 5090 (sm_120 Blackwell), use a different base image or nightly build
FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

ARG CONF_LLM_MODEL=translategemma
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CONF_LLM_MODEL=${CONF_LLM_MODEL}

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 sox libsox-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade PyTorch to 2.6 stable (CUDA 12.4, supports sm_89/sm_90)
RUN pip install --no-cache-dir --upgrade torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu124

# Python dependencies
RUN pip install --no-cache-dir \
    "faster-whisper>=1.1.0" \
    "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" \
    python-multipart httpx soundfile numpy \
    "huggingface-hub>=0.26.0" hf_transfer \
    "pydantic-settings>=2.0" websockets

# Install llama-cpp-python with CUDA support
RUN pip install --no-cache-dir llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 && \
    pip install --no-cache-dir "llama-cpp-python[server]"

# Install faster-qwen3-tts (CUDA graphs, 5-6x faster than qwen-tts)
# qwen-tts 0.1.1 pins transformers==4.57.3 but its code uses 5.x symbols.
# We keep 4.57.3 and backport the 3 missing symbols in tts.py (simpler than
# upgrading transformers which cascades into torch/torchvision/RoPE breakage).
RUN pip install --no-cache-dir "transformers==4.57.3" "accelerate>=1.12.0" \
    librosa einops onnxruntime sox && \
    pip install --no-cache-dir --no-deps "qwen-tts>=0.1.1" && \
    pip install --no-cache-dir --no-deps "faster-qwen3-tts>=0.2.1"

# Speaker verification for voice cloning (identifies target speaker)
# pyannote.audio pulls torchvision from PyPI (CPU-only), overriding the CUDA version.
# Re-install torch/torchvision/torchaudio from cu124 index to fix the mismatch.
RUN pip install --no-cache-dir pyannote.audio && \
    pip install --no-cache-dir --upgrade torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu124

# Cleanup
RUN rm -rf /root/.cache/pip /tmp/pip-*

# Copy application code
COPY api/ /app/api/
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# Models downloaded on first boot (fast with hf_transfer)
CMD ["/app/start.sh"]
