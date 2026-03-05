"""
Read claude.ai usage data from a local Chromium-based browser session.

Works with:
- Claude Desktop (Electron)
- Google Chrome / Chrome Canary
- Microsoft Edge
- Brave Browser
- Opera / Opera GX
- Vivaldi
- Chromium

How it works
------------
1. Scan candidate Chromium profile directories
2. Read Local State → DPAPI-decrypt the AES-256-GCM key (Windows DPAPI)
3. Copy the Cookies SQLite file to a temp path and decrypt claude.ai cookies
4. Call claude.ai's internal API endpoints with those cookies

Windows-only (uses Windows DPAPI for cookie key decryption).

Requirements
------------
    pip install pywin32 pycryptodome

Limitations
-----------
- The internal API endpoint may change with browser/Claude updates
- The browser must NOT hold an exclusive lock on the Cookies file
  (we copy it to a temp file, so this is usually fine)
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

def _claude_base_dirs() -> list[Path]:
    """
    Return candidate directories where Claude Desktop might store its Electron
    userData.  Covers:
    - Standard Electron: %APPDATA%\Claude  (Roaming)
    - Non-standard:      %LOCALAPPDATA%\Claude
    - MSIX/Store apps:   %LOCALAPPDATA%\Packages\<Anthropic.Claude_*>\LocalCache\Roaming\Claude
    - Squirrel installs: %LOCALAPPDATA%\AnthropicClaude\app-*\resources
      (not userData, but we check anyway)
    """
    candidates: list[Path] = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if not base:
            continue
        bp = Path(base)
        for name in (
            "Claude", "claude",
            "Claude Desktop", "claude-desktop",
            "AnthropicClaude", r"Anthropic\Claude",
        ):
            candidates.append(bp / name)

    # MSIX / Windows Store isolation:
    # %LOCALAPPDATA%\Packages\Anthropic.Claude_<hash>\LocalCache\Roaming\Claude
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        packages = Path(local) / "Packages"
        if packages.exists():
            try:
                for pkg in packages.iterdir():
                    if pkg.is_dir() and any(
                        kw in pkg.name.lower() for kw in ("claude", "anthropic")
                    ):
                        # Common MSIX userData paths
                        for sub in (
                            r"LocalCache\Roaming\Claude",
                            r"LocalCache\Local\Claude",
                            r"LocalState\Claude",
                            r"RoamingState\Claude",
                            "LocalCache",
                            "LocalState",
                        ):
                            candidates.append(pkg / sub)
            except OSError:
                pass

    return candidates


def _find_local_state_files_broadly() -> list[Path]:
    """
    Scan %APPDATA% and %LOCALAPPDATA% up to 5 levels deep for any
    'Local State' file.  Used in diagnostics to locate non-standard
    Electron userData paths.
    """
    results: list[Path] = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if not base:
            continue
        try:
            for p in Path(base).rglob("Local State"):
                if p.is_file():
                    # Quick sanity check: valid JSON with os_crypt key
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if "os_crypt" in data or "browser" in data:
                            results.append(p)
                    except Exception:
                        pass
                if len(results) > 20:   # safety limit
                    return results
        except OSError:
            pass
    return results


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(6) == b"SQLite"
    except OSError:
        return False


def _find_all_cookies_in_dir(base: Path, _depth: int = 0) -> list[Path]:
    """
    Recursively find every SQLite file named 'Cookies' within *base*.

    Uses a manual traversal (not rglob) so that a single inaccessible
    entry (e.g. a broken symlink that raises WinError 1920) does not
    abort the entire search.
    """
    if _depth > 8:
        return []
    found: list[Path] = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.name == "Cookies" and entry.is_file() and _is_sqlite(entry):
                found.append(entry)
            elif entry.is_dir():
                found.extend(_find_all_cookies_in_dir(entry, _depth + 1))
        except OSError:
            continue
    return found


def _local_state_for(cookies_path: Path) -> Optional[Path]:
    """
    Walk up the directory tree from *cookies_path* looking for Local State.
    Chromium always places Local State two levels above the Default profile:
      …/User Data/Local State    (browsers)
      …/Claude/Local State       (Claude Desktop)
    but for partitioned sessions it may be further up.
    """
    p = cookies_path.parent
    for _ in range(6):           # don't go too far up
        candidate = p / "Local State"
        if candidate.exists():
            return candidate
        p = p.parent
    return None


def _candidate_chromium_profiles() -> list[tuple[Path, Path]]:
    """
    Return (local_state_path, cookies_path) pairs for every Chromium-based
    session found on this machine that might hold claude.ai cookies.

    Search order:
    1. Claude Desktop – recursive scan of all candidate app-data dirs
    2. Google Chrome, Edge, Brave, Opera, Vivaldi, Chromium – well-known paths
    """
    roaming = os.environ.get("APPDATA", "")
    local   = os.environ.get("LOCALAPPDATA", "")

    results: list[tuple[Path, Path]] = []
    seen: set[Path] = set()

    def _add(local_state: Optional[Path], cookies: Path) -> None:
        if cookies in seen:
            return
        if local_state and local_state.exists() and cookies.exists() and _is_sqlite(cookies):
            results.append((local_state, cookies))
            seen.add(cookies)

    # ── 1. Claude Desktop (recursive) ────────────────────────────────────────
    for base in _claude_base_dirs():
        if not base.exists():
            continue
        for cookies in _find_all_cookies_in_dir(base):
            ls = _local_state_for(cookies)
            _add(ls, cookies)

    # ── 2. Known browser profiles ─────────────────────────────────────────────
    browser_profiles: list[tuple[str, str]] = [
        # Google Chrome
        (local, r"Google\Chrome\User Data\Default"),
        (local, r"Google\Chrome\User Data\Profile 1"),
        (local, r"Google\Chrome SxS\User Data\Default"),   # Canary
        # Microsoft Edge
        (local, r"Microsoft\Edge\User Data\Default"),
        (local, r"Microsoft\Edge\User Data\Profile 1"),
        # Brave
        (local, r"BraveSoftware\Brave-Browser\User Data\Default"),
        (local, r"BraveSoftware\Brave-Browser\User Data\Profile 1"),
        # Opera
        (roaming, r"Opera Software\Opera Stable"),
        (roaming, r"Opera Software\Opera GX Stable"),
        # Vivaldi
        (local, r"Vivaldi\User Data\Default"),
        # Chromium
        (local, r"Chromium\User Data\Default"),
    ]

    for base_env, sub in browser_profiles:
        if not base_env:
            continue
        profile = Path(base_env) / sub
        # Local State lives in "User Data" (parent of Default/Profile N)
        ls = (profile.parent / "Local State") if "\\" in sub else (profile / "Local State")
        if not ls.exists():
            ls = profile / "Local State"
        for cookies_sub in ("Network", ""):
            c = (profile / cookies_sub / "Cookies") if cookies_sub else (profile / "Cookies")
            _add(ls, c)

    return results


def _find_cookies_file() -> Optional[Path]:
    for _, c in _candidate_chromium_profiles():
        return c
    return None


def _find_local_state() -> Optional[Path]:
    for ls, _ in _candidate_chromium_profiles():
        return ls
    return None


# ── DPAPI + AES key decryption ───────────────────────────────────────────────

def _get_aes_key_from(local_state_path: Path) -> Optional[bytes]:
    """
    Read and DPAPI-decrypt the AES-256-GCM key stored in a specific
    Chromium 'Local State' file.
    """
    if sys.platform != "win32":
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
        print(f"[desktop_session] AES key read failed ({local_state_path}): {exc}")
        return None


def _get_aes_key() -> Optional[bytes]:
    """Convenience: get the AES key from the first known profile."""
    ls = _find_local_state()
    return _get_aes_key_from(ls) if ls else None


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

def _read_cookies_from(local_state: Path, cookie_path: Path) -> dict[str, str]:
    """
    Decrypt and return claude.ai / anthropic.com cookies from a specific
    (local_state, cookies_db) pair.  Returns {} on any error.
    """
    key = _get_aes_key_from(local_state)
    if key is None:
        return {}

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)
    try:
        shutil.copy2(str(cookie_path), tmp_path)
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
        print(f"[desktop_session] Cookie read error ({cookie_path}): {exc}")
        return {}
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp_path + suffix)
            except OSError:
                pass


def get_claude_cookies() -> dict[str, str]:
    """
    Try every known Chromium profile in order and return the first set of
    claude.ai / anthropic.com cookies found.
    """
    if sys.platform != "win32":
        return {}

    for local_state, cookies_path in _candidate_chromium_profiles():
        result = _read_cookies_from(local_state, cookies_path)
        if result:
            return result
    return {}


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


def _get_claude_desktop_userdata_dirs() -> list[Path]:
    """
    Return directories that look like Claude Desktop Electron userData
    (have 'Local State' + 'config.json' and typical Chromium sub-dirs).
    """
    result: list[Path] = []
    for d in _claude_base_dirs():
        if not d.exists():
            continue
        try:
            children = {c.name.lower() for c in d.iterdir()}
        except OSError:
            continue
        if "local state" in children and "config.json" in children:
            if any(k in children for k in ("network", "indexeddb", "local storage",
                                            "ant-did", "claude_desktop_config.json")):
                result.append(d)
    return result


def get_oauth_token() -> Optional[dict]:
    """
    Read and decrypt Claude Desktop's OAuth token from config.json.

    The value at key 'oauth:tokenCache' is a base64-encoded, v10-prefixed
    AES-256-GCM blob — the same format as Chromium cookie values.
    The AES key comes from 'Local State' in the same directory.

    Returns the decrypted token as a dict, or None on failure.
    """
    if sys.platform != "win32":
        return None

    for userdata in _get_claude_desktop_userdata_dirs():
        config_path      = userdata / "config.json"
        local_state_path = userdata / "Local State"

        if not config_path.exists() or not local_state_path.exists():
            continue

        key = _get_aes_key_from(local_state_path)
        if key is None:
            continue

        try:
            config = json.loads(config_path.read_bytes())
            encrypted_b64 = config.get("oauth:tokenCache")
            if not encrypted_b64:
                continue

            encrypted = base64.b64decode(encrypted_b64)
            decrypted = _decrypt_value(encrypted, key)
            token = json.loads(decrypted)
            if isinstance(token, dict):
                return token
        except Exception as exc:
            print(f"[desktop_session] OAuth token decryption failed: {exc}")
            continue

    return None


def _headers_for_oauth(token: dict) -> dict:
    """
    Build HTTP headers for claude.ai using a decrypted OAuth token dict.
    Tries common key names for access tokens and session tokens.
    """
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://claude.ai/",
        "Origin": "https://claude.ai",
    }

    # Bearer token (Anthropic API / oauth flow)
    access = (
        token.get("access_token")
        or token.get("accessToken")
        or token.get("token")
        or ""
    )
    if access:
        headers["Authorization"] = f"Bearer {access}"

    # Session cookie equivalent
    session = (
        token.get("session_token")
        or token.get("sessionToken")
        or token.get("__Secure-next-auth.session-token")
        or ""
    )
    if session:
        headers["Cookie"] = f"__Secure-next-auth.session-token={session}"

    return headers


def fetch_usage() -> dict:
    """
    Read Claude Desktop session cookies (or OAuth token) and call
    claude.ai's internal API to retrieve the usage percentages it displays.

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
    oauth   = get_oauth_token() if not cookies else None

    if not cookies and not oauth:
        empty["error"] = (
            "No Claude Desktop session found. "
            "Make sure you are signed in to Claude Desktop."
        )
        return empty

    if cookies:
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
    else:
        headers = _headers_for_oauth(oauth)  # type: ignore[arg-type]

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


import re as _re
# Match UUID-like hex-dash names: standard 8-4-4-4-12 OR
# non-standard first segment (Electron sometimes uses 9-char first segment).
_UUID_RE = _re.compile(
    r"^[0-9a-f]{6,12}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", _re.I
)


def _dir_tree(root: Path, max_depth: int = 3, _depth: int = 0) -> list[str]:
    """Return indented tree lines for *root*, up to *max_depth* levels."""
    lines: list[str] = []
    indent = "  " * _depth
    try:
        entries = sorted(root.iterdir())
    except (PermissionError, OSError):
        return [f"{indent}  (permission denied)"]
    for entry in entries[:30]:
        try:
            is_dir = entry.is_dir()
        except OSError:
            lines.append(f"{indent}  {entry.name}  (inaccessible)")
            continue
        suffix = "\\" if is_dir else ""
        lines.append(f"{indent}  {entry.name}{suffix}")
        if is_dir and _depth < max_depth - 1:
            lines.extend(_dir_tree(entry, max_depth, _depth + 1))
    if len(entries) > 30:
        lines.append(f"{indent}  … ({len(entries) - 30} more)")
    return lines


def _read_text_file(p: Path, max_bytes: int = 1024) -> str:
    """Read a text/JSON file; truncate and redact any long tokens."""
    try:
        raw = p.read_bytes()[:max_bytes]
        text = raw.decode("utf-8", errors="replace")
        # Redact strings longer than 40 chars that look like tokens
        text = _re.sub(
            r'("(?:token|key|secret|session|auth|jwt|access)[^"]*"\s*:\s*")[^"]{40,}(")',
            r"\1<redacted>\2",
            text,
            flags=_re.IGNORECASE,
        )
        if len(raw) == max_bytes:
            text += "\n… (truncated)"
        return text
    except Exception as exc:
        return f"(error: {exc})"


_SEP = "\\"


def _inspect_claude_desktop_dir(d: Path) -> list[str]:
    """
    Focused inspection for a Claude Desktop MSIX userData directory.
    Avoids dumping the entire 5-level tree; instead zooms in on what matters:
    - Top-level file list (1 level, skip Cache / Code Cache noise)
    - Contents of key auth files: ant-did, claude_desktop_config.json …
    - UUID-like partition sub-dirs: 2-level tree + Cookies / Local State probe
    - Session dirs: list sub-entries + read small files
    """
    lines: list[str] = []
    lines.append(f"\n  {d}")

    # ── Top-level listing (skip large cache dirs) ─────────────────────────────
    _SKIP = {"cache", "code cache", "gpucache", "dawncache", "no_vary_search"}
    try:
        entries = sorted(d.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            lines.append(f"    {entry.name}  (inaccessible)")
            continue
        suffix = _SEP if is_dir else ""
        lines.append(f"    {entry.name}{suffix}")

    # ── Key auth files ────────────────────────────────────────────────────────
    for name in (
        "ant-did",
        "claude_desktop_config.json",
        "config.json",
        "auth.json",
        "credentials.json",
    ):
        f = d / name
        if f.exists() and f.is_file():
            lines.append(f"\n  >>> {name} <<<")
            for ln in _read_text_file(f, max_bytes=2048).splitlines():
                lines.append(f"    {ln}")

    # ── UUID-like partition directories ───────────────────────────────────────
    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir or not _UUID_RE.match(entry.name):
            continue
        lines.append(f"\n  Partition {entry.name}{_SEP}")
        lines.extend(_dir_tree(entry, max_depth=2))
        # Probe for Chromium session files anywhere inside
        for rel in (
            "Local State",
            "Cookies",
            r"Network\Cookies",
            r"Default\Cookies",
            r"Default\Network\Cookies",
            r"Default\Local State",
        ):
            probe = entry / rel
            if probe.exists():
                lines.append(f"    *** Found: {rel} ***")

    # ── Session directories ───────────────────────────────────────────────────
    for sdir_name in (
        "de-sessions",
        "sessions",
        "claude-sessions",
        "node-sessions",
        "local-agent-mode-sessions",
    ):
        sdir = d / sdir_name
        if not sdir.exists():
            continue
        lines.append(f"\n  {sdir_name}{_SEP}")
        try:
            for entry in sorted(sdir.iterdir())[:5]:
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    lines.append(f"    {entry.name}  (inaccessible)")
                    continue
                suffix = _SEP if is_dir else ""
                lines.append(f"    {entry.name}{suffix}")
                if not is_dir:
                    snippet = _read_text_file(entry, 512)[:200].replace("\n", " ")
                    lines.append(f"      {snippet}")
                else:
                    for sub in sorted(entry.iterdir())[:4]:
                        try:
                            sub_is_dir = sub.is_dir()
                        except OSError:
                            continue
                        ssuffix = _SEP if sub_is_dir else ""
                        lines.append(f"      {sub.name}{ssuffix}")
                        if not sub_is_dir:
                            snippet = _read_text_file(sub, 512)[:200].replace(
                                "\n", " "
                            )
                            lines.append(f"        {snippet}")
        except OSError:
            pass

    return lines


def _diagnose_alt_auth(userdata_dirs: list[Path]) -> list[str]:
    """
    When no Chromium cookies are found, probe alternative auth storage:
    1. Windows Credential Manager (electron-keytar)
    2. Network\Cookies existence + size
    3. Local Storage\leveldb file list
    4. Full config.json (all keys)
    """
    lines: list[str] = []

    # ── A. Windows Credential Manager ────────────────────────────────────────
    lines.append("\n=== A. Windows Credential Manager ===")
    try:
        import win32cred  # type: ignore
        all_creds = win32cred.CredEnumerate(None, 0) or []
        found = [
            c for c in all_creds
            if any(
                kw in (c.get("TargetName") or "").lower()
                for kw in ("claude", "anthropic", "ant-", "anthr")
            )
        ]
        if found:
            for c in found:
                lines.append(f"  Target: {c.get('TargetName')}")
                lines.append(f"  User:   {c.get('UserName')}")
                blob = c.get("CredentialBlob")
                if blob:
                    try:
                        decoded = blob.decode("utf-16-le")
                        lines.append(f"  Blob (utf-16): {decoded[:120]}")
                    except Exception:
                        lines.append(f"  Blob (hex):    {blob[:60].hex()}")
        else:
            lines.append(f"  Searched {len(all_creds)} credentials — none match claude/anthropic")
    except ImportError:
        lines.append("  win32cred not available")
    except Exception as exc:
        lines.append(f"  Error: {exc}")

    # ── B. Direct Network\Cookies probe ──────────────────────────────────────
    lines.append("\n=== B. Network\\Cookies probe ===")
    for d in userdata_dirs:
        nc = d / "Network" / "Cookies"
        lines.append(f"  {nc}")
        if nc.exists():
            size = nc.stat().st_size
            sqlite_ok = _is_sqlite(nc)
            lines.append(f"    exists: yes  size: {size}  sqlite: {sqlite_ok}")
        else:
            lines.append("    exists: NO")
        # Also legacy path
        lc = d / "Cookies"
        if lc.exists():
            lines.append(f"  {lc}  (legacy)  size: {lc.stat().st_size}")

    # ── C. Local Storage\leveldb file list ───────────────────────────────────
    lines.append("\n=== C. Local Storage\\leveldb ===")
    for d in userdata_dirs:
        ldb_dir = d / "Local Storage" / "leveldb"
        if not ldb_dir.exists():
            lines.append(f"  {ldb_dir}  → does not exist")
            continue
        lines.append(f"  {ldb_dir}")
        try:
            for f in sorted(ldb_dir.iterdir())[:20]:
                lines.append(f"    {f.name}  ({f.stat().st_size} bytes)")
        except OSError as exc:
            lines.append(f"    (error: {exc})")

    # ── D. Full config.json key listing ──────────────────────────────────────
    lines.append("\n=== D. config.json ALL keys ===")
    for d in userdata_dirs:
        cfg = d / "config.json"
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_bytes())
            for k, v in data.items():
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:80] + "…"
                lines.append(f"  {k}: {v_str}")
        except Exception as exc:
            lines.append(f"  (error reading config.json: {exc})")

    # ── E. Decrypt oauth:tokenCache and attempt API call ─────────────────────
    lines.append("\n=== E. OAuth token decryption + API test ===")
    token = get_oauth_token()
    if token is None:
        lines.append("  get_oauth_token() returned None")
        lines.append("  (AES key failure, missing config.json, or no oauth:tokenCache key)")
    else:
        lines.append(f"  Decryption OK. Token keys: {list(token.keys())}")
        # Show redacted previews
        for k, v in token.items():
            v_str = str(v)
            if len(v_str) > 20:
                v_str = v_str[:10] + "…" + v_str[-6:]
            lines.append(f"    {k}: {v_str}")
        # Attempt API call using the token
        lines.append("\n  Attempting API call with OAuth token…")
        result = fetch_usage()
        if result["error"]:
            lines.append(f"  Error: {result['error']}")
        else:
            lines.append(f"  pct_5h = {result['pct_5h']}")
            lines.append(f"  pct_7d = {result['pct_7d']}")
        if result.get("_raw"):
            lines.append("  Raw responses:")
            for url, data in result["_raw"].items():
                lines.append(f"    {url}")
                lines.append(f"      {str(data)[:300]}")

    return lines


def diagnose() -> str:
    """Return a human-readable diagnostic string for the Settings window."""
    lines: list[str] = []

    # ── 1. Claude Desktop candidate directories ───────────────────────────────
    claude_dirs = _claude_base_dirs()
    existing = [d for d in claude_dirs if d.exists()]
    lines.append("Claude Desktop candidate dirs:")
    for d in claude_dirs:
        lines.append(f"  {'[OK]' if d.exists() else '[  ]'} {d}")

    # Identify which existing dirs look like Claude Desktop userData
    # (have >1 subdir / have session-related contents rather than just Logs)
    def _is_userdata(d: Path) -> bool:
        try:
            children = {c.name.lower() for c in d.iterdir()}
        except OSError:
            return False
        return bool(
            children - {"logs"}  # more than just a Logs dir
            and any(
                k in children
                for k in (
                    "cache", "code cache", "blob_storage",
                    "si_0.indexeddb.leveldb", "de-sessions",
                    "ant-did", "claude_desktop_config.json",
                )
            )
        )

    userdata_dirs = [d for d in existing if _is_userdata(d)]
    plain_dirs    = [d for d in existing if not _is_userdata(d)]

    if plain_dirs:
        lines.append("\nStandard dirs (only Logs / empty):")
        for d in plain_dirs:
            lines.append(f"  {d}")
            lines.extend(_dir_tree(d, max_depth=2))

    if userdata_dirs:
        lines.append("\n>>> Claude Desktop userData found — deep inspection <<<")
        for d in userdata_dirs:
            lines.extend(_inspect_claude_desktop_dir(d))

    # ── 2. Broad 'Local State' search ─────────────────────────────────────────
    lines.append("\nSearching all of %APPDATA% + %LOCALAPPDATA% for 'Local State'…")
    all_ls = _find_local_state_files_broadly()
    if all_ls:
        lines.append(f"Found {len(all_ls)} Local State file(s):")
        for p in all_ls:
            lines.append(f"  {p}")
    else:
        lines.append("  None found")

    # ── 3. Chromium profiles with Cookies ─────────────────────────────────────
    profiles = _candidate_chromium_profiles()
    lines.append(f"\nChromium profiles (Cookies + Local State): {len(profiles)}")
    for ls, c in profiles:
        lines.append(f"  {c}")

    if not profiles:
        lines.append(
            "\n→ No Chromium cookie store found.\n"
            "  Claude Desktop (MSIX) likely stores auth via IndexedDB or\n"
            "  a custom session file — see deep inspection above."
        )
        return "\n".join(lines)

    # ── 4. Cookie decryption ──────────────────────────────────────────────────
    total_cookies = 0
    for ls, c in profiles:
        key = _get_aes_key_from(ls)
        status = "OK" if key else "FAILED (pywin32?)"
        cookies = _read_cookies_from(ls, c) if key else {}
        lines.append(
            f"\n  {c.parent.parent.name}\\{c.parent.name}\\{c.name}:"
            f"\n    AES key: {status}"
            f"\n    claude.ai cookies: {len(cookies)}"
            + (f" ({', '.join(list(cookies)[:5])})" if cookies else "")
        )
        total_cookies += len(cookies)

    if total_cookies == 0:
        lines.append(
            "\n→ No claude.ai cookies found.\n"
            "  Checking alternative auth storage…"
        )
        lines.extend(_diagnose_alt_auth(userdata_dirs))
        return "\n".join(lines)

    # ── 5. API call ───────────────────────────────────────────────────────────
    lines.append("\nTrying claude.ai API endpoints…")
    result = fetch_usage()
    if result["error"]:
        lines.append(f"Error: {result['error']}")
    else:
        lines.append(f"pct_5h = {result['pct_5h']}")
        lines.append(f"pct_7d = {result['pct_7d']}")

    if result.get("_raw"):
        lines.append("\nRaw API responses:")
        for url, data in result["_raw"].items():
            lines.append(f"  {url}: {str(data)[:200]}")

    return "\n".join(lines)
