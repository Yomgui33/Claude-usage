"""
Application coordinator.

Data source priority (configurable in Settings)
-----------------------------------------------
auto     – try Desktop session → Anthropic API → JSONL files
desktop  – read Claude Desktop session cookies, call claude.ai internal API
api      – Anthropic API rate-limit headers (requires API key)
jsonl    – parse local Claude Code CLI conversation files
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone, timedelta
from typing import Optional

from pathlib import Path as _Path
from . import config as cfg_mod
from .data_store import DataStore
from .usage_reader import (
    compute_usage,
    compute_reset_time,
    build_history,
    diagnose,
)
from .api_client import fetch_usage as api_fetch_usage
from .desktop_session import fetch_usage as desktop_fetch_usage, is_available as desktop_is_available
from .startup import is_enabled as startup_is_enabled, set_enabled as startup_set
from .tray import TrayIcon
from .ui.main_window import MainWindow
from .ui.settings_window import SettingsWindow


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.withdraw()
        self.root.title("Claude Usage Monitor")

        # Auto-detect Claude data dir on very first launch
        cfg = cfg_mod.load()
        if cfg.get("claude_data_dir") == str(_Path.home() / ".claude"):
            detected = cfg_mod.auto_detect_claude_dir()
            if detected != cfg.get("claude_data_dir"):
                cfg["claude_data_dir"] = detected
                cfg_mod.save(cfg)
        self._cfg = cfg

        self._db   = DataStore(cfg_mod.db_path())
        self._last_refresh: Optional[datetime] = None
        self._after_id: Optional[str] = None
        # Track last API error so we don't spam the UI
        self._last_api_error: Optional[str] = None

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
    # Tray callbacks (called from the pystray thread → post to tk thread)
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
    # Core refresh logic
    # ------------------------------------------------------------------

    def _request_refresh(self) -> None:
        if self._after_id:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self._do_refresh()
        self._schedule_next_refresh()

    def _do_refresh(self) -> None:
        source     = self._cfg.get("data_source", "auto")
        api_key    = self._cfg.get("anthropic_api_key", "").strip()
        claude_dir = self._cfg.get("claude_data_dir", "")
        limit_5h   = self._cfg.get("limit_5h", 500_000)
        limit_7d   = self._cfg.get("limit_7d", 5_000_000)
        now        = datetime.now(timezone.utc)

        pct_5h = pct_7d = 0.0
        reset_5h = reset_7d = None
        tokens_5h = tokens_7d = 0

        use_desktop = (source == "desktop") or (
            source == "auto" and desktop_is_available() and not api_key
        )
        use_api = (source == "api") or (source == "auto" and bool(api_key))

        # ── 1. Claude Desktop session ─────────────────────────────────
        if use_desktop:
            desk = desktop_fetch_usage()
            if desk["error"]:
                if source == "desktop":
                    self._main_win.set_status(f"Desktop session: {desk['error']}")
                    self._last_refresh = now
                    self._tray.update_icon(0, 0)
                    return
                # auto mode → fall through
            else:
                pct_5h   = desk["pct_5h"]   or 0.0
                pct_7d   = desk["pct_7d"]   or 0.0
                reset_5h = desk["reset_5h"]
                reset_7d = desk["reset_7d"]
                # We don't have raw token counts from the desktop session
                tokens_5h = int(pct_5h / 100 * limit_5h)
                tokens_7d = int(pct_7d / 100 * limit_7d)
                self._main_win.set_status(None)
                self._persist_and_update(now, tokens_5h, tokens_7d, limit_5h, limit_7d,
                                         pct_5h, pct_7d, reset_5h, reset_7d)
                return

        # ── 2. Anthropic API (developer API key) ──────────────────────
        if use_api and api_key:
            api_data = api_fetch_usage(api_key)
            if api_data["error"]:
                self._last_api_error = api_data["error"]
                if source == "api":
                    self._main_win.set_status(f"API error: {api_data['error']}")
                    self._last_refresh = now
                    self._tray.update_icon(0, 0)
                    return
                # auto mode → fall through to JSONL
            else:
                self._last_api_error = None
                if api_data["pct_5h"] is not None:
                    pct_5h    = api_data["pct_5h"]
                    reset_5h  = api_data["reset_5h"]
                    tokens_5h = api_data["used_5h"] or 0
                    if api_data["limit_5h"]:
                        limit_5h = api_data["limit_5h"]
                if api_data["pct_7d"] is not None:
                    pct_7d    = api_data["pct_7d"]
                    reset_7d  = api_data["reset_7d"]
                    tokens_7d = api_data["used_7d"] or 0
                    if api_data["limit_7d"]:
                        limit_7d = api_data["limit_7d"]
                else:
                    jsonl = compute_usage(claude_dir)
                    tokens_7d = jsonl["tokens_7d"]
                    pct_7d = min(100.0, tokens_7d / limit_7d * 100) if limit_7d else 0.0
                    reset_7d = compute_reset_time(jsonl["oldest_event_7d"], timedelta(days=7))
                self._main_win.set_status(None)
                self._persist_and_update(now, tokens_5h, tokens_7d, limit_5h, limit_7d,
                                         pct_5h, pct_7d, reset_5h, reset_7d)
                return

        # ── 3. JSONL file parsing ─────────────────────────────────────
        usage = compute_usage(claude_dir)
        tokens_5h = usage["tokens_5h"]
        tokens_7d = usage["tokens_7d"]
        pct_5h = min(100.0, tokens_5h / limit_5h * 100) if limit_5h else 0.0
        pct_7d = min(100.0, tokens_7d / limit_7d * 100) if limit_7d else 0.0
        reset_5h = compute_reset_time(usage["oldest_event_5h"], timedelta(hours=5))
        reset_7d = compute_reset_time(usage["oldest_event_7d"], timedelta(days=7))

        # If nothing found via JSONL, show a helpful hint
        if usage["total_events"] == 0:
            self._main_win.set_status(
                "No usage data found. Add your API key in Settings for accurate data."
            )
        else:
            self._main_win.set_status(None)

        self._persist_and_update(now, tokens_5h, tokens_7d, limit_5h, limit_7d,
                                 pct_5h, pct_7d, reset_5h, reset_7d)

    def _persist_and_update(
        self,
        now: datetime,
        tokens_5h: int, tokens_7d: int,
        limit_5h: int, limit_7d: int,
        pct_5h: float, pct_7d: float,
        reset_5h, reset_7d,
    ) -> None:
        self._last_refresh = now
        self._db.insert_snapshot(now, tokens_5h, tokens_7d, limit_5h, limit_7d)
        self._db.purge_old()
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
        hours   = self._main_win.selected_period_hours
        history = self._db.get_history(hours_back=hours)
        self._main_win.update_chart(history)

    def _on_period_changed(self, event) -> None:  # noqa: ARG002
        self._refresh_chart()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _bootstrap_history(self) -> None:
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
        self._bootstrap_history()
        self._request_refresh()

    # ------------------------------------------------------------------
    # Startup toggle
    # ------------------------------------------------------------------

    def _on_startup_toggle(self, enabled: bool) -> None:
        ok = startup_set(enabled)
        if not ok:
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
