"""FastAPI dependency injection — service singletons, overridable in tests."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from config import Settings
from services.whisper import WhisperService
from services.translation import TranslationService
from services.voice_profile import VoiceProfileManager

logger = logging.getLogger(__name__)

# Module-level singletons (created once, reused across requests)
_whisper: Optional[WhisperService] = None
_translator: Optional[TranslationService] = None
_tts: Optional[TTSService] = None
_voice_profile: Optional[VoiceProfileManager] = None
_speaker_verifier = None  # Optional[SpeakerVerifier]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_whisper() -> WhisperService:
    global _whisper
    if _whisper is None:
        s = get_settings()
        _whisper = WhisperService(s.whisper_model, s.whisper_device, s.whisper_compute_type)
    return _whisper


def get_translator():
    global _translator
    if _translator is None:
        s = get_settings()
        _translator = TranslationService(s.llama_base_url, s.llm_model)
    return _translator


def get_tts():
    """Lazy-load TTSService (avoids importing transformers/torchvision at startup)."""
    global _tts
    s = get_settings()
    if not s.tts_enabled:
        return None
    if _tts is None:
        from services.tts import TTSService
        _tts = TTSService(s.tts_model_id, s.tts_device)
    return _tts


def _get_speaker_verifier():
    """Lazy-init speaker verifier (pyannote/embedding). Returns None if unavailable."""
    global _speaker_verifier
    s = get_settings()
    if not s.speaker_verify_enabled:
        return None
    if _speaker_verifier is not None:
        return _speaker_verifier
    try:
        from services.speaker_id import SpeakerVerifier
        _speaker_verifier = SpeakerVerifier(
            device=s.speaker_verify_device,
            hf_token=s.hf_token,
        )
        logger.info("SpeakerVerifier created (device=%s)", s.speaker_verify_device)
        return _speaker_verifier
    except Exception as e:
        logger.warning("Speaker verification unavailable: %s "
                       "(install pyannote.audio and set CONF_HF_TOKEN)", e)
        return None


def get_voice_profile() -> Optional[VoiceProfileManager]:  # noqa: UP007
    global _voice_profile
    s = get_settings()
    if not s.voice_clone_enabled:
        return None
    if _voice_profile is None:
        verifier = _get_speaker_verifier()
        _voice_profile = VoiceProfileManager(
            min_audio_s=s.voice_clone_min_audio_s,
            verifier=verifier,
            verify_threshold=s.speaker_verify_threshold,
        )
        if verifier:
            logger.info("Voice profile with speaker verification (threshold=%.2f)",
                        s.speaker_verify_threshold)
        else:
            logger.info("Voice profile WITHOUT speaker verification")
    return _voice_profile


def reset():
    """Reset singletons — used in tests."""
    global _whisper, _translator, _tts, _voice_profile, _speaker_verifier
    _whisper = None
    _translator = None
    _tts = None
    _voice_profile = None
    _speaker_verifier = None
    get_settings.cache_clear()
