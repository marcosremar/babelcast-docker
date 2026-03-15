"""Centralized configuration with env-var overrides via pydantic-settings."""

import os

from pydantic_settings import BaseSettings


def _default_hf_token() -> str:
    """Fallback: read HF_TOKEN env var if CONF_HF_TOKEN is not set."""
    return os.environ.get("HF_TOKEN", "")


class Settings(BaseSettings):
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    llama_base_url: str = "http://127.0.0.1:8002"
    llm_model: str = "translategemma"  # "translategemma" or "mistral"
    tts_model_id: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    tts_device: str = "cuda:0"
    tts_enabled: bool = True
    voice_clone_enabled: bool = True
    voice_clone_min_audio_s: float = 15.0
    # Speaker verification — only collect audio from the target speaker
    speaker_verify_enabled: bool = True
    speaker_verify_threshold: float = 0.60
    speaker_verify_device: str = "cpu"
    # HuggingFace token (for pyannote gated models)
    # Set via CONF_HF_TOKEN or HF_TOKEN
    hf_token: str = _default_hf_token()

    model_config = {"env_prefix": "CONF_"}
