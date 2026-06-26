"""
metrics_config.py
=================
The editable registry. ABS metrics use the *verified* abs-mcp curated vocabulary
(plain-English filters like region="nsw", measure="unemployment_rate") instead of
hand-built SDMX keys. abs-mcp fetches each dataflow's structure and builds the
positional key in the correct order, and tracks ABS dataflow changes (e.g. it
already routes the discontinued Retail Trade series to its live replacement, and
the Sydney CPI to the monthly indicator).

Every dataset_id + filter combination below was validated against the abs-mcp
curated definitions offline (see tests/test_config.py). transform values:
  rate       -> value is already a % (unemployment rate, or an ABS "change_year"
                series); change = latest minus previous obs (pt).
  index_yoy  -> value is a level/count; display through-the-year % change.
  level      -> display the latest raw value; change = % vs previous obs.
freq drives the year-ago lookup for index_yoy: M=12, Q=4, A=1.
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

    dict(id="pop", label="Population growth (NSW, TTY)", section="economy",
         dataset_id="ERP_Q", filters={"measure": "change_pct", "region": "nsw"},
         freq="Q", transform="rate", unit="%"),
]

# RBA: pulled from a plain CSV table, matched by Series ID (mnemonic).
RBA_METRICS = [
    dict(id="cash_rate", label="Cash rate target", section="economy",
         csv_url="https://www.rba.gov.au/statistics/tables/csv/f1.1-data.csv",
         fallback_url="https://www.rba.gov.au/statistics/tables/csv/f1-data.csv",
         series_id="FIRMMCRTD", unit="%", transform="rate"),
]

# Manual tiles live in manual_data.json and merge in unchanged.
# State Final Demand and GSP aren't in the abs-mcp curated set (its national
# accounts are Australia-only), so they're manual quarterly entries from the
# ABS State Accounts / NSW Budget papers. Property, NAB and the fiscal
# aggregates are manual as before.
MANUAL_TILES = ["sfd", "gsp", "nab",
                "house_median", "apt_median", "dwelling_index",
                "budget_result", "net_debt", "interest_expense", "credit_rating"]
