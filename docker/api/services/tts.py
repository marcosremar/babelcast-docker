"""Qwen3-TTS service using official qwen-tts package.

Uses Qwen3TTSModel with bfloat16. Only ONE patch: check_model_inputs no-op.
DO NOT patch pad_token_id, ROPE, DynamicCache, or ALL_ATTENTION_FUNCTIONS.
"""

# Only patch: check_model_inputs (qwen-tts imports it, transformers 4.57.3 lacks it)
try:
    import transformers.utils.generic as _tg
    if not hasattr(_tg, 'check_model_inputs'):
        _tg.check_model_inputs = lambda func=None: func if func is not None else (lambda f: f)
except Exception:
    pass

import io
import logging
import tempfile

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)


class TTSService:
    """Text-to-speech service with preset speakers and voice cloning."""

    def __init__(self, model_id: str, device: str):
        self._model_id = model_id
        self._device = device
        self._model = None
        self._is_base_model = "Base" in model_id

    def load(self):
        import torch
        from qwen_tts import Qwen3TTSModel

        log.info("Loading qwen-tts (%s) on %s with bfloat16...", self._model_id, self._device)
        self._model = Qwen3TTSModel.from_pretrained(
            self._model_id,
            device_map=self._device,
            dtype=torch.bfloat16,
        )
        log.info("qwen-tts loaded. Speakers: %s", self._model.get_supported_speakers())

    @property
    def is_base_model(self) -> bool:
        return self._is_base_model

    # ── Preset speaker synthesis (CustomVoice model) ─────────────────────

    def synthesize(self, text: str, language: str = "English", speaker: str = "Ryan") -> bytes:
        """Synthesize speech with a preset speaker. Returns WAV bytes."""
        if self._model is None:
            self.load()
        if self._is_base_model:
            raise RuntimeError("Base model requires voice cloning. Use synthesize_clone().")

        max_tokens = min(512, max(96, len(text) * 4))
        log.debug("TTS: speaker=%s lang=%s max_tokens=%d text='%s'",
                  speaker, language, max_tokens, text[:80])

        wavs, sr = self._model.generate_custom_voice(
            text=text,
            speaker=speaker,
            language=language,
            max_new_tokens=max_tokens,
            temperature=0.5,
        )

        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def synthesize_streaming(self, text: str, language: str = "English",
                             speaker: str = "Ryan", chunk_size: int = 8):
        """Yield (audio_chunk, sample_rate) tuples with a preset speaker."""
        if self._model is None:
            self.load()
        if self._is_base_model:
            raise RuntimeError("Base model requires voice cloning.")

        max_tokens = min(512, max(96, len(text) * 4))
        log.debug("TTS streaming: speaker=%s lang=%s text='%s'", speaker, language, text[:80])

        for audio_chunk, sr, timing in self._model.generate_custom_voice_streaming(
            text=text,
            speaker=speaker,
            language=language,
            chunk_size=chunk_size,
            max_new_tokens=max_tokens,
            temperature=0.5,
        ):
            yield audio_chunk, sr

    def synthesize_streaming_wav(self, text: str, language: str = "English",
                                 speaker: str = "Ryan", chunk_size: int = 8):
        """Yield WAV bytes for each streaming chunk."""
        for audio_chunk, sr in self.synthesize_streaming(text, language, speaker, chunk_size):
            buf = io.BytesIO()
            sf.write(buf, audio_chunk, sr, format="WAV", subtype="PCM_16")
            buf.seek(0)
            yield buf.read()

    # ── Voice cloning (Base model) ───────────────────────────────────────

    def _save_ref_audio(self, ref_audio: np.ndarray) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, ref_audio, 16000, format="WAV")
        return tmp.name

    def synthesize_clone(self, text: str, language: str,
                         ref_audio: np.ndarray, ref_text: str) -> bytes:
        """Synthesize speech cloning a voice. Returns WAV bytes."""
        if self._model is None:
            self.load()

        ref_path = self._save_ref_audio(ref_audio)
        log.debug("TTS clone: lang=%s ref=%.1fs text='%s'",
                  language, len(ref_audio) / 16000, text[:80])
        try:
            max_tokens = min(512, max(96, len(text) * 4))
            wavs, sr = self._model.generate_voice_clone(
                text=text,
                language=language,
                ref_audio=ref_path,
                ref_text=ref_text,
                max_new_tokens=max_tokens,
            )
        finally:
            import os
            os.unlink(ref_path)

        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def synthesize_clone_streaming(self, text: str, language: str,
                                   ref_audio: np.ndarray, ref_text: str,
                                   chunk_size: int = 8):
        """Yield (audio_chunk, sample_rate) tuples using a cloned voice."""
        if self._model is None:
            self.load()

        ref_path = self._save_ref_audio(ref_audio)
        log.debug("TTS clone stream: lang=%s ref=%.1fs text='%s'",
                  language, len(ref_audio) / 16000, text[:80])
        try:
            max_tokens = min(512, max(96, len(text) * 4))
            for audio_chunk, sr, timing in self._model.generate_voice_clone_streaming(
                text=text,
                language=language,
                ref_audio=ref_path,
                ref_text=ref_text,
                chunk_size=chunk_size,
                max_new_tokens=max_tokens,
            ):
                yield audio_chunk, sr
        finally:
            import os
            os.unlink(ref_path)
