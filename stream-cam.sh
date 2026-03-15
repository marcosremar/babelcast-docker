#!/bin/bash
# stream-cam.sh — Captura a webcam do Mac e envia para o bot (câmera virtual)
#
# Dependências: ffmpeg
#   brew install ffmpeg
#
# Uso:
#   ./stream-cam.sh                  # porta padrão 9091
#   ./stream-cam.sh 9091             # especifica porta
#   ./stream-cam.sh 9091 "0"         # especifica device de vídeo (padrão: 0)
#   ./stream-cam.sh --list           # lista dispositivos disponíveis

PORT="${1:-9091}"
DEVICE="${2:-0}"
WIDTH=640
HEIGHT=480
FPS=30   # avfoundation no macOS usa 30fps por padrão

echo "=== BabelCast Webcam Stream ==="
echo "  Capturando webcam (device $DEVICE) → tcp://localhost:$PORT"
echo "  Vídeo: ${WIDTH}x${HEIGHT} @ ${FPS}fps YUV420p"
echo "  Ctrl+C para parar"
echo ""

# Lista os dispositivos disponíveis se pedido
if [ "$1" = "--list" ]; then
    echo "Dispositivos de vídeo disponíveis:"
    ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 50 "AVFoundation video devices"
    exit 0
fi

# Verifica dependências
if ! command -v ffmpeg &>/dev/null; then
    echo "ERRO: ffmpeg não encontrado. Instale com: brew install ffmpeg"
    exit 1
fi

echo "  Aguardando o container estar pronto..."
# Espera o container aceitar a conexão TCP
for i in $(seq 1 30); do
    nc -z localhost $PORT 2>/dev/null && break
    sleep 1
done

echo "  Container pronto! Iniciando stream de vídeo..."
echo ""

# Captura webcam e envia raw YUV420p via TCP (loop automático em caso de queda)
while true; do
    ffmpeg -loglevel error \
        -f avfoundation \
        -framerate $FPS \
        -video_size "${WIDTH}x${HEIGHT}" \
        -pixel_format nv12 \
        -i "${DEVICE}:none" \
        -vf "scale=${WIDTH}:${HEIGHT},format=yuv420p" \
        -r $FPS \
        -f rawvideo \
        -pix_fmt yuv420p \
        "tcp://localhost:${PORT}"

    echo "  [cam] Conexão perdida, reconectando em 2s..."
    sleep 2
done
