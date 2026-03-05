"""
Main popup window – mirrors the macOS Claude Usage widget.

Layout
------
┌─────────────────────────────────────┐
│  Claude Usage                       │
│  5-Hour Window              7%      │
│  ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│  Resets 4 hr, 31 min                │
│                                     │
│  7-Day Window               68%     │
│  ████████████████████░░░░░░░░░░░░░  │
│  Resets 1 day, 18 hr                │
│                                     │
│  [1h] [6h] [1d] [7d] [30d]         │
│  ┌────────────────────────────────┐ │
│  │         Chart                  │ │
│  └────────────────────────────────┘ │
│                                     │
│  Updated 4 min ago  [Refresh][Quit] │
│  Launch at Login  [ Toggle ]        │
└─────────────────────────────────────┘
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# ── Colour palette ────────────────────────────────────────────────────────────

BG          = "#f0f4e8"     # light greenish background (matches screenshot)
BAR_BG      = "#d4ddc8"     # empty bar track
BAR_GREEN   = "#4caf50"
BAR_YELLOW  = "#ffb300"
BAR_RED     = "#f44336"
TEXT_DARK   = "#2e3d1e"
TEXT_GREY   = "#5a6e3a"
BTN_ACTIVE  = "#4a86c8"     # blue selected button
BTN_NORMAL  = "#dde4cc"
CHART_BG    = BG
LINE_5H     = "#2196f3"     # blue
LINE_7D     = "#ff9800"     # orange


def _bar_color(pct: float) -> str:
    if pct < 50:
        return BAR_GREEN
    if pct < 80:
        return BAR_YELLOW
    return BAR_RED


def _fmt_timedelta(td: timedelta | None) -> str:
    if td is None:
        return "—"
    total = int(td.total_seconds())
    if total <= 0:
        return "now"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hr")
    if mins and not days:
        parts.append(f"{mins} min")
    return ", ".join(parts) if parts else "< 1 min"


def _fmt_updated(ts: datetime | None) -> str:
    if ts is None:
        return "Never updated"
    diff = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
    secs = int(diff.total_seconds())
    if secs < 60:
        return f"Updated {secs} sec ago"
    mins = secs // 60
    if mins < 60:
        return f"Updated {mins} min ago"
    hrs = mins // 60
    return f"Updated {hrs} hr ago"


# ── Custom progress bar ───────────────────────────────────────────────────────

class UsageBar(tk.Canvas):
    """A simple coloured progress bar drawn on a Canvas."""

    HEIGHT = 14
    RADIUS = 7   # rounded ends

    def __init__(self, parent: tk.Widget, **kwargs):
        kwargs.setdefault("height", self.HEIGHT)
        kwargs.setdefault("bg", BG)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, **kwargs)
        self._pct = 0.0
        self.bind("<Configure>", lambda _e: self._draw())

    def set_pct(self, pct: float) -> None:
        self._pct = max(0.0, min(100.0, pct))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.HEIGHT
        if w < 2:
            return
        r = self.RADIUS

        # Background track
        self._rounded_rect(0, 0, w, h, r, BAR_BG)

        # Filled portion
        fill_w = max(0, int(w * self._pct / 100))
        if fill_w > 1:
            self._rounded_rect(0, 0, fill_w, h, r, _bar_color(self._pct))

    def _rounded_rect(self, x1, y1, x2, y2, r, color) -> None:
        """Draw a rectangle with rounded ends."""
        r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
        self.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, fill=color, outline=color)
        self.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, fill=color, outline=color)
        self.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, fill=color, outline=color)
        self.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, fill=color, outline=color)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline=color)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, fill=color, outline=color)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow:
    WIDTH  = 420
    HEIGHT = 600

    PERIODS = [
        ("1h",  1),
        ("6h",  6),
        ("1d",  24),
        ("7d",  24 * 7),
        ("30d", 24 * 30),
    ]

    def __init__(
        self,
        root: tk.Tk,
        on_refresh: Callable,
        on_settings: Callable,
        on_quit: Callable,
        on_startup_toggle: Callable[[bool], None],
    ):
        self.root = root
        self._on_refresh = on_refresh
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._on_startup_toggle = on_startup_toggle

        self._selected_period_hours = 6   # default: 6h
        self._visible = False

        self._win: Optional[tk.Toplevel] = None
        self._build_window()

    # ------------------------------------------------------------------
    # Window life-cycle
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        win = tk.Toplevel(self.root)
        self._win = win
        win.title("Claude Usage")
        win.resizable(False, False)
        win.configure(bg=BG)

        # No taskbar entry
        win.wm_attributes("-toolwindow", True)  # Windows-only; removes taskbar btn
        win.overrideredirect(False)

        # Close button just hides
        win.protocol("WM_DELETE_WINDOW", self.hide)
        # Hide on focus loss
        win.bind("<FocusOut>", self._on_focus_out)

        self._build_content(win)
        self._position_window()
        win.withdraw()

    def _position_window(self) -> None:
        """Place the window near the bottom-right of the screen."""
        win = self._win
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = sw - self.WIDTH - 20
        y = sh - self.HEIGHT - 60   # above taskbar
        win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def show(self) -> None:
        self._position_window()
        self._win.deiconify()
        self._win.lift()
        self._win.focus_force()
        self._visible = True

    def hide(self) -> None:
        self._win.withdraw()
        self._visible = False

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def _on_focus_out(self, event: tk.Event) -> None:
        # Only hide if focus moved completely outside the window
        focused = self._win.focus_get()
        if focused is None:
            self.hide()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_content(self, win: tk.Toplevel) -> None:
        pad = 16

        # ── Title ────────────────────────────────────────────────────
        tk.Label(
            win, text="Claude Usage",
            font=("Segoe UI", 16, "bold"),
            bg=BG, fg=TEXT_DARK,
            anchor="w",
        ).pack(fill="x", padx=pad, pady=(pad, 4))

        sep = tk.Frame(win, bg="#c8d4a8", height=1)
        sep.pack(fill="x", padx=pad, pady=(0, 8))

        # ── 5-Hour section ───────────────────────────────────────────
        self._lbl_5h_title = self._section_header(win, "5-Hour Window", "0%")
        self._bar_5h = UsageBar(win)
        self._bar_5h.pack(fill="x", padx=pad, pady=(2, 0))
        self._lbl_5h_reset = self._reset_label(win)

        # ── 7-Day section ────────────────────────────────────────────
        tk.Frame(win, bg=BG, height=8).pack()
        self._lbl_7d_title = self._section_header(win, "7-Day Window", "0%")
        self._bar_7d = UsageBar(win)
        self._bar_7d.pack(fill="x", padx=pad, pady=(2, 0))
        self._lbl_7d_reset = self._reset_label(win)

        sep2 = tk.Frame(win, bg="#c8d4a8", height=1)
        sep2.pack(fill="x", padx=pad, pady=(12, 8))

        # ── Period selector ──────────────────────────────────────────
        self._period_btns: dict[int, tk.Button] = {}
        btn_frame = tk.Frame(win, bg=BG)
        btn_frame.pack(padx=pad, fill="x")

        for label, hours in self.PERIODS:
            b = tk.Button(
                btn_frame,
                text=label,
                width=4,
                relief="flat",
                font=("Segoe UI", 10),
                bg=BTN_ACTIVE if hours == self._selected_period_hours else BTN_NORMAL,
                fg="white" if hours == self._selected_period_hours else TEXT_DARK,
                activebackground=BTN_ACTIVE,
                activeforeground="white",
                cursor="hand2",
                command=lambda h=hours: self._select_period(h),
            )
            b.pack(side="left", padx=2)
            self._period_btns[hours] = b

        # ── Chart ────────────────────────────────────────────────────
        self._fig = Figure(figsize=(3.8, 2.2), dpi=95, facecolor=CHART_BG)
        self._ax = self._fig.add_subplot(111)
        self._fig.subplots_adjust(left=0.10, right=0.97, top=0.92, bottom=0.18)
        self._chart_canvas = FigureCanvasTkAgg(self._fig, master=win)
        self._chart_canvas.get_tk_widget().pack(
            fill="x", padx=pad, pady=(6, 0),
        )
        self._draw_empty_chart()

        sep3 = tk.Frame(win, bg="#c8d4a8", height=1)
        sep3.pack(fill="x", padx=pad, pady=(8, 0))

        # ── Bottom bar ───────────────────────────────────────────────
        bottom = tk.Frame(win, bg=BG)
        bottom.pack(fill="x", padx=pad, pady=(6, 4))

        self._lbl_updated = tk.Label(
            bottom, text="Never updated",
            font=("Segoe UI", 9), bg=BG, fg=TEXT_GREY,
        )
        self._lbl_updated.pack(side="left")

        for text, cmd in [
            ("Refresh", self._on_refresh),
            ("Settings", self._on_settings),
            ("Quit", self._on_quit),
        ]:
            tk.Button(
                bottom, text=text,
                font=("Segoe UI", 9),
                relief="flat", bg=BTN_NORMAL, fg=TEXT_DARK,
                activebackground=BTN_ACTIVE, activeforeground="white",
                cursor="hand2", command=cmd, padx=6,
            ).pack(side="right", padx=2)

        # ── Startup toggle ───────────────────────────────────────────
        startup_row = tk.Frame(win, bg=BG)
        startup_row.pack(fill="x", padx=pad, pady=(0, pad))

        tk.Label(
            startup_row, text="Launch at Login",
            font=("Segoe UI", 10), bg=BG, fg=TEXT_DARK,
        ).pack(side="left")

        self._startup_var = tk.BooleanVar(value=False)
        self._startup_chk = ttk.Checkbutton(
            startup_row,
            variable=self._startup_var,
            command=self._toggle_startup,
            style="Switch.TCheckbutton",
        )
        # Fall back to a regular checkbutton if Switch style not available
        self._startup_chk.pack(side="right")

    def _section_header(self, parent: tk.Widget, title: str, pct_text: str) -> tk.Label:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x", padx=16)
        tk.Label(
            frame, text=title,
            font=("Segoe UI", 11, "bold"), bg=BG, fg=TEXT_DARK,
        ).pack(side="left")
        lbl_pct = tk.Label(
            frame, text=pct_text,
            font=("Segoe UI", 11, "bold"), bg=BG, fg=TEXT_DARK,
        )
        lbl_pct.pack(side="right")
        return lbl_pct

    def _reset_label(self, parent: tk.Widget) -> tk.Label:
        lbl = tk.Label(
            parent, text="—",
            font=("Segoe UI", 9), bg=BG, fg=TEXT_GREY,
            anchor="w",
        )
        lbl.pack(fill="x", padx=16, pady=(2, 0))
        return lbl

    # ------------------------------------------------------------------
    # Chart
    # ------------------------------------------------------------------

    def _draw_empty_chart(self) -> None:
        ax = self._ax
        ax.set_facecolor(CHART_BG)
        ax.tick_params(colors=TEXT_GREY, labelsize=8)
        ax.set_ylim(0, 100)
        ax.set_ylabel("%", fontsize=8, color=TEXT_GREY)
        for spine in ax.spines.values():
            spine.set_edgecolor("#c8d4a8")
        ax.grid(axis="y", color="#c8d4a8", linewidth=0.5, linestyle="--")
        self._chart_canvas.draw()

    def update_chart(self, history: list[dict]) -> None:
        """
        history: list of {"ts": datetime, "pct_5h": float, "pct_7d": float}
        """
        ax = self._ax
        ax.cla()
        ax.set_facecolor(CHART_BG)
        ax.set_ylim(0, 100)
        ax.set_ylabel("%", fontsize=8, color=TEXT_GREY)
        ax.tick_params(colors=TEXT_GREY, labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#c8d4a8")
        ax.grid(axis="y", color="#c8d4a8", linewidth=0.5, linestyle="--")

        if history:
            xs = [r["ts"] for r in history]
            y5 = [r["pct_5h"] for r in history]
            y7 = [r["pct_7d"] for r in history]
            ax.plot(xs, y5, color=LINE_5H, linewidth=1.8, label="5h")
            ax.plot(xs, y7, color=LINE_7D, linewidth=1.8, label="7d")
            ax.legend(
                loc="upper left",
                fontsize=8,
                framealpha=0.7,
                facecolor=BG,
                edgecolor="#c8d4a8",
            )

        self._fig.autofmt_xdate(rotation=30, ha="right")
        self._chart_canvas.draw()

    # ------------------------------------------------------------------
    # Period selection
    # ------------------------------------------------------------------

    def _select_period(self, hours: int) -> None:
        self._selected_period_hours = hours
        for h, btn in self._period_btns.items():
            active = (h == hours)
            btn.configure(
                bg=BTN_ACTIVE if active else BTN_NORMAL,
                fg="white" if active else TEXT_DARK,
            )
        # Notify the app to reload chart data
        # (app will call update_chart with the correct slice)
        self.root.event_generate("<<PeriodChanged>>")

    @property
    def selected_period_hours(self) -> int:
        return self._selected_period_hours

    # ------------------------------------------------------------------
    # Public update API
    # ------------------------------------------------------------------

    def update_usage(
        self,
        pct_5h: float,
        pct_7d: float,
        reset_5h: timedelta | None,
        reset_7d: timedelta | None,
        updated_at: datetime | None,
    ) -> None:
        self._bar_5h.set_pct(pct_5h)
        self._lbl_5h_title.configure(text=f"{pct_5h:.0f}%")
        reset_str_5h = f"Resets {_fmt_timedelta(reset_5h)}" if reset_5h else "No data"
        self._lbl_5h_reset.configure(text=reset_str_5h)

        self._bar_7d.set_pct(pct_7d)
        self._lbl_7d_title.configure(text=f"{pct_7d:.0f}%")
        reset_str_7d = f"Resets {_fmt_timedelta(reset_7d)}" if reset_7d else "No data"
        self._lbl_7d_reset.configure(text=reset_str_7d)

        self._lbl_updated.configure(text=_fmt_updated(updated_at))

    def set_startup_state(self, enabled: bool) -> None:
        self._startup_var.set(enabled)

    def _toggle_startup(self) -> None:
        self._on_startup_toggle(self._startup_var.get())
