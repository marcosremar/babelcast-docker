"""Translation service — backwards-compatible wrapper.

Delegates to services.translation.TranslationService; keeps the old module-level API
(translate, health_check) so that setup_and_run.sh / runpod_gpu.py keep working.
"""

from config import Settings as _S
from services.translation import TranslationService as _TS

_settings = _S()
_svc = _TS(_settings.llama_base_url)

translate = _svc.translate
health_check = _svc.health_check
