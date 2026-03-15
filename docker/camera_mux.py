#!/usr/bin/env python3
"""
camera_mux.py — Virtual webcam multiplexer for BabelCast bot.

Listens on TCP port 9091 for raw YUV420p frames from the Mac.
Writes a continuous Y4M stream to a named pipe so Chromium can use it
as a virtual camera (via --use-file-for-fake-video-capture).

When no Mac is connected, outputs a black frame at the configured framerate
so Chrome always has data to read.

Usage: python3 camera_mux.py /tmp/camera.y4m
"""

import os
import socket
import struct
import sys
import threading
import time

WIDTH  = int(os.environ.get("CAM_WIDTH",  "640"))
HEIGHT = int(os.environ.get("CAM_HEIGHT", "480"))
FPS    = int(os.environ.get("CAM_FPS",    "15"))
PORT   = int(os.environ.get("CAM_PORT",   "9091"))

FRAME_SIZE = WIDTH * HEIGHT * 3 // 2  # YUV420p bytes per frame

# Black frame (Y=0, U=128, V=128)
_BLACK = (
    b"FRAME\n"
    + bytes(WIDTH * HEIGHT)            # Y plane (black)
    + bytes([128] * (WIDTH * HEIGHT // 2))  # UV planes (neutral chroma)
)

_current_frame = _BLACK
_frame_lock = threading.Lock()
_clients = 0


def _receiver():
    """Accept Mac connections and update the current frame."""
    global _current_frame, _clients
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(1)
    print(f"[camera_mux] Listening for webcam on TCP :{PORT} ({WIDTH}x{HEIGHT} @ {FPS}fps)")

    while True:
        conn, addr = srv.accept()
        _clients += 1
        print(f"[camera_mux] Mac webcam connected from {addr}")
        buf = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= FRAME_SIZE:
                    raw = buf[:FRAME_SIZE]
                    buf = buf[FRAME_SIZE:]
                    with _frame_lock:
                        _current_frame = b"FRAME\n" + raw
        except Exception as e:
            print(f"[camera_mux] Mac disconnected: {e}")
        finally:
            conn.close()
            _clients -= 1
            with _frame_lock:
                _current_frame = _BLACK
            print("[camera_mux] Reverted to black frame")


def main():
    pipe_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/camera.y4m"

    # Start receiver thread (non-blocking, accepts Mac connections)
    t = threading.Thread(target=_receiver, daemon=True)
    t.start()

    # Create FIFO if needed
    if not os.path.exists(pipe_path):
        os.mkfifo(pipe_path)
        print(f"[camera_mux] Created FIFO: {pipe_path}")
    else:
        print(f"[camera_mux] Using existing FIFO: {pipe_path}")

    interval = 1.0 / FPS
    header = f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F{FPS}:1 Ip A0:0\n".encode()

    print(f"[camera_mux] Waiting for Chrome to open {pipe_path}...")
    # open() on a FIFO blocks until the reader (Chrome) opens it
    with open(pipe_path, "wb") as f:
        print("[camera_mux] Chrome opened camera FIFO — streaming started")
        f.write(header)
        f.flush()
        while True:
            t0 = time.monotonic()
            with _frame_lock:
                frame = _current_frame
            try:
                f.write(frame)
                f.flush()
            except BrokenPipeError:
                print("[camera_mux] Chrome closed camera — exiting")
                break
            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


if __name__ == "__main__":
    main()
