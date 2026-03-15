"""BabelCast Orchestrator

Acts as the single external endpoint (port 8080) that the BabelCast gateway
talks to.  Sits between the gateway, the bot container, and the pipeline.

Responsibilities:
  • Proxy GET /version, GET /health → bot container
  • Proxy POST /stop_record         → bot container
  • Proxy POST /join                → bot container, injecting
      streaming_output = ws://orchestrator:8080/ws/audio
  • WS /ws/audio  — receives raw PCM from the bot, bridges it to the
      pipeline /ws/audio WebSocket, stores translated transcripts
  • GET /transcripts                → returns buffered transcripts for gateway
  • WS /ws/mic-input               → Mac streams mic PCM here
  • WS /ws/mic-relay               → bot connects here to receive mic PCM
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Any

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("orchestrator")

# ── Config ────────────────────────────────────────────────────────────────────

BOT_URL         = os.environ.get("BOT_URL", "http://bot:8080")
PIPELINE_WS_URL = os.environ.get("PIPELINE_WS_URL", "ws://pipeline:8000/ws/audio")
SOURCE_LANG     = os.environ.get("SOURCE_LANG", "pt")
TARGET_LANG     = os.environ.get("TARGET_LANG", "en")

# External hostname/IP used by the bot to reach back to this service.
# When running on RunPod, set via SELF_HOST env var (e.g. "localhost").
SELF_HOST = os.environ.get("SELF_HOST", "orchestrator")
SELF_PORT = int(os.environ.get("SELF_PORT", "8080"))

# ── Mic relay (Mac mic → bot) ─────────────────────────────────────────────────

# Each connected bot gets a queue; Mac pushes chunks which are forwarded to all queues
_mic_queues: list[asyncio.Queue] = []


# ── Bot state tracking ────────────────────────────────────────────────────────

_bot_state: str = "idle"          # idle | joining | waiting_room | in_meeting
_bot_state_time: float = 0.0


def _set_bot_state(state: str) -> None:
    global _bot_state, _bot_state_time
    if _bot_state != state:
        log.info("[state] %s → %s", _bot_state, state)
        _bot_state = state
        _bot_state_time = time.time()


# ── Transcript store ──────────────────────────────────────────────────────────

# Stores {text, speaker, ts} dicts; gateway reads with a cursor
_transcripts: deque[dict] = deque(maxlen=500)


def _append_transcript(text: str, speaker: str = "") -> None:
    _transcripts.append({"text": text, "speaker": speaker, "ts": time.time()})


# ── FastAPI app ───────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager


async def _bot_state_watchdog():
    """Periodically poll bot /version; reset state to idle if bot reports idle."""
    await asyncio.sleep(5)  # give everything time to start
    while True:
        await asyncio.sleep(5)
        if _bot_state in ("waiting_room", "in_meeting"):
            try:
                r = await asyncio.wait_for(_http.get("/version", timeout=3), timeout=4)
                data = r.json()
                if data.get("status") == "idle":
                    log.info("[watchdog] Bot is idle — resetting state from %s", _bot_state)
                    _set_bot_state("idle")
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_bot_state_watchdog())
    yield
    task.cancel()
    await _http.aclose()


app = FastAPI(title="BabelCast Orchestrator", lifespan=lifespan)
_http = httpx.AsyncClient(base_url=BOT_URL, timeout=30)


# ── Health / version (proxy to bot) ──────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        r = await _http.get("/health", timeout=5)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception:
        return JSONResponse({"status": "pipeline_only"}, status_code=200)


@app.get("/bot-state")
async def bot_state():
    return {
        "state": _bot_state,
        "since": _bot_state_time,
        "transcript_count": len(_transcripts),
        "mic_active": len(_mic_queues) > 0,
    }


# ── /ws/mic-input — Mac mic audio → forwarded to bot ─────────────────────────

@app.websocket("/ws/mic-input")
async def ws_mic_input(ws: WebSocket):
    """Mac streams raw PCM mic audio here (16kHz mono Int16).
    Chunks are immediately forwarded to any connected bot mic-relay consumer.
    """
    await ws.accept()
    log.info("[mic-input] Mac microphone connected (%d bot consumers)", len(_mic_queues))
    try:
        while True:
            data = await ws.receive()
            if "bytes" in data and data["bytes"]:
                for q in list(_mic_queues):
                    try:
                        q.put_nowait(data["bytes"])
                    except asyncio.QueueFull:
                        pass  # drop stale chunks rather than block
    except (WebSocketDisconnect, Exception) as e:
        log.info("[mic-input] Mac mic disconnected: %s", e)


# ── /ws/mic-relay — bot connects here to receive Mac mic audio ────────────────

@app.websocket("/ws/mic-relay")
async def ws_mic_relay(ws: WebSocket):
    """Bot connects here (via streaming_input) to receive Mac mic PCM audio."""
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _mic_queues.append(q)
    log.info("[mic-relay] Bot mic consumer connected (total: %d)", len(_mic_queues))
    stop = asyncio.Event()

    async def _send():
        while not stop.is_set():
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
                await ws.send_bytes(chunk)
            except asyncio.TimeoutError:
                pass
            except Exception:
                stop.set()

    async def _watch():
        try:
            while True:
                await ws.receive()
        except Exception:
            stop.set()

    try:
        await asyncio.gather(_send(), _watch())
    finally:
        if q in _mic_queues:
            _mic_queues.remove(q)
        log.info("[mic-relay] Bot mic consumer disconnected")


@app.get("/version")
async def version():
    try:
        r = await _http.get("/version", timeout=5)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


# ── /join — proxy to bot, injecting streaming_output ─────────────────────────

@app.post("/join")
async def join(request: Request):
    body: dict[str, Any] = await request.json()

    # Inject streaming_output so the bot streams audio to us
    body["streaming_output"] = f"ws://{SELF_HOST}:{SELF_PORT}/ws/audio"
    body.setdefault("streaming_audio_frequency", 16000)

    # Inject streaming_input so the bot sends Mac mic audio into Teams
    if body.pop("enable_mic", False):
        body["streaming_input"] = f"ws://{SELF_HOST}:{SELF_PORT}/ws/mic-relay"
        log.info("Mic injection enabled → streaming_input=%s", body["streaming_input"])

    # Carry source/target lang so the pipeline WS handshake can use them
    global SOURCE_LANG, TARGET_LANG
    if "_source_lang" in body:
        SOURCE_LANG = body["_source_lang"]
    if "_target_lang" in body:
        TARGET_LANG = body["_target_lang"]

    log.info(
        "Proxying /join to bot | streaming_output=%s | %s→%s",
        body["streaming_output"], SOURCE_LANG, TARGET_LANG,
    )
    _set_bot_state("joining")
    try:
        r = await _http.post("/join", json=body)
        return JSONResponse(r.json() if r.headers.get("content-type", "").startswith("application/json") else {}, status_code=r.status_code)
    except Exception as e:
        log.error("Bot /join failed: %s", e)
        _set_bot_state("idle")
        return JSONResponse({"error": str(e)}, status_code=502)


# ── /stop_record — proxy to bot ───────────────────────────────────────────────

@app.post("/stop_record")
async def stop_record(request: Request):
    body = await request.json()
    try:
        r = await _http.post("/stop_record", json=body)
        return JSONResponse(r.json() if r.headers.get("content-type", "").startswith("application/json") else {}, status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ── /transcripts — polled by the gateway every ~1.5s ─────────────────────────

@app.get("/transcripts")
async def transcripts():
    """Return all accumulated transcripts.

    The gateway manages its own local cursor and slices from the array itself.
    Response: {"transcripts": [{"text": ..., "speaker": ...}]}
    """
    return {
        "transcripts": [{"text": t["text"], "speaker": t["speaker"]} for t in _transcripts],
    }


# ── WS /ws/audio — receive PCM from bot, bridge to pipeline ──────────────────

@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    """Bridge WebSocket: bot sends raw PCM here, we forward to pipeline.

    The pipeline processes it (Whisper STT → LLM translation) and sends back
    JSON results.  We store the translated text in _transcripts.
    """
    await ws.accept()
    _set_bot_state("waiting_room")
    log.info("[ws/audio] Bot connected — waiting for meeting approval")

    try:
        # Connect to pipeline
        pipeline_ws = await asyncio.wait_for(
            websockets.connect(PIPELINE_WS_URL, ping_interval=None),
            timeout=15,
        )
        log.info("[ws/audio] Pipeline WS connected: %s", PIPELINE_WS_URL)
    except Exception as e:
        log.error("[ws/audio] Cannot reach pipeline: %s", e)
        await ws.close(code=1011, reason="Pipeline unavailable")
        return

    # Send handshake to pipeline
    try:
        await pipeline_ws.send(json.dumps({
            "protocol_version": 1,
            "bot_id": "babelcast-bot",
            "sample_rate": 16000,
            "source_lang": SOURCE_LANG,
            "target_lang": TARGET_LANG,
        }))
        handshake_reply = json.loads(await asyncio.wait_for(pipeline_ws.recv(), timeout=10))
        log.info("[ws/audio] Pipeline handshake: %s", handshake_reply)
    except Exception as e:
        log.error("[ws/audio] Pipeline handshake failed: %s", e)
        await ws.close(code=1011, reason="Pipeline handshake failed")
        await pipeline_ws.close()
        return

    # Two concurrent tasks:
    #   bot_to_pipeline  — forward raw PCM bytes from bot → pipeline
    #   pipeline_to_us   — read results from pipeline → store transcripts

    stop_event = asyncio.Event()

    async def bot_to_pipeline():
        try:
            while True:
                data = await ws.receive()
                if "bytes" in data and data["bytes"]:
                    await pipeline_ws.send(data["bytes"])
                elif "text" in data:
                    await pipeline_ws.send(data["text"])
        except (WebSocketDisconnect, Exception) as e:
            log.info("[ws/audio] Bot disconnected: %s", e)
        finally:
            stop_event.set()

    async def pipeline_to_us():
        try:
            async for raw in pipeline_ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                msg_type = msg.get("type", "")
                if msg_type == "result":
                    text     = msg.get("translation") or msg.get("transcript", "")
                    speaker  = msg.get("speaker", "")
                    if text:
                        _set_bot_state("in_meeting")
                        _append_transcript(text, speaker)
                        log.info("[ws/audio] Transcript stored: %s", text[:80])
                        # Forward result back to bot so it can write /tmp/subtitle.txt
                        try:
                            await ws.send_text(raw)
                        except Exception:
                            pass
                elif msg_type == "silence":
                    pass
                else:
                    log.debug("[ws/audio] Pipeline msg: %s", msg)
        except Exception as e:
            log.info("[ws/audio] Pipeline WS closed: %s", e)
        finally:
            stop_event.set()

    async def wait_for_stop():
        await stop_event.wait()

    t1 = asyncio.create_task(bot_to_pipeline())
    t2 = asyncio.create_task(pipeline_to_us())
    try:
        await wait_for_stop()
    finally:
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        try:
            await pipeline_ws.close()
        except Exception:
            pass
        _set_bot_state("idle")
        log.info("[ws/audio] Audio bridge closed")
