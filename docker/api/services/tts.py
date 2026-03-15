"""Qwen3-TTS service using official qwen-tts package.

Uses Qwen3TTSModel with bfloat16. Only ONE patch: check_model_inputs no-op.
DO NOT patch pad_token_id, ROPE, DynamicCache, or ALL_ATTENTION_FUNCTIONS.
"""

# Two patches needed BEFORE qwen_tts import:
# 1. check_model_inputs no-op (qwen_tts imports it, transformers 4.57.3 lacks it)
# 2. PretrainedConfig.__init_subclass__ to auto-set pad_token_id = eos_token_id
#    (NOT pad_token_id=0 which corrupts codec. Use eos_token_id=2150)
try:
    import transformers.utils.generic as _tg
    if not hasattr(_tg, 'check_model_inputs'):
        _tg.check_model_inputs = lambda func=None: func if func is not None else (lambda f: f)
except Exception:
    pass

try:
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS as _ROPE
    if "default" not in _ROPE:
        import torch as _t
        def _dr(c, d, **kw):
            b = c.rope_theta; p = getattr(c, "partial_rotary_factor", 1.0)
            h = getattr(c, "head_dim", c.hidden_size // c.num_attention_heads)
            dim = int(h * p)
            return 1.0 / (b ** (_t.arange(0, dim, 2, dtype=_t.int64).float().to(d) / dim)), 1.0
        _ROPE["default"] = _dr
except Exception:
    pass

try:
    from transformers import PretrainedConfig as _PC
    _pc_orig_init = _PC.__init__
    def _pc_patched_init(self, *a, **kw):
        _pc_orig_init(self, *a, **kw)
        if not hasattr(self, 'pad_token_id') or self.pad_token_id is None:
            self.pad_token_id = getattr(self, 'eos_token_id', 2150) or 2150
    _PC.__init__ = _pc_patched_init
except Exception:
    pass

import io
import logging
import tempfile

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)


class TTSService:
    """Text-to-speech service with preset speakers and voice cloning.

    Loads TWO models:
      - CustomVoice: loaded immediately for preset speakers (Ryan, Vivian, etc.)
      - Base: lazy-loaded on first clone request (voice cloning via ref audio + ref text)
    """

    # Model IDs
    _CUSTOM_VOICE_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    _BASE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

    def __init__(self, model_id: str, device: str):
        self._model_id = model_id
        self._device = device
        # Dual-model: CustomVoice for presets, Base for cloning
        self._custom_model = None
        self._base_model = None
        # Legacy compat: keep _model pointing to the custom model
        self._model = None
        self._is_base_model = "Base" in model_id

    def _fix_pad_token(self, model) -> None:
        """Fix: set pad_token_id = eos_token_id on ALL internal configs."""
        eos_id = 2150  # Qwen3-TTS eos_token_id
        for obj in [model] + [getattr(model, a, None) for a in dir(model)]:
            if obj is not None and hasattr(obj, 'config'):
                cfg = obj.config if hasattr(obj.config, 'pad_token_id') or hasattr(obj.config, 'eos_token_id') else None
                if cfg is not None and (not hasattr(cfg, 'pad_token_id') or cfg.pad_token_id is None):
                    cfg.pad_token_id = getattr(cfg, 'eos_token_id', eos_id) or eos_id
                    log.debug("Set %s.config.pad_token_id = %d", type(obj).__name__, cfg.pad_token_id)

    def load(self):
        import torch
        from qwen_tts import Qwen3TTSModel

        # Always load CustomVoice first — presets work immediately
        custom_id = self._CUSTOM_VOICE_ID
        log.info("Loading qwen-tts CustomVoice (%s) on %s with bfloat16...", custom_id, self._device)
        self._custom_model = Qwen3TTSModel.from_pretrained(
            custom_id,
            device_map=self._device,
            dtype=torch.bfloat16,
        )
        # DO NOT call _fix_pad_token — transformers auto-sets pad_token_id correctly
        self._model = self._custom_model  # legacy compat
        log.info("CustomVoice loaded. Speakers: %s", self._custom_model.get_supported_speakers())

    def _ensure_base_model(self):
        """Lazy-load Base model only when cloning is first requested."""
        if self._base_model is not None:
            return
        import torch
        from qwen_tts import Qwen3TTSModel

        log.info("Loading qwen-tts Base (%s) on %s with bfloat16 (first clone request)...",
                 self._BASE_MODEL_ID, self._device)
        self._base_model = Qwen3TTSModel.from_pretrained(
            self._BASE_MODEL_ID,
            device_map=self._device,
            dtype=torch.bfloat16,
        )
        # DO NOT call _fix_pad_token on Base model — it corrupts voice cloning
        # transformers auto-sets pad_token_id=eos_token_id=2150 correctly
        log.info("Base model loaded — voice cloning ready")

    @property
    def is_base_model(self) -> bool:
        return self._is_base_model

    # ── Preset speaker synthesis (CustomVoice model) ─────────────────────

    def synthesize(self, text: str, language: str = "English", speaker: str = "Ryan") -> bytes:
        """Synthesize speech with a preset speaker. Returns WAV bytes."""
        if self._custom_model is None:
            self.load()

        max_tokens = min(512, max(96, len(text) * 4))
        log.debug("TTS: speaker=%s lang=%s max_tokens=%d text='%s'",
                  speaker, language, max_tokens, text[:80])

        wavs, sr = self._custom_model.generate_custom_voice(
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
        if self._custom_model is None:
            self.load()

        max_tokens = min(512, max(96, len(text) * 4))
        log.debug("TTS streaming: speaker=%s lang=%s text='%s'", speaker, language, text[:80])

        for audio_chunk, sr, timing in self._custom_model.generate_custom_voice_streaming(
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

    def synthesize_clone_from_path(self, text: str, language: str,
                                   ref_path: str, ref_text: str) -> bytes:
        """Synthesize speech cloning from a WAV file path. Returns WAV bytes."""
        self._ensure_base_model()
        log.debug("TTS clone from path: lang=%s ref=%s text='%s'", language, ref_path, text[:80])
        wavs, sr = self._base_model.generate_voice_clone(
            text=text, language=language,
            ref_audio=ref_path, ref_text=ref_text,
        )
        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def synthesize_clone(self, text: str, language: str,
                         ref_audio: np.ndarray, ref_text: str) -> bytes:
        """Synthesize speech cloning a voice. Returns WAV bytes.

        Uses the Base model (lazy-loaded on first call).
        """
        self._ensure_base_model()

        ref_path = self._save_ref_audio(ref_audio)
        log.debug("TTS clone: lang=%s ref=%.1fs text='%s'",
                  language, len(ref_audio) / 16000, text[:80])
        try:
            max_tokens = min(512, max(96, len(text) * 4))
            wavs, sr = self._base_model.generate_voice_clone(
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
        """Yield (audio_chunk, sample_rate) tuples using a cloned voice.

        Uses the Base model (lazy-loaded on first call).
        """
        self._ensure_base_model()

        ref_path = self._save_ref_audio(ref_audio)
        log.debug("TTS clone stream: lang=%s ref=%.1fs text='%s'",
                  language, len(ref_audio) / 16000, text[:80])
        try:
            max_tokens = min(512, max(96, len(text) * 4))
            for audio_chunk, sr, timing in self._base_model.generate_voice_clone_streaming(
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
