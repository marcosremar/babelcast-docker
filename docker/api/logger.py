"""Centralized logging for BabelCast Docker API.

- Daily log files in /app/logs/ (or ./logs/ locally)
- Console output (INFO) + file output (DEBUG)
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path("/app/logs") if Path("/app").exists() else Path(__file__).resolve().parent.parent / "logs"

_initialized = False


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure root logger with console + daily file handlers."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"gateway_{today}.log"

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger().info("Logging initialized -> %s", log_file)
