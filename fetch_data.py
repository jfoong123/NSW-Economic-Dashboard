#!/usr/bin/env python3
"""
fetch_data.py
=============
Pulls the automatable tiles and writes data.json.

ABS tiles go through the abs-mcp curated layer (verified plain-English filters,
correct SDMX key order handled for us). The RBA cash rate is parsed from a plain
CSV with the standard library. Every metric is fetched in its own try/except: a
failure logs a warning and keeps the previous value from data.json (flagged
stale), so one broken source never blanks the dashboard or crashes the job.

Usage:
  python fetch_data.py            # weekly run -> writes data.json
  python fetch_data.py --dry-run  # fetch and print, don't write
"""

import asyncio
import csv
import io
import json
import os
import re
import sys
import datetime as dt
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from metrics_config import ABS_METRICS, RBA_METRICS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
MANUAL_PATH = os.path.join(HERE, "manual_data.json")
UA = "nsw-shadow-dashboard/2.0"
TIMEOUT = 45


# ----------------------------------------------------------------------------- #
# Period handling
# ----------------------------------------------------------------------------- #
def period_key(p):
    p = (p or "").strip()
    m = re.match(r"^(\d{4})-Q([1-4])$", p)
    if m:
        return (int(m.group(1)), int(m.group(2)) * 3)
    m = re.match(r"^(\d{4})-(\d{2})$", p)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(\d{4})$", p)
    if m:
        return (int(m.group(1)), 12)
    return (0, 0)


def periods_per_year(freq):
    return {"M": 12, "Q": 4, "A": 1}.get(freq, 1)


# ----------------------------------------------------------------------------- #
# Transform: series=[(period,value)] ascending -> display dict
# ----------------------------------------------------------------------------- #
def apply_transform(series, metric):
    if not series:
        raise ValueError("empty series")
    period, latest = series[-1]
    prev = series[-2][1] if len(series) >= 2 else None
    ppy = periods_per_year(metric["freq"])
    yago = series[-(ppy + 1)][1] if len(series) > ppy else None
    t = metric["transform"]

    def pct(a, b):
        return None if (b in (None, 0)) else round((a - b) / b * 100, 1)

    def d(x):
        return "flat" if x in (None, 0) else ("up" if x > 0 else "down")

    if t == "rate":
        chg = None if prev is None else round(latest - prev, 1)
        return dict(value=round(latest, 1), period=period, change=chg, change_kind="pt", direction=d(chg))
    if t == "index_yoy":
        yoy = pct(latest, yago)
        prev_yoy = pct(series[-2][1], series[-(ppy + 2)][1]) if len(series) > ppy + 1 else None
        chg = None if (yoy is None or prev_yoy is None) else round(yoy - prev_yoy, 1)
        return dict(value=yoy, period=period, change=chg, change_kind="pt", direction=d(chg))
    # level
    return dict(value=round(latest, 2), period=period, change=pct(latest, prev), change_kind="pct", direction=d(pct(latest, prev)))


# ----------------------------------------------------------------------------- #
# ABS via abs-mcp
# ----------------------------------------------------------------------------- #
async def _pull_records(dataset_id, filters, start):
    from abs_mcp.server import _get_data_impl
    resp = await _get_data_impl(dataset_id, filters, start, None, "records", last_n=None)
    return resp.records


def fetch_abs(metric):
    start = str(dt.date.today().year - 6)
    records = asyncio.run(_pull_records(metric["dataset_id"], metric["filters"], start))
    series = []
    for r in records:
        val = getattr(r, "value", None)
        per = getattr(r, "period", None)
        if val is None or per is None:
            continue
        series.append((per, float(val)))
    series.sort(key=lambda x: period_key(x[0]))
    result = apply_transform(series, metric)
    result.update(id=metric["id"], label=metric["label"], section=metric["section"],
                  source="ABS", unit=metric.get("unit", ""))
    return result


# ----------------------------------------------------------------------------- #
# RBA via plain CSV
# ----------------------------------------------------------------------------- #
def http_get(url):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_rba_csv(text, series_id):
    rows = list(csv.reader(io.StringIO(text)))
    id_row = next((i for i, r in enumerate(rows)
                   if r and r[0].strip().lower() in ("series id", "series_id", "mnemonic")), None)
    if id_row is None:
        raise ValueError("no 'Series ID' row in RBA csv")
    header = rows[id_row]
    col = next((i for i, c in enumerate(header) if series_id.upper() in c.strip().upper()), None)
    if col is None:
        raise ValueError(f"series {series_id} not found")
    date_re = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$|^\d{4}-\d{2}-\d{2}$")
    last_val = last_date = None
    for r in rows[id_row + 1:]:
        if r and date_re.match(r[0].strip()) and col < len(r) and r[col].strip():
            try:
                last_val, last_date = float(r[col].strip()), r[0].strip()
            except ValueError:
                pass
    if last_val is None:
        raise ValueError(f"no values for {series_id}")
    return last_val, last_date


def fetch_rba(metric):
    try:
        val, date = parse_rba_csv(http_get(metric["csv_url"]), metric["series_id"])
    except Exception:
        val, date = parse_rba_csv(http_get(metric["fallback_url"]), metric["series_id"])
    return dict(id=metric["id"], label=metric["label"], section=metric["section"],
                source="RBA", unit=metric.get("unit", "%"), value=round(val, 2),
                period=date, change=None, change_kind="pt", direction="flat")


# Kept for the optional zero-dependency fallback path + its offline test.
def parse_sdmx_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.lower(): c for c in (reader.fieldnames or [])}
    pcol, vcol = cols.get("time_period"), cols.get("obs_value")
    if not pcol or not vcol:
        raise ValueError(f"unexpected columns: {reader.fieldnames}")
    out = []
    for row in reader:
        raw, per = (row.get(vcol) or "").strip(), (row.get(pcol) or "").strip()
        if raw and per:
            try:
                out.append((per, float(raw)))
            except ValueError:
                pass
    out.sort(key=lambda x: period_key(x[0]))
    return out


# ----------------------------------------------------------------------------- #
# Main
# ----------------------------------------------------------------------------- #
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def main():
    dry = "--dry-run" in sys.argv[1:]
    prev_tiles = load_json(DATA_PATH, {}).get("tiles", {})
    manual = load_json(MANUAL_PATH, {})
    tiles, errors = {}, []

    def run(metrics, fetch):
        for m in metrics:
            try:
                tiles[m["id"]] = fetch(m)
                t = tiles[m["id"]]
                print(f"  ok   {m['id']:<12} {t['value']} ({t['period']})")
            except Exception as e:                     # noqa: BLE001 (graceful by design)
                errors.append(f"{m['id']}: {e}")
                if m["id"] in prev_tiles:
                    tiles[m["id"]] = {**prev_tiles[m["id"]], "stale": True}
                print(f"  WARN {m['id']:<12} {e}")

    run(ABS_METRICS, fetch_abs)
    run(RBA_METRICS, fetch_rba)

    for tid, t in manual.get("tiles", {}).items():
        tiles[tid] = {**t, "source": t.get("source", "Manual"), "id": tid}

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "state_vs_state": manual.get("state_vs_state"),
        "tiles": tiles,
        "errors": errors,
    }
    print(f"\n{len(tiles)} tiles, {len(errors)} errors")
    if dry:
        print(json.dumps(out, indent=2)[:1800]); return
    with open(DATA_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {DATA_PATH}")


if __name__ == "__main__":
    main()
