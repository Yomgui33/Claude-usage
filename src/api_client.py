"""
Anthropic API client for fetching real-time rate-limit usage.

When an API key is configured the app makes a lightweight GET /v1/models
request and reads the rate-limit response headers:

    anthropic-ratelimit-tokens-limit
    anthropic-ratelimit-tokens-remaining
    anthropic-ratelimit-tokens-reset

This gives the same numbers that Claude Desktop displays, without any
dependency on local JSONL files.

Header mapping
--------------
Anthropic exposes **two** pairs of rate-limit headers (when applicable):
  - The primary window (typically 5-hour for Pro / Max plans)
  - A secondary window prefixed with ``input-tokens`` (7-day)

If only one pair is present we use it for the 5h slot and derive the 7d
figure from local JSONL data (or leave it at 0).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

_API_BASE = "https://api.anthropic.com"
_VERSION   = "2023-06-01"
_TIMEOUT   = 10  # seconds


def _parse_reset(value: str) -> Optional[datetime]:
    """Parse the ``anthropic-ratelimit-*-reset`` header (ISO-8601 UTC)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def fetch_usage(api_key: str) -> dict:
    """
    Call GET /v1/models to obtain current rate-limit headers.

    Returns
    -------
    dict with keys (all optional / may be None):
        limit_5h        int   – token limit for the primary window
        used_5h         int   – tokens consumed in the primary window
        pct_5h          float – used_5h / limit_5h * 100
        reset_5h        timedelta | None

        limit_7d        int   – token limit for the secondary window
        used_7d         int   – tokens consumed in the secondary window
        pct_7d          float
        reset_7d        timedelta | None

        error           str | None – human-readable error if request failed
    """
    empty = {
        "limit_5h": None, "used_5h": None, "pct_5h": None, "reset_5h": None,
        "limit_7d": None, "used_7d": None, "pct_7d": None, "reset_7d": None,
        "error": None,
    }

    try:
        resp = requests.get(
            f"{_API_BASE}/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": _VERSION,
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        empty["error"] = f"Network error: {exc}"
        return empty

    if resp.status_code == 401:
        empty["error"] = "Invalid API key (401 Unauthorized)"
        return empty
    if not resp.ok:
        empty["error"] = f"API error {resp.status_code}"
        return empty

    headers = resp.headers

    # ── Primary window (requests or tokens, 5-hour-ish) ────────────
    limit_tok     = _int(headers.get("anthropic-ratelimit-tokens-limit"))
    remaining_tok = _int(headers.get("anthropic-ratelimit-tokens-remaining"))
    reset_tok     = _parse_reset(headers.get("anthropic-ratelimit-tokens-reset", ""))

    # ── Input-token window (7-day on Pro/Max) ──────────────────────
    limit_inp     = _int(headers.get("anthropic-ratelimit-input-tokens-limit"))
    remaining_inp = _int(headers.get("anthropic-ratelimit-input-tokens-remaining"))
    reset_inp     = _parse_reset(headers.get("anthropic-ratelimit-input-tokens-reset", ""))

    now = datetime.now(timezone.utc)

    def _pct(used, limit):
        if used is None or limit is None or limit == 0:
            return None
        return min(100.0, used / limit * 100)

    def _remaining_td(reset_dt):
        if reset_dt is None:
            return None
        diff = reset_dt - now
        return diff if diff.total_seconds() > 0 else timedelta(0)

    # Primary window → 5h slot
    used_5h = (limit_tok - remaining_tok) if (limit_tok and remaining_tok is not None) else None

    # Secondary window → 7d slot
    used_7d = (limit_inp - remaining_inp) if (limit_inp and remaining_inp is not None) else None

    return {
        "limit_5h": limit_tok,
        "used_5h":  used_5h,
        "pct_5h":   _pct(used_5h, limit_tok),
        "reset_5h": _remaining_td(reset_tok),

        "limit_7d": limit_inp,
        "used_7d":  used_7d,
        "pct_7d":   _pct(used_7d, limit_inp),
        "reset_7d": _remaining_td(reset_inp),

        "error": None,
    }


def test_key(api_key: str) -> tuple[bool, str]:
    """
    Quick validation of an API key.

    Returns (ok: bool, message: str).
    """
    try:
        resp = requests.get(
            f"{_API_BASE}/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": _VERSION,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return True, "Connection successful."
        if resp.status_code == 401:
            return False, "Invalid API key."
        return False, f"Unexpected status: {resp.status_code}"
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
