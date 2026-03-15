#!/bin/bash
# BabelCast Monitor — runs on the Mac host
# Polls the container and plays sounds for key events:
#   • waiting_room → bot needs approval in Teams → plays a notification sound
#   • new transcripts → shows them in the terminal
#
# Usage: ./monitor.sh [host_port]
#   Default port: 8090

PORT="${1:-8090}"
BASE="http://localhost:$PORT"

LAST_STATE="unknown"
LAST_TRANSCRIPT_COUNT=0
WAITING_ROOM_NOTIFIED=false

echo "=== BabelCast Monitor (port $PORT) ==="
echo "  Polling every 3s — Ctrl+C to stop"
echo ""

while true; do
    # ── Bot state ──────────────────────────────────────────────────────────────
    STATE_JSON=$(curl -sf "$BASE/bot-state" 2>/dev/null)
    if [ -n "$STATE_JSON" ]; then
        STATE=$(echo "$STATE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state','?'))" 2>/dev/null)
        TC=$(echo "$STATE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_count',0))" 2>/dev/null)

        if [ "$STATE" != "$LAST_STATE" ]; then
            TS=$(date '+%H:%M:%S')
            echo "[$TS] Bot state: $LAST_STATE → $STATE"
            LAST_STATE="$STATE"

            case "$STATE" in
                waiting_room)
                    echo "  ⏳ Bot is in the waiting room — APPROVE in Teams now!"
                    # Play notification sound (macOS system sounds)
                    afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
                    afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
                    WAITING_ROOM_NOTIFIED=true
                    ;;
                in_meeting)
                    echo "  ✅ Bot joined the meeting — transcripts flowing"
                    afplay /System/Library/Sounds/Ping.aiff 2>/dev/null &
                    WAITING_ROOM_NOTIFIED=false
                    ;;
                idle)
                    echo "  💤 Bot is idle"
                    WAITING_ROOM_NOTIFIED=false
                    ;;
                joining)
                    echo "  🔄 Bot is joining..."
                    ;;
            esac
        fi

        # Remind every ~30s if still in waiting room (in case sound was missed)
        if [ "$STATE" = "waiting_room" ]; then
            ELAPSED=$(echo "$STATE_JSON" | python3 -c "
import sys, json, time
d = json.load(sys.stdin)
since = d.get('since', time.time())
elapsed = int(time.time() - since)
print(elapsed)
" 2>/dev/null)
            # Play reminder every 30 seconds
            if [ -n "$ELAPSED" ] && [ "$ELAPSED" -gt 0 ] && [ $((ELAPSED % 30)) -eq 0 ] 2>/dev/null; then
                echo "  ⚠️  Still in waiting room (${ELAPSED}s) — approve in Teams!"
                afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
            fi
        fi

        # ── New transcripts ────────────────────────────────────────────────────
        if [ -n "$TC" ] && [ "$TC" -gt "$LAST_TRANSCRIPT_COUNT" ] 2>/dev/null; then
            # Fetch and show new transcripts
            TRANS_JSON=$(curl -sf "$BASE/transcripts" 2>/dev/null)
            if [ -n "$TRANS_JSON" ]; then
                NEW=$(echo "$TRANS_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d.get('transcripts', [])
start = $LAST_TRANSCRIPT_COUNT
for item in items[start:]:
    print(item.get('text', ''))
" 2>/dev/null)
                if [ -n "$NEW" ]; then
                    TS=$(date '+%H:%M:%S')
                    echo ""
                    echo "$NEW" | while IFS= read -r line; do
                        echo "  [$TS] 📝 $line"
                    done
                    echo ""
                fi
            fi
            LAST_TRANSCRIPT_COUNT=$TC
        fi
    else
        if [ "$LAST_STATE" != "unreachable" ]; then
            echo "[$(date '+%H:%M:%S')] Container unreachable at $BASE"
            LAST_STATE="unreachable"
        fi
    fi

    sleep 3
done
