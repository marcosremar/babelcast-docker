#!/bin/bash
# stream-mic.sh — Captura o microfone do Mac e envia para o bot via WebSocket
#
# Dependências: ffmpeg + websocat
#   brew install ffmpeg websocat
#
# Uso:
#   ./stream-mic.sh                  # porta padrão 8090
#   ./stream-mic.sh 8090             # especifica porta
#   ./stream-mic.sh 8090 "1"         # especifica device de áudio (padrão: 0)

PORT="${1:-8090}"
DEVICE="${2:-0}"          # índice do dispositivo de áudio (avfoundation)
RATE=16000                # 16kHz mono Int16 — o que o bot espera

echo "=== BabelCast Mic Stream ==="
echo "  Capturando microfone (device :$DEVICE) → ws://localhost:$PORT/ws/mic-input"
echo "  Áudio: ${RATE}Hz mono Int16"
echo "  Ctrl+C para parar"
echo ""

# Lista os dispositivos disponíveis se pedido
if [ "$1" = "--list" ]; then
    echo "Dispositivos de áudio disponíveis:"
    ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 50 "AVFoundation audio devices"
    exit 0
fi

# Verifica dependências
if ! command -v ffmpeg &>/dev/null; then
    echo "ERRO: ffmpeg não encontrado. Instale com: brew install ffmpeg"
    exit 1
fi
if ! command -v websocat &>/dev/null; then
    echo "ERRO: websocat não encontrado. Instale com: brew install websocat"
    exit 1
fi

# Captura mic e envia via WebSocket (loop automático em caso de queda)
while true; do
    ffmpeg -loglevel error \
        -f avfoundation \
        -i ":${DEVICE}" \
        -ar $RATE \
        -ac 1 \
        -f s16le \
        pipe:1 \
    | websocat --binary "ws://localhost:${PORT}/ws/mic-input"

    echo "  [mic] Conexão perdida, reconectando em 2s..."
    sleep 2
done
