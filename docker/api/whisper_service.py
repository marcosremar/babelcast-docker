"""Whisper service — backwards-compatible wrapper.

Delegates to services.whisper.WhisperService; keeps the old module-level API
(load_model, transcribe) so that setup_and_run.sh / runpod_gpu.py keep working.
"""

from config import Settings as _S
from services.whisper import WhisperService as _WS

_settings = _S()
_svc = _WS(_settings.whisper_model, _settings.whisper_device, _settings.whisper_compute_type)

load_model = _svc.load
transcribe = _svc.transcribe
