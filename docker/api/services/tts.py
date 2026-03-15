"""Qwen3-TTS service using faster-qwen3-tts (CUDA graphs, real-time streaming).

5-6x faster than the official qwen-tts package on RTX 4090.
Supports streaming chunk generation for low-latency audio output.

Uses CustomVoice model for preset speakers + voice cloning.
"""

# Early patches — MUST run before any transformers model import
# These fix transformers 4.57.3 compat issues with qwen-tts 0.1.1
try:
    from transformers import PretrainedConfig
    _orig_init = PretrainedConfig.__init__
    def _patched_init(self, *a, **kw):
        _orig_init(self, *a, **kw)
        if not hasattr(self, 'pad_token_id') or self.pad_token_id is None:
            self.pad_token_id = 0
    PretrainedConfig.__init__ = _patched_init
except Exception:
    pass

try:
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS
    if "default" not in ROPE_INIT_FUNCTIONS:
        import torch as _t
        def _dr(c, d, **kw):
            b = c.rope_theta
            p = getattr(c, "partial_rotary_factor", 1.0)
            h = getattr(c, "head_dim", c.hidden_size // c.num_attention_heads)
            dim = int(h * p)
            return 1.0 / (b ** (_t.arange(0, dim, 2, dtype=_t.int64).float().to(d) / dim)), 1.0
        ROPE_INIT_FUNCTIONS["default"] = _dr
except Exception:
    pass

try:
    from transformers.cache_utils import DynamicCache
    if not hasattr(DynamicCache, "__getitem__"):
        DynamicCache.__getitem__ = lambda s, i: (s.layers[i].keys, s.layers[i].values) if hasattr(s, "layers") else (s.key_cache[i], s.value_cache[i])
        DynamicCache.__len__ = lambda s: len(s.layers) if hasattr(s, "layers") else len(s.key_cache)
except Exception:
    pass

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

logger = logging.getLogger(__name__)


def _patch_transformers_compat():
    """All patches needed for qwen-tts 0.1.1 + transformers 4.57.3.

    These patches were previously blamed for corrupting audio, but the real
    cause was faster-qwen3-tts CUDA graphs. With official qwen_tts (no CUDA
    graphs), these patches are safe and required.
    """
    """Backport transformers 5.x symbols to 4.57.x for qwen-tts 0.1.1.

    qwen-tts 0.1.1 code imports symbols from transformers 5.x
    (ALL_ATTENTION_FUNCTIONS, GradientCheckpointingLayer, check_model_inputs)
    but its PyPI metadata pins transformers==4.57.3. Instead of upgrading
    transformers (which causes cascading torch/torchvision/RoPE breakage),
    we stay on 4.57.x and backport the 3 missing symbols.
    """
    import math
    import sys
    import types

    import torch
    import transformers
    import transformers.modeling_utils as mu

    # 1. ALL_ATTENTION_FUNCTIONS — dict of attention implementations
    if not hasattr(mu, "ALL_ATTENTION_FUNCTIONS"):
        def _eager_attention(query, key, value, attn_mask=None, dropout_p=0.0, **kw):
            scale = 1.0 / math.sqrt(query.size(-1))
            w = torch.matmul(query, key.transpose(-2, -1)) * scale
            if attn_mask is not None:
                w = w + attn_mask
            w = torch.nn.functional.softmax(w, dim=-1)
            if dropout_p > 0.0:
                w = torch.nn.functional.dropout(w, p=dropout_p)
            return torch.matmul(w, value), w

        mu.ALL_ATTENTION_FUNCTIONS = {
            "default": _eager_attention,
            "eager": _eager_attention,
            "sdpa": torch.nn.functional.scaled_dot_product_attention,
            "flash_attention_2": _eager_attention,
        }
        logger.debug("Backported ALL_ATTENTION_FUNCTIONS")

    # 2. GradientCheckpointingLayer — base class for decoder layers
    if not hasattr(transformers, "modeling_layers"):
        mod = types.ModuleType("transformers.modeling_layers")
        transformers.modeling_layers = mod
        sys.modules["transformers.modeling_layers"] = mod
    if not hasattr(transformers.modeling_layers, "GradientCheckpointingLayer"):
        class GradientCheckpointingLayer(torch.nn.Module):
            pass
        transformers.modeling_layers.GradientCheckpointingLayer = GradientCheckpointingLayer
        logger.debug("Backported GradientCheckpointingLayer")

    # 3. Replace check_model_inputs with no-op decorator
    # In 4.57.x, check_model_inputs validates kwargs and blocks 'inputs_embeds'
    # which qwen-tts's decoder passes. Replace with a no-op that passes through.
    import transformers.utils.generic as _tg

    def _noop(func=None):
        return func if func is not None else (lambda f: f)
    _tg.check_model_inputs = _noop
    logger.debug("Replaced check_model_inputs with no-op")

_compat_patched = False


def _ensure_compat():
    """Run the compat patch once, lazily (not at import time).

    This avoids crashing the server on startup when torchvision is broken
    (e.g. a dependency installed CPU-only torchvision over CUDA build).
    """
    global _compat_patched
    if not _compat_patched:
        _patch_transformers_compat()
        _compat_patched = True


class TTSService:
    """Text-to-speech service with voice cloning and streaming support."""

    def __init__(self, model_id: str, device: str):
        self._model_id = model_id
        self._device = device
        self._model = None
        self._is_base_model = "Base" in model_id

    def load(self):
        _ensure_compat()

        # Use official qwen_tts (reliable audio quality) instead of faster_qwen3_tts
        # (CUDA graphs produce garbled/incomprehensible audio with transformers 4.57.x patches)
        # Fix: patch pad_token_id on ALL config classes before loading model
        # Qwen3TTSTalkerConfig is created during generate_custom_voice(), not from_pretrained()
        from transformers import PretrainedConfig
        if not hasattr(PretrainedConfig, '_orig_pad_token_id'):
            _orig_init = PretrainedConfig.__init__
            def _patched_init(self_cfg, *args, **kwargs):
                _orig_init(self_cfg, *args, **kwargs)
                if not hasattr(self_cfg, 'pad_token_id') or self_cfg.pad_token_id is None:
                    self_cfg.pad_token_id = 0
            PretrainedConfig.__init__ = _patched_init
            PretrainedConfig._orig_pad_token_id = True
            logger.debug("Patched PretrainedConfig.__init__ to set pad_token_id=0")

        from qwen_tts import Qwen3TTSModel
        logger.info(f"Loading qwen-tts ({self._model_id})...")
        self._model = Qwen3TTSModel.from_pretrained(self._model_id)
        logger.info("qwen-tts loaded. Base model: %s", self._is_base_model)

    @property
    def is_base_model(self) -> bool:
        return self._is_base_model

    # ── Preset speaker synthesis (CustomVoice model only) ─────────────

    def synthesize(self, text: str, language: str = "English", speaker: str = "Ryan") -> bytes:
        """Synthesize speech with a preset speaker. Returns complete WAV bytes.
        Only works with CustomVoice model — Base model requires voice cloning."""
        if self._model is None:
            self.load()
        if self._is_base_model:
            raise RuntimeError("Base model does not support preset speakers. Use synthesize_clone().")

        # Limit max_new_tokens based on text length to prevent infinite generation.
        # ~12 tokens per character at 12Hz codec = ~0.08s per char. Cap at 1024.
        # ~12Hz codec: ~3-5 tokens per character. Cap at 512 to prevent infinite generation.
        max_tokens = min(512, max(96, len(text) * 4))
        logger.debug("TTS synthesize: speaker=%s lang=%s max_tokens=%d text='%s'",
                      speaker, language, max_tokens, text[:80])
        wavs, sr = self._model.generate_custom_voice(
            text=text,
            speaker=speaker,
            language=language,
            max_new_tokens=max_tokens,
            temperature=0.5,  # lower = more stable/clear audio
        )

        buffer = io.BytesIO()
        sf.write(buffer, wavs[0], sr, format="WAV", subtype="PCM_16")
        buffer.seek(0)
        return buffer.read()

    def synthesize_streaming(self, text: str, language: str = "English",
                             speaker: str = "Ryan", chunk_size: int = 8):
        """Yield (audio_chunk, sample_rate) tuples with a preset speaker."""
        if self._model is None:
            self.load()
        if self._is_base_model:
            raise RuntimeError("Base model does not support preset speakers. Use synthesize_clone_streaming().")

        max_tokens = min(512, max(96, len(text) * 4))
        logger.debug("TTS streaming: speaker=%s lang=%s max_tokens=%d text='%s'",
                      speaker, language, max_tokens, text[:80])
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
        """Yield WAV bytes for each streaming chunk (preset speaker)."""
        for audio_chunk, sr in self.synthesize_streaming(text, language, speaker, chunk_size):
            buffer = io.BytesIO()
            sf.write(buffer, audio_chunk, sr, format="WAV")
            buffer.seek(0)
            yield buffer.read()

    # ── Voice cloning synthesis (Base model only) ────────────────────────

    def _save_ref_audio(self, ref_audio: np.ndarray) -> str:
        """Save reference audio to a temp WAV file (generate_voice_clone expects a path)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, ref_audio, 16000, format="WAV")
        return tmp.name

    def synthesize_clone(self, text: str, language: str,
                         ref_audio: np.ndarray, ref_text: str) -> bytes:
        """Synthesize speech cloning a voice from reference audio. Returns WAV bytes."""
        if self._model is None:
            self.load()

        ref_path = self._save_ref_audio(ref_audio)
        logger.debug("TTS clone: lang=%s ref=%.1fs text='%s'",
                      language, len(ref_audio) / 16000, text[:80])
        try:
            # ~12Hz codec: ~1 token per character of text, with minimum for short phrases
            max_tokens = min(512, max(64, len(text) * 2))
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

        buffer = io.BytesIO()
        sf.write(buffer, wavs[0], sr, format="WAV", subtype="PCM_16")
        buffer.seek(0)
        return buffer.read()

    def synthesize_clone_streaming(self, text: str, language: str,
                                   ref_audio: np.ndarray, ref_text: str,
                                   chunk_size: int = 8):
        """Yield (audio_chunk, sample_rate) tuples using a cloned voice."""
        if self._model is None:
            self.load()

        ref_path = self._save_ref_audio(ref_audio)
        logger.debug("TTS clone streaming: lang=%s ref=%.1fs text='%s'",
                      language, len(ref_audio) / 16000, text[:80])
        try:
            for audio_chunk, sr, timing in self._model.generate_voice_clone_streaming(
                text=text,
                language=language,
                ref_audio=ref_path,
                ref_text=ref_text,
                chunk_size=chunk_size,
            ):
                yield audio_chunk, sr
        finally:
            import os
            os.unlink(ref_path)

    def synthesize_clone_streaming_wav(self, text: str, language: str,
                                       ref_audio: np.ndarray, ref_text: str,
                                       chunk_size: int = 8):
        """Yield WAV bytes for each streaming chunk (cloned voice)."""
        for audio_chunk, sr in self.synthesize_clone_streaming(
            text, language, ref_audio, ref_text, chunk_size
        ):
            buffer = io.BytesIO()
            sf.write(buffer, audio_chunk, sr, format="WAV")
            buffer.seek(0)
            yield buffer.read()
