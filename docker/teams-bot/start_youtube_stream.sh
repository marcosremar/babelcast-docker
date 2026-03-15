#!/bin/bash
# start_youtube_stream.sh — Server-side YouTube streaming via FFmpeg
# Captures Xvfb screen + PulseAudio + subtitle overlay → RTMP to YouTube
#
# Usage: ./start_youtube_stream.sh <STREAM_KEY>
#
# Optional: Mac webcam PiP is received on RTMP port 1936 via nginx-rtmp or
# direct FFmpeg input. When a webcam stream is detected, it's composited as PiP.

set -euo pipefail

STREAM_KEY="${1:?Usage: start_youtube_stream.sh <STREAM_KEY>}"
YOUTUBE_URL="rtmp://a.rtmp.youtube.com/live2/${STREAM_KEY}"
SUBTITLE_FILE="/tmp/subtitle.txt"
WEBCAM_INPUT="rtmp://0.0.0.0:1936/live"

# Resolution from env (default 720p — matches Xvfb)
RESOLUTION="${RESOLUTION:-720}"
if [ "$RESOLUTION" = "1080" ]; then
    WIDTH=1920; HEIGHT=1080; BITRATE="6500k"
else
    WIDTH=1280; HEIGHT=720; BITRATE="4000k"
fi

# Ensure subtitle file exists (FFmpeg drawtext reload=1 needs it)
echo "" > "$SUBTITLE_FILE"

echo "=== YouTube Stream ==="
echo "Resolution: ${WIDTH}x${HEIGHT}"
echo "Bitrate: ${BITRATE}"
echo "Stream key: ${STREAM_KEY:0:4}****"
echo "Subtitle file: ${SUBTITLE_FILE}"
echo "Webcam RTMP input: ${WEBCAM_INPUT}"

# Wait for Xvfb display
for i in $(seq 1 30); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "Display :99 ready"
        break
    fi
    echo "Waiting for display :99... ($i/30)"
    sleep 1
done

# Wait for PulseAudio
for i in $(seq 1 15); do
    if pactl info >/dev/null 2>&1; then
        echo "PulseAudio ready"
        break
    fi
    echo "Waiting for PulseAudio... ($i/15)"
    sleep 1
done

# FFmpeg: x11grab + PulseAudio → YouTube RTMP
# Subtitle overlay via drawtext with reload=1 (reads file every frame)
# Webcam PiP: if Mac connects to port 1936, we can add it as overlay
exec ffmpeg -nostdin \
    -f x11grab -video_size "${WIDTH}x${HEIGHT}" -framerate 30 -i :99 \
    -f pulse -i virtual_speaker.monitor \
    -vf "drawtext=textfile=${SUBTITLE_FILE}:reload=1:\
fontsize=28:fontcolor=white:borderw=2:bordercolor=black:\
x=(w-text_w)/2:y=h-80:\
font=DejaVu Sans" \
    -c:v libx264 -preset veryfast -tune zerolatency \
    -b:v "${BITRATE}" -maxrate "${BITRATE}" -bufsize "$((${BITRATE%k} * 2))k" \
    -g 60 -keyint_min 60 \
    -c:a aac -b:a 128k -ar 44100 \
    -f flv \
    -flvflags no_duration_filesize \
    "${YOUTUBE_URL}"
