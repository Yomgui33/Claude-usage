"""
Claude Usage Monitor – Windows System Tray Application
======================================================

Monitors Claude Code (claude CLI) token usage by reading local conversation
history files stored at ~/.claude/projects/ and displays the rolling
5-hour and 7-day consumption against configured rate limits.

Usage:
    python main.py                  # run from source
    ClaudeUsageMonitor.exe          # packaged with PyInstaller

The application:
  - Resides in the Windows system tray
  - Left-click or double-click the tray icon to open the popup
  - Auto-refreshes at a configurable interval (default: 5 minutes)
  - Can launch automatically at Windows startup
"""
import sys
import tkinter as tk


def _check_deps() -> None:
    """Friendly error if required packages are missing."""
    missing = []
    for pkg in ("pystray", "PIL", "matplotlib"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        msg = (
            "Missing required packages:\n  " + ", ".join(missing) +
            "\n\nPlease run:\n  pip install -r requirements.txt"
        )
        try:
            import tkinter.messagebox as mb
            root = tk.Tk()
            root.withdraw()
            mb.showerror("Claude Usage Monitor – Missing Dependencies", msg)
            root.destroy()
        except Exception:
            print(msg, file=sys.stderr)
        sys.exit(1)


def main() -> None:
    _check_deps()

    from src.app import App

    root = tk.Tk()
    root.withdraw()

    # Prevent the root window from showing in the taskbar
    try:
        root.wm_attributes("-toolwindow", True)
    except tk.TclError:
        pass  # Non-Windows platform

    _app = App(root)  # noqa: F841 – keep reference alive

    root.mainloop()


if __name__ == "__main__":
    main()
