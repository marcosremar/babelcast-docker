"""Whisper large-v3-turbo service for French speech transcription."""

import io
import logging

logger = logging.getLogger(__name__)


class WhisperService:
    """Transcription service with injectable configuration."""

    def __init__(self, model_name: str, device: str, compute_type: str):
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def load(self):
        from faster_whisper import WhisperModel

        logger.info(f"Loading Whisper {self._model_name}...")
        self._model = WhisperModel(
            self._model_name, device=self._device, compute_type=self._compute_type
        )
        logger.info("Whisper loaded.")

    def transcribe(self, audio_bytes: bytes, language: str = "fr", prompt: str = "") -> dict:
        """Transcribe audio bytes. Returns dict with text, language, duration.

        Args:
            prompt: Previous transcription text for context (Whisper initial_prompt).
                    Improves consistency of names, terms, and split sentences.
        """
        if self._model is None:
            self.load()
        logger.debug("Transcribing %d bytes, language=%s prompt_len=%d", len(audio_bytes), language, len(prompt))
        kwargs = dict(
            language=language,
            beam_size=5,  # beam search for better accuracy (especially technical/academic terms)
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
        )
        if prompt:
            kwargs["initial_prompt"] = prompt
        segments, info = self._model.transcribe(
            io.BytesIO(audio_bytes),
            **kwargs,
        )
        text = " ".join(seg.text for seg in segments).strip()
        logger.info("Transcription: lang=%s prob=%.3f duration=%.2fs text='%s'",
                     info.language, info.language_probability, info.duration, text[:100])
        return {
            "text": text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 2),
        }
