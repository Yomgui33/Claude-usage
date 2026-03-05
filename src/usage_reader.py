"""
Reads Claude Code local JSONL conversation files and extracts token usage.

Claude Code stores conversation history in:
  ~/.claude/projects/<hash>/  (each file is a JSONL session)

Each JSONL line is a JSON object. We're interested in assistant turns:
  {
    "type": "assistant",
    "timestamp": "2024-01-01T10:00:00.000Z",
    "message": {
      "usage": {
        "input_tokens": 1234,
        "output_tokens": 567,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 890
      }
    }
  }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator


def scan_dirs(claude_dir: str) -> dict:
    """
    Return a diagnostic summary of what was found in the data directory.

    Useful for the Settings window to help users understand why usage
    might show as 0%.
    """
    root = Path(claude_dir)
    result = {
        "dir_exists": root.exists(),
        "jsonl_count": 0,
        "total_size_kb": 0,
        "paths_tried": [str(root)],
    }
    if root.exists():
        files = list(root.rglob("*.jsonl"))
        result["jsonl_count"] = len(files)
        result["total_size_kb"] = sum(f.stat().st_size for f in files) // 1024
    return result


def _iter_jsonl_files(claude_dir: str) -> Iterator[Path]:
    """
    Yield every .jsonl file found under the claude data directory.

    Searches both Claude Code CLI layout (~/.claude/projects/) and
    Claude Desktop layout (AppData/Claude/conversations/ etc.).
    """
    root = Path(claude_dir)
    if not root.exists():
        return

    # Claude Code CLI: ~/.claude/projects/<hash>/*.jsonl
    projects_dir = root / "projects"
    if projects_dir.exists():
        yield from projects_dir.rglob("*.jsonl")

    # Claude Desktop may store JSONL under conversations/ or logs/
    for subdir in ("conversations", "logs", "history"):
        d = root / subdir
        if d.exists():
            yield from d.rglob("*.jsonl")

    # Flat JSONL files directly under root (older versions)
    yield from root.glob("*.jsonl")


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse ISO-8601 timestamp to UTC-aware datetime."""
    if not ts:
        return None
    try:
        # Python 3.11+ supports Z suffix natively; handle older versions
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, AttributeError):
        return None


def _tokens_from_entry(entry: dict) -> int:
    """Extract total tokens from a single JSONL entry (assistant turn)."""
    if entry.get("type") != "assistant":
        return 0
    msg = entry.get("message", {})
    usage = msg.get("usage", {})
    if not usage:
        return 0
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )


def iter_usage_events(claude_dir: str, since: datetime | None = None) -> Iterator[tuple[datetime, int]]:
    """
    Yield (timestamp, tokens) pairs for every assistant turn found.

    Parameters
    ----------
    claude_dir : str
        Path to the Claude Code data directory (typically ~/.claude).
    since : datetime | None
        If given, only yield events on or after this UTC timestamp.
    """
    for jsonl_path in _iter_jsonl_files(claude_dir):
        try:
            with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts = _parse_timestamp(entry.get("timestamp", ""))
                    if ts is None:
                        continue

                    if since is not None and ts < since:
                        continue

                    tokens = _tokens_from_entry(entry)
                    if tokens > 0:
                        yield ts, tokens
        except OSError:
            continue


def compute_usage(claude_dir: str) -> dict:
    """
    Compute token usage for the 5-hour and 7-day rolling windows.

    Returns
    -------
    dict with keys:
        tokens_5h       – total tokens in the last 5 hours
        tokens_7d       – total tokens in the last 7 days
        oldest_event_5h – earliest event timestamp in the 5h window
        oldest_event_7d – earliest event timestamp in the 7d window
        total_events    – number of assistant turns parsed
    """
    now = datetime.now(timezone.utc)
    cutoff_5h = now - timedelta(hours=5)
    cutoff_7d = now - timedelta(days=7)

    tokens_5h = 0
    tokens_7d = 0
    oldest_5h: datetime | None = None
    oldest_7d: datetime | None = None
    total_events = 0

    for ts, tokens in iter_usage_events(claude_dir, since=cutoff_7d):
        total_events += 1

        if ts >= cutoff_7d:
            tokens_7d += tokens
            if oldest_7d is None or ts < oldest_7d:
                oldest_7d = ts

        if ts >= cutoff_5h:
            tokens_5h += tokens
            if oldest_5h is None or ts < oldest_5h:
                oldest_5h = ts

    return {
        "tokens_5h": tokens_5h,
        "tokens_7d": tokens_7d,
        "oldest_event_5h": oldest_5h,
        "oldest_event_7d": oldest_7d,
        "total_events": total_events,
        "computed_at": now,
    }


def compute_reset_time(oldest_event: datetime | None, window: timedelta) -> timedelta | None:
    """
    For a rolling window, the window 'resets' (oldest token ages out) at:
        oldest_event + window

    Returns the remaining time until that moment, or None if no events.
    """
    if oldest_event is None:
        return None
    now = datetime.now(timezone.utc)
    expires_at = oldest_event + window
    remaining = expires_at - now
    return remaining if remaining.total_seconds() > 0 else timedelta(0)


def diagnose(claude_dir: str) -> dict:
    """
    Return human-readable diagnostic information about the data source.

    Used by the Settings window to explain why usage might show as 0%.
    """
    root = Path(claude_dir)
    lines = []

    if not root.exists():
        lines.append(f"Directory not found: {claude_dir}")
        lines.append("→ Possible causes:")
        lines.append("  • Claude Code CLI is not installed")
        lines.append("  • You only use Claude Desktop (different storage format)")
        lines.append("  • The path is incorrect – update it in Settings")
        return {"ok": False, "message": "\n".join(lines), "jsonl_count": 0}

    all_jsonl = list(root.rglob("*.jsonl"))
    if not all_jsonl:
        lines.append(f"Directory exists but contains no .jsonl files: {claude_dir}")
        lines.append("→ Possible causes:")
        lines.append("  • You use Claude Desktop, not Claude Code CLI")
        lines.append("    → Add your Anthropic API key in Settings for accurate data")
        lines.append("  • You haven't started any Claude Code sessions yet")
        return {"ok": False, "message": "\n".join(lines), "jsonl_count": 0}

    size_kb = sum(f.stat().st_size for f in all_jsonl) // 1024
    lines.append(f"Found {len(all_jsonl)} JSONL file(s) ({size_kb} KB) in {claude_dir}")

    # Quick sanity-check: try to find at least one usage entry
    sample_tokens = 0
    for path in all_jsonl[:5]:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line.strip())
                        sample_tokens += _tokens_from_entry(entry)
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    if sample_tokens == 0:
        lines.append("⚠ Files found but no token usage detected in the first 5 files.")
        lines.append("  The files may use an unsupported format.")
        lines.append("  → Add your Anthropic API key in Settings for accurate data.")
        return {"ok": False, "message": "\n".join(lines), "jsonl_count": len(all_jsonl)}

    lines.append(f"✓ Token usage data found. Data source is working correctly.")
    return {"ok": True, "message": "\n".join(lines), "jsonl_count": len(all_jsonl)}


def build_history(claude_dir: str, bucket_minutes: int = 60) -> list[dict]:
    """
    Build a chronological list of {timestamp, tokens_5h_pct, tokens_7d_pct}
    snapshots derived entirely from local JSONL data.

    Used to populate the historical chart on first launch.

    Parameters
    ----------
    bucket_minutes : int
        Granularity of each data point (default: 1-hour buckets).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    bucket_secs = bucket_minutes * 60

    # Collect all events in the last 30 days
    raw: list[tuple[datetime, int]] = sorted(
        iter_usage_events(claude_dir, since=cutoff),
        key=lambda x: x[0],
    )

    if not raw:
        return []

    # Generate bucket timestamps at `bucket_minutes` intervals
    first_ts = raw[0][0]
    bucket_start = first_ts.replace(second=0, microsecond=0)
    # Align to bucket boundary
    minute_offset = bucket_start.minute % bucket_minutes
    bucket_start -= timedelta(minutes=minute_offset)

    history: list[dict] = []
    cursor = bucket_start

    while cursor <= now:
        bucket_end = cursor + timedelta(minutes=bucket_minutes)
        win_5h_start = bucket_end - timedelta(hours=5)
        win_7d_start = bucket_end - timedelta(days=7)

        tok_5h = sum(t for ts, t in raw if win_5h_start <= ts < bucket_end)
        tok_7d = sum(t for ts, t in raw if win_7d_start <= ts < bucket_end)

        history.append({
            "timestamp": bucket_end,
            "tokens_5h": tok_5h,
            "tokens_7d": tok_7d,
        })
        cursor = bucket_end

    return history
