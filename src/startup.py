"""
Windows startup management – adds / removes the app from
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
so it launches automatically when the user logs in.

Gracefully degrades on non-Windows platforms (no-op).
"""
from __future__ import annotations

import sys
import os

APP_NAME = "ClaudeUsageMonitor"


def _get_executable() -> str:
    """Return the path that should be registered for startup."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller .exe
        return sys.executable
    # Running as a plain Python script – launch via pythonw to avoid a console
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main.py")
    main_py = os.path.normpath(main_py)
    return f'"{pythonw}" "{main_py}"'


def is_enabled() -> bool:
    """Return True if the app is currently set to run at startup."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, APP_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def enable() -> bool:
    """Register the app for startup. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _get_executable())
        winreg.CloseKey(key)
        return True
    except Exception as exc:
        print(f"[startup] Failed to enable: {exc}")
        return False


def disable() -> bool:
    """Remove the app from startup. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass  # Already absent
        winreg.CloseKey(key)
        return True
    except Exception as exc:
        print(f"[startup] Failed to disable: {exc}")
        return False


def set_enabled(enabled: bool) -> bool:
    return enable() if enabled else disable()
