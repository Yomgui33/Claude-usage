"""
Configuration management – persists settings to %APPDATA%\ClaudeUsageMonitor\config.json
"""
import json
import os
from pathlib import Path

APP_NAME = "ClaudeUsageMonitor"


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return _config_dir() / "config.json"


def db_path() -> Path:
    return _config_dir() / "data.db"


DEFAULTS = {
    # Path where Claude Code stores its data
    "claude_data_dir": str(Path.home() / ".claude"),
    # Token limits per window (adjustable by tier)
    "limit_5h": 500_000,       # Max tier 5-hour limit (tokens)
    "limit_7d": 5_000_000,     # Max tier 7-day limit (tokens)
    # Refresh interval in seconds
    "refresh_interval": 300,
    # Launch at Windows startup
    "launch_at_startup": False,
}


def load() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults so new keys are always present
        merged = {**DEFAULTS, **data}
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save(cfg: dict) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
