"""
System tray icon using pystray.

The icon is generated dynamically with Pillow and shows the current
5-hour usage percentage. It runs in a dedicated daemon thread so the
tkinter main loop stays in the main thread.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont


# ── Icon generation ──────────────────────────────────────────────────────────

_ICON_SIZE = 64


def _pct_color(pct: float) -> tuple[int, int, int]:
    if pct < 50:
        return (76, 175, 80)   # green
    if pct < 80:
        return (255, 152, 0)   # orange
    return (244, 67, 54)       # red


def make_icon(pct_5h: float = 0.0) -> Image.Image:
    """
    Generate a square icon image with:
      - Coloured arc (filled sector) showing 5h usage
      - Percentage text in the centre
    """
    size = _ICON_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color = _pct_color(pct_5h)
    bg = (220, 220, 220, 255)

    margin = 4
    box = [margin, margin, size - margin, size - margin]

    # Background circle
    draw.ellipse(box, fill=bg)

    # Filled arc for usage
    if pct_5h > 0:
        end_angle = -90 + (pct_5h / 100) * 360
        draw.pieslice(box, start=-90, end=end_angle, fill=color)

    # Inner white circle (donut effect)
    inner_margin = 14
    inner_box = [inner_margin, inner_margin, size - inner_margin, size - inner_margin]
    draw.ellipse(inner_box, fill=(255, 255, 255, 255))

    # Percentage text
    label = f"{int(pct_5h)}%"
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except (IOError, OSError):
        font = ImageFont.load_default()

    # Centre the text
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2
    draw.text((tx, ty), label, fill=(50, 50, 50, 255), font=font)

    return img


# ── Tray icon wrapper ─────────────────────────────────────────────────────────

class TrayIcon:
    """
    Manages the pystray.Icon life-cycle.

    Callbacks
    ---------
    on_open     – called when the user left-clicks the icon or selects "Open"
    on_refresh  – called when the user selects "Refresh"
    on_settings – called when the user selects "Settings"
    on_quit     – called when the user selects "Quit"
    """

    def __init__(
        self,
        on_open: Callable,
        on_refresh: Callable,
        on_settings: Callable,
        on_quit: Callable,
    ):
        self._on_open = on_open
        self._on_refresh = on_refresh
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._icon: Optional["pystray.Icon"] = None  # type: ignore[name-defined]
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the tray icon in a daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()

    def update_icon(self, pct_5h: float, pct_7d: float) -> None:
        """Regenerate and push a new icon image."""
        if self._icon is None:
            return
        self._icon.icon = make_icon(pct_5h)
        tooltip = (
            f"Claude Usage\n"
            f"5h: {pct_5h:.1f}%   7d: {pct_7d:.1f}%"
        )
        self._icon.title = tooltip

    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            import pystray
        except ImportError:
            print("[tray] pystray not installed – tray icon disabled")
            return

        menu = pystray.Menu(
            pystray.MenuItem("Open", self._cb_open, default=True),
            pystray.MenuItem("Refresh", self._cb_refresh),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", self._cb_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._cb_quit),
        )

        self._icon = pystray.Icon(
            name="ClaudeUsageMonitor",
            icon=make_icon(0),
            title="Claude Usage Monitor",
            menu=menu,
        )
        self._icon.run()

    # ------------------------------------------------------------------
    # Callbacks (run in pystray thread → must be thread-safe)

    def _cb_open(self, icon, item) -> None:  # noqa: ARG002
        self._on_open()

    def _cb_refresh(self, icon, item) -> None:  # noqa: ARG002
        self._on_refresh()

    def _cb_settings(self, icon, item) -> None:  # noqa: ARG002
        self._on_settings()

    def _cb_quit(self, icon, item) -> None:  # noqa: ARG002
        self._on_quit()
        if self._icon:
            self._icon.stop()
