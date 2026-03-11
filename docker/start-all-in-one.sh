#!/bin/bash
# BabelCast All-in-One startup script
# Starts: Xvfb + PulseAudio → bot (8090) → pipeline (8000) → orchestrator (8080)

set -e
echo "=== BabelCast All-in-One (Groq Cloud) ==="
echo "Python: $(python3 --version 2>&1)"
echo "Node:   $(node --version 2>&1)"
echo "GPU:    $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'none')"
echo ""

# ── Hot-update code from GitHub ───────────────────────────────────────────────
if [ "${BABELCAST_HOT_UPDATE:-1}" = "1" ]; then
    echo "[0/5] Updating code from GitHub..."
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1 || true
    if command -v git &>/dev/null; then
        TMP=$(mktemp -d)
        if git clone --depth 1 --filter=blob:none --sparse \
            https://github.com/marcosremar/babelcast.git "$TMP" 2>/dev/null; then
            cd "$TMP" && git sparse-checkout set docker/api docker/all-in-one/orchestrator 2>/dev/null
            [ -d "$TMP/docker/api" ]          && cp -r "$TMP/docker/api/"*           /app/api/
            [ -f "$TMP/docker/all-in-one/orchestrator/main.py" ] && \
                cp "$TMP/docker/all-in-one/orchestrator/main.py" /app/orchestrator/main.py
            echo "  Code updated from GitHub"
        fi
        rm -rf "$TMP"; cd /app
    fi
fi

# ── Download models ───────────────────────────────────────────────────────────
export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

echo "[1/5] Downloading Whisper large-v3-turbo..."
python3 -c "
from faster_whisper import WhisperModel
WhisperModel('large-v3-turbo', device='cpu')
print('  Whisper OK')
" || echo "  WARNING: Whisper download failed"

echo "[2/5] Groq Cloud API (no local LLM needed)"
if [ -z "$CONF_GROQ_API_KEY" ]; then
    echo "  WARNING: CONF_GROQ_API_KEY not set!"
else
    echo "  Groq key configured (${CONF_GROQ_API_KEY:0:8}...)"
fi

# ── Virtual display + audio (for Chromium bot) ───────────────────────────────
echo "[3/5] Starting virtual display and audio..."
export DISPLAY=:99
export PULSE_RUNTIME_PATH=/tmp/pulse
export XDG_RUNTIME_DIR=/tmp/pulse
mkdir -p $PULSE_RUNTIME_PATH

Xvfb :99 -screen 0 1280x860x24 -ac +extension GLX +render -noreset -nolisten tcp &
sleep 2
unclutter -display :99 -idle 0 -root &

pulseaudio --start --log-target=stderr --log-level=error &
sleep 3

# Verify PulseAudio
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

# ── Start bot (port 8090, internal) ──────────────────────────────────────────
echo "[4/5] Starting meet-teams-bot on :8090..."
cd /app/bot
node build/src/main.js &
BOT_PID=$!

# Wait for bot to be ready
for i in $(seq 1 30); do
    if curl -sf http://localhost:8090/version >/dev/null 2>&1; then
        echo "  Bot ready"
        break
    fi
    sleep 2
done

# ── Start pipeline (port 8000, internal) ─────────────────────────────────────
echo "[4.5/5] Starting translation pipeline on :8000..."
cd /app/api
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level warning &
PIPELINE_PID=$!

# Wait for pipeline
for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo "  Pipeline ready"
        break
    fi
    sleep 3
done

# ── Start orchestrator (port 8080, external) ──────────────────────────────────
echo "[5/5] Starting orchestrator on :8080..."
cd /app/orchestrator
BOT_URL=http://localhost:8090 \
PIPELINE_WS_URL=ws://localhost:8000/ws/audio \
SOURCE_LANG=${CONF_SOURCE_LANG:-pt} \
TARGET_LANG=${CONF_TARGET_LANG:-en} \
SELF_HOST=localhost \
SELF_PORT=8080 \
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 --log-level info &
ORCH_PID=$!

echo ""
echo "=== All services running ==="
echo "  Bot:          http://localhost:8090"
echo "  Pipeline:     http://localhost:8000"
echo "  Orchestrator: http://localhost:8080  ← gateway endpoint"
echo ""

# Keep container alive, restart any crashed service
trap "kill $BOT_PID $PIPELINE_PID $ORCH_PID 2>/dev/null; exit 0" SIGTERM SIGINT

while true; do
    if ! kill -0 $ORCH_PID 2>/dev/null; then
        echo "[watchdog] Orchestrator crashed, restarting..."
        cd /app/orchestrator
        BOT_URL=http://localhost:8090 \
        PIPELINE_WS_URL=ws://localhost:8000/ws/audio \
        SOURCE_LANG=${CONF_SOURCE_LANG:-pt} \
        TARGET_LANG=${CONF_TARGET_LANG:-en} \
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
