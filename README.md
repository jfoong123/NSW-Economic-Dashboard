# Shadow Treasurer's Dashboard (NSW)

A weekly economic-accountability dashboard for the NSW opposition, built on
**official ABS and RBA data**. The ABS tiles refresh themselves through a
verified data layer; a handful of tiles that have no free feed are a few minutes
of manual entry per release.

```
fetch_data.py        Engine: pulls ABS (via abs-mcp) + RBA, writes data.json
metrics_config.py    Registry: edit here to add/change an auto tile
manual_data.json     Manual tiles + talking points + state table
data.json            Output read by the dashboard (a seed ships so it renders day 1)
index.html           The dashboard (static; reads data.json)
.github/workflows/   Weekly scheduler (GitHub Actions)
tests/               Offline tests: parsers/transforms + config validation
```

## How the ABS pull works (and why it's reliable)

ABS dimension codes are non-obvious and the positional SDMX key must be built in
the exact order the API's data-structure defines — easy to get subtly wrong by
hand. So instead of hand-rolling query URLs, the ABS tiles go through
[`abs-mcp`](https://pypi.org/project/abs-mcp/) (MIT, pinned to `0.13.8`), which
fetches each dataflow's structure, builds the key correctly, and tracks ABS
dataflow changes. Each tile is just a dataset + plain-English filters, e.g.:

```python
dict(id="unemp_nsw", dataset_id="LF",
     filters={"measure": "unemployment_rate", "region": "nsw"}, ...)
```

Every config was validated against the curated vocabulary offline
(`tests/test_config.py`), and the parsing/transform maths is covered by
`tests/test_parsers.py`. Two ABS changes this layer already handles for you: the
**Retail Trade** series was discontinued (Jul 2025) — spending now comes from the
Monthly Household Spending Indicator (`HSI_M`); and the **Sydney CPI** lives in
the monthly indicator, not the quarterly series.

If `abs-mcp` is ever unavailable, each tile simply keeps its **last-known value**
(flagged `stale`); the dashboard never blanks or crashes. The pin means a
breaking upstream change can't silently alter your numbers.

## Quick start

```bash
pip install -r requirements.txt
python tests/test_parsers.py     # offline: parsing + transform maths
python tests/test_config.py      # offline: every ABS filter resolves
python fetch_data.py --dry-run   # live pull, print only
python fetch_data.py             # write data.json
python -m http.server 8000       # view at http://localhost:8000
```

`index.html` opened directly renders the seed; served over http it overlays the
live `data.json`.

## Deploy (free, no server)

1. Push to a GitHub repo.
2. **Settings → Pages →** deploy from `main` / root (hosts `index.html`).
3. **Actions** is pre-configured: the workflow runs Mondays ~06:00 Sydney,
   installs deps, runs the tests, pulls data, and commits `data.json`. Trigger it
   manually any time from the **Actions** tab. The commit history is your audit
   trail of exactly what the official numbers were each week.

## What's automated vs manual

| Auto (verified) | Source |
|---|---|
| GDP growth (national), NSW + AUS unemployment, NSW employment growth, Sydney CPI, NSW wage growth, NSW household spending, NSW dwelling approvals, NSW population growth | ABS via `abs-mcp` |
| Cash rate | RBA Table F1.1 (CSV) |

| Manual (per release) | Source / cadence |
|---|---|
| State Final Demand, Gross State Product | ABS State Accounts / NSW Budget — quarterly/annual |
| Sydney house / apartment / dwelling medians | Cotality or Domain — monthly |
| Business conditions | NAB Monthly Business Survey |
| Budget result, net debt, interest expense, credit rating | NSW Budget & Half-Yearly Review — twice yearly |

State Final Demand and GSP aren't in the verified curated set (its national
accounts are Australia-only), so they're manual for now. They can be added as
auto tiles later via raw SDMX once their exact dataflow keys are confirmed in the
ABS Data Explorer — but manual quarterly entry is the safe default.

Property monthly medians are the one paywalled gap. Given the committee's Domain
connections, a small Domain Developer API feed is the natural upgrade; otherwise
it's a 2-minute monthly entry from the public Cotality/Domain release.


## Colour convention

`direction` (up/down/flat) sets a tile's colour and is **semantic** — "unemployment
rising" is `down` (a bad reading), not green. The auto tiles currently colour by
the raw sign of the change; for full good/bad semantics on them, add a `polarity`
field per metric in `metrics_config.py` and invert it in `apply_transform`.
