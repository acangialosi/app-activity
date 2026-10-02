from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

from .app_classification import uses_title_details
from .database import ActivityState


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)

version.GetFileVersionInfoSizeW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
version.GetFileVersionInfoW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
]
version.GetFileVersionInfoW.restype = wintypes.BOOL
version.VerQueryValueW.argtypes = [
    wintypes.LPCVOID,
    wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.LPVOID),
    ctypes.POINTER(wintypes.UINT),
]
version.VerQueryValueW.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int,
]
user32.GetWindowTextW.restype = ctypes.c_int


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
            path = Path(buffer.value)
            app_name, legacy_names = _application_metadata(path)
            window_title = _window_title(window)
            if _is_lock_screen(app_name, window_title):
                return ActivityState(app_name="Idle", is_idle=True)
            return ActivityState(
                app_name=app_name,
                is_idle=False,
                process_name=path.stem or None,
                window_title=window_title,
                detail_key=_detail_key(app_name, int(window), window_title),
                legacy_names=legacy_names,
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


@lru_cache(maxsize=256)
def _application_name(path: Path) -> str:
    return _application_metadata(path)[0]


@lru_cache(maxsize=256)
def _application_metadata(path: Path) -> tuple[str, tuple[str, ...]]:
    product_name = _version_string(path, "ProductName")
    description = _version_string(path, "FileDescription")
    display_name = product_name or description or path.stem or "Unknown"
    legacy_names = tuple(
        name
        for name in (description,)
        if name and name != display_name
    )
    return display_name, legacy_names


def _window_title(window: int) -> str:
    length = user32.GetWindowTextLengthW(window)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    copied = user32.GetWindowTextW(window, buffer, len(buffer))
    return buffer.value.strip() if copied else ""


def _detail_key(app_name: str, window: int, window_title: str) -> str:
    if uses_title_details(app_name) and window_title:
        return f"tab:{window}:{window_title}"
    return f"window:{window}"


def _is_lock_screen(app_name: str, window_title: str) -> bool:
    return (
        app_name.casefold() == "windows default lock screen"
        or window_title.casefold() == "windows default lock screen"
    )


def _version_string(path: Path, field: str) -> str | None:
    ignored = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size:
        return None

    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None

    translations_pointer = wintypes.LPVOID()
    translations_size = wintypes.UINT()
    translations: list[tuple[int, int]] = []
    if version.VerQueryValueW(
        data,
        r"\VarFileInfo\Translation",
        ctypes.byref(translations_pointer),
        ctypes.byref(translations_size),
    ):
        pair_count = translations_size.value // (ctypes.sizeof(wintypes.WORD) * 2)
        words = ctypes.cast(
            translations_pointer, ctypes.POINTER(wintypes.WORD)
        )
        translations = [
            (words[index * 2], words[index * 2 + 1])
            for index in range(pair_count)
        ]

    translations.extend([(0x0409, 0x04B0), (0x0409, 0x04E4)])
    for language, code_page in translations:
        value_pointer = wintypes.LPVOID()
        value_size = wintypes.UINT()
        query = (
            rf"\StringFileInfo\{language:04x}{code_page:04x}\{field}"
        )
        if version.VerQueryValueW(
            data,
            query,
            ctypes.byref(value_pointer),
            ctypes.byref(value_size),
        ):
            value = (
                ctypes.wstring_at(value_pointer, value_size.value)
                .rstrip("\0")
                .strip()
            )
            if value:
                return value
    return None
