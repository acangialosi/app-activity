from __future__ import annotations

import os
import threading
import traceback

from .collector import ActivityCollector
from .dashboard import create_server
from .database import ActivityDatabase
from .paths import DATABASE_PATH, LOG_PATH, PID_PATH, PORT, STOP_PATH, ensure_data_dir
from .tray import TrayApplication


def run_service() -> None:
    ensure_data_dir()
    STOP_PATH.unlink(missing_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="ascii")
    stop_event = threading.Event()

    database: ActivityDatabase | None = None
    try:
        database = ActivityDatabase(DATABASE_PATH)
        collector = ActivityCollector(database, stop_event)
        collector_thread = threading.Thread(
            target=collector.run, name="activity-collector", daemon=True
        )
        collector_thread.start()
        server = create_server(database, PORT)
        server_thread = threading.Thread(
            target=server.serve_forever, name="activity-dashboard", daemon=True
        )
        server_thread.start()
        tray = TrayApplication(STOP_PATH, f"http://127.0.0.1:{PORT}")
        tray.run()
        stop_event.set()
        server.shutdown()
        collector_thread.join(timeout=10)
        server_thread.join(timeout=10)
        server.server_close()
    except Exception:
        with LOG_PATH.open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        raise
    finally:
        if database is not None:
            database.close()
        PID_PATH.unlink(missing_ok=True)
        STOP_PATH.unlink(missing_ok=True)
