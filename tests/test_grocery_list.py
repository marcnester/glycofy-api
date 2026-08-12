from app.routers.plans import _grocery_category, _grocery_name, _grocery_unit


def test_grocery_name_normalizes_aliases_and_whitespace():
    assert _grocery_name("  Mixed   Berries  ") == ("mixed berries", "Mixed berries")
    assert _grocery_name("berries") == ("mixed berries", "Mixed berries")
    assert _grocery_name("eggs") == ("egg", "Eggs")


def test_grocery_unit_normalizes_common_plural_units():
    assert _grocery_unit("Tablespoons") == "tbsp"
    assert _grocery_unit("ounces") == "oz"
    assert _grocery_unit("cups") == "cup"


def test_grocery_category_uses_whole_words_and_explicit_metadata():
    assert _grocery_category("Baby spinach") == "Produce"
    assert _grocery_category("Chicken breast") == "Meat & Seafood"
    assert _grocery_category("Veggie mix") == "Other"
    assert _grocery_category("Anything", {"category": "Frozen"}) == "Frozen"
