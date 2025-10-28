# app/services/planner.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import date

# ---- Data models ----

MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack")

@dataclass
class Targets:
    date: str
    tdee_kcal: int
    training_kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int

@dataclass
class Meal:
    title: str
    meal_type: str  # "breakfast" | "lunch" | "dinner" | "snack"
    kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int
    ingredients: List[str]
    instructions: str
    diet_tags: List[str]

@dataclass
class DayPlan:
    date: str
    locked: bool
    targets: Targets
    meals: List[Meal]
    grocery_list: List[str]

# ---- Helpers ----

def _clamp_int(v: float, lo: int, hi: int) -> int:
    iv = int(round(v))
    return max(lo, min(hi, iv))

def _mifflin_st_jeor(sex: str, weight_kg: float, height_cm: float, age_years: int) -> float:
    # BMR (Mifflin-St Jeor)
    if (sex or "").lower().startswith("f"):
        return 10 * weight_kg + 6.25 * height_cm - 5 * age_years - 161
    # default male if unknown
    return 10 * weight_kg + 6.25 * height_cm - 5 * age_years + 5

def compute_targets(
    *,
    sex: str,
    height_cm: float,
    weight_kg: float,
    age_years: int,
    goal: str,
    training_kcal: int
) -> Targets:
    """
    Compute maintenance + training, then macro split:
      - Protein ~ 2.0 g/kg
      - Fat ~ 0.9 g/kg
      - Carbs fill remainder
    """
    # Baseline activity factor (NEAT + light activity)
    bmr = _mifflin_st_jeor(sex, weight_kg, height_cm, age_years)
    maint = bmr * 1.45  # moderate default
    goal_adj = 0
    g = (goal or "").lower()
    if g == "cut":
        goal_adj = -300
    elif g == "gain":
        goal_adj = +300

    total_kcal = maint + training_kcal + goal_adj

    protein_g = weight_kg * 2.0
    fat_g = weight_kg * 0.9
    # kcal from P/F, remainder -> carbs (4 kcal/g)
    kcal_pf = protein_g * 4 + fat_g * 9
    carbs_g = max(0.0, (total_kcal - kcal_pf) / 4.0)

    return Targets(
        date=date.today().isoformat(),
        tdee_kcal=_clamp_int(total_kcal, 1200, 6000),
        training_kcal=int(round(training_kcal)),
        protein_g=int(round(protein_g)),
        carbs_g=int(round(carbs_g)),
        fat_g=int(round(fat_g)),
    )

# Very small “template” cookbook for MVP
# Each entry returns a Meal for a given kcal target fraction.
def _templates_for_diet(diet_pref: str) -> Dict[str, List[Tuple[str, List[str], str, List[str]]]]:
    """
    Returns per-meal templates: title, ingredients, instructions, tags
    """
    omni = {
        "breakfast": [
            ("Greek Yogurt + Berries + Granola",
             ["1 cup Greek yogurt", "1/2 cup berries", "1/4 cup granola", "honey (optional)"],
             "Combine in bowl.", ["omnivore", "vegetarian"]),
            ("Eggs + Toast",
             ["2 eggs", "2 slices whole-grain toast", "butter or olive oil"],
             "Scramble eggs; toast bread; serve.", ["omnivore"]),
        ],
        "lunch": [
            ("Chicken Rice Bowl",
             ["6 oz chicken breast", "1 cup cooked rice", "mixed greens", "vinaigrette"],
             "Grill chicken; assemble bowl.", ["omnivore"]),
            ("Turkey Sandwich + Fruit",
             ["2 slices whole-grain bread", "4 oz turkey", "lettuce", "tomato", "mustard", "1 fruit"],
             "Build sandwich; serve with fruit.", ["omnivore"]),
        ],
        "dinner": [
            ("Salmon + Rice + Veg",
             ["6 oz salmon", "1 cup cooked rice", "1–2 cups veggies", "soy or teriyaki"],
             "Bake salmon; steam/sauté veggies; serve.", ["pescatarian", "omnivore"]),
            ("Beef Stir-Fry + Rice",
             ["6 oz lean beef", "1 cup cooked rice", "stir-fry veggies", "teriyaki"],
             "Stir-fry beef+veg; serve with rice.", ["omnivore"]),
        ],
        "snack": [
            ("Banana + PB",
             ["1 banana", "2 tbsp peanut butter"],
             "Slice banana; add PB.", ["omnivore", "vegetarian", "vegan"]),
            ("Protein Shake",
             ["1 scoop whey protein", "water or milk"],
             "Shake well.", ["omnivore"]),
        ],
    }
    pesc = {
        "breakfast": [
            ("Smoked Salmon Toast",
             ["2 slices sourdough", "3 oz smoked salmon", "1/2 avocado", "capers", "tomato"],
             "Toast; top with avocado, salmon, tomato, capers.", ["pescatarian", "omnivore"]),
            ("Greek Yogurt + Berries + Granola",
             ["1 cup Greek yogurt", "1/2 cup berries", "1/4 cup granola", "honey (optional)"],
             "Combine in bowl.", ["pescatarian", "vegetarian"]),
        ],
        "lunch": [
            ("Salmon Rice Bowl",
             ["6 oz salmon", "1 cup cooked rice", "edamame", "seaweed salad", "soy/teriyaki"],
             "Bake salmon; assemble bowl.", ["pescatarian", "omnivore"]),
            ("Tuna Wrap",
             ["1 whole-grain wrap", "1 can tuna", "lettuce", "tomato", "mustard"],
             "Mix tuna; wrap with veg.", ["pescatarian"]),
        ],
        "dinner": [
            ("Teriyaki Salmon + Rice + Bok Choy",
             ["6 oz salmon", "1 cup cooked jasmine rice", "1 cup bok choy", "teriyaki sauce"],
             "Bake salmon; steam bok choy; serve.", ["pescatarian", "omnivore"]),
            ("Shrimp Pasta",
             ["6 oz shrimp", "2 cups cooked pasta", "garlic", "olive oil", "lemon"],
             "Sauté shrimp; toss pasta with oil & lemon.", ["pescatarian"]),
        ],
        "snack": [
            ("Roasted Edamame",
             ["1 cup shelled edamame", "salt", "oil spray"],
             "Roast 12–15 min at 400°F.", ["vegan", "pescatarian", "omnivore"]),
            ("Protein Shake",
             ["1 scoop whey protein", "water or milk"],
             "Shake well.", ["pescatarian", "omnivore"]),
        ],
    }
    vegan = {
        "breakfast": [
            ("Tofu Scramble + Toast",
             ["6 oz firm tofu", "spices", "2 slices toast", "olive oil"],
             "Crumble tofu & cook; toast bread.", ["vegan"]),
            ("Overnight Oats",
             ["1/2 cup oats", "plant milk", "1 tbsp chia", "berries"],
             "Mix & refrigerate overnight.", ["vegan"]),
        ],
        "lunch": [
            ("Chickpea Bowl",
             ["1 cup chickpeas", "1 cup rice or quinoa", "greens", "tahini"],
             "Assemble bowl; drizzle tahini.", ["vegan"]),
            ("Veggie Wrap",
             ["whole-grain wrap", "hummus", "mixed veggies"],
             "Spread hummus; wrap veggies.", ["vegan"]),
        ],
        "dinner": [
            ("Tofu Stir-Fry + Rice",
             ["6 oz tofu", "stir-fry veggies", "1 cup cooked rice", "soy sauce"],
             "Stir-fry; serve with rice.", ["vegan"]),
            ("Lentil Pasta",
             ["2 cups cooked pasta", "1 cup cooked lentils", "tomato sauce"],
             "Heat sauce with lentils; toss pasta.", ["vegan"]),
        ],
        "snack": [
            ("Apple + Almonds",
             ["1 apple", "1 oz almonds"],
             "Snack time.", ["vegan"]),
            ("Roasted Edamame",
             ["1 cup shelled edamame", "salt", "oil spray"],
             "Roast 12–15 min at 400°F.", ["vegan"]),
        ],
    }

    pref = (diet_pref or "").lower()
    if pref.startswith("pesca"):
        return pesc
    if pref.startswith("vegan"):
        return vegan
    return omni

def _allocate_kcal(targets: Targets) -> Dict[str, int]:
    # Simple split, rounded to whole numbers
    # breakfast 22%, lunch 28%, dinner 32%, snack 18%
    total = targets.tdee_kcal
    return {
        "breakfast": int(round(total * 0.22)),
        "lunch": int(round(total * 0.28)),
        "dinner": int(round(total * 0.32)),
        "snack": int(round(total * 0.18)),
    }

def _macro_split(kcal: int) -> Tuple[int, int, int]:
    # 25P / 50C / 25F split for a single meal
    p = int(round(kcal * 0.25 / 4.0))
    c = int(round(kcal * 0.50 / 4.0))
    f = int(round(kcal * 0.25 / 9.0))
    return p, c, f

def generate_plan_meals(diet_pref: str, targets: Targets) -> List[Meal]:
    templates = _templates_for_diet(diet_pref)
    alloc = _allocate_kcal(targets)
    meals: List[Meal] = []

    for mt in MEAL_ORDER:
        templ_list = templates.get(mt) or []
        if not templ_list:
            continue
        title, ingredients, instructions, tags = templ_list[0]
        kcal = max(300, alloc.get(mt, 500))
        p, c, f = _macro_split(kcal)
        meals.append(
            Meal(
                title=title,
                meal_type=mt,
                kcal=kcal,
                protein_g=p,
                carbs_g=c,
                fat_g=f,
                ingredients=ingredients[:],
                instructions=instructions,
                diet_tags=tags[:],
            )
        )
    return meals

def pick_swap(diet_pref: str, meal_type: str, exclude_titles: List[str], kcal_hint: int) -> Optional[Meal]:
    templates = _templates_for_diet(diet_pref)
    candidates = templates.get(meal_type, [])
    for (title, ingredients, instructions, tags) in candidates:
        if title in (exclude_titles or []):
            continue
        kp = max(300, kcal_hint)
        p, c, f = _macro_split(kp)
        return Meal(
            title=title,
            meal_type=meal_type,
            kcal=kp,
            protein_g=p,
            carbs_g=c,
            fat_g=f,
            ingredients=ingredients[:],
            instructions=instructions,
            diet_tags=tags[:],
        )
    return None

def grocery_list_for(meals: List[Meal]) -> List[str]:
    seen = set()
    out: List[str] = []
    for m in meals:
        for ing in m.ingredients:
            key = ing.strip().lower()
            if key not in seen:
                seen.add(key)
                out.append(ing.strip())
    # small normalization example: combine "6 oz salmon" lines
    # naive: replace duplicate base name patterns
    # (Keep MVP simple; dedup above helps a lot.)
    out.sort(key=lambda s: s.lower())
    return out

def to_dict(plan: DayPlan) -> Dict:
    return {
        "date": plan.date,
        "locked": plan.locked,
        "targets": asdict(plan.targets),
        "meals": [asdict(m) for m in plan.meals],
        "grocery_list": list(plan.grocery_list),
    }