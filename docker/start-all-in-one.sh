#!/bin/bash
# BabelCast All-in-One startup script
#
# Boot order:
#   1. Orchestrator on :8080 FIRST (gateway health-check passes immediately)
#   2. Xvfb + PulseAudio (for Chromium bot)
#   3. Bot on :8090 (patched port)
#   4. Pipeline on :8000 (Whisper download happens here — slow)
#
# Orchestrator proxies /join → bot, bridges audio → pipeline.
# Gateway only needs :8080 to be up — it polls /transcripts after joining.

set -e
echo "=== BabelCast All-in-One (Groq Cloud) ==="
echo "Python: $(python3 --version 2>&1)"
echo "Node:   $(node --version 2>&1)"
echo "GPU:    $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"
echo ""

# ── Hot-update code from GitHub ───────────────────────────────────────────────
if [ "${BABELCAST_HOT_UPDATE:-1}" = "1" ]; then
    echo "[0/4] Updating code from GitHub..."
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1 || true
    if command -v git &>/dev/null; then
        TMP=$(mktemp -d)
        if git clone --depth 1 --filter=blob:none --sparse \
            https://github.com/marcosremar/babelcast.git "$TMP" 2>/dev/null; then
            cd "$TMP"
            git sparse-checkout set docker/api docker/all-in-one/orchestrator 2>/dev/null
            [ -d "$TMP/docker/api" ] && cp -r "$TMP/docker/api/"* /app/api/
            [ -f "$TMP/docker/all-in-one/orchestrator/main.py" ] && \
                cp "$TMP/docker/all-in-one/orchestrator/main.py" /app/orchestrator/main.py
            echo "  Code updated from GitHub"
        fi
        rm -rf "$TMP"
        cd /app
    fi
fi

export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# ── 1. Start orchestrator FIRST on :8080 ─────────────────────────────────────
echo "[1/4] Starting orchestrator on :8080 (gateway-facing)..."
cd /app/orchestrator
BOT_URL=http://localhost:8090 \
PIPELINE_WS_URL=ws://localhost:8000/ws/audio \
SOURCE_LANG=${CONF_SOURCE_LANG:-pt} \
TARGET_LANG=${CONF_TARGET_LANG:-en} \
SELF_HOST=localhost \
SELF_PORT=8080 \
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 --log-level info &
ORCH_PID=$!

# Wait for orchestrator to be up
for i in $(seq 1 20); do
    if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
        echo "  Orchestrator ready on :8080"
        break
    fi
    sleep 1
done

# ── 2. Virtual display + audio (for Chromium) ─────────────────────────────────
echo "[2/4] Starting virtual display and audio..."
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

# ── 3. Start bot on :8090 ─────────────────────────────────────────────────────
echo "[3/4] Starting meet-teams-bot on :8090..."
cd /app/bot
node build/src/main.js &
BOT_PID=$!

for i in $(seq 1 30); do
    if curl -sf http://localhost:8090/version >/dev/null 2>&1; then
        echo "  Bot ready on :8090"
        break
    fi
    sleep 2
done

# ── 4. Start pipeline on :8000 (downloads Whisper in background) ──────────────
echo "[4/4] Starting translation pipeline on :8000 (Whisper loading...)..."

# Download Whisper model first (needed before pipeline is useful)
python3 -c "
from faster_whisper import WhisperModel
WhisperModel('large-v3-turbo', device='cpu')
print('  Whisper OK')
" &

cd /app/api
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level warning &
PIPELINE_PID=$!

echo ""
echo "=== All services started ==="
echo "  Orchestrator: :8080 (gateway endpoint) ✅"
echo "  Bot:          :8090 (internal)"
echo "  Pipeline:     :8000 (internal, loading...)"
echo ""

# Watchdog: restart crashed services
trap "kill $BOT_PID $PIPELINE_PID $ORCH_PID 2>/dev/null; exit 0" SIGTERM SIGINT

while true; do
    if ! kill -0 $ORCH_PID 2>/dev/null; then
        echo "[watchdog] Orchestrator crashed, restarting..."
        cd /app/orchestrator
        BOT_URL=http://localhost:8090 PIPELINE_WS_URL=ws://localhost:8000/ws/audio \
        SOURCE_LANG=${CONF_SOURCE_LANG:-pt} TARGET_LANG=${CONF_TARGET_LANG:-en} \
        SELF_HOST=localhost SELF_PORT=8080 \
        python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 --log-level info &
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
