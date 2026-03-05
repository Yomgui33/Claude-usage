"""
Read usage data from Claude Desktop's local Chromium session.

How it works
------------
1. Find Claude Desktop's Chromium data directory (%APPDATA%\\Claude\\)
2. Read Local State to get the DPAPI-encrypted AES-256-GCM key
3. Read the Cookies SQLite file and decrypt the claude.ai session cookies
4. Call claude.ai's internal API endpoints with those cookies to get
   the same usage data that Claude Desktop displays

Windows-only (uses Windows DPAPI for cookie key decryption).

Requirements
------------
    pip install pywin32 pycryptodome

Limitations
-----------
- Requires Claude Desktop to be installed and previously signed in
- The internal API endpoint may change with Claude Desktop updates
- Claude Desktop must NOT have an exclusive lock on the Cookies file
  (usually fine; we copy it to a temp file before reading)
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

_TIMEOUT = 10


# ── Directory helpers ────────────────────────────────────────────────────────

def _claude_desktop_dirs() -> list[Path]:
    """Candidate Claude Desktop data directories on Windows."""
    dirs: list[Path] = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if base:
            dirs.append(Path(base) / "Claude")
    return dirs


def _find_cookies_file() -> Optional[Path]:
    """Return the first Cookies file found in any Claude Desktop directory."""
    for base in _claude_desktop_dirs():
        for sub in ("Network", ""):
            p = (base / sub / "Cookies") if sub else (base / "Cookies")
            if p.exists():
                return p
    return None


def _find_local_state() -> Optional[Path]:
    for base in _claude_desktop_dirs():
        p = base / "Local State"
        if p.exists():
            return p
    return None


# ── DPAPI + AES key decryption ───────────────────────────────────────────────

def _get_aes_key() -> Optional[bytes]:
    """
    Read the AES-256-GCM key that Chromium uses to encrypt cookies.

    The key is stored (DPAPI-encrypted) in Local State under
    os_crypt.encrypted_key (Base64, prefixed with the literal "DPAPI").
    """
    if sys.platform != "win32":
        return None

    local_state_path = _find_local_state()
    if local_state_path is None:
        return None

    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        b64_key = state.get("os_crypt", {}).get("encrypted_key", "")
        if not b64_key:
            return None
        encrypted = base64.b64decode(b64_key)[5:]   # strip 5-byte "DPAPI" prefix
        import win32crypt                             # pywin32
        _, key = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
        return key
    except Exception as exc:
        print(f"[desktop_session] Could not read AES key: {exc}")
        return None


def _decrypt_value(encrypted_value: bytes, key: bytes) -> str:
    """
    Decrypt a Chromium cookie value.

    Modern Chromium (v80+): prefix b'v10' or b'v11', then 12-byte GCM nonce,
    then ciphertext, then 16-byte GCM auth tag.

    Older cookies: raw DPAPI ciphertext (no v10 prefix).
    """
    if encrypted_value[:3] in (b"v10", b"v11"):
        try:
            from Crypto.Cipher import AES            # pycryptodome
            nonce      = encrypted_value[3:15]
            ciphertext = encrypted_value[15:-16]
            tag        = encrypted_value[-16:]
            cipher     = AES.new(key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except Exception:
            return ""
    else:
        # Legacy DPAPI per-cookie encryption
        try:
            import win32crypt
            _, dec = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)
            return dec.decode("utf-8")
        except Exception:
            return ""


# ── Cookie reading ───────────────────────────────────────────────────────────

def get_claude_cookies() -> dict[str, str]:
    """
    Return all cookies for claude.ai and anthropic.com from Claude Desktop.

    Returns an empty dict if:
    - Not on Windows
    - Claude Desktop is not installed
    - Decryption fails
    """
    key = _get_aes_key()
    if key is None:
        return {}

    cookie_path = _find_cookies_file()
    if cookie_path is None:
        return {}

    # Copy DB to a temp file so we don't conflict with Claude Desktop's lock
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)

    try:
        shutil.copy2(str(cookie_path), tmp_path)
        # Copy WAL and SHM files if they exist (needed for consistency)
        for ext in ("-wal", "-shm"):
            src = Path(str(cookie_path) + ext)
            if src.exists():
                shutil.copy2(str(src), tmp_path + ext)

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%.claude.ai%' "
            "   OR host_key LIKE '%.anthropic.com%'"
        ).fetchall()
        conn.close()

        result: dict[str, str] = {}
        for row in rows:
            try:
                val = _decrypt_value(bytes(row["encrypted_value"]), key)
                if val:
                    result[row["name"]] = val
            except Exception:
                pass

        return result

    except Exception as exc:
        print(f"[desktop_session] Cookie read error: {exc}")
        return {}
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp_path + suffix)
            except OSError:
                pass


# ── Usage fetching ───────────────────────────────────────────────────────────

# Known (or likely) endpoints Claude Desktop / claude.ai uses for usage data.
# We try each in order and stop at the first successful JSON response that
# contains recognisable usage fields.
_CANDIDATE_ENDPOINTS = [
    "https://claude.ai/api/usage_status",
    "https://claude.ai/api/usage",
    "https://claude.ai/api/account",
    "https://claude.ai/api/auth/session",
    "https://claude.ai/api/rate_limit_status",
    "https://claude.ai/api/organizations/current/usage",
]


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _parse_timedelta(value) -> Optional[timedelta]:
    """Try to parse a timestamp or seconds value into a future timedelta."""
    if value is None:
        return None
    now = datetime.now(timezone.utc)
    # ISO string
    if isinstance(value, str):
        try:
            reset_dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            diff = reset_dt - now
            return diff if diff.total_seconds() > 0 else timedelta(0)
        except ValueError:
            pass
    # Seconds remaining
    if isinstance(value, (int, float)) and value > 0:
        return timedelta(seconds=value)
    return None


def _extract_usage(data: object, depth: int = 0) -> Optional[dict]:
    """
    Recursively search a parsed JSON value for usage percentage fields.

    We look for keys/patterns that suggest a rate-limit window:
      - pct, percent, percentage, used_pct, fraction
      - 5h / 5hour / five_hour / window_5h ...
      - 7d / 7day / seven_day / window_7d ...
    """
    if depth > 5 or not isinstance(data, (dict, list)):
        return None

    if isinstance(data, list):
        for item in data:
            result = _extract_usage(item, depth + 1)
            if result:
                return result
        return None

    # Normalise keys to lowercase
    lower = {k.lower(): v for k, v in data.items()}

    # --- Direct percentage fields ---
    def _pct_from_keys(*keys):
        for k in keys:
            v = lower.get(k)
            if isinstance(v, (int, float)) and 0 <= v <= 100:
                return float(v)
            # Fraction 0-1
            if isinstance(v, float) and 0 <= v <= 1:
                return v * 100
        return None

    pct_5h = _pct_from_keys(
        "5h_pct", "five_hour_pct", "window_5h_pct",
        "pct_5h", "usage_5h_pct", "rate_5h",
    )
    pct_7d = _pct_from_keys(
        "7d_pct", "seven_day_pct", "window_7d_pct",
        "pct_7d", "usage_7d_pct", "rate_7d",
    )

    # --- used / limit pair ---
    def _used_limit_pct(*used_keys):
        used = limit = None
        for k in used_keys:
            if lower.get(k + "_used") is not None:
                used  = lower[k + "_used"]
                limit = lower.get(k + "_limit")
                break
            if lower.get("used_" + k) is not None:
                used  = lower["used_" + k]
                limit = lower.get("limit_" + k)
                break
        if used is not None and limit:
            try:
                return min(100.0, float(used) / float(limit) * 100)
            except (ZeroDivisionError, TypeError):
                pass
        return None

    if pct_5h is None:
        pct_5h = _used_limit_pct("5h", "five_hour", "window_5h", "tokens_5h")
    if pct_7d is None:
        pct_7d = _used_limit_pct("7d", "seven_day", "window_7d", "tokens_7d")

    # --- Nested objects named after the windows ---
    def _from_nested(*keys):
        for k in keys:
            sub = lower.get(k)
            if isinstance(sub, dict):
                sub_lower = {sk.lower(): sv for sk, sv in sub.items()}
                pct = sub_lower.get("percent") or sub_lower.get("pct") or sub_lower.get("percentage")
                if isinstance(pct, (int, float)):
                    return float(pct)
                used  = sub_lower.get("used") or sub_lower.get("consumed")
                limit = sub_lower.get("limit") or sub_lower.get("max")
                if used is not None and limit:
                    try:
                        return min(100.0, float(used) / float(limit) * 100)
                    except (ZeroDivisionError, TypeError):
                        pass
        return None

    if pct_5h is None:
        pct_5h = _from_nested("5h", "five_hour", "window_5h", "hour_5", "5_hour")
    if pct_7d is None:
        pct_7d = _from_nested("7d", "seven_day", "window_7d", "day_7", "7_day")

    # Extract reset times
    reset_5h = _parse_timedelta(
        lower.get("reset_5h") or lower.get("5h_reset") or lower.get("window_5h_reset")
    )
    reset_7d = _parse_timedelta(
        lower.get("reset_7d") or lower.get("7d_reset") or lower.get("window_7d_reset")
    )

    if pct_5h is not None or pct_7d is not None:
        return {
            "pct_5h":   pct_5h,
            "pct_7d":   pct_7d,
            "reset_5h": reset_5h,
            "reset_7d": reset_7d,
        }

    # Recurse into nested dicts
    for v in data.values():
        result = _extract_usage(v, depth + 1)
        if result:
            return result

    return None


def fetch_usage() -> dict:
    """
    Read Claude Desktop session cookies and call claude.ai's internal API
    to retrieve the usage percentages it displays.

    Returns
    -------
    dict with keys:
        pct_5h      float | None
        pct_7d      float | None
        reset_5h    timedelta | None
        reset_7d    timedelta | None
        error       str | None
        _raw        dict – raw JSON responses keyed by URL (for diagnostics)
    """
    empty = {
        "pct_5h": None, "pct_7d": None,
        "reset_5h": None, "reset_7d": None,
        "error": None, "_raw": {},
    }

    if sys.platform != "win32":
        empty["error"] = "Desktop session reading is only supported on Windows."
        return empty

    cookies = get_claude_cookies()
    if not cookies:
        empty["error"] = (
            "No Claude Desktop session found. "
            "Make sure you are signed in to Claude Desktop."
        )
        return empty

    headers = {
        "Cookie": _cookie_header(cookies),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://claude.ai/",
        "Origin": "https://claude.ai",
    }

    raw_responses: dict[str, object] = {}

    for url in _CANDIDATE_ENDPOINTS:
        try:
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
            if resp.status_code in (401, 403):
                empty["error"] = (
                    "Session expired or not authenticated. "
                    "Please open Claude Desktop and sign in again."
                )
                return empty

            if resp.ok:
                try:
                    data = resp.json()
                except ValueError:
                    raw_responses[url] = resp.text[:200]
                    continue

                raw_responses[url] = data
                usage = _extract_usage(data)
                if usage:
                    usage["error"] = None
                    usage["_raw"] = raw_responses
                    return usage

        except requests.RequestException as exc:
            raw_responses[url] = str(exc)

    empty["_raw"] = raw_responses
    empty["error"] = (
        "Could not parse usage data from Claude Desktop's API.\n"
        "The internal endpoint may have changed.\n"
        "See Settings → Diagnostics for raw responses."
    )
    return empty


def is_available() -> bool:
    """Quick check: is Claude Desktop installed and a session present?"""
    return bool(_find_cookies_file() and _find_local_state())


def diagnose() -> str:
    """Return a human-readable diagnostic string."""
    lines = []

    dirs = _claude_desktop_dirs()
    lines.append(f"Claude Desktop candidate dirs: {[str(d) for d in dirs]}")

    cookie_file = _find_cookies_file()
    lines.append(f"Cookies file: {cookie_file or 'NOT FOUND'}")

    local_state = _find_local_state()
    lines.append(f"Local State:  {local_state or 'NOT FOUND'}")

    if not cookie_file or not local_state:
        lines.append("\n→ Claude Desktop does not appear to be installed.")
        return "\n".join(lines)

    key = _get_aes_key()
    lines.append(f"AES key read: {'OK' if key else 'FAILED (pywin32 installed?)'}")

    cookies = get_claude_cookies()
    lines.append(f"Cookies found: {len(cookies)} ({', '.join(list(cookies)[:5])}{'…' if len(cookies) > 5 else ''})")

    if not cookies:
        lines.append("\n→ No cookies found. Are you signed in to Claude Desktop?")
        return "\n".join(lines)

    lines.append("\nTrying API endpoints…")
    result = fetch_usage()
    if result["error"]:
        lines.append(f"Error: {result['error']}")
    else:
        lines.append(f"pct_5h = {result['pct_5h']}")
        lines.append(f"pct_7d = {result['pct_7d']}")

    if result.get("_raw"):
        lines.append("\nRaw responses:")
        for url, data in result["_raw"].items():
            lines.append(f"  {url}: {str(data)[:120]}")

    return "\n".join(lines)
