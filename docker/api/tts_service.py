"""TTS service — backwards-compatible wrapper.

Delegates to services.tts.TTSService; keeps the old module-level API
(load_model, synthesize, synthesize_streaming) so that setup_and_run.sh / runpod_gpu.py keep working.
"""

from config import Settings as _S
from services.tts import TTSService as _TTS

_settings = _S()
_svc = _TTS(_settings.tts_model_id, _settings.tts_device)

load_model = _svc.load
synthesize = _svc.synthesize
synthesize_streaming = _svc.synthesize_streaming
