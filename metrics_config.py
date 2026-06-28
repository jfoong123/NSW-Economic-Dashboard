"""
metrics_config.py
=================
The editable registry. ABS metrics use the verified abs-mcp curated vocabulary
(plain-English filters). RBA metrics parse plain CSV tables by Series ID. All
values validated against the curated definitions offline (tests/test_config.py).

transform: rate | index_yoy | level   (see fetch_data.py)
freq: M | Q | A   (drives the year-ago lookup)
"""

# id, label, section, dataset_id, filters, freq, transform, unit
ABS_METRICS = [
    dict(id="gdp_nat",   label="GDP growth (national, QoQ)", section="economy",
         dataset_id="ANA_AGG", filters={"measure": "change", "series": "gdp"},
         freq="Q", transform="rate", unit="%"),
    dict(id="unemp_nsw", label="Unemployment rate (NSW)", section="economy",
         dataset_id="LF", filters={"measure": "unemployment_rate", "region": "nsw"},
         freq="M", transform="rate", unit="%"),
    dict(id="unemp_aus", label="Unemployment rate (AUS)", section="economy",
         dataset_id="LF", filters={"measure": "unemployment_rate", "region": "australia"},
         freq="M", transform="rate", unit="%"),
    dict(id="emp_growth", label="Employment growth (NSW, TTY)", section="economy",
         dataset_id="LF", filters={"measure": "employed_persons", "region": "nsw"},
         freq="M", transform="index_yoy", unit="%"),
    dict(id="cpi", label="CPI inflation (Sydney, TTY)", section="economy",
         dataset_id="CPI_MONTHLY",
         filters={"measure": "change_year", "category": "all_groups", "region": "sydney"},
         freq="M", transform="rate", unit="%"),
    dict(id="wpi", label="Wage Price Index (NSW, TTY)", section="economy",
         dataset_id="WPI", filters={"measure": "change_year", "region": "nsw"},
         freq="Q", transform="rate", unit="%"),
    dict(id="spending", label="Household spending (NSW, TTY)", section="economy",
         dataset_id="HSI_M",
         filters={"measure": "household_spending_yoy", "category": "total", "region": "nsw"},
         freq="M", transform="rate", unit="%"),
    dict(id="approvals", label="Dwelling approvals (NSW, monthly)", section="economy",
         dataset_id="BA_GCCSA",
         filters={"measure": "dwelling_units", "region": "nsw", "building_type": "total_residential"},
         freq="M", transform="level", unit=""),
    dict(id="completions", label="Dwelling completions (NSW, qtrly)", section="economy",
         dataset_id="BUILDING_ACTIVITY",
         filters={"measure": "dwelling_completions", "region": "nsw",
                  "building_type": "total_dwellings", "sector": "all_sectors"},
         freq="Q", transform="level", unit=""),
    dict(id="pop", label="Population growth (NSW, TTY)", section="economy",
         dataset_id="ERP_Q", filters={"measure": "change_pct", "region": "nsw"},
         freq="Q", transform="rate", unit="%"),
]

# RBA: parsed from plain CSV tables, matched by Series ID (mnemonic).
RBA_BASE = "https://www.rba.gov.au/statistics/tables/csv/"
RBA_METRICS = [
    dict(id="cash_rate", label="Cash rate target", section="economy",
         csv_url=RBA_BASE + "f1.1-data.csv", fallback_url=RBA_BASE + "f1-data.csv",
         series_id="FIRMMCRTD", unit="%", decimals=2),
    dict(id="fx", label="AUD / USD", section="markets",
         csv_url=RBA_BASE + "f11.1-data.csv", fallback_url=RBA_BASE + "f11.1-data.csv",
         series_id="FXRUSD", unit="", decimals=2, prefix="US$"),
    dict(id="bond10", label="10yr Govt bond yield", section="markets",
         csv_url=RBA_BASE + "f2.1-data.csv", fallback_url=RBA_BASE + "f2-data.csv",
         series_id="FCMYGBAG10", unit="%", decimals=2),
]

# Live state-vs-state comparison. 5 rows that exist for every state in the
# curated vocab (State Final Demand isn't available by state, so it's omitted).
STATE_TABLE = {
    "columns":    ["nsw", "vic", "qld", "sa", "wa", "tas", "australia"],
    "col_labels": ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "AUS"],
    "rows": [
        dict(label="Unemployment rate (%)",        dataset_id="LF",
             filters={"measure": "unemployment_rate"}, agg="latest", dp=1),
        dict(label="Household spending (TTY %)",    dataset_id="HSI_M",
             filters={"measure": "household_spending_yoy", "category": "total"}, agg="latest", dp=1),
        dict(label="Building approvals (12m, '000)", dataset_id="BA_GCCSA",
             filters={"measure": "dwelling_units", "building_type": "total_residential"},
             agg="sum12_k", dp=1),
        dict(label="Population (y/y %)",            dataset_id="ERP_Q",
             filters={"measure": "change_pct"}, agg="latest", dp=1),
        dict(label="Wage growth (TTY %)",           dataset_id="WPI",
             filters={"measure": "change_year"}, agg="latest", dp=1),
    ],
}

# Weekly news digest via Google News RSS (no API key needed). Edit the query freely.
NEWS = {
    "query": "NSW economy OR NSW budget OR NSW treasurer OR NSW cost of living",
    "max": 6,
}

MANUAL_TILES = ["sfd", "gsp", "nab",
                "house_median", "apt_median", "dwelling_index",
                "budget_result", "net_debt", "interest_expense", "credit_rating"]
