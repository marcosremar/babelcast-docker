#!/bin/bash
# BabelCast All-in-One startup
#
# Port layout:
#   8080 — nginx (external, gateway-facing)
#   8081 — orchestrator (FastAPI, internal)
#   8082 — meet-teams-bot (Node.js, internal, patched from 8080)
#   8000 — translation pipeline (Whisper + Groq, internal)
#
# nginx starts FIRST so the gateway health-check passes immediately.

set -e
echo "=== BabelCast All-in-One (Groq Cloud) ==="
echo "Python: $(python3 --version 2>&1)"
echo "Node:   $(node --version 2>&1)"
echo "GPU:    $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"

export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# ── Hot-update Python code from GitHub (optional) ────────────────────────────
if [ "${BABELCAST_HOT_UPDATE:-1}" = "1" ]; then
    echo "[0/5] Checking for code updates..."
    if command -v git &>/dev/null; then
        TMP=$(mktemp -d)
        if git clone --depth 1 --filter=blob:none --sparse \
            https://github.com/marcosremar/babelcast.git "$TMP" 2>/dev/null; then
            cd "$TMP"
            git sparse-checkout set docker/api docker/all-in-one/orchestrator 2>/dev/null
            [ -d "$TMP/docker/api" ] && cp -r "$TMP/docker/api/"* /app/api/ 2>/dev/null || true
            [ -f "$TMP/docker/all-in-one/orchestrator/main.py" ] && \
                cp "$TMP/docker/all-in-one/orchestrator/main.py" /app/orchestrator/main.py 2>/dev/null || true
            echo "  Updated from GitHub"
        else
            echo "  (private repo — using baked-in code)"
        fi
        rm -rf "$TMP"; cd /app
    fi
fi

# ── 1. nginx on :8080 — FIRST, so gateway sees healthy immediately ────────────
echo "[1/5] Starting nginx on :8080..."
nginx
echo "  nginx up"

# ── 2. Orchestrator on :8081 ─────────────────────────────────────────────────
echo "[2/5] Starting orchestrator on :8081..."
cd /app/orchestrator
BOT_URL=http://localhost:8082 \
PIPELINE_WS_URL=ws://localhost:8000/ws/audio \
SOURCE_LANG=${CONF_SOURCE_LANG:-pt} \
TARGET_LANG=${CONF_TARGET_LANG:-en} \
SELF_HOST=localhost \
SELF_PORT=8080 \
python3 -m uvicorn main:app --host 0.0.0.0 --port 8081 --workers 1 --log-level info &
ORCH_PID=$!
echo "  Orchestrator started (PID $ORCH_PID)"

# ── 3. Virtual display + audio for Chromium ───────────────────────────────────
echo "[3/5] Starting Xvfb + PulseAudio..."
export DISPLAY=:99
export PULSE_RUNTIME_PATH=/tmp/pulse
export XDG_RUNTIME_DIR=/tmp/pulse
mkdir -p $PULSE_RUNTIME_PATH

Xvfb :99 -screen 0 1280x860x24 -ac +extension GLX +render -noreset -nolisten tcp &
sleep 2
unclutter -display :99 -idle 0 -root &

pulseaudio --start --log-target=stderr --log-level=error &
sleep 3
if ! pactl info >/dev/null 2>&1; then
    pulseaudio --kill || true; sleep 1
    pulseaudio --start --log-target=stderr --log-level=error &
    sleep 3
fi
pactl load-module module-null-sink sink_name=virtual_speaker \
    sink_properties=device.description=Virtual_Speaker 2>/dev/null || true
pactl load-module module-virtual-source source_name=virtual_mic 2>/dev/null || true
pactl set-default-sink virtual_speaker 2>/dev/null || true
echo "  Display and audio ready"

# ── 4. Bot on :8082 ───────────────────────────────────────────────────────────
echo "[4/5] Starting meet-teams-bot on :8082..."
cd /app/bot
node build/src/main.js &
BOT_PID=$!
for i in $(seq 1 30); do
    curl -sf http://localhost:8082/version >/dev/null 2>&1 && echo "  Bot ready on :8082" && break
    sleep 2
done

# ── 5. Translation pipeline on :8000 ─────────────────────────────────────────
echo "[5/5] Starting translation pipeline on :8000..."
cd /app/api
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level warning &
PIPELINE_PID=$!

echo ""
echo "=== All services started ==="
echo "  nginx:        :8080 (gateway endpoint) ← external"
echo "  orchestrator: :8081"
echo "  bot:          :8082"
echo "  pipeline:     :8000 (Whisper loading...)"
echo ""

# Watchdog
trap "nginx -s stop; kill $ORCH_PID $BOT_PID $PIPELINE_PID 2>/dev/null; exit 0" SIGTERM SIGINT

while true; do
    if ! kill -0 $ORCH_PID 2>/dev/null; then
        echo "[watchdog] Orchestrator crashed, restarting..."
        cd /app/orchestrator
        BOT_URL=http://localhost:8082 PIPELINE_WS_URL=ws://localhost:8000/ws/audio \
        SOURCE_LANG=${CONF_SOURCE_LANG:-pt} TARGET_LANG=${CONF_TARGET_LANG:-en} \
        SELF_HOST=localhost SELF_PORT=8080 \
        python3 -m uvicorn main:app --host 0.0.0.0 --port 8081 --workers 1 --log-level info &
        ORCH_PID=$!
    fi
    if ! kill -0 $PIPELINE_PID 2>/dev/null; then
        echo "[watchdog] Pipeline crashed, restarting..."
        cd /app/api
        python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level warning &
        PIPELINE_PID=$!
    fi
    sleep 10
done
