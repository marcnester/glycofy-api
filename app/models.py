from sqlalchemy import Column, Integer, String, Date, ForeignKey, Float, JSON, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    sex = Column(String, nullable=True)          # "male"/"female"/"other"
    dob = Column(Date, nullable=True)
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    diet_pref = Column(String, nullable=True)    # "omnivore"/"pescatarian"/"vegan"
    goal = Column(String, nullable=True)         # "maintain"/"lose"/"gain"
    timezone = Column(String, nullable=True)

class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    provider = Column(String, nullable=False)    # "strava"
    source_id = Column(String, nullable=False)   # provider activity id
    start_time = Column(DateTime, nullable=False)
    duration_s = Column(Integer, nullable=False)
    distance_m = Column(Float, nullable=True)
    avg_hr = Column(Float, nullable=True)
    kcal = Column(Float, nullable=True)
    sport = Column(String, nullable=True)
    __table_args__ = (UniqueConstraint("user_id","provider","source_id", name="uq_act_src"),)

class DailyNutrition(Base):
    __tablename__ = "daily_nutrition"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    date = Column(Date, nullable=False)
    training_kcal = Column(Integer, nullable=False, default=0)
    tdee_kcal = Column(Integer, nullable=False)
    protein_g = Column(Integer, nullable=False)
    carbs_g = Column(Integer, nullable=False)
    fat_g = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("user_id","date", name="uq_dn_day"),)

class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    diet_tags = Column(JSON, nullable=False, default=[])
    meal_type = Column(String, nullable=False)   # breakfast/lunch/dinner/snack
    kcal = Column(Integer, nullable=False)
    protein_g = Column(Integer, nullable=False)
    carbs_g = Column(Integer, nullable=False)
    fat_g = Column(Integer, nullable=False)
    ingredients = Column(JSON, nullable=False, default=[])
    instructions = Column(String, nullable=False)

class MealPlan(Base):
    __tablename__ = "meal_plan"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    date = Column(Date, nullable=False)
    items = Column(JSON, nullable=False, default=[])  # list of {recipe_id, servings, meal_type}
    __table_args__ = (UniqueConstraint("user_id","date", name="uq_mp_day"),)