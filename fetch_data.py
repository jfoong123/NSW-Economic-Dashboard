#!/usr/bin/env python3
# fetch_data.py  --  v2.2  (adds full history sparklines + "vs a year ago"; single-loop ABS fetch; cash-rate 2dp)
"""
Pulls the automatable tiles and writes data.json.

ABS tiles go through the abs-mcp curated layer (verified plain-English filters,
correct SDMX key order handled for us). The RBA cash rate is parsed from a plain
CSV. Each auto tile now also carries:
  * spark      -- the full available history of the displayed metric (for a sparkline)
  * year_ago   -- the value ~12 months ago and the change since

Every metric is isolated: a failure keeps the previous value (flagged stale), so
one broken source never blanks the dashboard or crashes the job. All ABS pulls
run inside ONE asyncio event loop.

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

from metrics_config import ABS_METRICS, RBA_METRICS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
MANUAL_PATH = os.path.join(HERE, "manual_data.json")
UA = "nsw-shadow-dashboard/2.2"
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
# Build the "displayed metric over time" series, then derive tile fields
# ----------------------------------------------------------------------------- #
def build_plot_series(series, metric):
    """series=[(period,raw)] ascending -> [(period, displayed_value)] ascending."""
    t = metric["transform"]
    if t in ("rate", "level"):
        return list(series)
    if t == "index_yoy":
        ppy = periods_per_year(metric["freq"])
        out = []
        for i in range(ppy, len(series)):
            base = series[i - ppy][1]
            if base not in (None, 0):
                out.append((series[i][0], round((series[i][1] - base) / base * 100, 1)))
        return out
    return list(series)


def apply_transform(series, metric):
    plot = build_plot_series(series, metric)
    if not plot:
        raise ValueError("empty series")
    period, latest = plot[-1]
    prev = plot[-2][1] if len(plot) >= 2 else None
    ppy = periods_per_year(metric["freq"])
    yago = plot[-(ppy + 1)][1] if len(plot) > ppy else None
    is_level = metric["transform"] == "level"

    def pct(a, b):
        return None if (b in (None, 0)) else round((a - b) / b * 100, 1)

    def d(x):
        return "flat" if x in (None, 0) else ("up" if x > 0 else "down")

    if is_level:
        change, kind, value = pct(latest, prev), "pct", round(latest, 2)
        ya = None if yago is None else dict(value=round(yago, 2), change=pct(latest, yago),
                                            change_kind="pct", direction=d(pct(latest, yago)))
    else:
        change = None if prev is None else round(latest - prev, 1)
        kind, value = "pt", round(latest, 1)
        ya = None if yago is None else dict(value=round(yago, 1), change=round(latest - yago, 1),
                                            change_kind="pt", direction=d(round(latest - yago, 1)))

    return dict(value=value, period=period, change=change, change_kind=kind, direction=d(change),
                spark=[round(v, 2) for _, v in plot], year_ago=ya)


# ----------------------------------------------------------------------------- #
# ABS via abs-mcp — all metrics in ONE event loop
# ----------------------------------------------------------------------------- #
async def _pull_one(dataset_id, filters, start):
    from abs_mcp.server import _get_data_impl
    resp = await _get_data_impl(dataset_id, filters, start, None, "records", last_n=None)
    return resp.records


async def _fetch_all_abs(metrics):
    start = str(dt.date.today().year - 8)          # pull a wide history for the sparklines
    results, errors = {}, {}
    for m in metrics:
        try:
            records = await _pull_one(m["dataset_id"], m["filters"], start)
            series = []
            for r in records:
                val, per = getattr(r, "value", None), getattr(r, "period", None)
                if val is not None and per:
                    series.append((per, float(val)))
            series.sort(key=lambda x: period_key(x[0]))
            res = apply_transform(series, m)
            res.update(id=m["id"], label=m["label"], section=m["section"],
                       source="ABS", unit=m.get("unit", ""))
            results[m["id"]] = res
        except Exception as e:                         # noqa: BLE001 (graceful by design)
            errors[m["id"]] = str(e)
    try:
        from abs_mcp.server import reset_client_for_tests
        await reset_client_for_tests()
    except Exception:
        pass
    return results, errors


# ----------------------------------------------------------------------------- #
# RBA via plain CSV (with monthly history)
# ----------------------------------------------------------------------------- #
def http_get(url):
    with urlopen(Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


_MONTHS = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
           "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}


def _rba_ym(s):
    s = s.strip()
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{_MONTHS.get(m.group(2).lower(), '00')}"
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _rba_locate(text, series_id):
    rows = list(csv.reader(io.StringIO(text)))
    id_row = next((i for i, r in enumerate(rows)
                   if r and r[0].strip().lower() in ("series id", "series_id", "mnemonic")), None)
    if id_row is None:
        raise ValueError("no 'Series ID' row in RBA csv")
    col = next((i for i, c in enumerate(rows[id_row]) if series_id.upper() in c.strip().upper()), None)
    if col is None:
        raise ValueError(f"series {series_id} not found")
    return rows, id_row, col


def parse_rba_csv(text, series_id):
    """Latest (value, date) — kept for the offline test."""
    rows, id_row, col = _rba_locate(text, series_id)
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


def parse_rba_monthly(text, series_id, keep=84):
    """Downsample daily RBA data to one point per month (last value in month)."""
    rows, id_row, col = _rba_locate(text, series_id)
    monthly = {}
    for r in rows[id_row + 1:]:
        if not r or col >= len(r) or not r[col].strip():
            continue
        ym = _rba_ym(r[0])
        if not ym:
            continue
        try:
            monthly[ym] = float(r[col].strip())
        except ValueError:
            pass
    keys = sorted(monthly)[-keep:]
    return [(k, monthly[k]) for k in keys]


def fetch_rba(metric):
    try:
        ser = parse_rba_monthly(http_get(metric["csv_url"]), metric["series_id"])
    except Exception:
        ser = parse_rba_monthly(http_get(metric["fallback_url"]), metric["series_id"])
    if not ser:
        raise ValueError("no RBA history")
    period, latest = ser[-1]
    vals = [v for _, v in ser]
    ya = None
    if len(vals) > 12:
        yv = vals[-13]
        ya = dict(value=round(yv, 2), change=round(latest - yv, 1), change_kind="pt",
                  direction=("up" if latest > yv else "down" if latest < yv else "flat"))
    return dict(id=metric["id"], label=metric["label"], section=metric["section"],
                source="RBA", unit=metric.get("unit", "%"), value=round(latest, 2),
                decimals=metric.get("decimals", 2), period=period, change=None,
                change_kind="pt", direction="flat",
                spark=[round(v, 2) for v in vals], year_ago=ya)


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

    abs_results, abs_errors = asyncio.run(_fetch_all_abs(ABS_METRICS))
    for m in ABS_METRICS:
        if m["id"] in abs_results:
            tiles[m["id"]] = abs_results[m["id"]]
            print(f"  ok   {m['id']:<12} {tiles[m['id']]['value']} ({tiles[m['id']]['period']})  spark={len(tiles[m['id']]['spark'])}pts")
        else:
            err = abs_errors.get(m["id"], "unknown error")
            errors.append(f"{m['id']}: {err}")
            if m["id"] in prev_tiles:
                tiles[m["id"]] = {**prev_tiles[m["id"]], "stale": True}
            print(f"  WARN {m['id']:<12} {err}")

    for m in RBA_METRICS:
        try:
            tiles[m["id"]] = fetch_rba(m)
            print(f"  ok   {m['id']:<12} {tiles[m['id']]['value']} ({tiles[m['id']]['period']})  spark={len(tiles[m['id']]['spark'])}pts")
        except Exception as e:                         # noqa: BLE001
            errors.append(f"{m['id']}: {e}")
            if m["id"] in prev_tiles:
                tiles[m["id"]] = {**prev_tiles[m["id"]], "stale": True}
            print(f"  WARN {m['id']:<12} {e}")

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
        print(json.dumps(out, indent=2)[:1500]); return
    with open(DATA_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {DATA_PATH}")


if __name__ == "__main__":
    main()
