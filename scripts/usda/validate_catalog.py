"""Read-only synthetic accuracy/timing comparison. Never generate meals or write caches.

python -m scripts.usda.validate_catalog [--api] [--output /tmp/report.json]
--api uses the configured USDA key without printing it (24 public ingredient queries).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import httpx

from app.config import settings
from app.services.usda_local import LocalCatalog
from app.services.usda_nutrition import FDC_API_URL, FDC_DATA_TYPES, USDANutritionError, select_match

QUERIES = [
    "cod raw",
    "cod cooked",
    "salmon raw",
    "salmon cooked",
    "romaine lettuce raw",
    "lettuce raw",
    "banana raw",
    "apple raw",
    "egg whole raw",
    "egg whole cooked",
    "oats dry",
    "rice brown cooked",
    "rice brown raw",
    "quinoa cooked",
    "olive oil",
    "broccoli raw",
    "broccoli cooked",
    "spinach raw",
    "almonds raw",
    "chickpeas cooked",
    "lentils cooked",
    "yogurt plain",
    "milk whole",
    "tofu raw",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw = Path(settings.USDA_LOCAL_CATALOG_PATH).read_bytes()
    started = time.perf_counter()
    catalog = LocalCatalog(json.loads(raw))
    cold_ms = (time.perf_counter() - started) * 1000
    results = []
    with httpx.Client(timeout=20) as client:
        for query in QUERIES:
            durations = []
            match = None
            for _ in range(10):
                started = time.perf_counter()
                match = catalog.lookup(query)
                durations.append((time.perf_counter() - started) * 1000)
            row = {
                "query": query,
                "local_fdc_id": match.fdc_id if match else None,
                "local_ms_median": statistics.median(durations),
            }
            if args.api:
                if not settings.USDA_FDC_API_KEY:
                    raise RuntimeError("USDA API key required for comparison")
                started = time.perf_counter()
                response = client.post(
                    FDC_API_URL,
                    params={"api_key": settings.USDA_FDC_API_KEY},
                    json={"query": query, "dataType": FDC_DATA_TYPES, "pageSize": 20, "requireAllWords": False},
                )
                # Never raise an HTTP exception containing the credential-bearing URL.
                if response.status_code != 200:
                    raise RuntimeError(f"USDA comparison returned HTTP {response.status_code}")
                try:
                    remote = select_match(query, response.json().get("foods", []))
                except USDANutritionError:
                    remote = None
                row.update(
                    {
                        "api_ms": (time.perf_counter() - started) * 1000,
                        "api_fdc_id": remote.fdc_id if remote else None,
                        "same_match": match == remote,
                        "local_nutrients": match.nutrients_per_100g if match else None,
                        "api_nutrients": remote.nutrients_per_100g if remote else None,
                    }
                )
            results.append(row)
    report = {
        "release": catalog.release,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "foods": len(catalog.foods),
        "cold_load_ms": cold_ms,
        "results": results,
        "note": "Synthetic ingredient lookups only; not end-to-end generation or load test. Different IDs require review before enabling.",
    }
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
