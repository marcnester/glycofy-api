# scripts/seed_recipes.py
from __future__ import annotations
from sqlalchemy.orm import Session
from app.db import SessionLocal, Base, engine
from app.models import Recipe

RECIPES = [
    # breakfast
    {
        "title": "Smoked Salmon Toast",
        "diet_tags": "pescatarian,omnivore",
        "meal_type": "breakfast",
        "kcal": 480, "protein_g": 32, "carbs_g": 40, "fat_g": 20,
        "ingredients": "2 slices sourdough\n3 oz smoked salmon\n1/2 avocado\ntomato\ncapers",
        "instructions": "Toast; top with avocado, salmon, tomato, capers.",
    },
    {
        "title": "Greek Yogurt + Berries",
        "diet_tags": "omnivore",
        "meal_type": "breakfast",
        "kcal": 300, "protein_g": 25, "carbs_g": 35, "fat_g": 5,
        "ingredients": "1 cup Greek yogurt\n1/2 cup mixed berries\n1 tsp honey",
        "instructions": "Combine and serve.",
    },
    {
        "title": "Overnight Oats (Vegan)",
        "diet_tags": "vegan",
        "meal_type": "breakfast",
        "kcal": 420, "protein_g": 16, "carbs_g": 62, "fat_g": 12,
        "ingredients": "1/2 cup rolled oats\n1 cup oat milk\n1 tbsp chia seeds\nbanana, sliced",
        "instructions": "Combine in jar; chill overnight; top with banana.",
    },

    # lunch
    {
        "title": "Salmon Rice Bowl",
        "diet_tags": "pescatarian,omnivore",
        "meal_type": "lunch",
        "kcal": 680, "protein_g": 42, "carbs_g": 70, "fat_g": 22,
        "ingredients": "6 oz salmon\n1 cup cooked rice\nedamame\nseaweed salad\nsoy/teriyaki",
        "instructions": "Bake salmon; assemble bowl.",
    },
    {
        "title": "Tofu Veggie Bowl",
        "diet_tags": "vegan",
        "meal_type": "lunch",
        "kcal": 650, "protein_g": 35, "carbs_g": 80, "fat_g": 18,
        "ingredients": "6 oz tofu\n1 cup cooked rice\nmixed veggies\nsesame\nsoy sauce",
        "instructions": "Sauté tofu and veggies; assemble bowl.",
    },
    {
        "title": "Tuna Poke Bowl",
        "diet_tags": "pescatarian,omnivore",
        "meal_type": "lunch",
        "kcal": 700, "protein_g": 46, "carbs_g": 78, "fat_g": 18,
        "ingredients": "6 oz sushi-grade tuna\n1 cup cooked rice\ncucumber\navocado\nponzu",
        "instructions": "Cube tuna; toss with ponzu; assemble bowl.",
    },

    # dinner
    {
        "title": "Teriyaki Salmon + Rice + Bok Choy",
        "diet_tags": "pescatarian,omnivore",
        "meal_type": "dinner",
        "kcal": 700, "protein_g": 45, "carbs_g": 80, "fat_g": 18,
        "ingredients": "6 oz salmon\n1 cup cooked jasmine rice\n1 cup bok choy\nteriyaki sauce",
        "instructions": "Bake salmon; steam bok choy; serve.",
    },
    {
        "title": "Shrimp Pasta",
        "diet_tags": "pescatarian,omnivore",
        "meal_type": "dinner",
        "kcal": 720, "protein_g": 45, "carbs_g": 85, "fat_g": 20,
        "ingredients": "6 oz shrimp\n2 cups cooked pasta\ntomato sauce\nparsley",
        "instructions": "Cook pasta; sauté shrimp; combine with sauce.",
    },
    {
        "title": "Tofu Stir Fry + Rice",
        "diet_tags": "vegan",
        "meal_type": "dinner",
        "kcal": 690, "protein_g": 32, "carbs_g": 90, "fat_g": 20,
        "ingredients": "8 oz tofu\n2 cups mixed veggies\n1 cup cooked rice\nstir-fry sauce",
        "instructions": "Stir fry tofu + veggies; serve over rice.",
    },

    # snacks
    {
        "title": "Roasted Edamame",
        "diet_tags": "vegan,pescatarian,omnivore",
        "meal_type": "snack",
        "kcal": 260, "protein_g": 24, "carbs_g": 18, "fat_g": 10,
        "ingredients": "1 cup shelled edamame\nsalt\noil spray",
        "instructions": "Roast 12–15 min at 400°F.",
    },
    {
        "title": "Banana + PB",
        "diet_tags": "vegan,omnivore",
        "meal_type": "snack",
        "kcal": 300, "protein_g": 9, "carbs_g": 35, "fat_g": 14,
        "ingredients": "1 banana\n1.5 tbsp peanut butter",
        "instructions": "Slice banana; spread PB.",
    },
    {
        "title": "Apple + Almonds",
        "diet_tags": "vegan,pescatarian,omnivore",
        "meal_type": "snack",
        "kcal": 280, "protein_g": 8, "carbs_g": 28, "fat_g": 16,
        "ingredients": "1 apple\n20 almonds",
        "instructions": "Wash apple; portion almonds.",
    },
]

def main():
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        created = 0
        for r in RECIPES:
            exists = db.query(Recipe).filter(Recipe.title == r["title"]).first()
            if exists:
                continue
            db.add(Recipe(**r))
            created += 1
        db.commit()
        print(f"✅ Seeded recipes. Added {created}.")
    finally:
        db.close()

if __name__ == "__main__":
    main()