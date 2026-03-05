"""
Settings dialog – lets the user configure the app.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable

BG       = "#f5f5f5"
TEXT     = "#222222"
ENTRY_BG = "#ffffff"
HINT     = "#888888"


class SettingsWindow:
    def __init__(self, parent: tk.Tk, cfg: dict, on_save: Callable[[dict], None]):
        self._parent  = parent
        self._cfg     = dict(cfg)
        self._on_save = on_save
        self._win: tk.Toplevel | None = None

    # ------------------------------------------------------------------

    def show(self) -> None:
        if self._win and self._win.winfo_exists():
            self._win.lift()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Toplevel(self._parent)
        self._win = win
        win.title("Claude Usage Monitor – Settings")
        win.resizable(False, False)
        win.configure(bg=BG)
        win.grab_set()   # modal

        pad = 12

        # ── Anthropic API Key ─────────────────────────────────────────
        self._add_section(
            win,
            "Anthropic API Key  (recommended)",
            tip="Provides the same usage numbers shown in Claude Desktop.\n"
                "Get your key at console.anthropic.com → API Keys.",
        )

        key_frame = tk.Frame(win, bg=BG)
        key_frame.pack(fill="x", padx=pad, pady=(0, 2))

        self._var_key = tk.StringVar(value=self._cfg.get("anthropic_api_key", ""))
        self._entry_key = tk.Entry(
            key_frame,
            textvariable=self._var_key,
            width=42,
            bg=ENTRY_BG,
            show="•",   # mask key
        )
        self._entry_key.pack(side="left", fill="x", expand=True)

        tk.Button(
            key_frame, text="Show",
            relief="flat", bg="#dddddd",
            command=self._toggle_key_visibility,
        ).pack(side="left", padx=(4, 0))

        tk.Button(
            key_frame, text="Test",
            relief="flat", bg="#dddddd",
            command=self._test_api_key,
        ).pack(side="left", padx=(4, 0))

        self._lbl_key_status = tk.Label(
            win, text="", font=("Segoe UI", 9), bg=BG, fg=HINT, anchor="w",
        )
        self._lbl_key_status.pack(fill="x", padx=pad, pady=(0, 4))

        # ── Data source ───────────────────────────────────────────────
        self._add_section(win, "Data Source")

        src_frame = tk.Frame(win, bg=BG)
        src_frame.pack(fill="x", padx=pad, pady=(0, 8))
        self._var_source = tk.StringVar(value=self._cfg.get("data_source", "auto"))
        for label, val in [
            ("Auto (Desktop session → API key → JSONL files)", "auto"),
            ("Claude Desktop session  ← recommended for subscribers", "desktop"),
            ("Anthropic API key only  ← for API users", "api"),
            ("Local JSONL files only  ← for Claude Code CLI users", "jsonl"),
        ]:
            tk.Radiobutton(
                src_frame, text=label, variable=self._var_source, value=val,
                bg=BG, fg=TEXT, activebackground=BG,
            ).pack(anchor="w")

        # ── Claude data directory ─────────────────────────────────────
        self._add_section(
            win,
            "Claude Code Data Directory",
            tip="Used for JSONL-based reading. Typically ~/.claude on all platforms.",
        )

        dir_frame = tk.Frame(win, bg=BG)
        dir_frame.pack(fill="x", padx=pad, pady=(0, 4))

        self._var_dir = tk.StringVar(value=self._cfg.get("claude_data_dir", ""))
        tk.Entry(dir_frame, textvariable=self._var_dir, width=38, bg=ENTRY_BG).pack(
            side="left", fill="x", expand=True)
        tk.Button(
            dir_frame, text="Browse…",
            command=self._browse_dir,
            relief="flat", bg="#dddddd",
        ).pack(side="left", padx=(4, 0))

        # ── Claude Desktop – webview login ────────────────────────────
        if __import__("sys").platform == "win32":
            self._add_section(
                win,
                "Claude Desktop — Connect",
                tip=(
                    "Opens a browser window to sign in to claude.ai.\n"
                    "The session key is stored securely in Windows Credential Manager\n"
                    "and used to fetch your usage from Claude's API."
                ),
            )
            conn_row = tk.Frame(win, bg=BG)
            conn_row.pack(fill="x", padx=pad, pady=(0, 8))

            self._lbl_conn_status = tk.Label(
                conn_row, text=self._conn_status_text(),
                bg=BG, fg=HINT, font=("", 9),
            )
            self._lbl_conn_status.pack(side="left", padx=(0, 8))

            tk.Button(
                conn_row, text="Sign in to Claude…",
                relief="flat", bg="#4a90d9", fg="white",
                command=self._do_webview_login,
            ).pack(side="left", padx=(0, 4))

            tk.Button(
                conn_row, text="Sign out",
                relief="flat", bg="#dddddd", fg=TEXT,
                command=self._do_webview_logout,
            ).pack(side="left")

        # Diagnostics buttons
        diag_row = tk.Frame(win, bg=BG)
        diag_row.pack(fill="x", padx=pad, pady=(0, 8))
        tk.Button(
            diag_row, text="Diagnose JSONL directory",
            relief="flat", bg="#dddddd", fg=TEXT,
            command=self._run_diagnostics,
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            diag_row, text="Diagnose Desktop session",
            relief="flat", bg="#dddddd", fg=TEXT,
            command=self._run_desktop_diagnostics,
        ).pack(side="left")

        # ── Token limits ──────────────────────────────────────────────
        self._add_section(
            win,
            "Token Limits (for JSONL mode only)",
            tip="When using the API key the limits are read from headers automatically.",
        )

        limits_frame = tk.Frame(win, bg=BG)
        limits_frame.pack(fill="x", padx=pad, pady=(0, 4))

        tk.Label(limits_frame, text="5-Hour window:", bg=BG, fg=TEXT).grid(
            row=0, column=0, sticky="w", pady=3)
        self._var_5h = tk.StringVar(value=str(self._cfg.get("limit_5h", 500_000)))
        tk.Entry(limits_frame, textvariable=self._var_5h, width=12, bg=ENTRY_BG).grid(
            row=0, column=1, padx=(8, 0), sticky="w")
        tk.Label(limits_frame, text="tokens", bg=BG, fg=HINT).grid(
            row=0, column=2, padx=(4, 0), sticky="w")

        tk.Label(limits_frame, text="7-Day window:", bg=BG, fg=TEXT).grid(
            row=1, column=0, sticky="w", pady=3)
        self._var_7d = tk.StringVar(value=str(self._cfg.get("limit_7d", 5_000_000)))
        tk.Entry(limits_frame, textvariable=self._var_7d, width=12, bg=ENTRY_BG).grid(
            row=1, column=1, padx=(8, 0), sticky="w")
        tk.Label(limits_frame, text="tokens", bg=BG, fg=HINT).grid(
            row=1, column=2, padx=(4, 0), sticky="w")

        # Preset buttons
        preset_frame = tk.Frame(win, bg=BG)
        preset_frame.pack(fill="x", padx=pad, pady=(0, 4))
        tk.Label(preset_frame, text="Presets:", bg=BG, fg=TEXT).pack(side="left")
        for label, l5, l7 in [
            ("Pro",      250_000,   2_000_000),
            ("Max 5×",   500_000,   5_000_000),
            ("Max 20×",  2_000_000, 20_000_000),
        ]:
            tk.Button(
                preset_frame, text=label, relief="flat", bg="#dddddd",
                command=lambda a=l5, b=l7: self._apply_preset(a, b),
            ).pack(side="left", padx=4)

        # ── Refresh interval ──────────────────────────────────────────
        self._add_section(win, "Refresh Interval")

        ref_frame = tk.Frame(win, bg=BG)
        ref_frame.pack(fill="x", padx=pad, pady=(0, 8))
        self._var_interval = tk.StringVar(
            value=str(self._cfg.get("refresh_interval", 300)))
        tk.Entry(ref_frame, textvariable=self._var_interval, width=8, bg=ENTRY_BG).pack(
            side="left")
        tk.Label(ref_frame, text="seconds", bg=BG, fg=TEXT).pack(side="left", padx=4)

        # ── Save / Cancel ─────────────────────────────────────────────
        btn_frame = tk.Frame(win, bg=BG)
        btn_frame.pack(fill="x", padx=pad, pady=(4, pad))
        tk.Button(
            btn_frame, text="Save",
            bg="#4a86c8", fg="white", relief="flat",
            command=self._save,
        ).pack(side="right", padx=4)
        tk.Button(
            btn_frame, text="Cancel",
            bg="#dddddd", fg=TEXT, relief="flat",
            command=win.destroy,
        ).pack(side="right")

    # ------------------------------------------------------------------

    def _add_section(self, parent: tk.Widget, text: str, tip: str = "") -> None:
        tk.Label(
            parent, text=text, font=("Segoe UI", 10, "bold"),
            bg=BG, fg=TEXT, anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 0))
        if tip:
            tk.Label(
                parent, text=tip,
                font=("Segoe UI", 8), bg=BG, fg=HINT,
                anchor="w", justify="left",
            ).pack(fill="x", padx=12, pady=(0, 2))
        tk.Frame(parent, bg="#cccccc", height=1).pack(fill="x", padx=12, pady=(2, 4))

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(
            parent=self._win,
            title="Select Claude Code data directory",
            initialdir=self._var_dir.get(),
        )
        if d:
            self._var_dir.set(d)

    def _apply_preset(self, l5: int, l7: int) -> None:
        self._var_5h.set(str(l5))
        self._var_7d.set(str(l7))

    def _toggle_key_visibility(self) -> None:
        current = self._entry_key.cget("show")
        self._entry_key.configure(show="" if current == "•" else "•")

    def _test_api_key(self) -> None:
        from ..api_client import test_key
        key = self._var_key.get().strip()
        if not key:
            self._lbl_key_status.configure(
                text="Enter an API key first.", fg="#cc8800")
            return
        self._lbl_key_status.configure(text="Testing…", fg=HINT)
        self._win.update()
        ok, msg = test_key(key)
        self._lbl_key_status.configure(
            text=f"{'✓' if ok else '✗'}  {msg}",
            fg="#2e7d32" if ok else "#c62828",
        )

    def _run_diagnostics(self) -> None:
        from ..usage_reader import diagnose
        result = diagnose(self._var_dir.get())
        messagebox.showinfo(
            "JSONL Diagnostics",
            result["message"],
            parent=self._win,
        )

    def _conn_status_text(self) -> str:
        try:
            from ..desktop_session import load_session_key
            key = load_session_key()
            return "● Connected" if key else "○ Not connected"
        except Exception:
            return ""

    def _do_webview_login(self) -> None:
        """Open webview login and fetch usage once logged in."""
        import threading
        from ..desktop_session import (
            fetch_usage_via_webview, save_session_key,
            get_oauth_token, _org_id_from_oauth,
        )

        oauth = get_oauth_token()
        org_id = _org_id_from_oauth(oauth) if oauth else None

        if not org_id:
            messagebox.showerror(
                "No org ID",
                "Could not determine your organization ID from Claude Desktop.\n"
                "Make sure Claude Desktop is installed and you have signed in.",
                parent=self._win,
            )
            return

        self._lbl_conn_status.config(text="⏳ Opening browser…")
        self._win.update_idletasks()

        def _run():
            result = fetch_usage_via_webview(org_id)
            # If the webview made the call successfully, we can also extract
            # sessionKey via JS — but fetch_usage_via_webview handles auth
            # internally.  Just update the status label.
            if result and not result.get("error"):
                self._lbl_conn_status.config(text="● Connected")
            else:
                err = (result or {}).get("error", "Unknown error")
                self._lbl_conn_status.config(text="✗ Failed")
                messagebox.showerror("Login failed", str(err), parent=self._win)

        threading.Thread(target=_run, daemon=True).start()

    def _do_webview_logout(self) -> None:
        from ..desktop_session import delete_session_key
        delete_session_key()
        self._lbl_conn_status.config(text="○ Not connected")

    def _run_desktop_diagnostics(self) -> None:
        import os, subprocess, tempfile
        from ..desktop_session import diagnose as desktop_diagnose
        msg = desktop_diagnose()
        # Write to a temp file and open it in Notepad
        tmp = os.path.join(tempfile.gettempdir(), "claude_desktop_diag.txt")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(msg)
        try:
            subprocess.Popen(["notepad.exe", tmp])
        except Exception:
            pass
        messagebox.showinfo(
            "Claude Desktop Session Diagnostics",
            f"Diagnostic saved to:\n{tmp}\n\nNotepad should open automatically.",
            parent=self._win,
        )

    def _save(self) -> None:
        try:
            limit_5h = int(self._var_5h.get().replace(",", "").replace("_", ""))
            limit_7d = int(self._var_7d.get().replace(",", "").replace("_", ""))
            interval = int(self._var_interval.get())
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Please enter valid integers for token limits and refresh interval.",
                parent=self._win,
            )
            return

        new_cfg = {
            **self._cfg,
            "anthropic_api_key": self._var_key.get().strip(),
            "data_source": self._var_source.get(),
            "claude_data_dir": self._var_dir.get().strip(),
            "limit_5h": limit_5h,
            "limit_7d": limit_7d,
            "refresh_interval": interval,
        }
        self._on_save(new_cfg)
        if self._win:
            self._win.destroy()
