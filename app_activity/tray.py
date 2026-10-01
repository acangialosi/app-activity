from __future__ import annotations

import ctypes
import webbrowser
from ctypes import wintypes
from pathlib import Path


WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_USER = 0x0400
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_TRAYICON = WM_USER + 1
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
SW_HIDE = 0
IDI_APPLICATION = 32512
IDC_ARROW = 32512
OPEN_REPORT = 1001
EXIT_APP = 1002


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadCursorW.restype = wintypes.HANDLE
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.FillRect.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.HBRUSH,
]
user32.CreatePopupMenu.restype = wintypes.HMENU
shell32.Shell_NotifyIconW.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(NOTIFYICONDATAW),
]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.LPVOID,
]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class TrayApplication:
    def __init__(self, stop_path: Path, dashboard_url: str) -> None:
        self.stop_path = stop_path
        self.dashboard_url = dashboard_url
        self.hwnd: int | None = None
        self.icon: int | None = None
        self._notify_data: NOTIFYICONDATAW | None = None
        self._window_proc = WNDPROC(self._handle_message)

    def run(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        class_name = "AppActivityTrayWindow"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._window_proc
        window_class.hInstance = instance
        window_class.hCursor = user32.LoadCursorW(None, IDC_ARROW)
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != 1410:
                raise ctypes.WinError(error)

        self.hwnd = user32.CreateWindowExW(
            0,
            class_name,
            "App Activity",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        self.icon = _create_activity_icon()
        notify_data = NOTIFYICONDATAW()
        notify_data.cbSize = ctypes.sizeof(notify_data)
        notify_data.hWnd = self.hwnd
        notify_data.uID = 1
        notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        notify_data.uCallbackMessage = WM_TRAYICON
        notify_data.hIcon = self.icon
        notify_data.szTip = "App Activity - tracking is running"
        self._notify_data = notify_data
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data)):
            raise ctypes.WinError(ctypes.get_last_error())

        user32.ShowWindow(self.hwnd, SW_HIDE)
        user32.SetTimer(self.hwnd, 1, 1000, None)
        message = MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _handle_message(
        self, hwnd: int, message: int, wparam: int, lparam: int
    ) -> int:
        if message == WM_TRAYICON:
            if lparam == WM_LBUTTONUP:
                self.open_report()
            elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu(hwnd)
            return 0
        if message == WM_COMMAND:
            command = wparam & 0xFFFF
            if command == OPEN_REPORT:
                self.open_report()
            elif command == EXIT_APP:
                self.stop_path.touch()
                user32.DestroyWindow(hwnd)
            return 0
        if message == WM_TIMER:
            if self.stop_path.exists():
                user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def open_report(self) -> None:
        webbrowser.open(self.dashboard_url)

    def _show_menu(self, hwnd: int) -> None:
        menu = user32.CreatePopupMenu()
        try:
            user32.AppendMenuW(menu, MF_STRING, OPEN_REPORT, "Open usage report")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, EXIT_APP, "Exit App Activity")
            position = POINT()
            user32.GetCursorPos(ctypes.byref(position))
            user32.SetForegroundWindow(hwnd)
            user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
                position.x,
                position.y,
                0,
                hwnd,
                None,
            )
        finally:
            user32.DestroyMenu(menu)

    def _remove_icon(self) -> None:
        if self._notify_data is not None:
            shell32.Shell_NotifyIconW(
                NIM_DELETE, ctypes.byref(self._notify_data)
            )
            self._notify_data = None
        if self.icon:
            user32.DestroyIcon(self.icon)
            self.icon = None


def _create_activity_icon() -> int:
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    color_bitmap = gdi32.CreateCompatibleBitmap(screen_dc, 32, 32)
    old_bitmap = gdi32.SelectObject(memory_dc, color_bitmap)
    try:
        _fill(memory_dc, 0, 0, 32, 32, _color(21, 29, 43))
        _fill(memory_dc, 5, 21, 10, 27, _color(114, 167, 255))
        _fill(memory_dc, 13, 14, 18, 27, _color(114, 167, 255))
        _fill(memory_dc, 21, 7, 26, 27, _color(114, 167, 255))
        _fill(memory_dc, 4, 28, 28, 30, _color(145, 160, 184))
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)

    mask_bits = (ctypes.c_ubyte * 128)()
    mask_bitmap = gdi32.CreateBitmap(32, 32, 1, 1, mask_bits)
    icon_info = ICONINFO(True, 0, 0, mask_bitmap, color_bitmap)
    icon = user32.CreateIconIndirect(ctypes.byref(icon_info))
    gdi32.DeleteObject(mask_bitmap)
    gdi32.DeleteObject(color_bitmap)
    if not icon:
        raise ctypes.WinError(ctypes.get_last_error())
    return icon


def _fill(
    device_context: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
    color: int,
) -> None:
    brush = gdi32.CreateSolidBrush(color)
    rectangle = wintypes.RECT(left, top, right, bottom)
    user32.FillRect(device_context, ctypes.byref(rectangle), brush)
    gdi32.DeleteObject(brush)


def _color(red: int, green: int, blue: int) -> int:
    return red | (green << 8) | (blue << 16)
