from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from .database import ActivityState


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def get_activity_state(idle_threshold_seconds: int) -> ActivityState:
    idle_seconds = _idle_seconds()
    if idle_seconds >= idle_threshold_seconds:
        return ActivityState(app_name="Idle", is_idle=True)

    window = user32.GetForegroundWindow()
    if not window:
        return ActivityState(app_name="Unknown", is_idle=False)

    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    process = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value
    )
    if not process:
        return ActivityState(app_name="Unknown", is_idle=False)

    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(capacity)
        ):
            return ActivityState(
                app_name=Path(buffer.value).stem or "Unknown", is_idle=False
            )
    finally:
        kernel32.CloseHandle(process)
    return ActivityState(app_name="Unknown", is_idle=False)


def _idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick_count = kernel32.GetTickCount64()
    return max(0.0, (tick_count - info.dwTime) / 1000.0)
