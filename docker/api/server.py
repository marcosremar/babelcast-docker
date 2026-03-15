"""
BabelCast Translation API Gateway

Pipeline: French Audio → Whisper (transcription) → LLM translation (TranslateGemma 12B / Mistral 7B) → Qwen3-TTS (English speech)

Endpoints:
  GET  /health                - Health check (with transport info for client auto-discovery)
  GET  /logs                  - Recent application logs (for remote debugging)
  POST /v1/speech             - Full pipeline: Audio → STT → translate → TTS → JSON (used by AIClient)
  POST /v1/transcribe         - Audio → text (Whisper STT)
  POST /v1/translate/text     - Text → translated text (LLM)
  POST /v1/translate          - Audio → transcribed + translated text
  POST /v1/translate/speech   - Audio → STT → translate → TTS (full pipeline, WAV response)
  POST /v1/tts                - Text → WAV audio
  POST /v1/tts/stream         - Text → streaming WAV chunks
  POST /api/stream-audio      - Audio → SSE streaming pipeline (STT → translate → TTS chunks)
  WS   /ws/stream             - WebSocket bidirectional audio streaming
  WS   /ws/audio              - WebSocket raw PCM audio from meet-teams-bot (STT → translate)
  WS   /ws/translate          - WebSocket text translation (legacy)
"""
from __future__ import annotations  # defer annotation eval — avoids importing TTSService at startup

import asyncio
import base64
import functools
import io
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Optional

from fastapi import Depends, FastAPI, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from logger import setup_logging
from deps import (
    get_settings, get_translator, get_tts, get_voice_profile, get_whisper,
)
from services.translation import TranslationService
from services.voice_profile import VoiceProfileManager
from services.whisper import WhisperService

if TYPE_CHECKING:
    from services.tts import TTSService

setup_logging()
logger = logging.getLogger("gateway")

# Thread pools for CPU/GPU-bound sync calls to avoid blocking uvicorn
# Separate pools for STT and TTS since they use different GPU models
_stt_executor = ThreadPoolExecutor(max_workers=2)
_tts_executor = ThreadPoolExecutor(max_workers=2)
# Legacy alias for code that doesn't distinguish
_executor = _stt_executor

# Shared language code → name mapping (used by pipeline, SSE, and WebSocket handlers)
LANG_MAP = {
    "fr": "French", "en": "English", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "zh": "Chinese", "ja": "Japanese",
}


def _extract_audio_numpy(audio_bytes: bytes) -> tuple:
    """Extract float32 numpy array and sample rate from WAV/audio bytes."""
    import soundfile as _sf
    buf = io.BytesIO(audio_bytes)
    try:
        data, sr = _sf.read(buf, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)  # mono
        return data, sr
    except Exception:
        return None, 0


def _feed_voice_profile(profile: VoiceProfileManager | None, audio_bytes: bytes,
                        transcript: str) -> bool:
    """Feed audio + transcript to the voice profile. Returns True if just locked."""
    if profile is None or profile.is_locked:
        return False
    audio_np, sr = _extract_audio_numpy(audio_bytes)
    if audio_np is None or len(audio_np) == 0:
        return False
    return profile.feed(audio_np, transcript, sample_rate=sr)


_last_tts_error: str = ""

def _tts_synthesize(tts: TTSService, text: str, language: str, speaker: str,
                    profile: VoiceProfileManager | None) -> bytes | None:
    """Synthesize using cloned voice if available, otherwise preset speaker.
    Returns None if Base model and no voice profile locked."""
    global _last_tts_error
    if profile is not None and profile.is_locked:
        try:
            result = tts.synthesize_clone(text, language, profile.ref_audio, profile.ref_text)
            _last_tts_error = ""
            return result
        except Exception as e:
            _last_tts_error = str(e)
            logger.exception("Voice clone synthesis failed: %s", e)
    if tts.is_base_model:
        return None
    return tts.synthesize(text, language=language, speaker=speaker)


def _tts_streaming(tts: TTSService, text: str, language: str, speaker: str,
                   profile: VoiceProfileManager | None):
    """Stream TTS using cloned voice if available, otherwise preset speaker.
    Yields nothing if Base model and no voice profile locked."""
    if profile is not None and profile.is_locked:
        try:
            yield from tts.synthesize_clone_streaming(text, language, profile.ref_audio, profile.ref_text)
            return
        except Exception as e:
            logger.warning("Voice clone streaming failed: %s", e)
    if tts.is_base_model:
        return
    yield from tts.synthesize_streaming(text, language=language, speaker=speaker)

# Startup phase tracking (visible via /health)
_startup_phase = "starting"
_startup_t0 = time.time()
_service_status = {
    "whisper": "pending",
    "llama_cpp": "pending",
    "tts": "pending",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load TTS first (fast), then Whisper + LLM in background."""
    global _startup_phase

    # 1. Load TTS first — fastest model, makes us useful immediately
    _startup_phase = "loading_tts"
    logger.info("Loading TTS model...")
    tts = get_tts()
    if tts is not None:
        try:
            tts.load()
            _service_status["tts"] = "loaded"
            logger.info("TTS loaded.")
        except Exception as e:
            logger.warning(f"TTS model failed to load: {e}. Disabling TTS.")
            get_settings().tts_enabled = False
            _service_status["tts"] = f"failed: {e}"
    else:
        _service_status["tts"] = "disabled"

    # 2. Mark ready as soon as TTS is loaded — Whisper + LLM load in background
    _startup_phase = "ready"
    _service_status["whisper"] = "loading"
    _service_status["llama_cpp"] = "loading"
    logger.info("Gateway ready (TTS active, Whisper + LLM loading in background)")

    async def _load_whisper_and_llm_background():
        loop = asyncio.get_event_loop()
        # Load Whisper first
        logger.info("[background] Loading Whisper model...")
        try:
            whisper = get_whisper()
            await loop.run_in_executor(None, whisper.load)
            _service_status["whisper"] = "loaded"
            logger.info("[background] Whisper loaded.")
        except Exception as e:
            logger.error("[background] Whisper failed to load: %s", e)
            _service_status["whisper"] = f"failed: {e}"

        # Then wait for llama.cpp
        logger.info("[background] Waiting for llama.cpp...")
        translator = get_translator()
        for attempt in range(60):  # up to 300s
            try:
                ok = await asyncio.wait_for(translator.health_check(), timeout=5.0)
            except asyncio.TimeoutError:
                ok = False
            if ok:
                _service_status["llama_cpp"] = "ready"
                logger.info("[background] llama.cpp ready after %ds.", (attempt + 1) * 5)
                return
            logger.debug("[background] llama.cpp not ready (attempt %d/60)", attempt + 1)
            await asyncio.sleep(5)
        # Keep polling even after timeout
        logger.warning("[background] llama.cpp not ready after 300s — polling every 30s")
        _service_status["llama_cpp"] = "loading (slow GPU)"
        while True:
            await asyncio.sleep(30)
            try:
                ok = await asyncio.wait_for(translator.health_check(), timeout=5.0)
            except asyncio.TimeoutError:
                ok = False
            if ok:
                _service_status["llama_cpp"] = "ready"
                logger.info("[background] llama.cpp eventually ready")
                return

    asyncio.create_task(_load_whisper_and_llm_background())

    yield


app = FastAPI(
    title="BabelCast Translation API",
    description="French → English real-time translation pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

_ALLOWED_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS or ["*"],
    allow_credentials=bool(_ALLOWED_ORIGINS),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
async def health(
    request: Request,
    translator: TranslationService = Depends(get_translator),
    tts: Optional[TTSService] = Depends(get_tts),
):
    try:
        llm_ok = await asyncio.wait_for(translator.health_check(), timeout=5.0)
    except asyncio.TimeoutError:
        llm_ok = False
        logger.warning("/health: LLM health check timed out after 5s")
    if llm_ok:
        _service_status["llama_cpp"] = "ready"

    host = request.headers.get("host", "localhost:8000")
    scheme = request.url.scheme
    ws_scheme = "wss" if scheme == "https" else "ws"

    # Human-readable phase detail
    phase_labels = {
        "starting": "Initializing API gateway...",
        "loading_whisper": "Loading Whisper STT model...",
        "loading_tts": "Loading TTS voice model...",
        "waiting_llm": "Waiting for LLM...",
        "ready": "All services operational",
    }

    # Healthy when phase is ready, LLM is up, and TTS is loaded
    all_ok = _startup_phase == "ready" and llm_ok and _service_status["tts"] in ("loaded", "disabled")

    return {
        "status": "ok" if all_ok else "degraded",
        "phase": _startup_phase,
        "detail": phase_labels.get(_startup_phase, _startup_phase),
        "uptime_s": int(time.time() - _startup_t0),
        "services": {
            "whisper": _service_status["whisper"],
            "llama_cpp": "ready" if llm_ok else _service_status["llama_cpp"],
            "tts": _service_status["tts"],
        },
        "streaming": "sse",
        "transports": {
            "sse": {"endpoint": f"{scheme}://{host}/api/stream-audio"},
            "websocket": {"url": f"{ws_scheme}://{host}/ws/stream"},
        },
    }


@app.get("/logs")
async def get_logs(
    lines: int = Query(200, ge=1, le=2000),
):
    """Return recent application logs for remote debugging."""
    from logger import LOGS_DIR
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"gateway_{today}.log"
    if not log_file.exists():
        return Response(content="(no log file found)", media_type="text/plain")
    text = log_file.read_text(encoding="utf-8", errors="replace")
    # Return last N lines
    tail = "\n".join(text.splitlines()[-lines:])
    return Response(content=tail, media_type="text/plain")


@app.post("/v1/transcribe")
async def api_transcribe(
    file: UploadFile = File(...),
    language: str = Query("fr", description="Source language code"),
    prompt: str = Query("", description="Previous transcription for context (Whisper initial_prompt)"),
    whisper: WhisperService = Depends(get_whisper),
):
    """Transcribe audio to text using Whisper large-v3-turbo."""
    # Validate file size (max 25MB)
    audio_bytes = await file.read()
    if len(audio_bytes) > 25 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "Audio file exceeds 25MB limit"})
    if len(audio_bytes) < 44:
        return JSONResponse(status_code=400, content={"error": "Audio file too small or empty"})
    # Sanitize filename for logging (prevent log injection)
    safe_filename = (file.filename or "unknown").replace("\n", "").replace("\r", "")[:100]
    logger.info("Transcribe request: file=%s size=%d lang=%s prompt_len=%d", safe_filename, len(audio_bytes), language, len(prompt))
    t0 = time.monotonic()
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor, functools.partial(whisper.transcribe, audio_bytes, language=language, prompt=prompt)
        )
        elapsed = time.monotonic() - t0
        logger.info("Transcribe result (%.2fs): %s", elapsed, result.get("text", "")[:120])
        return result
    except Exception:
        logger.exception("Transcribe failed")
        return JSONResponse(status_code=500, content={"error": "Transcription failed"})


@app.post("/v1/tts")
async def api_tts(
    body: dict,
    tts: Optional[TTSService] = Depends(get_tts),
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Synthesize speech from text. Returns WAV audio.
    Uses cloned voice if voice profile is locked, otherwise preset speaker."""
    if tts is None:
        return JSONResponse(status_code=501, content={"error": "TTS service not available."})
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse(status_code=400, content={"error": "Empty text"})
    language = body.get("language", "English")
    speaker = body.get("speaker", "Ryan")
    loop = asyncio.get_running_loop()
    wav_bytes = await loop.run_in_executor(
        _executor, functools.partial(_tts_synthesize, tts, text, language, speaker, profile)
    )
    if wav_bytes is None:
        return JSONResponse(status_code=503, content={
            "error": "Voice profile not ready. Send audio through /v1/speech first.",
            "voice_profile": profile.status() if profile else None,
            "tts_error": _last_tts_error or None,
        })
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/v1/tts/stream")
async def api_tts_stream(
    body: dict,
    tts: Optional[TTSService] = Depends(get_tts),
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Streaming TTS — returns WAV chunks as they're generated.

    First byte latency ~160ms on RTX 4090 vs 1-3s for batch synthesis.
    Supports voice cloning when a voice profile is locked.
    """
    if tts is None:
        return JSONResponse(status_code=501, content={"error": "TTS service not available."})
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse(status_code=400, content={"error": "Empty text"})
    language = body.get("language", "English")
    speaker = body.get("speaker", "Ryan")

    # Check if Base model without voice profile — can't synthesize
    if tts.is_base_model and (profile is None or not profile.is_locked):
        return JSONResponse(status_code=503, content={
            "error": "Voice profile not ready. Send audio through /v1/speech first.",
            "voice_profile": profile.status() if profile else None,
        })

    def generate():
        import soundfile as sf
        try:
            for audio_chunk, sr in _tts_streaming(tts, text, language, speaker, profile):
                chunk_io = io.BytesIO()
                sf.write(chunk_io, audio_chunk, sr, format="WAV")
                yield chunk_io.getvalue()
        except Exception as e:
            logger.exception("TTS streaming error: %s", e)
            # Fallback to batch synthesis
            try:
                wav_bytes = _tts_synthesize(tts, text, language, speaker, profile)
                if wav_bytes:
                    yield wav_bytes
            except Exception:
                logger.exception("TTS batch fallback also failed")

    return StreamingResponse(generate(), media_type="audio/wav")


# ============================================================================
# PIPELINE ENDPOINT (JSON in/out — used by AIClient.tryGpuPipeline)
# ============================================================================

@app.post("/v1/speech")
async def api_pipeline(
    request: Request,
    whisper: WhisperService = Depends(get_whisper),
    translator: TranslationService = Depends(get_translator),
    tts: Optional[TTSService] = Depends(get_tts),
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Full pipeline: Audio → STT → Translate → TTS → JSON.

    Accepts multipart/form-data:
      - audio: WAV/WebM file
      - system_prompt: LLM system prompt (optional)
      - language: source language code (default: "fr")
      - history: JSON array of chat messages (optional)

    Returns JSON:
      { transcription, response, audio_base64, content_type, timing }
    """
    content_type_header = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type_header:
        form = await request.form()
        audio_file = form.get("audio")
        if not audio_file:
            return JSONResponse({"error": "Missing 'audio' field"}, status_code=400)
        audio_bytes = await audio_file.read()
        language_code = form.get("language", "fr")
        target_code = form.get("target", "en")
        speaker = form.get("speaker", "Ryan")
    elif "audio/" in content_type_header or "application/octet-stream" in content_type_header:
        audio_bytes = await request.body()
        language_code = request.query_params.get("source", "fr")
        target_code = request.query_params.get("target", "en")
        speaker = request.query_params.get("speaker", "Ryan")
    else:
        return JSONResponse({"error": f"Unsupported content type: {content_type_header}"}, status_code=400)

    if not audio_bytes or len(audio_bytes) < 44:
        return JSONResponse({"error": "No audio data or too short"}, status_code=400)

    # Map language code to full name for translator
    source_lang = LANG_MAP.get(language_code, "French")
    target_lang = LANG_MAP.get(target_code, "English")

    loop = asyncio.get_running_loop()
    t_start = time.time()
    timing = {}

    try:
        # 1. STT
        transcription = await loop.run_in_executor(
            _stt_executor, functools.partial(whisper.transcribe, audio_bytes, language=language_code)
        )
        transcript = transcription["text"]
        timing["stt_ms"] = int((time.time() - t_start) * 1000)

        # Feed voice profile with source audio + transcript
        just_locked = _feed_voice_profile(profile, audio_bytes, transcript)
        if just_locked:
            logger.info("[Pipeline] Voice profile locked — switching to cloned voice")

        # 2. Translate
        t_llm = time.time()
        translation = await translator.translate(transcript, source_lang=source_lang, target_lang=target_lang)
        translated = translation["translated_text"]
        timing["llm_ms"] = int((time.time() - t_llm) * 1000)

        # 3. TTS (uses cloned voice if profile is locked)
        audio_b64 = ""
        ct = ""
        if tts is not None:
            t_tts = time.time()
            wav_bytes = await loop.run_in_executor(
                _tts_executor, functools.partial(
                    _tts_synthesize, tts, translated, target_lang, speaker, profile
                )
            )
            if wav_bytes:
                audio_b64 = base64.b64encode(wav_bytes).decode()
                ct = "audio/wav"
            timing["tts_ms"] = int((time.time() - t_tts) * 1000)

        timing["total_ms"] = int((time.time() - t_start) * 1000)
        logger.info("[Pipeline] STT=%dms LLM=%dms TTS=%dms Total=%dms | '%s' → '%s'",
                    timing.get("stt_ms", 0), timing.get("llm_ms", 0),
                    timing.get("tts_ms", 0), timing["total_ms"],
                    transcript[:60], translated[:60])

        result = {
            "transcription": transcript,
            "response": translated,
            "audio_base64": audio_b64,
            "content_type": ct,
            "timing": timing,
        }
        if profile is not None:
            result["voice_profile"] = profile.status()
        return result
    except Exception:
        logger.exception("[Pipeline] Error")
        return JSONResponse({"error": "Pipeline processing failed"}, status_code=500)


# ============================================================================
# SSE STREAMING ENDPOINT (matches parle-s2s / AI Gateway client protocol)
# ============================================================================

@app.post("/api/stream-audio")
async def stream_audio(
    request: Request,
    whisper: WhisperService = Depends(get_whisper),
    translator: TranslationService = Depends(get_translator),
    tts: Optional[TTSService] = Depends(get_tts),
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Full translation pipeline with SSE streaming output.

    Accepts:
      - multipart/form-data: audio file in 'audio' field
      - application/json: {"audio_base64": "..."} with base64-encoded audio

    SSE event protocol (compatible with AI Gateway SpeechClient):
      event: status     data: {"stage": "stt"}
      event: transcript data: {"transcript": "...", "stt_ms": N}
      event: status     data: {"stage": "llm"}
      event: response   data: {"response": "...", "llm_ms": N}
      event: status     data: {"stage": "tts"}
      event: audio      data: {"chunk": "<base64 WAV>", "index": N}
      event: complete   data: {"transcript":"...", "response":"...", "timing":{...}}
      event: error      data: {"message": "..."}
    """
    import soundfile as sf

    content_type = request.headers.get("content-type", "")
    source_lang = "French"
    target_lang = "English"
    speaker = "Ryan"
    language_code = "fr"

    if "multipart/form-data" in content_type:
        form = await request.form()
        audio_file = form.get("audio")
        if not audio_file:
            return JSONResponse({"error": "Missing 'audio' field"}, status_code=400)
        audio_bytes = await audio_file.read()
        source_lang = form.get("source_lang", "French")
        target_lang = form.get("target_lang", "English")
        speaker = form.get("speaker", "Ryan")
        language_code = form.get("language", "fr")
    elif "application/json" in content_type:
        body = await request.json()
        audio_b64 = body.get("audio_base64", "") or body.get("audio", "")
        if not audio_b64:
            return JSONResponse({"error": "Missing 'audio_base64' or 'audio' field"}, status_code=400)
        audio_bytes = base64.b64decode(audio_b64)
        source_lang = body.get("source_lang", "French")
        target_lang = body.get("target_lang", "English")
        speaker = body.get("speaker", "Ryan")
        language_code = body.get("language", "fr")
    else:
        audio_bytes = await request.body()
        language_code = request.query_params.get("source", "fr")
        target_code = request.query_params.get("target", "en")
        speaker = request.query_params.get("speaker", "Ryan")
        source_lang = LANG_MAP.get(language_code, "French")
        target_lang = LANG_MAP.get(target_code, "English")

    if not audio_bytes:
        return JSONResponse({"error": "No audio data received"}, status_code=400)
    if len(audio_bytes) > 10 * 1024 * 1024:
        return JSONResponse({"error": "Audio too large (max 10MB)"}, status_code=413)

    loop = asyncio.get_running_loop()

    def generate_sse():
        try:
            t_start = time.time()

            # 1. STT
            yield f"event: status\ndata: {json.dumps({'stage': 'stt'})}\n\n"
            transcription = whisper.transcribe(audio_bytes, language=language_code)
            transcript = transcription["text"]
            stt_ms = int((time.time() - t_start) * 1000)
            yield f"event: transcript\ndata: {json.dumps({'transcript': transcript, 'stt_ms': stt_ms})}\n\n"

            # Feed voice profile
            just_locked = _feed_voice_profile(profile, audio_bytes, transcript)
            if just_locked:
                yield f"event: voice_profile\ndata: {json.dumps({'state': 'locked'})}\n\n"

            # 2. Translate (sync wrapper for async translator)
            yield f"event: status\ndata: {json.dumps({'stage': 'llm'})}\n\n"
            t_llm = time.time()
            # Run async translate in a new event loop (we're in a sync generator)
            import asyncio as _asyncio
            _loop = _asyncio.new_event_loop()
            try:
                translation = _loop.run_until_complete(
                    translator.translate(transcript, source_lang=source_lang, target_lang=target_lang)
                )
            finally:
                _loop.close()
            translated = translation["translated_text"]
            llm_ms = int((time.time() - t_llm) * 1000)
            yield f"event: response\ndata: {json.dumps({'response': translated, 'llm_ms': llm_ms})}\n\n"

            # 3. TTS streaming
            if tts is not None:
                yield f"event: status\ndata: {json.dumps({'stage': 'tts'})}\n\n"
                t_tts = time.time()
                chunk_count = 0
                first_chunk_ms = 0

                try:
                    for audio_chunk, chunk_sr in _tts_streaming(
                        tts, translated, target_lang, speaker, profile
                    ):
                        chunk_io = io.BytesIO()
                        sf.write(chunk_io, audio_chunk, chunk_sr, format="WAV")
                        chunk_b64 = base64.b64encode(chunk_io.getvalue()).decode()
                        chunk_count += 1
                        if chunk_count == 1:
                            first_chunk_ms = int((time.time() - t_start) * 1000)
                            logger.info(f"[SSE] TTFC: {first_chunk_ms}ms")
                        yield f"event: audio\ndata: {json.dumps({'chunk': chunk_b64, 'index': chunk_count - 1})}\n\n"
                except Exception as tts_err:
                    logger.warning(f"[SSE] TTS streaming failed: {tts_err}, fallback to batch")
                    try:
                        wav_bytes = _tts_synthesize(tts, translated, target_lang, speaker, profile)
                        if wav_bytes:
                            fallback_b64 = base64.b64encode(wav_bytes).decode()
                            chunk_count = 1
                            yield f"event: audio\ndata: {json.dumps({'chunk': fallback_b64, 'index': 0, 'fallback': True})}\n\n"
                    except Exception as fb_err:
                        logger.error(f"[SSE] Fallback TTS also failed: {fb_err}")

                tts_ms = int((time.time() - t_tts) * 1000)
            else:
                tts_ms = 0
                chunk_count = 0
                first_chunk_ms = 0

            total_ms = int((time.time() - t_start) * 1000)
            logger.info(f"[SSE] Done: STT {stt_ms}ms | LLM {llm_ms}ms | TTS {tts_ms}ms | Total {total_ms}ms | {chunk_count} chunks")

            yield f"event: complete\ndata: {json.dumps({'transcript': transcript, 'response': translated, 'timing': {'stt_ms': stt_ms, 'llm_ms': llm_ms, 'tts_ms': tts_ms, 'total_ms': total_ms, 'ttfa_ms': first_chunk_ms, 'chunks': chunk_count}})}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"[SSE] Error: {e}")
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# WEBSOCKET STREAMING ENDPOINT (matches parle-s2s / AI Gateway client protocol)
# ============================================================================

@app.websocket("/ws/stream")
async def websocket_stream(
    ws: WebSocket,
    whisper: WhisperService = Depends(get_whisper),
    translator: TranslationService = Depends(get_translator),
    tts: Optional[TTSService] = Depends(get_tts),
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """WebSocket for bidirectional audio streaming.

    Query params (on connect URL):
      - source: source language code (default: "fr")
      - target: target language code (default: "en")
      - speaker: TTS voice (default: "Ryan")

    Client sends:
      - Binary data: WAV audio for full pipeline (STT → translate → TTS)
      - JSON {"type": "ping"}: keepalive
      - JSON {"type": "tts", "text": "..."}: TTS-only
      - JSON {"type": "text", "message": "..."}: translate + TTS
      - JSON {"type": "config", "source": "fr", "target": "en"}: update langs

    Server sends:
      - JSON status updates: {"status": "processing", "stage": "stt|llm|tts"}
      - JSON with transcript/response
      - Binary WAV chunks (TTS audio)
      - JSON {"status": "complete", ...} with timing
    """
    await ws.accept()
    logger.info("[WS] Client connected")

    # Session-level language config from query params
    ws_source_code = ws.query_params.get("source", "fr")
    ws_target_code = ws.query_params.get("target", "en")
    ws_source_lang = LANG_MAP.get(ws_source_code, "French")
    ws_target_lang = LANG_MAP.get(ws_target_code, "English")
    ws_speaker = ws.query_params.get("speaker", "Ryan")

    try:
        while True:
            data = await ws.receive()

            if "bytes" in data:
                # Binary audio → full pipeline
                audio_bytes = data["bytes"]
                t_start = time.time()
                loop = asyncio.get_running_loop()

                await ws.send_json({"status": "processing", "stage": "stt"})

                # 1. STT
                transcription = await loop.run_in_executor(
                    _executor, functools.partial(whisper.transcribe, audio_bytes, language=ws_source_code)
                )
                transcript = transcription["text"]
                stt_ms = int((time.time() - t_start) * 1000)

                # Feed voice profile
                _feed_voice_profile(profile, audio_bytes, transcript)

                await ws.send_json({
                    "status": "processing",
                    "stage": "llm",
                    "transcript": transcript,
                    "stt_ms": stt_ms,
                })

                # 2. Translate
                t_llm = time.time()
                translation = await translator.translate(transcript, source_lang=ws_source_lang, target_lang=ws_target_lang)
                translated = translation["translated_text"]
                llm_ms = int((time.time() - t_llm) * 1000)

                await ws.send_json({
                    "status": "processing",
                    "stage": "tts",
                    "response": translated,
                    "llm_ms": llm_ms,
                })

                # 3. TTS streaming — send WAV chunks as binary (cloned voice if available)
                if tts is not None:
                    import soundfile as sf
                    t_tts = time.time()
                    chunk_count = 0
                    ttfa_ms = None

                    try:
                        _tl, _sp, _pr = ws_target_lang, ws_speaker, profile
                        chunks = await loop.run_in_executor(
                            _executor,
                            lambda: list(_tts_streaming(tts, translated, _tl, _sp, _pr))
                        )
                        for audio_chunk, chunk_sr in chunks:
                            chunk_io = io.BytesIO()
                            sf.write(chunk_io, audio_chunk, chunk_sr, format="WAV")
                            await ws.send_bytes(chunk_io.getvalue())
                            chunk_count += 1
                            if ttfa_ms is None:
                                ttfa_ms = int((time.time() - t_start) * 1000)
                    except Exception as tts_err:
                        logger.warning(f"[WS] TTS streaming failed: {tts_err}, fallback to batch")
                        wav_bytes = await loop.run_in_executor(
                            _executor,
                            functools.partial(_tts_synthesize, tts, translated, ws_target_lang, ws_speaker, profile)
                        )
                        if wav_bytes:
                            await ws.send_bytes(wav_bytes)
                            chunk_count = 1
                            if ttfa_ms is None:
                                ttfa_ms = int((time.time() - t_start) * 1000)

                    tts_ms = int((time.time() - t_tts) * 1000)
                else:
                    tts_ms = 0
                    chunk_count = 0
                    ttfa_ms = 0

                total_ms = int((time.time() - t_start) * 1000)
                logger.info(f"[WS] Done: STT {stt_ms}ms | LLM {llm_ms}ms | TTS {tts_ms}ms | Total {total_ms}ms | {chunk_count} chunks")

                await ws.send_json({
                    "status": "complete",
                    "transcript": transcript,
                    "response": translated,
                    "timing": {
                        "stt_ms": stt_ms,
                        "llm_ms": llm_ms,
                        "tts_ms": tts_ms,
                        "ttfa_ms": ttfa_ms,
                        "total_ms": total_ms,
                    },
                    "chunks_sent": chunk_count,
                })

            elif "text" in data:
                try:
                    msg = json.loads(data["text"])

                    if msg.get("type") == "ping":
                        await ws.send_json({"type": "pong"})

                    elif msg.get("type") == "config":
                        # Update session language config
                        if "source" in msg:
                            ws_source_code = msg["source"]
                            ws_source_lang = LANG_MAP.get(ws_source_code, ws_source_lang)
                        if "target" in msg:
                            ws_target_code = msg["target"]
                            ws_target_lang = LANG_MAP.get(ws_target_code, ws_target_lang)
                        if "speaker" in msg:
                            ws_speaker = msg["speaker"]
                        await ws.send_json({"type": "config_ack",
                                           "source": ws_source_code, "target": ws_target_code,
                                           "speaker": ws_speaker})

                    elif msg.get("type") == "tts":
                        # TTS only
                        text = msg.get("text", "")
                        speaker = msg.get("speaker", ws_speaker)
                        language = msg.get("language", ws_target_lang)
                        if tts is None:
                            await ws.send_json({"error": "TTS not available"})
                            continue

                        import soundfile as sf
                        t_tts = time.time()
                        loop = asyncio.get_running_loop()
                        chunk_count = 0
                        ttfa_ms = None
                        try:
                            _lang, _spk = language, speaker
                            chunks = await loop.run_in_executor(
                                _executor,
                                lambda: list(tts.synthesize_streaming(text, language=_lang, speaker=_spk))
                            )
                            for audio_chunk, chunk_sr in chunks:
                                chunk_io = io.BytesIO()
                                sf.write(chunk_io, audio_chunk, chunk_sr, format="WAV")
                                await ws.send_bytes(chunk_io.getvalue())
                                chunk_count += 1
                                if ttfa_ms is None:
                                    ttfa_ms = int((time.time() - t_tts) * 1000)
                        except Exception:
                            wav_bytes = await loop.run_in_executor(
                                _executor,
                                functools.partial(tts.synthesize, text, language=language, speaker=speaker)
                            )
                            await ws.send_bytes(wav_bytes)
                            chunk_count = 1
                            if ttfa_ms is None:
                                ttfa_ms = int((time.time() - t_tts) * 1000)

                        tts_ms = int((time.time() - t_tts) * 1000)
                        await ws.send_json({
                            "status": "complete",
                            "timing": {"tts_ms": tts_ms, "ttfa_ms": ttfa_ms},
                            "chunks_sent": chunk_count,
                        })

                    elif msg.get("type") == "text":
                        # Text → translate → TTS
                        text = msg.get("message", "")
                        speaker = msg.get("speaker", ws_speaker)
                        source_lang = msg.get("source_lang", ws_source_lang)
                        target_lang = msg.get("target_lang", ws_target_lang)
                        t_start = time.time()
                        loop = asyncio.get_running_loop()

                        # Translate
                        translation = await translator.translate(text, source_lang=source_lang, target_lang=target_lang)
                        translated = translation["translated_text"]
                        llm_ms = int((time.time() - t_start) * 1000)

                        # TTS
                        if tts is not None:
                            import soundfile as sf
                            t_tts = time.time()
                            chunk_count = 0
                            ttfa_ms = None
                            try:
                                _tl, _spk = target_lang, speaker
                                chunks = await loop.run_in_executor(
                                    _executor,
                                    lambda: list(tts.synthesize_streaming(translated, language=_tl, speaker=_spk))
                                )
                                for audio_chunk, chunk_sr in chunks:
                                    chunk_io = io.BytesIO()
                                    sf.write(chunk_io, audio_chunk, chunk_sr, format="WAV")
                                    await ws.send_bytes(chunk_io.getvalue())
                                    chunk_count += 1
                                    if ttfa_ms is None:
                                        ttfa_ms = int((time.time() - t_start) * 1000)
                            except Exception:
                                wav_bytes = await loop.run_in_executor(
                                    _executor,
                                    functools.partial(tts.synthesize, translated, language=target_lang, speaker=speaker)
                                )
                                await ws.send_bytes(wav_bytes)
                                chunk_count = 1
                                if ttfa_ms is None:
                                    ttfa_ms = int((time.time() - t_start) * 1000)

                            tts_ms = int((time.time() - t_tts) * 1000)
                        else:
                            tts_ms = 0
                            chunk_count = 0
                            ttfa_ms = 0

                        total_ms = int((time.time() - t_start) * 1000)
                        await ws.send_json({
                            "status": "complete",
                            "response": translated,
                            "timing": {"llm_ms": llm_ms, "tts_ms": tts_ms, "ttfa_ms": ttfa_ms, "total_ms": total_ms},
                            "chunks_sent": chunk_count,
                        })

                except json.JSONDecodeError:
                    await ws.send_json({"error": "Invalid JSON"})

    except (WebSocketDisconnect, RuntimeError) as e:
        msg = str(e)
        if "disconnect" in msg.lower() or "receive" in msg.lower():
            logger.info("[WS] Client disconnected")
        else:
            logger.warning(f"[WS] Connection error: {e}")
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ============================================================================
# VOICE PROFILE ENDPOINTS
# ============================================================================

@app.get("/v1/voice-profile/status")
async def voice_profile_status(
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Get current voice profile state (collecting/locked)."""
    if profile is None:
        return {"state": "disabled"}
    return profile.status()


@app.post("/v1/tts/clone-test")
async def api_tts_clone_test(
    body: dict,
    tts: Optional[TTSService] = Depends(get_tts),
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Debug: test voice clone synthesis with detailed error info."""
    if tts is None:
        return JSONResponse(status_code=501, content={"error": "TTS not available"})
    if profile is None or not profile.is_locked:
        return JSONResponse(status_code=400, content={
            "error": "Voice profile not locked",
            "voice_profile": profile.status() if profile else None,
        })
    text = body.get("text", "The conference will begin shortly.")
    language = body.get("language", "English")
    import traceback
    ref_info = {
        "ref_audio_shape": list(profile.ref_audio.shape) if profile.ref_audio is not None else None,
        "ref_audio_dtype": str(profile.ref_audio.dtype) if profile.ref_audio is not None else None,
        "ref_text_len": len(profile.ref_text),
        "ref_text_preview": profile.ref_text[:100],
    }
    try:
        loop = asyncio.get_running_loop()
        wav_bytes = await loop.run_in_executor(
            _executor, functools.partial(
                tts.synthesize_clone, text, language, profile.ref_audio, profile.ref_text
            )
        )
        return {"ok": True, "wav_bytes_len": len(wav_bytes), "ref_info": ref_info}
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "traceback": traceback.format_exc(),
            "ref_info": ref_info,
        })


@app.post("/v1/voice-profile/reset")
async def voice_profile_reset(
    profile: Optional[VoiceProfileManager] = Depends(get_voice_profile),
):
    """Reset voice profile to start collecting again (e.g., new speaker)."""
    if profile is None:
        return {"ok": False, "error": "Voice cloning disabled"}
    profile.reset()
    return {"ok": True, "state": "collecting"}


# ============================================================================
# LEGACY HTTP ENDPOINTS
# ============================================================================

@app.post("/v1/translate/text")
async def api_translate_text(
    body: dict,
    translator: TranslationService = Depends(get_translator),
):
    """Translate pre-transcribed text (no audio). Used by the Swift app which does on-device transcription."""
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse(status_code=400, content={"error": "Empty text"})
    source_lang = body.get("source_lang", "French")
    target_lang = body.get("target_lang", "English")
    glossary = body.get("glossary", "")
    logger.info("Translate text: %s -> %s | '%s'", source_lang, target_lang, text[:80])
    t0 = time.monotonic()
    try:
        translation = await translator.translate(text, source_lang=source_lang, target_lang=target_lang, glossary=glossary)
        elapsed = time.monotonic() - t0
        logger.info("Translation result (%.2fs): '%s'", elapsed, translation["translated_text"][:80])
        return {"translated_text": translation["translated_text"]}
    except Exception:
        logger.exception("Translation failed for text: %s", text[:80])
        return JSONResponse(status_code=500, content={"error": "Translation failed"})


@app.post("/v1/translate")
async def api_translate(
    file: UploadFile = File(...),
    source_lang: str = Query("French"),
    target_lang: str = Query("English"),
    whisper: WhisperService = Depends(get_whisper),
    translator: TranslationService = Depends(get_translator),
):
    """Transcribe + translate audio. Returns source and translated text."""
    audio_bytes = await file.read()
    logger.info("Translate pipeline: file=%s size=%d %s->%s", file.filename, len(audio_bytes), source_lang, target_lang)
    t0 = time.monotonic()

    # Step 1: Transcribe
    loop = asyncio.get_running_loop()
    try:
        transcription = await loop.run_in_executor(
            _executor, functools.partial(whisper.transcribe, audio_bytes, language="fr" if source_lang == "French" else "en")
        )
        logger.info("Step 1 transcribe (%.2fs): '%s'", time.monotonic() - t0, transcription["text"][:100])
    except Exception:
        logger.exception("Transcribe step failed")
        return JSONResponse(status_code=500, content={"error": "Transcription failed"})

    # Step 2: Translate
    t1 = time.monotonic()
    try:
        translation = await translator.translate(
            transcription["text"],
            source_lang=source_lang,
            target_lang=target_lang,
        )
        logger.info("Step 2 translate (%.2fs): '%s'", time.monotonic() - t1, translation["translated_text"][:100])
    except Exception:
        logger.exception("Translation step failed")
        return JSONResponse(status_code=500, content={"error": "Translation failed"})

    total = time.monotonic() - t0
    logger.info("Pipeline complete (%.2fs total)", total)

    return {
        "source_text": transcription["text"],
        "translated_text": translation["translated_text"],
        "source_lang": source_lang,
        "target_lang": target_lang,
        "audio_duration": transcription["duration"],
    }


@app.post("/v1/translate/speech")
async def api_translate_speech(
    file: UploadFile = File(...),
    source_lang: str = Query("French"),
    target_lang: str = Query("English"),
    speaker: str = Query("Ryan", description="TTS voice: Ryan, Aria, Luna, etc."),
    whisper: WhisperService = Depends(get_whisper),
    translator: TranslationService = Depends(get_translator),
    tts: Optional[TTSService] = Depends(get_tts),
):
    """Full pipeline: Transcribe → Translate → Synthesize speech. Returns WAV audio."""
    if tts is None:
        return JSONResponse(
            status_code=501,
            content={"error": "TTS service not available. Use /v1/translate instead."},
        )
    audio_bytes = await file.read()

    loop = asyncio.get_running_loop()

    # Step 1: Transcribe French audio
    logger.info("Step 1: Transcribing...")
    transcription = await loop.run_in_executor(
        _executor, functools.partial(whisper.transcribe, audio_bytes, language="fr" if source_lang == "French" else "en")
    )
    logger.info(f"Transcribed: {transcription['text'][:100]}...")

    # Step 2: Translate to English
    logger.info("Step 2: Translating...")
    translation = await translator.translate(
        transcription["text"],
        source_lang=source_lang,
        target_lang=target_lang,
    )
    logger.info(f"Translated: {translation['translated_text'][:100]}...")

    # Step 3: Synthesize English speech
    logger.info("Step 3: Synthesizing speech...")
    wav_bytes = await loop.run_in_executor(
        _executor, functools.partial(tts.synthesize, translation["translated_text"], language=target_lang, speaker=speaker)
    )
    logger.info(f"Synthesized {len(wav_bytes)} bytes of audio.")

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Source-Text": transcription["text"][:200],
            "X-Translated-Text": translation["translated_text"][:200],
        },
    )


# ============================================================================
# WebSocket: /ws/audio — meet-teams-bot raw PCM audio streaming pipeline
# ============================================================================

def _make_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM bytes in a WAV header."""
    import struct
    data_size = len(pcm)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1,  # PCM format
        channels, sample_rate,
        sample_rate * channels * (bits // 8),  # byte rate
        channels * (bits // 8),  # block align
        bits,
        b'data', data_size,
    )
    return header + pcm


@app.websocket("/ws/audio")
async def ws_audio(
    ws: WebSocket,
    whisper: WhisperService = Depends(get_whisper),
    translator: TranslationService = Depends(get_translator),
):
    """WebSocket for meet-teams-bot raw PCM audio streaming.

    Protocol:
      1. Client sends JSON handshake: {"protocol_version": 1, "bot_id": "...", "sample_rate": 16000}
      2. Server replies: {"status": "ready"}
      3. Client sends binary Int16 PCM frames continuously
      4. Server buffers ~3 seconds, then runs STT → translate and sends JSON result:
         {"transcript": "...", "translation": "...", "timing": {...}}
      5. Client can send JSON {"type": "stop"} to end gracefully
    """
    await ws.accept()
    logger.info("[ws/audio] Client connected")

    # 1. Wait for handshake
    try:
        handshake = await ws.receive_json()
    except Exception as e:
        logger.error(f"[ws/audio] Handshake failed: {e}")
        await ws.close(code=1002, reason="Expected JSON handshake")
        return

    bot_id = handshake.get("bot_id", "unknown")
    sample_rate = int(handshake.get("sample_rate", 16000))
    source_code = handshake.get("source_lang", "fr")
    target_code = handshake.get("target_lang", "en")
    source_lang = LANG_MAP.get(source_code, "French")
    target_lang = LANG_MAP.get(target_code, "English")

    logger.info(f"[ws/audio] Handshake OK: bot={bot_id} rate={sample_rate} {source_code}->{target_code}")
    await ws.send_json({"status": "ready", "bot_id": bot_id})

    # 2. Receive binary PCM, buffer, process
    buffer = bytearray()
    BUFFER_THRESHOLD = sample_rate * 2 * 3  # 3 seconds of Int16 (2 bytes/sample)
    loop = asyncio.get_running_loop()
    segment_count = 0

    try:
        while True:
            data = await ws.receive()

            if "bytes" in data and data["bytes"]:
                buffer.extend(data["bytes"])

                if len(buffer) >= BUFFER_THRESHOLD:
                    segment_count += 1
                    pcm_chunk = bytes(buffer)
                    buffer.clear()

                    t_start = time.time()

                    # Wrap PCM in WAV header for Whisper
                    wav_bytes = _make_wav(pcm_chunk, sample_rate)

                    # STT
                    transcription = await loop.run_in_executor(
                        _executor,
                        functools.partial(whisper.transcribe, wav_bytes, language=source_code),
                    )
                    transcript = transcription["text"].strip()
                    stt_ms = int((time.time() - t_start) * 1000)

                    if not transcript:
                        logger.debug(f"[ws/audio] Segment #{segment_count}: silence")
                        await ws.send_json({
                            "type": "silence",
                            "segment": segment_count,
                        })
                        continue

                    # Translate
                    t_llm = time.time()
                    translation = await translator.translate(
                        transcript, source_lang=source_lang, target_lang=target_lang
                    )
                    translated = translation["translated_text"]
                    llm_ms = int((time.time() - t_llm) * 1000)

                    total_ms = int((time.time() - t_start) * 1000)
                    logger.info(
                        f"[ws/audio] #{segment_count}: STT {stt_ms}ms | LLM {llm_ms}ms | Total {total_ms}ms | "
                        f"'{transcript[:60]}' -> '{translated[:60]}'"
                    )

                    await ws.send_json({
                        "type": "result",
                        "segment": segment_count,
                        "transcript": transcript,
                        "translation": translated,
                        "timing": {
                            "stt_ms": stt_ms,
                            "llm_ms": llm_ms,
                            "total_ms": total_ms,
                        },
                    })

            elif "text" in data:
                try:
                    msg = json.loads(data["text"])
                    if not isinstance(msg, dict):
                        # Bot may send JSON arrays (e.g. speaker state) — ignore
                        continue
                    if msg.get("type") == "stop":
                        logger.info(f"[ws/audio] Bot {bot_id} sent stop after {segment_count} segments")
                        break
                    elif msg.get("type") == "config":
                        if "source_lang" in msg:
                            source_code = msg["source_lang"]
                            source_lang = LANG_MAP.get(source_code, source_lang)
                        if "target_lang" in msg:
                            target_code = msg["target_lang"]
                            target_lang = LANG_MAP.get(target_code, target_lang)
                        await ws.send_json({"type": "config_ack", "source": source_code, "target": target_code})
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        logger.info(f"[ws/audio] Bot {bot_id} disconnected after {segment_count} segments")
    except Exception:
        logger.exception(f"[ws/audio] Error for bot {bot_id}")
        try:
            await ws.close(code=1011)
        except Exception:
            pass


@app.websocket("/ws/translate")
async def ws_translate(
    ws: WebSocket,
    translator: TranslationService = Depends(get_translator),
    tts: Optional[TTSService] = Depends(get_tts),
):
    """WebSocket for low-latency text translation.

    Client sends JSON: {"text": "...", "source_lang": "French", "target_lang": "English"}
    Server replies JSON: {"translated_text": "..."}
    Optionally include "tts": true, "speaker": "Ryan" to get TTS audio (base64).
    """

    await ws.accept()
    logger.info("WebSocket client connected")
    msg_count = 0
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            text = msg.get("text", "").strip()
            if not text:
                await ws.send_json({"error": "empty text"})
                continue

            msg_count += 1
            source_lang = msg.get("source_lang", "French")
            target_lang = msg.get("target_lang", "English")
            logger.debug("WS msg #%d: '%s' (%s->%s)", msg_count, text[:60], source_lang, target_lang)

            t0 = time.monotonic()
            translation = await translator.translate(text, source_lang=source_lang, target_lang=target_lang)
            response = {"translated_text": translation["translated_text"]}
            logger.debug("WS translation (%.2fs): '%s'", time.monotonic() - t0, translation["translated_text"][:60])

            # Optional TTS
            if msg.get("tts") and tts is not None:
                speaker = msg.get("speaker", "Ryan")
                t1 = time.monotonic()
                loop = asyncio.get_running_loop()
                wav_bytes = await loop.run_in_executor(
                    _executor, functools.partial(tts.synthesize, translation["translated_text"], language=target_lang, speaker=speaker)
                )
                response["tts_audio"] = base64.b64encode(wav_bytes).decode("ascii")
                logger.debug("WS TTS (%.2fs): %d bytes", time.monotonic() - t1, len(wav_bytes))

            await ws.send_json(response)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected (processed %d messages)", msg_count)
    except Exception:
        logger.exception("WebSocket error after %d messages", msg_count)
        try:
            await ws.close(code=1011)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
