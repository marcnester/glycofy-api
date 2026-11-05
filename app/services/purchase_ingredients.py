"""Purchase identities and retailer quantity checks, independent of meal nutrition."""

import re
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation


@dataclass(frozen=True)
class PurchaseIngredient:
    key: str
    label: str
    aliases: tuple[str, ...]
    note: str


PURCHASE_INGREDIENTS = (
    PurchaseIngredient(
        "cod fillet",
        "Cod fillets",
        (
            "cod fillet",
            "cod fillets",
            "raw cod fillet",
            "raw cod fillets",
            "cod fillet raw",
            "cod fillets raw",
            "frozen cod fillet",
            "frozen cod fillets",
            "frozen raw cod fillets",
            "fresh cod fillet",
            "fresh cod fillets",
            "cod fillet frozen",
            "cod fillets frozen",
        ),
        "Fresh or frozen plain cod fillets can cover these meals. Package weight does not tell us the number or size of fillets.",
    ),
    PurchaseIngredient(
        "romaine lettuce",
        "Romaine lettuce",
        (
            "lettuce",
            "romaine",
            "romaine lettuce",
            "fresh romaine lettuce",
            "romaine lettuce raw",
            "lettuce romaine",
            "romaine hearts",
            "romaine lettuce hearts",
        ),
        "Romaine is the shopping choice for generic lettuce in this list. Other named lettuce varieties stay separate.",
    ),
)
_LOOKUP = {alias: item for item in PURCHASE_INGREDIENTS for alias in item.aliases}


def purchase_ingredient(key: str) -> PurchaseIngredient | None:
    # Exact vocabulary only: never erase cooked/breaded forms or named varieties.
    return _LOOKUP.get(key)


_MASS = {
    "g": "1",
    "gram": "1",
    "grams": "1",
    "kg": "1000",
    "kilogram": "1000",
    "kilograms": "1000",
    "oz": "28.349523125",
    "ounce": "28.349523125",
    "ounces": "28.349523125",
    "lb": "453.59237",
    "lbs": "453.59237",
    "pound": "453.59237",
    "pounds": "453.59237",
}
_COUNT = {"item", "items", "ct", "count", "each"}
_PACKAGE = re.compile(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)


def _measure(quantity, unit):
    try:
        amount = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000"):
        return None
    unit = str(unit or "").strip().lower().rstrip(".")
    if unit in _MASS:
        factor = Decimal(_MASS[unit])
        return "mass", amount * factor, factor
    if unit in _COUNT:
        return "count", amount, Decimal(1)
    return None


def package_coverage(quantity, unit, size, packages=1):
    """Compare an approved shopping amount with a declared fixed package size.

    This checks quantity, not product suitability or dietary portion sizes.
    Count-only lettuce heads cannot be compared with grams without a known weight.
    """
    unknown = {"status": "unknown", "suggested_packages": None}
    required = _measure(quantity, unit)
    size = str(size or "").strip()
    match = _PACKAGE.fullmatch(size) if len(size) <= 80 else None
    if not required or not match or not isinstance(packages, int) or not 1 <= packages <= 99:
        return unknown
    multiplier, amount, package_unit = match.groups()
    package = _measure(Decimal(amount) * Decimal(multiplier or 1), package_unit)
    if not package or package[0] != required[0]:
        return unknown
    needed, per_pack, factor = required[1], package[1], required[2]
    suggested = int((needed / per_pack).to_integral_value(rounding=ROUND_CEILING))
    total = per_pack * packages
    return {
        "status": "sufficient" if total >= needed else "short",
        "required_quantity": float(needed / factor),
        "unit": unit,
        "quantity_per_package": float(per_pack / factor),
        "selected_quantity": float(total / factor),
        "shortfall": float(max(Decimal(0), needed - total) / factor),
        "extra": float(max(Decimal(0), total - needed) / factor),
        "suggested_packages": suggested if suggested <= 99 else None,
    }
