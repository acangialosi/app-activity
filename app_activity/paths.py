from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "AppActivity"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
DATABASE_PATH = DATA_DIR / "activity.db"
PID_PATH = DATA_DIR / "service.pid"
STOP_PATH = DATA_DIR / "stop.requested"
LOG_PATH = DATA_DIR / "service.log"
PORT = 8765


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
