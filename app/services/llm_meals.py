from __future__ import annotations

import random


def _tile_foods():
    # minimal bank; extend later or plug real LLM
    return {
        "breakfast": [
            ("Greek yogurt 2%", 200, "g", 146, 20, 7, 3),
            ("Blueberries", 120, "g", 68, 1, 17, 0),
            ("Honey", 10, "g", 31, 0, 8, 0),
            ("Granola", 30, "g", 140, 3, 20, 5),
            ("Oatmeal (dry)", 60, "g", 228, 8, 39, 4),
            ("Banana", 120, "g", 105, 1, 27, 0),
        ],
        "lunch": [
            ("Cooked rice", 200, "g", 260, 5, 57, 1),
            ("Chicken breast", 150, "g", 248, 46, 0, 5),
            ("Broccoli", 120, "g", 42, 3, 8, 0),
            ("Olive oil", 10, "g", 90, 0, 0, 10),
            ("Tortilla (flour)", 50, "g", 160, 5, 28, 4),
            ("Black beans", 130, "g", 170, 10, 30, 1),
        ],
        "dinner": [
            ("Cooked pasta", 220, "g", 290, 11, 60, 2),
            ("Salmon", 150, "g", 280, 34, 0, 14),
            ("Spinach", 100, "g", 23, 3, 4, 0),
            ("Parmesan", 15, "g", 60, 6, 1, 4),
            ("Olive oil", 10, "g", 90, 0, 0, 10),
            ("Tomato sauce", 150, "g", 70, 2, 14, 1),
        ],
        "snack": [
            ("Apple", 180, "g", 95, 0, 25, 0),
            ("Peanut butter", 32, "g", 188, 8, 6, 16),
            ("Protein shake", 1, "scoop", 120, 24, 3, 2),
            ("Crackers", 30, "g", 140, 3, 22, 5),
        ],
    }


def _fit_to_allocation(items, target):
    # Greedy adjust: keep adding items until close to target kcal/macros
    meal = []
    totals = {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    while totals["kcal"] < target["kcal"] * 0.92 and len(meal) < 8:
        it = random.choice(items)
        meal.append(
            {
                "name": it[0],
                "qty": float(it[1]),
                "unit": it[2],
                "kcal": int(it[3]),
                "protein_g": int(it[4]),
                "carbs_g": int(it[5]),
                "fat_g": int(it[6]),
            }
        )
        for k in totals:
            totals[k] += meal[-1][k]
    # small tuning: remove last if we overshoot a lot
    if totals["kcal"] > target["kcal"] * 1.15 and len(meal) > 1:
        last = meal.pop()
        for k in totals:
            totals[k] -= last[k]
    return meal, totals


def propose_meals_simple(date_str: str, allocations: list[dict[str, int]]):
    bank = _tile_foods()
    result = []
    for a in allocations:
        slot = a["slot"]
        items, sums = _fit_to_allocation(bank[slot if slot in bank else "snack"], a)
        result.append(
            {
                "slot": slot,
                "title": slot.capitalize(),
                "instructions": "",
                "items": items,
            }
        )
    return {"date": date_str, "meals": result}
