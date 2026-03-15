#!/usr/bin/env python3
"""
subtitle_overlay.py — Exibe legendas traduzidas na tela do Xvfb.

Lê /tmp/subtitle.txt a cada 300ms e renderiza uma barra de legenda
na parte inferior da tela. Como o ScreenRecorder usa x11grab para
capturar o Xvfb, as legendas aparecem automaticamente no vídeo gravado
e na visão que o Teams vê do bot.

Uso: DISPLAY=:99 python3 subtitle_overlay.py
"""

import os
import time
import tkinter as tk

SUBTITLE_FILE = os.environ.get("SUBTITLE_FILE", "/tmp/subtitle.txt")
SCREEN_W      = int(os.environ.get("SCREEN_W", "1280"))
SCREEN_H      = int(os.environ.get("SCREEN_H", "860"))
BAR_H         = 90
FONT_SIZE     = 22
BG_COLOR      = "#111111"
FG_COLOR      = "#FFFFFF"
ALPHA         = 0.82   # window transparency (0.0 transparent … 1.0 opaque)


def read_subtitle() -> str:
    try:
        if os.path.exists(SUBTITLE_FILE):
            return open(SUBTITLE_FILE, encoding="utf-8").read().strip()
    except Exception:
        pass
    return ""


def main():
    root = tk.Tk()
    root.overrideredirect(True)                          # no window decorations
    root.wm_attributes("-topmost", True)                 # always on top
    try:
        root.wm_attributes("-alpha", ALPHA)              # transparency (if supported)
    except Exception:
        pass

    # Position at bottom of screen
    root.geometry(f"{SCREEN_W}x{BAR_H}+0+{SCREEN_H - BAR_H}")
    root.configure(bg=BG_COLOR)

    label = tk.Label(
        root,
        text="",
        bg=BG_COLOR,
        fg=FG_COLOR,
        font=("DejaVu Sans", FONT_SIZE, "bold"),
        wraplength=SCREEN_W - 40,
        justify="center",
        anchor="center",
    )
    label.pack(expand=True, fill="both", padx=20)

    last_text = None

    def poll():
        nonlocal last_text
        text = read_subtitle()
        if text != last_text:
            label.config(text=text)
            # Show/hide bar based on whether there's text
            if text:
                root.deiconify()
            else:
                root.withdraw()
            last_text = text
        root.after(300, poll)

    # Start hidden; appears when first subtitle arrives
    root.withdraw()
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
