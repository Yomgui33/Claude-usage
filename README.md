# Claude Usage Monitor for Windows

A Windows system tray application that monitors your [Claude Code](https://claude.ai/code) token usage in real time — inspired by the macOS Claude Usage widget.

![Claude Usage Monitor popup](docs/screenshot.png)

## Features

- **System tray icon** showing current 5-hour usage percentage (colour-coded green / orange / red)
- **Popup window** with:
  - 5-Hour and 7-Day rolling-window usage progress bars
  - Estimated time until the window resets
  - Historical usage chart (1h / 6h / 1d / 7d / 30d views)
- **Auto-refresh** at a configurable interval (default: 5 minutes)
- **Launch at login** toggle (Windows registry)
- **Settings dialog** to configure token limits per Claude plan tier

## How it works

Claude Code stores all conversation history locally in `~/.claude/projects/` as JSONL files. This app parses those files to calculate your token usage for rolling 5-hour and 7-day windows — **no API key required**.

## Requirements

- Python 3.9 or later
- Windows 10 / 11

## Installation (from source)

```cmd
git clone https://github.com/your-user/claude-usage-monitor.git
cd claude-usage-monitor
pip install -r requirements.txt
python main.py
```

## Build a standalone .exe

```cmd
# Generate the app icon first
python assets/generate_icon.py

# Build the executable
build.bat
```

The resulting `dist\ClaudeUsageMonitor.exe` is a single self-contained file.
Copy it anywhere and double-click to run.

## Configuration

Open **Settings** from the tray right-click menu to configure:

| Setting | Description |
|---|---|
| Claude data directory | Path to `~/.claude` (auto-detected on first run) |
| 5-Hour limit | Token limit for your Claude plan (5h window) |
| 7-Day limit | Token limit for your Claude plan (7d window) |
| Refresh interval | How often to re-scan JSONL files (seconds) |

### Token limits by plan

| Plan | 5-Hour | 7-Day |
|---|---|---|
| Claude Pro | ~250 000 | ~2 000 000 |
| Claude Max 5× | ~500 000 | ~5 000 000 |
| Claude Max 20× | ~2 000 000 | ~20 000 000 |

*Exact limits may vary. Check the [Anthropic rate limits page](https://docs.anthropic.com/en/api/rate-limits) for your tier.*

## Auto-start at login

Toggle **Launch at Login** in the popup window. This adds (or removes) the app from
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`.

## Project structure

```
claude-usage-monitor/
├── main.py                     Entry point
├── requirements.txt
├── build.bat                   PyInstaller build script
├── claude_usage_monitor.spec   PyInstaller spec
├── assets/
│   ├── generate_icon.py        One-time icon generator
│   └── icon.ico                (generated)
└── src/
    ├── app.py                  Application coordinator
    ├── config.py               Config (AppData JSON)
    ├── usage_reader.py         JSONL parser & usage calculator
    ├── data_store.py           SQLite historical data
    ├── startup.py              Windows registry startup
    ├── tray.py                 System tray (pystray)
    └── ui/
        ├── main_window.py      Main popup window
        └── settings_window.py  Settings dialog
```

## Dependencies

| Package | Purpose |
|---|---|
| `pystray` | Windows system tray icon |
| `Pillow` | Dynamic tray icon generation |
| `matplotlib` | Usage history chart |
| `requests` | (future: Anthropic API integration) |

## License

MIT
