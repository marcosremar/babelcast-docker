"""
BabelCast Qwen3-TTS — Standalone TTS Server

Endpoints:
  GET  /health          - Health check
  POST /v1/tts          - Text → WAV audio (preset speaker)
  POST /v1/tts/stream   - Text → streaming WAV chunks
  POST /v1/audio/speech - OpenAI-compatible TTS endpoint
"""

import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import soundfile as sf
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from config import Settings
from services.tts import TTSService

logger = logging.getLogger(__name__)
_start_time = time.time()
_executor = ThreadPoolExecutor(max_workers=2)

# ── Settings & TTS singleton ──────────────────────────────────────────────

_settings = Settings()
# Default to CustomVoice for standalone TTS (supports preset speakers like Ryan, Aiden)
# Override with CONF_TTS_MODEL_ID env var if needed
_tts_model_id = _settings.tts_model_id
if "Base" in _tts_model_id:
    _tts_model_id = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_tts: Optional[TTSService] = None


def get_tts() -> TTSService:
    global _tts
    if _tts is None:
        _tts = TTSService(_tts_model_id, _settings.tts_device)
    return _tts


# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(title="BabelCast Qwen3-TTS", version="1.0.0")


@app.get("/health")
async def health():
    tts = get_tts()
    tts_status = "loaded" if tts._model is not None else "ready"
    return {
        "status": "ok",
        "service": "qwen3-tts",
        "uptime_s": int(time.time() - _start_time),
        "model": _tts_model_id,
        "tts": tts_status,
    }


@app.post("/v1/tts")
async def api_tts(body: dict):
    """Synthesize speech from text. Returns WAV audio."""
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse(status_code=400, content={"error": "Empty text"})

    language = body.get("language", "English")
    speaker = body.get("speaker", "Ryan")

    import asyncio
    import functools

    loop = asyncio.get_running_loop()
    tts = get_tts()
    try:
        wav_bytes = await loop.run_in_executor(
            _executor, functools.partial(tts.synthesize, text, language, speaker)
        )
    except Exception as e:
        logger.exception("TTS error")
        return JSONResponse(status_code=500, content={"error": str(e)})

    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/v1/tts/stream")
async def api_tts_stream(body: dict):
    """Streaming TTS — returns WAV chunks as they're generated."""
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse(status_code=400, content={"error": "Empty text"})

    language = body.get("language", "English")
    speaker = body.get("speaker", "Ryan")
    tts = get_tts()

    def generate():
        try:
            for audio_chunk, sr in tts.synthesize_streaming(text, language, speaker):
                chunk_io = io.BytesIO()
                sf.write(chunk_io, audio_chunk, sr, format="WAV")
                yield chunk_io.getvalue()
        except Exception as e:
            logger.exception("TTS streaming error: %s", e)

    return StreamingResponse(generate(), media_type="audio/wav")


class SpeechRequest(BaseModel):
    model: str = "qwen3-tts"
    input: str
    voice: str = "Ryan"
    response_format: str = "wav"
    speed: float = 1.0


@app.post("/v1/audio/speech")
async def api_audio_speech(body: SpeechRequest):
    """OpenAI-compatible TTS endpoint."""
    if not body.input.strip():
        return JSONResponse(status_code=400, content={"error": "Empty input"})

    import asyncio
    import functools

    loop = asyncio.get_running_loop()
    tts = get_tts()
    try:
        wav_bytes = await loop.run_in_executor(
            _executor, functools.partial(tts.synthesize, body.input, "English", body.voice)
        )
    except Exception as e:
        logger.exception("TTS error")
        return JSONResponse(status_code=500, content={"error": str(e)})

    return Response(content=wav_bytes, media_type="audio/wav")
