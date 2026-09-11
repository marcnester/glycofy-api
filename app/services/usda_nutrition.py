from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Weekly planning already runs its seven model calls concurrently. Each call
# can then validate dozens of ingredients, so an unbounded nested executor can
# exhaust a small production web instance. This process-wide gate keeps FDC
# I/O parallel without allowing a request to starve health checks/job polling.
_FDC_CONCURRENCY = max(1, int(os.environ.get("USDA_FDC_MAX_CONCURRENCY", "8")))
_FDC_GATE = threading.BoundedSemaphore(_FDC_CONCURRENCY)

FDC_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]
NUTRIENT_IDS = {"kcal": 1008, "protein_g": 1003, "carbs_g": 1005, "fat_g": 1004}
_STOP_WORDS = {"fresh", "large", "medium", "small", "sliced", "diced", "chopped", "plain"}
_DISQUALIFIERS = {
    "baby food",
    "breaded",
    "canned",
    "dehydrated",
    "fast food",
    "fried",
    "frozen meal",
    "infant formula",
    "restaurant",
}


class USDANutritionError(RuntimeError):
    """Raised when authoritative nutrition cannot be established."""


@dataclass(frozen=True)
class FDCMatch:
    fdc_id: int
    description: str
    data_type: str
    nutrients_per_100g: dict[str, float]


def _canonical_token(token: str) -> str:
    aliases = {
        "chickpeas": "chickpea",
        "garbanzo": "chickpea",
        "yoghurt": "yogurt",
        "bellpeppers": "bellpepper",
    }
    token = aliases.get(token, token)
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(value: str) -> set[str]:
    return {
        _canonical_token(token)
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 1 and token not in _STOP_WORDS
    }


def _nutrients(food: dict[str, Any]) -> dict[str, float] | None:
    by_id = {
        int(item.get("nutrientId")): float(item.get("value"))
        for item in food.get("foodNutrients", [])
        if item.get("nutrientId") is not None and item.get("value") is not None
    }
    if any(nutrient_id not in by_id for nutrient_id in NUTRIENT_IDS.values()):
        return None
    return {name: by_id[nutrient_id] for name, nutrient_id in NUTRIENT_IDS.items()}


def _match_score(query: str, food: dict[str, Any]) -> float | None:
    query_tokens = _tokens(query)
    description = str(food.get("description") or "")
    description_tokens = _tokens(description)
    if not query_tokens:
        return None
    overlap = query_tokens & description_tokens
    # FDC descriptions use a controlled vocabulary (for example "Oil, olive")
    # that rarely contains every natural-language modifier emitted by a recipe.
    # Require the food identity to overlap strongly, while treating modifiers as
    # ranking signals instead of making otherwise valid foods impossible to match.
    required_overlap = 1 if len(query_tokens) <= 2 else max(2, (len(query_tokens) + 1) // 2)
    if len(overlap) < required_overlap:
        return None
    for preparation in ("raw", "cooked", "roasted", "boiled", "baked"):
        if preparation in query_tokens and preparation not in description_tokens:
            return None
    lowered_query = query.lower()
    lowered_description = description.lower()
    for term in _DISQUALIFIERS:
        if term in lowered_description and term not in lowered_query:
            return None
    missing = len(query_tokens - description_tokens)
    extra = len(description_tokens - query_tokens)
    type_bonus = 3.0 if food.get("dataType") == "Foundation" else 1.0
    return (10.0 * len(overlap)) - (4.0 * missing) - extra + type_bonus


def select_match(query: str, foods: list[dict[str, Any]]) -> FDCMatch:
    candidates: list[tuple[float, dict[str, Any], dict[str, float]]] = []
    for food in foods:
        score = _match_score(query, food)
        nutrients = _nutrients(food)
        if score is not None and nutrients is not None:
            candidates.append((score, food, nutrients))
    if not candidates:
        raise USDANutritionError(f"No unambiguous USDA match for {query!r}")
    candidates.sort(key=lambda row: (-row[0], int(row[1].get("fdcId") or 0)))
    best_score, best, nutrients = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_score:
        raise USDANutritionError(f"Ambiguous USDA match for {query!r}")
    return FDCMatch(
        fdc_id=int(best["fdcId"]),
        description=str(best["description"]),
        data_type=str(best["dataType"]),
        nutrients_per_100g=nutrients,
    )


@lru_cache(maxsize=1024)
def lookup_food(query: str) -> FDCMatch:
    api_key = (settings.USDA_FDC_API_KEY or "").strip()
    if not api_key:
        raise USDANutritionError("USDA_FDC_API_KEY is not configured")
    try:
        with _FDC_GATE, httpx.Client(timeout=settings.USDA_FDC_TIMEOUT_SECONDS) as client:
            response = client.post(
                FDC_API_URL,
                params={"api_key": api_key},
                json={
                    "query": query,
                    "dataType": FDC_DATA_TYPES,
                    "pageSize": 20,
                    "requireAllWords": False,
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "usda_request_rejected",
            extra={"provider": "usda_fdc", "status_code": exc.response.status_code},
        )
        raise USDANutritionError("USDA FoodData Central rejected the request") from exc
    except httpx.HTTPError as exc:
        logger.warning("usda_request_unavailable", extra={"provider": "usda_fdc"})
        raise USDANutritionError("USDA FoodData Central is temporarily unavailable") from exc
    return select_match(query, response.json().get("foods", []))


def resolve_foods(queries: list[str], *, max_workers: int = 2) -> dict[str, FDCMatch | USDANutritionError]:
    """Resolve unique USDA descriptions concurrently for a whole planning request."""
    normalized = sorted({query.strip().lower() for query in queries if query.strip()})
    if not normalized:
        return {}
    resolved: dict[str, FDCMatch | USDANutritionError] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(normalized))) as executor:
        futures = {executor.submit(lookup_food, query): query for query in normalized}
        for future in as_completed(futures):
            query = futures[future]
            try:
                resolved[query] = future.result()
            except USDANutritionError as exc:
                resolved[query] = exc
            except Exception:  # defensive boundary around third-party data
                resolved[query] = USDANutritionError("USDA FoodData Central is temporarily unavailable")
    return resolved


def verify_ingredients(
    ingredients: list[dict[str, Any]],
    *,
    resolved_foods: dict[str, FDCMatch | USDANutritionError] | None = None,
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    if not ingredients:
        raise USDANutritionError("Recipe has no ingredients")
    for ingredient in ingredients:
        name = str(ingredient.get("name") or "").strip()
        query = str(ingredient.get("usda_search_query") or name).strip()
        try:
            amount_g = float(ingredient.get("amount_g"))
            display_amount = float(ingredient.get("amount"))
        except (TypeError, ValueError) as exc:
            raise USDANutritionError(f"Ingredient {name!r} has no valid gram weight") from exc
        if (
            not name
            or not query
            or str(ingredient.get("unit") or "").strip().lower() not in {"g", "gram", "grams"}
            or abs(display_amount - amount_g) > 0.1
            or not 0 < amount_g <= 5000
        ):
            raise USDANutritionError(f"Ingredient {name!r} cannot be verified")
        normalized_query = query.lower()
        match = resolved_foods.get(normalized_query) if resolved_foods is not None else lookup_food(normalized_query)
        # The model supplies a USDA-oriented description, but a concise recipe
        # name can be a better FDC search for foods whose controlled name omits
        # culinary modifiers. Both candidates are resolved up front in weekly
        # mode, so this fallback adds no request-path latency.
        normalized_name = name.lower()
        if isinstance(match, USDANutritionError) and normalized_name != normalized_query:
            match = resolved_foods.get(normalized_name) if resolved_foods is not None else lookup_food(normalized_name)
        if isinstance(match, USDANutritionError):
            raise match
        if match is None:
            raise USDANutritionError(f"No USDA result was resolved for {query!r}")
        factor = amount_g / 100.0
        nutrition = {key: round(value * factor, 1) for key, value in match.nutrients_per_100g.items()}
        verified.append(
            {
                **ingredient,
                "amount_g": round(amount_g, 1),
                "nutrition": nutrition,
                "food_ref_id": str(match.fdc_id),
                "nutrition_source": {
                    "provider": "USDA FoodData Central",
                    "fdc_id": match.fdc_id,
                    "description": match.description,
                    "data_type": match.data_type,
                    "basis": "per_100g_scaled_to_exact_weight",
                },
            }
        )
    return verified
