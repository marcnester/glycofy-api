"""Immutable, versioned USDA snapshot; no network or customer data writes."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LocalCatalog:
    def __init__(self, payload: dict[str, Any]):
        from app.services.usda_nutrition import FDC_DATA_TYPES, _nutrients, _tokens

        if payload.get("schema") != 1 or not payload.get("release"):
            raise ValueError("Unsupported USDA snapshot")
        self.release = str(payload["release"])
        self.foods: dict[int, dict[str, Any]] = {}
        self.index: dict[str, set[int]] = {}
        for food in payload["foods"]:
            fdc_id = int(food["fdcId"])
            if fdc_id in self.foods or food["dataType"] not in FDC_DATA_TYPES or _nutrients(food) is None:
                raise ValueError("Invalid USDA snapshot record")
            values = _nutrients(food)
            if values is None or any(not math.isfinite(v) or v < 0 for v in values.values()):
                raise ValueError("Invalid USDA nutrient values")
            self.foods[fdc_id] = food
            for token in _tokens(food["description"]):
                self.index.setdefault(token, set()).add(fdc_id)
        if not self.foods:
            raise ValueError("Empty USDA snapshot")

    def lookup(self, query: str):
        from app.services.usda_nutrition import USDANutritionError, _tokens, select_match

        tokens = _tokens(query)
        required = 1 if len(tokens) <= 2 else max(2, (len(tokens) + 1) // 2)
        overlap: Counter[int] = Counter()
        for token in tokens:
            overlap.update(self.index.get(token, ()))
        ids = {fdc_id for fdc_id, count in overlap.items() if count >= required}
        # Never truncate candidates: omitted ties could hide nutritional ambiguity.
        try:
            return select_match(query, [self.foods[i] for i in sorted(ids)])
        except USDANutritionError:
            return None


@lru_cache(maxsize=4)
def load_catalog(path: str, expected_sha256: str) -> LocalCatalog | None:
    try:
        raw = Path(path).read_bytes()
        if not expected_sha256 or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("USDA snapshot checksum mismatch")
        return LocalCatalog(json.loads(raw))
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("usda_local_catalog_unavailable")
        return None


def lookup_local(query: str):
    from app.config import settings

    if not settings.USDA_LOCAL_CATALOG_ENABLED:
        return None
    catalog = load_catalog(settings.USDA_LOCAL_CATALOG_PATH, settings.USDA_LOCAL_CATALOG_SHA256)
    return catalog.lookup(query) if catalog else None
