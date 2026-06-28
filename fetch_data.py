#!/usr/bin/env python3
# fetch_data.py  --  v2.5  (ranked news this-week/last-week + theme volumes + active outlets; live state table; completions; markets; history sparklines)
"""
Pulls the automatable tiles, the live state-vs-state table, and a weekly NSW
economic news digest, and writes data.json.

ABS tiles + the state table go through the abs-mcp curated layer (verified
filters), all inside ONE asyncio event loop. RBA market tiles (cash rate,
AUD/USD, 10yr bond) parse plain CSVs. News comes from Google News RSS (no key).
Every source is isolated: a failure keeps the previous value / list, so one
broken feed never blanks the dashboard or crashes the job.
"""

import asyncio
import csv
import io
import json
import os
import re
import sys
import datetime as dt
import xml.etree.ElementTree as ET
from urllib.parse import quote
from urllib.request import Request, urlopen

from metrics_config import ABS_METRICS, RBA_METRICS, STATE_TABLE, NEWS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
MANUAL_PATH = os.path.join(HERE, "manual_data.json")
UA = "nsw-shadow-dashboard/2.4 (Mozilla/5.0 compatible)"
TIMEOUT = 60


# ------------------------- period helpers ------------------------- #
def period_key(p):
    p = (p or "").strip()
    m = re.match(r"^(\d{4})-Q([1-4])$", p)
    if m: return (int(m.group(1)), int(m.group(2)) * 3)
    m = re.match(r"^(\d{4})-(\d{2})$", p)
    if m: return (int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(\d{4})$", p)
    if m: return (int(m.group(1)), 12)
    return (0, 0)

def periods_per_year(freq):
    return {"M": 12, "Q": 4, "A": 1}.get(freq, 1)


# ------------------------- transform ------------------------- #
def build_plot_series(series, metric):
    t = metric["transform"]
    if t in ("rate", "level"):
        return list(series)
    if t == "index_yoy":
        ppy = periods_per_year(metric["freq"]); out = []
        for i in range(ppy, len(series)):
            base = series[i - ppy][1]
            if base not in (None, 0):
                out.append((series[i][0], round((series[i][1] - base) / base * 100, 1)))
        return out
    return list(series)

def apply_transform(series, metric):
    plot = build_plot_series(series, metric)
    if not plot: raise ValueError("empty series")
    period, latest = plot[-1]
    prev = plot[-2][1] if len(plot) >= 2 else None
    ppy = periods_per_year(metric["freq"])
    yago = plot[-(ppy + 1)][1] if len(plot) > ppy else None
    is_level = metric["transform"] == "level"
    def pct(a, b): return None if (b in (None, 0)) else round((a - b) / b * 100, 1)
    def d(x): return "flat" if x in (None, 0) else ("up" if x > 0 else "down")
    if is_level:
        change, kind, value = pct(latest, prev), "pct", round(latest, 2)
        ya = None if yago is None else dict(value=round(yago, 2), change=pct(latest, yago), change_kind="pct", direction=d(pct(latest, yago)))
    else:
        change = None if prev is None else round(latest - prev, 1)
        kind, value = "pt", round(latest, 1)
        ya = None if yago is None else dict(value=round(yago, 1), change=round(latest - yago, 1), change_kind="pt", direction=d(round(latest - yago, 1)))
    return dict(value=value, period=period, change=change, change_kind=kind, direction=d(change),
                spark=[round(v, 2) for _, v in plot], spark_p=[p for p, _ in plot], year_ago=ya)


# ------------------------- ABS (single loop) ------------------------- #
async def _pull_one(dataset_id, filters, start):
    from abs_mcp.server import _get_data_impl
    resp = await _get_data_impl(dataset_id, filters, start, None, "records", last_n=None)
    return resp.records

def _records_to_series(records):
    s = []
    for r in records:
        v, p = getattr(r, "value", None), getattr(r, "period", None)
        if v is not None and p:
            s.append((p, float(v)))
    s.sort(key=lambda x: period_key(x[0]))
    return s

async def _build_state_table(st, start):
    rows_out = []
    for row in st["rows"]:
        vals = []
        for reg in st["columns"]:
            try:
                series = _records_to_series(await _pull_one(row["dataset_id"], {**row["filters"], "region": reg}, start))
                if not series:
                    vals.append(None)
                elif row["agg"] == "sum12_k":
                    vals.append(round(sum(v for _, v in series[-12:]) / 1000, row["dp"]))
                else:
                    vals.append(round(series[-1][1], row["dp"]))
            except Exception:
                vals.append(None)
        rows_out.append({"label": row["label"], "vals": vals})
    return {"columns": st["col_labels"], "rows": rows_out, "live": True}

async def _orchestrate(metrics, state_table):
    start = str(dt.date.today().year - 8)
    results, errors = {}, {}
    for m in metrics:
        try:
            res = apply_transform(_records_to_series(await _pull_one(m["dataset_id"], m["filters"], start)), m)
            res.update(id=m["id"], label=m["label"], section=m["section"], source="ABS", unit=m.get("unit", ""))
            results[m["id"]] = res
        except Exception as e:
            errors[m["id"]] = str(e)
    svs = None
    try:
        svs = await _build_state_table(state_table, start)
    except Exception as e:
        errors["state_table"] = str(e)
    try:
        from abs_mcp.server import reset_client_for_tests
        await reset_client_for_tests()
    except Exception:
        pass
    return results, errors, svs


# ------------------------- RBA ------------------------- #
def http_get(url):
    with urlopen(Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")

_MONTHS = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06","jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}
def _rba_ym(s):
    s = s.strip()
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if m: return f"{m.group(3)}-{_MONTHS.get(m.group(2).lower(),'00')}"
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", s)
    if m: return f"{m.group(1)}-{m.group(2)}"
    return None

def _rba_locate(text, series_id):
    rows = list(csv.reader(io.StringIO(text)))
    id_row = next((i for i, r in enumerate(rows) if r and r[0].strip().lower() in ("series id","series_id","mnemonic")), None)
    if id_row is None: raise ValueError("no 'Series ID' row")
    col = next((i for i, c in enumerate(rows[id_row]) if series_id.upper() in c.strip().upper()), None)
    if col is None: raise ValueError(f"series {series_id} not found")
    return rows, id_row, col

def parse_rba_csv(text, series_id):
    rows, id_row, col = _rba_locate(text, series_id)
    date_re = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$|^\d{4}-\d{2}-\d{2}$")
    lv = ld = None
    for r in rows[id_row+1:]:
        if r and date_re.match(r[0].strip()) and col < len(r) and r[col].strip():
            try: lv, ld = float(r[col].strip()), r[0].strip()
            except ValueError: pass
    if lv is None: raise ValueError("no values")
    return lv, ld

def parse_rba_monthly(text, series_id, keep=84):
    rows, id_row, col = _rba_locate(text, series_id)
    monthly = {}
    for r in rows[id_row+1:]:
        if not r or col >= len(r) or not r[col].strip(): continue
        ym = _rba_ym(r[0])
        if not ym: continue
        try: monthly[ym] = float(r[col].strip())
        except ValueError: pass
    keys = sorted(monthly)[-keep:]
    return [(k, monthly[k]) for k in keys]

def parse_sdmx_csv(text):
    """Zero-dependency fallback parser for ABS SDMX-CSV (kept for offline test)."""
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.lower(): c for c in (reader.fieldnames or [])}
    pcol, vcol = cols.get("time_period"), cols.get("obs_value")
    if not pcol or not vcol:
        raise ValueError(f"unexpected columns: {reader.fieldnames}")
    out = []
    for row in reader:
        raw, per = (row.get(vcol) or "").strip(), (row.get(pcol) or "").strip()
        if raw and per:
            try: out.append((per, float(raw)))
            except ValueError: pass
    out.sort(key=lambda x: period_key(x[0]))
    return out

def fetch_rba(metric):
    try:
        ser = parse_rba_monthly(http_get(metric["csv_url"]), metric["series_id"])
    except Exception:
        ser = parse_rba_monthly(http_get(metric["fallback_url"]), metric["series_id"])
    if not ser: raise ValueError("no RBA history")
    period, latest = ser[-1]; vals = [v for _, v in ser]
    ya = None
    if len(vals) > 12:
        yv = vals[-13]
        ya = dict(value=round(yv, 2), change=round(latest - yv, 1), change_kind="pt",
                  direction=("up" if latest > yv else "down" if latest < yv else "flat"))
    t = dict(id=metric["id"], label=metric["label"], section=metric["section"],
             source="RBA", unit=metric.get("unit", "%"), value=round(latest, 2),
             decimals=metric.get("decimals", 2), period=period, change=None,
             change_kind="pt", direction="flat",
             spark=[round(v, 2) for v in vals], spark_p=[p for p, _ in ser], year_ago=ya)
    if metric.get("prefix"): t["prefix"] = metric["prefix"]
    return t


# ------------------------- News (Google News RSS) ------------------------- #
def _news_date(rfc822):
    m = re.match(r"\w+,\s*(\d{1,2})\s+([A-Za-z]{3})", rfc822 or "")
    return f"{int(m.group(1))} {m.group(2)}" if m else ""

def parse_news_rss(xml_text, limit):
    root = ET.fromstring(xml_text)
    out = []
    for it in root.findall(".//item")[:limit]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        src_el = it.find("source")
        source = (src_el.text.strip() if src_el is not None and src_el.text else "")
        if source and title.endswith(" - " + source):
            title = title[:-(len(source) + 3)].strip()
        out.append({"title": title, "link": link, "source": source, "date": _news_date(pub)})
    return out

def _news_url(query, after=None, before=None):
    q = query
    if after:  q += f" after:{after}"
    if before: q += f" before:{before}"
    return f"https://news.google.com/rss/search?q={quote(q)}&hl=en-AU&gl=AU&ceid=AU:en"

def _news_items(query, after=None, before=None):
    return parse_news_rss(http_get(_news_url(query, after, before)), 200)

def _top_sources(items, k=3):
    from collections import Counter
    c = Counter(i["source"] for i in items if i["source"])
    return [{"source": s, "count": n} for s, n in c.most_common(k)]

def fetch_news():
    try:
        today = dt.date.today()
        d7  = (today - dt.timedelta(days=7)).isoformat()
        d14 = (today - dt.timedelta(days=14)).isoformat()
        tom = (today + dt.timedelta(days=1)).isoformat()
        this_all = _news_items(NEWS["base"], after=d7,  before=tom)
        last_all = _news_items(NEWS["base"], after=d14, before=d7)
        themes = []
        for label, q in NEWS.get("themes", {}).items():
            try:
                themes.append({"label": label, "count": len(_news_items(q, after=d7, before=tom))})
            except Exception:
                pass
        themes.sort(key=lambda x: -x["count"])
        return {
            "this_week":   this_all[:NEWS["max"]],
            "last_week":   last_all[:NEWS["max"]],
            "volume":      {"this": len(this_all), "last": len(last_all)},
            "top_sources": _top_sources(this_all),
            "themes":      themes,
        }
    except Exception:
        return None


# ------------------------- main ------------------------- #
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except (OSError, json.JSONDecodeError): return default

def main():
    dry = "--dry-run" in sys.argv[1:]
    prev = load_json(DATA_PATH, {})
    prev_tiles = prev.get("tiles", {})
    manual = load_json(MANUAL_PATH, {})
    tiles, errors = {}, []

    abs_results, abs_errors, live_svs = asyncio.run(_orchestrate(ABS_METRICS, STATE_TABLE))
    for m in ABS_METRICS:
        if m["id"] in abs_results:
            tiles[m["id"]] = abs_results[m["id"]]
            print(f"  ok   {m['id']:<12} {tiles[m['id']]['value']} ({tiles[m['id']]['period']})  spark={len(tiles[m['id']]['spark'])}")
        else:
            err = abs_errors.get(m["id"], "?"); errors.append(f"{m['id']}: {err}")
            if m["id"] in prev_tiles: tiles[m["id"]] = {**prev_tiles[m["id"]], "stale": True}
            print(f"  WARN {m['id']:<12} {err}")

    for m in RBA_METRICS:
        try:
            tiles[m["id"]] = fetch_rba(m)
            print(f"  ok   {m['id']:<12} {tiles[m['id']]['value']} ({tiles[m['id']]['period']})")
        except Exception as e:
            errors.append(f"{m['id']}: {e}")
            if m["id"] in prev_tiles: tiles[m["id"]] = {**prev_tiles[m["id"]], "stale": True}
            print(f"  WARN {m['id']:<12} {e}")

    for tid, t in manual.get("tiles", {}).items():
        tiles[tid] = {**t, "source": t.get("source", "Manual"), "id": tid}

    # live state table if it has any data, else manual fallback
    if live_svs and any(any(v is not None for v in r["vals"]) for r in live_svs["rows"]):
        state_vs_state = live_svs
    else:
        state_vs_state = manual.get("state_vs_state")
        if "state_table" in abs_errors: print(f"  WARN state_table   {abs_errors['state_table']}")

    news = fetch_news()
    if news is None:
        news = prev.get("news") or {}
        errors.append("news: fetch failed (kept previous)")
    n_this = len((news or {}).get("this_week", []))
    print(f"  news {n_this} headlines this week, {len((news or {}).get('themes', []))} themes")

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "state_vs_state": state_vs_state,
        "news": news,
        "tiles": tiles,
        "errors": errors,
    }
    print(f"\n{len(tiles)} tiles, {len(errors)} errors, state table {'live' if (state_vs_state or {}).get('live') else 'manual'}")
    if dry:
        print(json.dumps(out, indent=2)[:1200]); return
    with open(DATA_PATH, "w") as f: json.dump(out, f, indent=2)
    print(f"wrote {DATA_PATH}")

if __name__ == "__main__":
    main()
