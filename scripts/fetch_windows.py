#!/usr/bin/env python3
"""Fetch Claude plan rate-limit windows (the data /usage shows).

Reads the OAuth access token from ~/.claude/.credentials.json and calls the
(undocumented) https://api.anthropic.com/api/oauth/usage endpoint. Emits a
small JSON summary on stdout. On any failure prints {"ok": false, "error": …}
so the bar widget can degrade gracefully without ever blocking.
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

CREDS = os.path.expanduser("~/.claude/.credentials.json")
URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

# Caches: serve the last good result when the API errors (e.g. HTTP 429), so
# a transient rate-limit never blanks the widget. The plan rarely changes, so
# cache it long and avoid the extra profile request on every poll.
# Stored under XDG cache, independent of where the plugin is installed.
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "claude-usage")
os.makedirs(CACHE_DIR, exist_ok=True)
WINDOWS_CACHE = os.path.join(CACHE_DIR, "windows_cache.json")
PLAN_CACHE = os.path.join(CACHE_DIR, "plan_cache.json")
PLAN_TTL_SEC = 86400          # refetch plan at most once a day
STALE_OK_SEC = 1800           # serve cached windows up to 30 min old on error

# Map rate_limit_tier -> friendly plan label.
TIER_LABELS = {
    "default_claude_max_20x": "Max 20×",
    "default_claude_max_5x": "Max 5×",
    "default_claude_pro": "Pro",
}
# Windows we surface, in display order: (api_key, label).
WINDOWS = [
    ("five_hour", "Session (5h)"),
    ("seven_day", "Week"),
    ("seven_day_opus", "Week · Opus"),
    ("seven_day_sonnet", "Week · Sonnet"),
]


def _token():
    with open(CREDS, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    oauth = d.get("claudeAiOauth", {})
    return oauth.get("accessToken"), oauth.get("expiresAt")


def _reset_human(iso):
    """Turn an ISO8601 reset timestamp into a short local 'when'."""
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return None
    now = dt.datetime.now().astimezone()
    delta = t - now
    secs = delta.total_seconds()
    if secs <= 0:
        return "now"
    # Same calendar day -> show clock time; else weekday + time.
    if t.date() == now.date():
        when = t.strftime("%-I:%M%p").lower()
    elif 0 < secs < 7 * 86400:
        when = t.strftime("%a %-I%p").lower()
    else:
        when = t.strftime("%b %-d")
    # Compact relative hint.
    if secs < 3600:
        rel = f"{int(secs // 60)}m"
    elif secs < 86400:
        rel = f"{int(secs // 3600)}h"
    else:
        rel = f"{int(secs // 86400)}d"
    return f"{when} ({rel})"


def _get(url, token):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-usage-widget",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _read_cache(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_cache(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    except OSError:
        pass


def _now():
    return dt.datetime.now().timestamp()


def _plan(token):
    """Friendly plan label, cached for a day (the plan rarely changes)."""
    cached = _read_cache(PLAN_CACHE)
    if cached and _now() - cached.get("_ts", 0) < PLAN_TTL_SEC:
        return cached.get("plan")
    try:
        p = _get(PROFILE_URL, token)
    except Exception:
        return cached.get("plan") if cached else None
    org = p.get("organization") or {}
    tier = org.get("rate_limit_tier", "")
    plan = TIER_LABELS.get(tier, tier or None)
    _write_cache(PLAN_CACHE, {"plan": plan, "_ts": _now()})
    return plan


def fetch():
    token, expires = _token()
    if not token:
        return {"ok": False, "error": "no token"}
    if expires and expires / 1000 < dt.datetime.now().timestamp():
        # Token expired; Claude Code refreshes it on next use. Don't refresh
        # ourselves (would need the refresh flow); just report staleness.
        return {"ok": False, "error": "token expired — open Claude Code"}

    def _stale(reason):
        """Serve recent cached windows on error; flag them as stale."""
        cached = _read_cache(WINDOWS_CACHE)
        if cached and _now() - cached.get("_ts", 0) < STALE_OK_SEC:
            cached = dict(cached)
            cached["stale"] = True
            cached["error"] = reason
            return cached
        return {"ok": False, "error": reason}

    try:
        data = _get(URL, token)
    except urllib.error.HTTPError as e:
        # 429 = polled too often; reuse cache rather than blanking the widget.
        return _stale("rate limited — retrying" if e.code == 429
                      else f"http {e.code}")
    except Exception as e:  # network, timeout, json
        return _stale(type(e).__name__)

    windows = []
    for key, label in WINDOWS:
        w = data.get(key)
        if not w or w.get("utilization") is None:
            continue
        resets_at = w.get("resets_at")
        resets_in_min = None
        if resets_at:
            try:
                rt = dt.datetime.fromisoformat(resets_at).astimezone()
                resets_in_min = max(0, (rt - dt.datetime.now().astimezone())
                                    .total_seconds() / 60)
            except ValueError:
                pass
        windows.append({
            "key": key,
            "label": label,
            "pct": round(float(w["utilization"])),
            "resets": _reset_human(resets_at),
            "resets_in_min": round(resets_in_min) if resets_in_min else None,
        })

    extra = data.get("extra_usage") or {}
    out = {"ok": True, "stale": False, "windows": windows, "plan": _plan(token)}
    if extra.get("is_enabled"):
        out["credits"] = {
            "used": extra.get("used_credits", 0),
            "limit": extra.get("monthly_limit", 0),
            "currency": extra.get("currency", "USD"),
        }
    out["_ts"] = _now()
    _write_cache(WINDOWS_CACHE, out)
    return out


if __name__ == "__main__":
    json.dump(fetch(), sys.stdout)
    sys.stdout.write("\n")
