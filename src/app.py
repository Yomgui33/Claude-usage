"""
Application coordinator.

Responsibilities:
- Own the tkinter root window (hidden – only the tray icon is visible)
- Manage the refresh timer
- Coordinate between TrayIcon, MainWindow, and SettingsWindow
- Bridge thread-safe calls from the tray into the tkinter main thread
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone, timedelta
from typing import Optional

from . import config as cfg_mod
from .data_store import DataStore
from .usage_reader import compute_usage, compute_reset_time, build_history
from .startup import is_enabled as startup_is_enabled, set_enabled as startup_set
from .tray import TrayIcon
from .ui.main_window import MainWindow
from .ui.settings_window import SettingsWindow


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.withdraw()          # invisible root – we use Toplevel for the popup
        self.root.title("Claude Usage Monitor")

        self._cfg  = cfg_mod.load()
        self._db   = DataStore(cfg_mod.db_path())
        self._last_refresh: Optional[datetime] = None
        self._after_id: Optional[str] = None

        # Build UI components
        self._main_win = MainWindow(
            root=self.root,
            on_refresh=self._request_refresh,
            on_settings=self._open_settings,
            on_quit=self._quit,
            on_startup_toggle=self._on_startup_toggle,
        )
        self._main_win.set_startup_state(startup_is_enabled())

        self._settings_win: Optional[SettingsWindow] = None

        # System tray
        self._tray = TrayIcon(
            on_open=self._tray_open,
            on_refresh=self._tray_refresh,
            on_settings=self._tray_settings,
            on_quit=self._tray_quit,
        )

        # Listen for period changes from the chart selector
        self.root.bind("<<PeriodChanged>>", self._on_period_changed)

        # Bootstrap historical data on first run (populate DB from JSONL)
        if not self._db.has_history():
            self._bootstrap_history()

        # Initial refresh
        self._do_refresh()

        # Start tray
        self._tray.start()

        # Schedule periodic refresh
        self._schedule_next_refresh()

    # ------------------------------------------------------------------
    # Tray callbacks (called from the pystray thread)
    # All must post work back to the tkinter thread via after()
    # ------------------------------------------------------------------

    def _tray_open(self) -> None:
        self.root.after(0, self._main_win.toggle)

    def _tray_refresh(self) -> None:
        self.root.after(0, self._request_refresh)

    def _tray_settings(self) -> None:
        self.root.after(0, self._open_settings)

    def _tray_quit(self) -> None:
        self.root.after(0, self._quit)

    # ------------------------------------------------------------------
    # Core refresh logic (always runs in the tkinter main thread)
    # ------------------------------------------------------------------

    def _request_refresh(self) -> None:
        """Cancel any pending scheduled refresh and do one immediately."""
        if self._after_id:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self._do_refresh()
        self._schedule_next_refresh()

    def _do_refresh(self) -> None:
        claude_dir = self._cfg.get("claude_data_dir", "")
        limit_5h   = self._cfg.get("limit_5h", 500_000)
        limit_7d   = self._cfg.get("limit_7d", 5_000_000)

        # Compute current usage
        usage = compute_usage(claude_dir)
        now   = datetime.now(timezone.utc)

        tokens_5h = usage["tokens_5h"]
        tokens_7d = usage["tokens_7d"]
        pct_5h    = min(100.0, tokens_5h / limit_5h * 100) if limit_5h else 0.0
        pct_7d    = min(100.0, tokens_7d / limit_7d * 100) if limit_7d else 0.0

        reset_5h = compute_reset_time(usage["oldest_event_5h"], timedelta(hours=5))
        reset_7d = compute_reset_time(usage["oldest_event_7d"], timedelta(days=7))

        self._last_refresh = now

        # Persist snapshot
        self._db.insert_snapshot(now, tokens_5h, tokens_7d, limit_5h, limit_7d)
        self._db.purge_old()

        # Update UI
        self._main_win.update_usage(pct_5h, pct_7d, reset_5h, reset_7d, now)
        self._tray.update_icon(pct_5h, pct_7d)
        self._refresh_chart()

    def _schedule_next_refresh(self) -> None:
        interval_ms = max(30_000, self._cfg.get("refresh_interval", 300) * 1000)
        self._after_id = self.root.after(interval_ms, self._request_refresh)

    # ------------------------------------------------------------------
    # Chart
    # ------------------------------------------------------------------

    def _refresh_chart(self) -> None:
        hours = self._main_win.selected_period_hours
        history = self._db.get_history(hours_back=hours)
        self._main_win.update_chart(history)

    def _on_period_changed(self, event) -> None:  # noqa: ARG002
        self._refresh_chart()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _bootstrap_history(self) -> None:
        """
        On first launch, parse the full JSONL history to populate the DB.
        This runs synchronously but is quick enough for typical JSONL sizes.
        """
        claude_dir = self._cfg.get("claude_data_dir", "")
        limit_5h   = self._cfg.get("limit_5h", 500_000)
        limit_7d   = self._cfg.get("limit_7d", 5_000_000)
        rows = build_history(claude_dir, bucket_minutes=60)
        if rows:
            self._db.bulk_insert(rows, limit_5h, limit_7d)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        self._settings_win = SettingsWindow(
            parent=self.root,
            cfg=self._cfg,
            on_save=self._on_settings_saved,
        )
        self._settings_win.show()

    def _on_settings_saved(self, new_cfg: dict) -> None:
        self._cfg = new_cfg
        cfg_mod.save(new_cfg)
        # Re-bootstrap history if limits changed significantly
        self._bootstrap_history()
        self._request_refresh()

    # ------------------------------------------------------------------
    # Startup toggle
    # ------------------------------------------------------------------

    def _on_startup_toggle(self, enabled: bool) -> None:
        ok = startup_set(enabled)
        if not ok:
            # Revert the toggle on failure
            self._main_win.set_startup_state(not enabled)
        self._cfg["launch_at_startup"] = enabled
        cfg_mod.save(self._cfg)

    # ------------------------------------------------------------------
    # Quit
    # ------------------------------------------------------------------

    def _quit(self) -> None:
        if self._after_id:
            self.root.after_cancel(self._after_id)
        self._tray.stop()
        self.root.after(200, self.root.destroy)
