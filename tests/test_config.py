"""
Offline config validation. Confirms every ABS metric in metrics_config.py uses a
dataset_id and filter values that exist in the abs-mcp curated vocabulary — so a
typo is caught here, before it ever hits the live API. Skips cleanly if abs-mcp
isn't installed (e.g. a quick local check without the dependency).
Run: python tests/test_config.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from metrics_config import ABS_METRICS

try:
    from abs_mcp import curated
except ImportError:
    print("abs-mcp not installed — skipping config validation (CI installs it).")
    sys.exit(0)

fails = []
for m in ABS_METRICS:
    cd = curated.get(m["dataset_id"])
    if not cd:
        fails.append(f"{m['id']}: dataset '{m['dataset_id']}' not in curated registry")
        continue
    for k, v in m["filters"].items():
        dim = cd.dimensions.get(k)
        if not dim:
            fails.append(f"{m['id']}: unknown filter '{k}' for {m['dataset_id']}")
        elif dim.values and v not in dim.values and not dim.permissive:
            fails.append(f"{m['id']}: {k}='{v}' not a valid value for {m['dataset_id']}")
    print(f"  {'✓' if not fails or m['id'] not in fails[-1] else '✗'} {m['id']:<12} {m['dataset_id']} {m['filters']}")

if fails:
    print("\nCONFIG ERRORS:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print(f"\nAll {len(ABS_METRICS)} ABS metric configs valid against abs-mcp curated vocabulary ✅")
