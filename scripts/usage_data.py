"""Parse ~/.claude/projects/**/*.jsonl for Claude token usage.

Aggregates real token counts (input/output/cache) per day and per model,
deduplicating by requestId so streamed/retried records aren't double-counted.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
from collections import defaultdict

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Approximate blended USD per *million* tokens, used only for a rough cost
# estimate in the popup. Cache reads are cheap; cache writes ~= input.
PRICING = {
    # model substring -> (input, output) $/Mtok
    "opus":   (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku":  (0.80, 4.0),
}
CACHE_READ_DISCOUNT = 0.1   # cache reads ~10% of input price
CACHE_WRITE_FACTOR = 1.25   # 5m cache writes ~125% of input price


def _price_for(model: str):
    for key, val in PRICING.items():
        if key in model:
            return val
    return (3.0, 15.0)  # default to sonnet-ish


def _short_model(model: str) -> str:
    for key in ("opus", "sonnet", "haiku"):
        if key in model:
            return key
    return model or "other"


def collect():
    """Return a dict of aggregated usage.

    {
      "by_day":   {date_str: {"input","output","cache_read","cache_write","total","cost"}},
      "by_model": {short:    {"input","output","cache_read","cache_write","total","cost"}},
      "today": <date_str>,
    }
    """
    seen_requests = set()
    by_day = defaultdict(lambda: _empty())
    by_model = defaultdict(lambda: _empty())

    for path in glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "assistant":
                        continue
                    msg = rec.get("message") or {}
                    usage = msg.get("usage") or {}
                    if not usage:
                        continue

                    # Dedup: a single API response can appear on multiple lines.
                    rid = rec.get("requestId") or msg.get("id")
                    if rid and rid in seen_requests:
                        continue
                    if rid:
                        seen_requests.add(rid)

                    ts = rec.get("timestamp", "")
                    day = _local_day(ts)
                    if not day:
                        continue

                    model = msg.get("model", "")
                    if model.startswith("<") or not model:
                        continue  # synthetic/placeholder records carry no real usage
                    short = _short_model(model)
                    inp = usage.get("input_tokens", 0) or 0
                    out = usage.get("output_tokens", 0) or 0
                    cr = usage.get("cache_read_input_tokens", 0) or 0
                    cw = usage.get("cache_creation_input_tokens", 0) or 0

                    p_in, p_out = _price_for(model)
                    cost = (
                        inp / 1e6 * p_in
                        + out / 1e6 * p_out
                        + cr / 1e6 * p_in * CACHE_READ_DISCOUNT
                        + cw / 1e6 * p_in * CACHE_WRITE_FACTOR
                    )

                    for bucket in (by_day[day], by_model[short]):
                        bucket["input"] += inp
                        bucket["output"] += out
                        bucket["cache_read"] += cr
                        bucket["cache_write"] += cw
                        bucket["total"] += inp + out + cr + cw
                        bucket["cost"] += cost
        except OSError:
            continue

    return {
        "by_day": dict(by_day),
        "by_model": dict(by_model),
        "today": dt.date.today().isoformat(),
    }


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "total": 0, "cost": 0.0}


def _local_day(ts: str):
    """Convert an ISO8601 UTC timestamp to a local YYYY-MM-DD string."""
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return t.astimezone().date().isoformat()
    except ValueError:
        return None


def last_n_days(by_day, n=7):
    """Return [(date_str, total_tokens), ...] for the last n days incl. today."""
    today = dt.date.today()
    out = []
    for i in range(n - 1, -1, -1):
        d = (today - dt.timedelta(days=i)).isoformat()
        out.append((d, by_day.get(d, {}).get("total", 0)))
    return out


def human(n: float) -> str:
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def _iter_events(since_days=7):
    """Yield (datetime_local, total_tokens) for assistant records in the
    last `since_days`, deduped by requestId. Used for burn-rate analysis."""
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=since_days)
    seen = set()
    for path in glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"),
                          recursive=True):
        try:
            # Skip files untouched before the cutoff entirely.
            if dt.datetime.fromtimestamp(
                    os.path.getmtime(path)).astimezone() < cutoff:
                continue
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "assistant":
                        continue
                    msg = rec.get("message") or {}
                    usage = msg.get("usage") or {}
                    if not usage:
                        continue
                    model = msg.get("model", "")
                    if not model or model.startswith("<"):
                        continue
                    rid = rec.get("requestId") or msg.get("id")
                    if rid and rid in seen:
                        continue
                    if rid:
                        seen.add(rid)
                    ts = rec.get("timestamp", "")
                    try:
                        t = dt.datetime.fromisoformat(
                            ts.replace("Z", "+00:00")).astimezone()
                    except ValueError:
                        continue
                    if t < cutoff:
                        continue
                    tot = ((usage.get("input_tokens", 0) or 0)
                           + (usage.get("output_tokens", 0) or 0)
                           + (usage.get("cache_read_input_tokens", 0) or 0)
                           + (usage.get("cache_creation_input_tokens", 0) or 0))
                    yield t, tot
        except OSError:
            continue


def burn_rate(recent_min=15, block_min=15):
    """Compute current vs typical token burn rate.

    recent: mean tokens/min over the last `recent_min` minutes.
    baseline: median tokens/min across *active* (nonzero) 15-min blocks in the
              last 7 days — so idle gaps don't flatten the baseline.
    Returns {recent_tpm, baseline_tpm, ratio} (ratio None if no baseline).
    """
    now = dt.datetime.now().astimezone()
    recent_cut = now - dt.timedelta(minutes=recent_min)
    recent_tok = 0
    blocks = defaultdict(int)  # block_index -> tokens

    for t, tot in _iter_events(since_days=7):
        if t >= recent_cut:
            recent_tok += tot
        # bucket into block_min-sized blocks keyed by epoch//block
        idx = int(t.timestamp() // (block_min * 60))
        blocks[idx] += tot

    recent_tpm = recent_tok / recent_min
    active = sorted(v for v in blocks.values() if v > 0)
    baseline_tpm = None
    if active:
        mid = len(active) // 2
        median_block = (active[mid] if len(active) % 2
                        else (active[mid - 1] + active[mid]) / 2)
        baseline_tpm = median_block / block_min

    ratio = None
    if baseline_tpm and baseline_tpm > 0:
        ratio = recent_tpm / baseline_tpm

    return {
        "recent_tpm": round(recent_tpm),
        "baseline_tpm": round(baseline_tpm) if baseline_tpm else 0,
        "ratio": round(ratio, 2) if ratio is not None else None,
    }


def as_summary(data):
    """Flat dict for the dms plugin: today, 7-day series, models."""
    by_day = data["by_day"]
    td = by_day.get(data["today"], _empty())
    days = last_n_days(by_day)
    return {
        "today": {
            "total": td["total"],
            "cost": round(td["cost"], 2),
            "human": human(td["total"]),
        },
        "week": {
            "total": sum(t for _, t in days),
            "series": [t for _, t in days],
            "labels": [d for d, _ in days],
        },
        "models": [
            {"name": m, "total": b["total"], "human": human(b["total"]),
             "cost": round(b["cost"], 2)}
            for m, b in sorted(data["by_model"].items(),
                               key=lambda x: -x[1]["total"])
        ],
        "rate": burn_rate(),
    }


if __name__ == "__main__":
    import sys
    if "--rate" in sys.argv:
        json.dump(burn_rate(), sys.stdout)
        sys.stdout.write("\n")
        sys.exit(0)
    data = collect()
    if "--json" in sys.argv:
        json.dump(as_summary(data), sys.stdout)
        sys.stdout.write("\n")
    else:
        td = data["by_day"].get(data["today"], _empty())
        print("Today total:", human(td["total"]), f"(${td['cost']:.2f})")
        print("7-day:", [(d, human(t)) for d, t in last_n_days(data["by_day"])])
        for m, b in sorted(data["by_model"].items(), key=lambda x: -x[1]["total"]):
            print(f"  {m:8} {human(b['total']):>8}  ${b['cost']:.2f}")
