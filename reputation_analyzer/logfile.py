"""File logging for the reputation analyzer.

Attaches a rotating file handler to the ``reputation_analyzer`` package logger
so every log line (DEBUG and up) from the pipeline is persisted to
``logs/reputation/reputation.log`` in addition to the console.

Guarded like every other integration in this repo: if the log directory can't
be created (read-only FS, permissions) the API keeps working console-only.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = os.getenv("REPUTATION_LOG_DIR", "./logs/reputation")
LOG_FILE = "reputation.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Marker attribute so re-imports (uvicorn reload, tests) don't stack handlers.
_MARKER = "_reputation_file_handler"


def setup_file_logging() -> None:
    """Idempotently attach the reputation file handler to the package logger."""
    pkg_log = logging.getLogger("reputation_analyzer")
    if any(getattr(h, _MARKER, False) for h in pkg_log.handlers):
        return

    try:
        log_dir = Path(LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as e:
        logging.getLogger(__name__).warning(
            "reputation file logging disabled (cannot open %s): %s", LOG_DIR, e
        )
        return

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FORMAT))
    setattr(handler, _MARKER, True)
    pkg_log.addHandler(handler)
    # DEBUG so the file captures everything regardless of the app's root level.
    pkg_log.setLevel(logging.DEBUG)
