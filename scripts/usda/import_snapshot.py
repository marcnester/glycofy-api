"""Compile extracted USDA CSV releases into a deterministic runtime snapshot.

Usage: python -m scripts.usda.import_snapshot --release 2026-04 --output /tmp/catalog.json DIR [DIR ...]
Input directories must each contain food.csv, nutrient.csv and food_nutrient.csv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

TYPES = {"foundation_food": "Foundation", "sr_legacy_food": "SR Legacy", "survey_fndds_food": "Survey (FNDDS)"}
IDS = {1003, 1004, 1005, 1008, 2047, 2048}


def rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        yield from csv.DictReader(stream)


def compile_snapshot(directories, release):
    foods = {}
    sources = []
    for directory in directories:
        if directory.is_file():
            with directory.open("rb") as stream:
                sources.append({"file": directory.name, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()})
            payload = json.loads(directory.read_text())
            for collection in payload.values():
                for row in collection:
                    if row.get("dataType") not in TYPES.values():
                        continue
                    food = {
                        "fdcId": int(row["fdcId"]),
                        "description": row["description"],
                        "dataType": row["dataType"],
                        "foodNutrients": [
                            {"nutrientId": n["nutrient"]["id"], "value": n["amount"]}
                            for n in row["foodNutrients"]
                            if n["nutrient"]["id"] in IDS and n.get("amount") is not None
                        ],
                    }
                    if food["fdcId"] in foods:
                        raise ValueError("Duplicate FDC ID")
                    foods[food["fdcId"]] = food
            continue
        for name in ("food.csv", "nutrient.csv", "food_nutrient.csv"):
            path = directory / name
            sources.append(
                {
                    "file": str(directory.name + "/" + name),
                    "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
                }
            )
        nutrient_ids = {r["id"]: int(r["id"]) for r in rows(directory / "nutrient.csv") if int(r["id"]) in IDS}
        current = {}
        for row in rows(directory / "food.csv"):
            if row["data_type"] in TYPES:
                current[int(row["fdc_id"])] = {
                    "fdcId": int(row["fdc_id"]),
                    "description": row["description"],
                    "dataType": TYPES[row["data_type"]],
                    "foodNutrients": [],
                }
        for row in rows(directory / "food_nutrient.csv"):
            food = current.get(int(row["fdc_id"]))
            if food is not None and row["nutrient_id"] in nutrient_ids and row["amount"]:
                food["foodNutrients"].append(
                    {"nutrientId": nutrient_ids[row["nutrient_id"]], "value": float(row["amount"])}
                )
        for key, food in current.items():
            if key in foods and foods[key] != food:
                raise ValueError("Conflicting FDC IDs across releases")
            foods[key] = food
    from app.services.usda_nutrition import _nutrients

    complete = [
        food
        for _, food in sorted(foods.items())
        if (values := _nutrients(food)) is not None and all(math.isfinite(v) and v >= 0 for v in values.values())
    ]
    if not complete:
        raise ValueError("No complete core USDA foods")
    return {
        "schema": 1,
        "release": release,
        "sources": sources,
        "foods": complete,
        "excluded_incomplete": len(foods) - len(complete),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = compile_snapshot(args.directories, args.release)
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_bytes(raw)
    temporary.replace(args.output)
    print(
        json.dumps({"foods": len(payload["foods"]), "sha256": hashlib.sha256(raw).hexdigest(), "release": args.release})
    )


if __name__ == "__main__":
    main()
