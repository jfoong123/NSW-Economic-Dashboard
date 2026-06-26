"""
Offline tests — no network. Prove the SDMX-CSV and RBA-CSV parsers and the
transform maths behave on real-format sample data. Run: python tests/test_parsers.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch_data import parse_sdmx_csv, parse_rba_csv, apply_transform, period_key

# --- Fixture 1: ABS SDMX-CSV, quarterly CPI index (shape ABS actually returns) ---
ABS_CPI_CSV = """DATAFLOW,FREQ,MEASURE,INDEX,TSEST,REGION,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE
ABS:CPI(1.1.0),Q,1,10001,10,50,2025-Q1,138.0,INDEX
ABS:CPI(1.1.0),Q,1,10001,10,50,2025-Q2,139.1,INDEX
ABS:CPI(1.1.0),Q,1,10001,10,50,2025-Q3,140.4,INDEX
ABS:CPI(1.1.0),Q,1,10001,10,50,2025-Q4,141.6,INDEX
ABS:CPI(1.1.0),Q,1,10001,10,50,2026-Q1,143.5,INDEX
"""

# --- Fixture 2: ABS monthly unemployment rate (a 'rate' series) ---
ABS_UNEMP_CSV = """DATAFLOW,FREQ,MEASURE,REGION,TIME_PERIOD,OBS_VALUE
ABS:LF(1.0.0),M,UNEMP,1,2026-03,4.0
ABS:LF(1.0.0),M,UNEMP,1,2026-04,3.9
ABS:LF(1.0.0),M,UNEMP,1,2026-05,4.1
"""

# --- Fixture 3: RBA-style CSV with metadata header block then dated rows ---
RBA_CSV = """Title,Cash Rate Target
Description,Cash Rate Target,Interbank Overnight Cash Rate
Frequency,Daily,Daily
Type,Original,Original
Units,Per cent,Per cent
Source,RBA,RBA
Series ID,FIRMMCRTD,FIRMMCRI
18-Jun-2026,4.35,4.34
19-Jun-2026,4.35,4.35
20-Jun-2026,4.35,4.35
"""


def approx(a, b, tol=0.05):
    return a is not None and abs(a - b) <= tol


def test_period_sort():
    assert period_key("2026-Q1") > period_key("2025-Q4")
    assert period_key("2026-05") > period_key("2026-04")
    assert period_key("2025") > period_key("2024")
    print("  ✓ period sorting across freqs")


def test_abs_index_yoy():
    series = parse_sdmx_csv(ABS_CPI_CSV)
    assert series[-1] == ("2026-Q1", 143.5), series[-1]
    r = apply_transform(series, dict(freq="Q", transform="index_yoy"))
    # YoY = (143.5-138.0)/138.0*100 = 3.986 -> 4.0
    assert approx(r["value"], 4.0), r
    assert r["direction"] in ("up", "down", "flat")
    print(f"  ✓ ABS SDMX-CSV parse + index_yoy  -> {r['value']}% ({r['period']})")


def test_abs_rate():
    series = parse_sdmx_csv(ABS_UNEMP_CSV)
    r = apply_transform(series, dict(freq="M", transform="rate"))
    assert approx(r["value"], 4.1), r
    assert approx(r["change"], 0.2), r          # 4.1 - 3.9
    assert r["direction"] == "up"
    print(f"  ✓ ABS rate series                -> {r['value']}% ({r['change']:+}pt)")


def test_rba():
    val, date = parse_rba_csv(RBA_CSV, "FIRMMCRTD")
    assert approx(val, 4.35), (val, date)
    assert date == "20-Jun-2026", date
    print(f"  ✓ RBA CSV parse                  -> {val}% ({date})")


if __name__ == "__main__":
    print("Running offline parser tests...")
    test_period_sort()
    test_abs_index_yoy()
    test_abs_rate()
    test_rba()
    print("\nAll tests passed ✅  — parsing + transform logic verified offline.")
