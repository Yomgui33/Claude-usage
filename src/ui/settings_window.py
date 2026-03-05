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


class SettingsWindow:
    def __init__(self, parent: tk.Tk, cfg: dict, on_save: Callable[[dict], None]):
        self._parent   = parent
        self._cfg      = dict(cfg)
        self._on_save  = on_save
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

        # ── Claude data directory ─────────────────────────────────────
        self._add_section(win, "Claude Code Data Directory")

        dir_frame = tk.Frame(win, bg=BG)
        dir_frame.pack(fill="x", padx=pad, pady=(0, 8))

        self._var_dir = tk.StringVar(value=self._cfg.get("claude_data_dir", ""))
        e = tk.Entry(dir_frame, textvariable=self._var_dir, width=40, bg=ENTRY_BG)
        e.pack(side="left", fill="x", expand=True)

        tk.Button(
            dir_frame, text="Browse…",
            command=self._browse_dir,
            relief="flat", bg="#dddddd",
        ).pack(side="left", padx=(4, 0))

        # ── Token limits ──────────────────────────────────────────────
        self._add_section(win, "Token Limits (adjust to match your Claude plan)")

        limits_frame = tk.Frame(win, bg=BG)
        limits_frame.pack(fill="x", padx=pad, pady=(0, 8))

        tk.Label(limits_frame, text="5-Hour window:", bg=BG, fg=TEXT).grid(
            row=0, column=0, sticky="w", pady=3)
        self._var_5h = tk.StringVar(value=str(self._cfg.get("limit_5h", 500_000)))
        tk.Entry(limits_frame, textvariable=self._var_5h, width=12, bg=ENTRY_BG).grid(
            row=0, column=1, padx=(8, 0), sticky="w")
        tk.Label(limits_frame, text="tokens", bg=BG, fg="#888888").grid(
            row=0, column=2, padx=(4, 0), sticky="w")

        tk.Label(limits_frame, text="7-Day window:", bg=BG, fg=TEXT).grid(
            row=1, column=0, sticky="w", pady=3)
        self._var_7d = tk.StringVar(value=str(self._cfg.get("limit_7d", 5_000_000)))
        tk.Entry(limits_frame, textvariable=self._var_7d, width=12, bg=ENTRY_BG).grid(
            row=1, column=1, padx=(8, 0), sticky="w")
        tk.Label(limits_frame, text="tokens", bg=BG, fg="#888888").grid(
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
                preset_frame,
                text=label,
                relief="flat",
                bg="#dddddd",
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
        btn_frame.pack(fill="x", padx=pad, pady=(8, pad))

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

    def _add_section(self, parent: tk.Widget, text: str) -> None:
        lbl = tk.Label(parent, text=text, font=("Segoe UI", 10, "bold"),
                       bg=BG, fg=TEXT, anchor="w")
        lbl.pack(fill="x", padx=12, pady=(10, 2))
        sep = tk.Frame(parent, bg="#cccccc", height=1)
        sep.pack(fill="x", padx=12, pady=(0, 4))

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
            "claude_data_dir": self._var_dir.get().strip(),
            "limit_5h": limit_5h,
            "limit_7d": limit_7d,
            "refresh_interval": interval,
        }
        self._on_save(new_cfg)
        if self._win:
            self._win.destroy()
