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


def _candidate_claude_dirs() -> list[str]:
    """
    Return all plausible Claude data directories on this machine,
    in order of preference.
    """
    dirs = []
    home = Path.home()

    # Claude Code CLI (primary)
    dirs.append(str(home / ".claude"))

    # Claude Desktop – Windows
    for env_var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if base:
            dirs.append(str(Path(base) / "Claude"))

    # Claude Desktop – macOS
    dirs.append(str(home / "Library" / "Application Support" / "Claude"))

    # Claude Desktop – Linux
    dirs.append(str(home / ".config" / "Claude"))

    return dirs


def auto_detect_claude_dir() -> str:
    """Return the first candidate directory that actually contains JSONL files."""
    from pathlib import Path as _Path
    for d in _candidate_claude_dirs():
        p = _Path(d)
        if p.exists():
            # Check for JSONL files anywhere underneath
            if any(p.rglob("*.jsonl")):
                return d
    # Fallback: return the default even if empty
    return str(Path.home() / ".claude")


DEFAULTS = {
    # Path where Claude Code / Claude Desktop stores its data.
    # Auto-detected at first launch; override in Settings if needed.
    "claude_data_dir": str(Path.home() / ".claude"),
    # Anthropic API key (optional but recommended for accurate usage data).
    # When set, the app fetches rate-limit headers directly from the API,
    # which matches exactly what Claude Desktop displays.
    "anthropic_api_key": "",
    # Token limits per window – only used for JSONL-based estimation.
    # When an API key is configured these limits are read from the headers.
    "limit_5h": 500_000,
    "limit_7d": 5_000_000,
    # Refresh interval in seconds
    "refresh_interval": 300,
    # Launch at Windows startup
    "launch_at_startup": False,
    # Data source: "api" | "jsonl" | "auto"
    "data_source": "auto",
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
