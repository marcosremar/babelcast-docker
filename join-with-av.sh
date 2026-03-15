#!/bin/bash
# join-with-av.sh — Entra na reunião com áudio e vídeo do Mac
#
# Uso:
#   ./join-with-av.sh "URL_DA_REUNIÃO"
#   ./join-with-av.sh "URL" --no-video   # só áudio
#   ./join-with-av.sh "URL" --no-audio   # só vídeo

MEETING_URL="$1"
PORT=8090

if [ -z "$MEETING_URL" ]; then
    echo "Uso: $0 \"URL_DA_REUNIÃO\" [--no-video] [--no-audio]"
    exit 1
fi

ENABLE_VIDEO=true
ENABLE_AUDIO=true

for arg in "$@"; do
    [ "$arg" = "--no-video" ] && ENABLE_VIDEO=false
    [ "$arg" = "--no-audio" ] && ENABLE_AUDIO=false
done

echo "=== BabelCast Join with A/V ==="
echo "  URL: $MEETING_URL"
echo "  Áudio: $ENABLE_AUDIO"
echo "  Vídeo: $ENABLE_VIDEO"
echo ""

# Inicia streams em background
if $ENABLE_AUDIO; then
    if ! command -v websocat &>/dev/null; then
        echo "AVISO: websocat não encontrado — áudio desabilitado"
        echo "  Instale com: brew install websocat"
        ENABLE_AUDIO=false
    fi
fi

if $ENABLE_AUDIO; then
    echo "[1/3] Iniciando stream de microfone..."
    /Users/marcos/babelcast-docker/stream-mic.sh $PORT &
    MIC_PID=$!
    sleep 1
    echo "  Mic PID: $MIC_PID"
fi

if $ENABLE_VIDEO; then
    echo "[2/3] Iniciando stream de webcam..."
    /Users/marcos/babelcast-docker/stream-cam.sh 9091 &
    CAM_PID=$!
    sleep 1
    echo "  Cam PID: $CAM_PID"
fi

echo "[3/3] Entrando na reunião..."
RESPONSE=$(curl -s -X POST "http://localhost:${PORT}/join" \
    -H "Content-Type: application/json" \
    -d "{
        \"meeting_url\": \"$MEETING_URL\",
        \"enable_mic\": $ENABLE_AUDIO
    }")
echo "  Resposta: $RESPONSE"
echo ""

# Cleanup ao sair
cleanup() {
    echo ""
    echo "Encerrando streams..."
    [ -n "$MIC_PID" ] && kill $MIC_PID 2>/dev/null
    [ -n "$CAM_PID" ] && kill $CAM_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "=== Streams ativos ==="
echo "  Ctrl+C para parar tudo"
echo ""

# Monitora transcrições enquanto em reunião
LAST=0
PREV_STATE=""
SOUND_PLAYED=false
while true; do
    sleep 4
    S=$(curl -s "http://localhost:${PORT}/bot-state" 2>/dev/null)
    STATE=$(echo "$S" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['state'])" 2>/dev/null)
    TC=$(echo "$S" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['transcript_count'])" 2>/dev/null)
    MIC=$(echo "$S" | python3 -c "import sys,json; d=json.load(sys.stdin); print('🎤' if d.get('mic_active') else '🔇')" 2>/dev/null)

    echo "$(date '+%H:%M:%S') [$STATE | $MIC | $TC tx]"

    # Toca som UMA VEZ quando entra na waiting_room
    if [ "$STATE" = "waiting_room" ] && [ "$PREV_STATE" != "waiting_room" ] && [ "$SOUND_PLAYED" = "false" ]; then
        echo "  ⏳ Bot na sala de espera — APROVE no Teams!"
        afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
        afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
        SOUND_PLAYED=true
    fi

    # Toca som quando entra na reunião
    if [ "$STATE" = "in_meeting" ] && [ "$PREV_STATE" != "in_meeting" ]; then
        afplay /System/Library/Sounds/Ping.aiff 2>/dev/null &
    fi

    PREV_STATE="$STATE"

    if [ "$TC" -gt "$LAST" ] 2>/dev/null; then
        TRANS=$(curl -s "http://localhost:${PORT}/transcripts" 2>/dev/null)
        echo "$TRANS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
ts=d['transcripts']
for t in ts[$LAST:]: print('  📝',t['text'])
" 2>/dev/null
        LAST=$TC
    fi

    [ "$STATE" = "idle" ] && [ "$LAST" -gt 0 ] && echo "  ✅ Reunião encerrada" && break
done

cleanup
