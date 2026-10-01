from __future__ import annotations

import argparse
import ctypes
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
import winreg
from pathlib import Path

from .paths import DATA_DIR, PID_PATH, PORT, STOP_PATH, ensure_data_dir
from .service import run_service


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AppActivity"
ROOT = Path(__file__).resolve().parent.parent
HIDDEN_RUNNER = ROOT / "run_hidden.pyw"
IS_FROZEN = bool(getattr(sys, "frozen", False))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="app-activity",
        description="Local Windows application activity tracker",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="Run the service in the foreground")
    commands.add_parser("start", help="Start the background service")
    commands.add_parser("stop", help="Stop the background service")
    commands.add_parser("status", help="Show service and startup status")
    commands.add_parser("dashboard", help="Open the local dashboard")
    commands.add_parser("install", help="Run automatically when you sign in")
    uninstall = commands.add_parser("uninstall", help="Remove automatic startup")
    uninstall.add_argument(
        "--delete-data", action="store_true", help="Also delete collected history"
    )
    args = parser.parse_args()

    command = args.command or "dashboard"
    if command == "run":
        run_service()
    elif command == "start":
        started = start_service()
        print("App Activity started." if started else "App Activity is already running.")
    elif command == "stop":
        print("App Activity stopped." if stop_service() else "App Activity is not running.")
    elif command == "status":
        running = is_running()
        print(f"Service: {'running' if running else 'stopped'}")
        print(f"Start at sign-in: {'enabled' if startup_enabled() else 'disabled'}")
        if running:
            print(f"Dashboard: http://127.0.0.1:{PORT}")
    elif command == "dashboard":
        start_service()
        if wait_until_ready():
            webbrowser.open(f"http://127.0.0.1:{PORT}")
            print(f"Dashboard opened at http://127.0.0.1:{PORT}")
        else:
            raise SystemExit("The App Activity service did not start. Check the log.")
    elif command == "install":
        install_startup()
        start_service()
        print("App Activity will now run automatically when you sign in.")
        print(f"Dashboard: http://127.0.0.1:{PORT}")
    elif command == "uninstall":
        remove_startup()
        stop_service()
        if args.delete_data and DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        print("Automatic startup removed.")


def start_service() -> bool:
    ensure_data_dir()
    if is_running():
        return False
    STOP_PATH.unlink(missing_ok=True)
    command, working_directory = _service_command()
    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    subprocess.Popen(
        command,
        cwd=working_directory,
        creationflags=creation_flags,
        close_fds=True,
    )
    return True


def stop_service() -> bool:
    if not is_running():
        PID_PATH.unlink(missing_ok=True)
        return False
    STOP_PATH.touch()
    deadline = time.time() + 12
    while time.time() < deadline:
        if not is_running():
            return True
        time.sleep(0.25)
    raise SystemExit("Timed out waiting for App Activity to stop.")


def is_running() -> bool:
    try:
        pid = int(PID_PATH.read_text(encoding="ascii"))
    except (FileNotFoundError, ValueError):
        return False
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    ctypes.windll.kernel32.CloseHandle(process)
    return True


def wait_until_ready() -> bool:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/status", timeout=1
            ) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.2)
    return False


def install_startup() -> None:
    service_command, _ = _service_command()
    command = subprocess.list2cmdline(service_command)
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)


def remove_startup() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except FileNotFoundError:
        return False


def _service_command() -> tuple[list[str], Path]:
    if IS_FROZEN:
        executable = Path(sys.executable).resolve()
        return [str(executable), "run"], executable.parent
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else Path(sys.executable)
    return [str(executable), str(HIDDEN_RUNNER)], ROOT
