from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings

logger = logging.getLogger(__name__)

# Weekly planning already runs its seven model calls concurrently. Each call
# can then validate dozens of ingredients, so an unbounded nested executor can
# exhaust a small production web instance. This process-wide gate keeps FDC
# I/O parallel without allowing a request to starve health checks/job polling.
_FDC_CONCURRENCY = max(1, int(os.environ.get("USDA_FDC_MAX_CONCURRENCY", "3")))
_FDC_GATE = threading.BoundedSemaphore(_FDC_CONCURRENCY)
_VALIDATION_WORKER_LOCK = threading.Lock()
_VALIDATION_WORKER_STARTED = False

FDC_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]
NUTRIENT_IDS = {"protein_g": 1003, "carbs_g": 1005, "fat_g": 1004}
# Foundation Foods stopped publishing the legacy Energy nutrient (1008) in
# 2020. Prefer its food-specific Atwater calculation, then the general Atwater
# value, while retaining 1008 for SR Legacy and Survey foods.
ENERGY_NUTRIENT_IDS = (2048, 2047, 1008)
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


class USDAUnavailableError(USDANutritionError):
    """Raised when FoodData Central is temporarily unavailable."""


@dataclass(frozen=True)
class FDCMatch:
    fdc_id: int
    description: str
    data_type: str
    nutrients_per_100g: dict[str, float]


def _query_key(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())[:240]


def _catalog_match(query: str) -> FDCMatch | None:
    """Read shared verified evidence without making meal planning depend on it."""
    from app.db import SessionLocal
    from app.models import NutritionCatalogEntry

    key = _query_key(query)
    if not key:
        return None
    db = SessionLocal()
    try:
        row = db.query(NutritionCatalogEntry).filter(NutritionCatalogEntry.query_key == key).first()
        if row is None:
            return None
        nutrients = {name: float(row.nutrients_per_100g[name]) for name in ("kcal", "protein_g", "carbs_g", "fat_g")}
        return FDCMatch(
            fdc_id=row.fdc_id,
            description=row.description,
            data_type=row.data_type,
            nutrients_per_100g=nutrients,
        )
    except (KeyError, TypeError, ValueError, SQLAlchemyError):
        return None
    finally:
        db.close()


def _store_catalog_match(query: str, match: FDCMatch) -> None:
    from app.db import SessionLocal
    from app.models import NutritionCatalogEntry, NutritionValidationJob

    key = _query_key(query)
    if not key:
        return
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        row = db.query(NutritionCatalogEntry).filter(NutritionCatalogEntry.query_key == key).first()
        if row is None:
            row = NutritionCatalogEntry(query_key=key, created_at=now)
            db.add(row)
        row.fdc_id = match.fdc_id
        row.description = match.description
        row.data_type = match.data_type
        row.nutrients_per_100g = match.nutrients_per_100g
        row.source = "usda_fdc"
        row.verified_at = now
        row.updated_at = now
        queued = db.query(NutritionValidationJob).filter(NutritionValidationJob.query_key == key).first()
        if queued is not None:
            queued.status = "resolved"
            queued.last_error_code = None
            queued.resolved_at = now
            queued.updated_at = now
        db.commit()
    except SQLAlchemyError:
        # Another concurrent request may have inserted the same unique key.
        # The catalog is an optimization; never fail a valid USDA lookup here.
        db.rollback()
    finally:
        db.close()


def _validation_error_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "no unambiguous" in message:
        return "no_match"
    if "ambiguous" in message:
        return "ambiguous_match"
    if isinstance(exc, USDAUnavailableError):
        return "provider_unavailable"
    return "unresolved"


def enqueue_nutrition_validation(query: str, exc: Exception) -> None:
    """Deduplicate unresolved model-generated food descriptions for retry."""
    from app.db import SessionLocal
    from app.models import NutritionCatalogEntry, NutritionValidationJob

    key = _query_key(query)
    if not key:
        return
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        if db.query(NutritionCatalogEntry.id).filter(NutritionCatalogEntry.query_key == key).first():
            return
        row = db.query(NutritionValidationJob).filter(NutritionValidationJob.query_key == key).first()
        if row is None:
            row = NutritionValidationJob(
                query_key=key,
                status="queued",
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        elif row.status == "resolved":
            return
        row.last_error_code = _validation_error_code(exc)
        row.updated_at = now
        db.commit()
    except SQLAlchemyError:
        # Local tests and a narrow migration rollout can briefly precede the
        # queue tables. Planning remains available during that window.
        db.rollback()
    finally:
        db.close()


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
    # Energy is the authoritative completeness anchor. FDC search results often
    # omit a zero-valued macro entirely (water has no macros, oil commonly has
    # no carbohydrate/protein row). Accept omitted macros only when the listed
    # energy is already explained by the nutrients FDC did return; otherwise an
    # omitted row could be unknown rather than zero and must fail closed.
    energy = next((by_id[nutrient_id] for nutrient_id in ENERGY_NUTRIENT_IDS if nutrient_id in by_id), None)
    if energy is None:
        return None
    nutrients = {name: by_id.get(nutrient_id, 0.0) for name, nutrient_id in NUTRIENT_IDS.items()}
    nutrients = {"kcal": energy, **nutrients}
    missing_macros = [name for name in ("protein_g", "carbs_g", "fat_g") if NUTRIENT_IDS[name] not in by_id]
    if missing_macros:
        explained = 4 * nutrients["protein_g"] + 4 * nutrients["carbs_g"] + 9 * nutrients["fat_g"]
        tolerance = max(10.0, nutrients["kcal"] * 0.12)
        if abs(nutrients["kcal"] - explained) > tolerance:
            return None
    return nutrients


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
    # USDA descriptions commonly express recipe preparation using controlled
    # terms (for example, "dry heat") instead of the user's word ("baked").
    # Keep raw/cooked distinct, while accepting equivalent cooking methods.
    preparation_terms = {
        "raw": {"raw"},
        "cooked": {"cooked", "roasted", "boiled", "baked", "grilled", "steamed", "heat"},
        "roasted": {"roasted", "baked", "grilled", "heat"},
        "baked": {"baked", "roasted", "heat"},
        "grilled": {"grilled", "roasted", "heat"},
        "boiled": {"boiled", "steamed", "cooked", "heat"},
        "steamed": {"steamed", "boiled", "cooked", "heat"},
    }
    for preparation, equivalents in preparation_terms.items():
        if preparation in query_tokens and not (equivalents & description_tokens):
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


def _nutritionally_equivalent(left: dict[str, float], right: dict[str, float]) -> bool:
    """Return whether two USDA records are interchangeable for macro planning.

    FoodData Central frequently returns duplicate or near-duplicate records at
    the same relevance score (for example, two releases of the same staple).
    Rejecting those ties made common foods impossible to use.  We only accept a
    tie when every per-100g value is close enough that choosing either official
    record cannot materially change the planned meal.
    """
    tolerances = {
        "kcal": (10.0, 0.05),
        "protein_g": (1.0, 0.12),
        "carbs_g": (1.0, 0.12),
        "fat_g": (1.0, 0.12),
    }
    for nutrient, (absolute, relative) in tolerances.items():
        left_value = float(left[nutrient])
        right_value = float(right[nutrient])
        allowed = max(absolute, max(abs(left_value), abs(right_value)) * relative)
        if abs(left_value - right_value) > allowed:
            return False
    return True


def select_match(query: str, foods: list[dict[str, Any]]) -> FDCMatch:
    candidates: list[tuple[float, int, dict[str, Any], dict[str, float]]] = []
    for relevance_rank, food in enumerate(foods):
        score = _match_score(query, food)
        nutrients = _nutrients(food)
        if score is not None and nutrients is not None:
            candidates.append((score, relevance_rank, food, nutrients))
    if not candidates:
        raise USDANutritionError(f"No unambiguous USDA match for {query!r}")
    # FDC search results are relevance ordered. Preserve that authoritative
    # order instead of using an arbitrary database id as the tie breaker.
    candidates.sort(key=lambda row: (-row[0], row[1]))
    best_score, _, best, nutrients = candidates[0]
    tied = [candidate for candidate in candidates[1:] if candidate[0] == best_score]
    if tied and not all(_nutritionally_equivalent(nutrients, candidate[3]) for candidate in tied):
        raise USDANutritionError(f"Ambiguous USDA match for {query!r}")
    return FDCMatch(
        fdc_id=int(best["fdcId"]),
        description=str(best["description"]),
        data_type=str(best["dataType"]),
        nutrients_per_100g=nutrients,
    )


@lru_cache(maxsize=1024)
def lookup_food(query: str) -> FDCMatch:
    cached = _catalog_match(query)
    if cached is not None:
        return cached
    api_key = (settings.USDA_FDC_API_KEY or "").strip()
    if not api_key:
        raise USDANutritionError("USDA_FDC_API_KEY is not configured")
    response = None
    for attempt in range(3):
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
            break
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {429, 500, 502, 503, 504} and attempt < 2:
                logger.info(
                    "usda_request_retry",
                    extra={"provider": "usda_fdc", "status_code": status_code, "attempt": attempt + 1},
                )
                time.sleep(0.4 * (2**attempt))
                continue
            logger.warning(
                "usda_request_rejected",
                extra={"provider": "usda_fdc", "status_code": status_code},
            )
            if status_code in {429, 500, 502, 503, 504}:
                raise USDAUnavailableError("USDA FoodData Central is temporarily unavailable") from exc
            raise USDANutritionError("USDA FoodData Central rejected the request") from exc
        except httpx.HTTPError as exc:
            if attempt < 2:
                time.sleep(0.4 * (2**attempt))
                continue
            logger.warning("usda_request_unavailable", extra={"provider": "usda_fdc"})
            raise USDAUnavailableError("USDA FoodData Central is temporarily unavailable") from exc
    if response is None:  # defensive: every unsuccessful path above raises
        raise USDAUnavailableError("USDA FoodData Central is temporarily unavailable")
    match = select_match(query, response.json().get("foods", []))
    _store_catalog_match(query, match)
    return match


def resolve_foods(
    queries: list[str],
    *,
    max_workers: int = 2,
    max_live_lookups: int | None = None,
) -> dict[str, FDCMatch | USDANutritionError]:
    """Resolve cached foods plus a bounded number of live USDA descriptions."""
    normalized = sorted({query.strip().lower() for query in queries if query.strip()})
    if not normalized:
        return {}
    resolved: dict[str, FDCMatch | USDANutritionError] = {}
    pending: list[str] = []
    for query in normalized:
        cached = _catalog_match(query)
        if cached is not None:
            resolved[query] = cached
        else:
            pending.append(query)
    if max_live_lookups is not None:
        pending = pending[: max(0, max_live_lookups)]
    if not pending:
        return resolved
    worker_count = max(1, min(max_workers, len(pending)))
    provider_unavailable = False
    # Submit only one worker-sized batch at a time. If USDA is down, at most
    # one small batch consumes retries before the request switches to estimates.
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for offset in range(0, len(pending), worker_count):
            futures = {executor.submit(lookup_food, query): query for query in pending[offset : offset + worker_count]}
            for future in as_completed(futures):
                query = futures[future]
                try:
                    resolved[query] = future.result()
                except USDAUnavailableError as exc:
                    resolved[query] = exc
                    enqueue_nutrition_validation(query, exc)
                    provider_unavailable = True
                except USDANutritionError as exc:
                    resolved[query] = exc
                    enqueue_nutrition_validation(query, exc)
                except Exception:  # defensive boundary around third-party data
                    resolved[query] = USDAUnavailableError("USDA FoodData Central is temporarily unavailable")
                    provider_unavailable = True
            if provider_unavailable:
                break
    return resolved


def verify_ingredients_resilient(
    ingredients: list[dict[str, Any]],
    *,
    resolved_foods: dict[str, FDCMatch | USDANutritionError] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Verify what USDA can resolve and mark the remainder for background work.

    Invalid quantities still fail closed. Only a valid measured ingredient
    whose food identity could not be resolved is allowed to remain provisional.
    """
    output: list[dict[str, Any]] = []
    unresolved = 0
    for ingredient in ingredients:
        try:
            verified = verify_ingredients([ingredient], resolved_foods=resolved_foods)[0]
            output.append({**verified, "nutrition_status": "verified"})
        except USDANutritionError as exc:
            message = str(exc).lower()
            if "gram weight" in message or "cannot be verified" in message or "no ingredients" in message:
                raise
            query = str(ingredient.get("usda_search_query") or ingredient.get("name") or "").strip()
            enqueue_nutrition_validation(query, exc)
            output.append(
                {
                    **ingredient,
                    "nutrition_status": "pending_validation",
                    "nutrition": None,
                    "food_ref_id": None,
                    "nutrition_source": {"provider": "Glycofy estimate", "status": "pending_validation"},
                }
            )
            unresolved += 1
    return output, unresolved


def reconcile_nutrition_validation_queue(*, limit: int = 20) -> dict[str, int]:
    """Resolve due foods into the shared catalog with bounded retry backoff."""
    from app.db import SessionLocal
    from app.models import NutritionValidationJob

    now = datetime.utcnow()
    db = SessionLocal()
    try:
        rows = (
            db.query(NutritionValidationJob)
            .filter(
                NutritionValidationJob.status.in_(("queued", "retry")),
                NutritionValidationJob.next_attempt_at <= now,
            )
            .order_by(NutritionValidationJob.next_attempt_at.asc())
            .limit(max(1, min(limit, 100)))
            .all()
        )
        keys = [row.query_key for row in rows]
    except SQLAlchemyError:
        db.rollback()
        return {"processed": 0, "resolved": 0, "retry": 0}
    finally:
        db.close()

    resolved_count = 0
    retry_count = 0
    for key in keys:
        try:
            match = lookup_food(key)
            # Also resolves the queue row when the in-process LRU already had
            # the match and therefore bypassed lookup_food's storage path.
            _store_catalog_match(key, match)
            resolved_count += 1
            continue
        except USDANutritionError as exc:
            error_code = _validation_error_code(exc)

        db = SessionLocal()
        try:
            row = db.query(NutritionValidationJob).filter(NutritionValidationJob.query_key == key).first()
            if row is None or row.status == "resolved":
                continue
            row.attempt_count += 1
            row.status = "abandoned" if row.attempt_count >= 8 else "retry"
            row.last_error_code = error_code
            row.next_attempt_at = datetime.utcnow() + timedelta(
                minutes=min(24 * 60, 5 * (2 ** min(row.attempt_count, 8)))
            )
            row.updated_at = datetime.utcnow()
            db.commit()
            retry_count += int(row.status == "retry")
        except SQLAlchemyError:
            db.rollback()
        finally:
            db.close()
    return {"processed": len(keys), "resolved": resolved_count, "retry": retry_count}


def start_nutrition_validation_worker() -> bool:
    """Start one lightweight reconciliation loop per web process."""
    global _VALIDATION_WORKER_STARTED
    with _VALIDATION_WORKER_LOCK:
        if _VALIDATION_WORKER_STARTED:
            return False
        _VALIDATION_WORKER_STARTED = True

    interval_seconds = max(60, int(os.environ.get("NUTRITION_VALIDATION_INTERVAL_SECONDS", "900")))
    batch_size = max(1, int(os.environ.get("NUTRITION_VALIDATION_BATCH_SIZE", "20")))

    def run() -> None:
        while True:
            try:
                result = reconcile_nutrition_validation_queue(limit=batch_size)
                if result["processed"]:
                    logger.info("nutrition_validation_reconciliation", extra=result)
            except Exception:
                logger.exception("nutrition_validation_worker_failed")
            threading.Event().wait(interval_seconds)

    threading.Thread(target=run, name="nutrition-validation", daemon=True).start()
    return True


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


def fit_portions_to_targets(
    ingredients: list[dict[str, Any]],
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fit gram portions to macro targets using only USDA-derived evidence.

    The model chooses foods; this deterministic pass adjusts their serving
    weights. It cannot invent nutrition because each coefficient comes from
    the already verified per-ingredient USDA values.
    """
    macro_names = ("kcal", "protein_g", "carbs_g", "fat_g")
    try:
        target = [float(targets[name]) for name in macro_names]
    except (KeyError, TypeError, ValueError):
        return ingredients
    if any(value <= 0 for value in target):
        return ingredients

    evidence: list[list[float]] = []
    base_weights: list[float] = []
    adjustable: list[int] = []
    for index, ingredient in enumerate(ingredients):
        nutrition = ingredient.get("nutrition")
        try:
            weight = float(ingredient.get("amount_g"))
            values = [float(nutrition[name]) for name in macro_names]
        except (KeyError, TypeError, ValueError):
            return ingredients
        if weight <= 0 or any(value < 0 for value in values):
            return ingredients
        evidence.append(values)
        base_weights.append(weight)
        # Leave water, spices, and other nutritionally negligible additions at
        # their recipe amount. Portion-fit only foods that can move a target.
        if values[0] >= 20 or max(values[1:]) >= 2:
            adjustable.append(index)
    if not adjustable:
        return ingredients

    # Coordinate descent over serving multipliers minimizes normalized target
    # error with a small preference for the model's original practical amount.
    factors = [1.0 for _ in ingredients]
    normalized = [[value / target[pos] for pos, value in enumerate(row)] for row in evidence]
    regularization = 0.002
    for _ in range(60):
        for index in adjustable:
            contribution = normalized[index]
            other = [
                sum(normalized[row][metric] * factors[row] for row in range(len(ingredients)) if row != index)
                for metric in range(len(macro_names))
            ]
            numerator = (
                sum(contribution[metric] * (1.0 - other[metric]) for metric in range(len(macro_names))) + regularization
            )
            denominator = sum(value * value for value in contribution) + regularization
            factors[index] = min(4.0, max(0.25, numerator / denominator))

    fitted: list[dict[str, Any]] = []
    for index, ingredient in enumerate(ingredients):
        if index not in adjustable:
            fitted.append(ingredient)
            continue
        factor = factors[index]
        amount_g = round(base_weights[index] * factor, 1)
        if amount_g <= 0 or amount_g > 5000:
            return ingredients
        nutrition = {name: round(evidence[index][position] * factor, 1) for position, name in enumerate(macro_names)}
        fitted.append(
            {
                **ingredient,
                "amount": amount_g,
                "amount_g": amount_g,
                "unit": "g",
                "nutrition": nutrition,
            }
        )
    return fitted
