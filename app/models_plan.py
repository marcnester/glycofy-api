from __future__ import annotations

from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship

from app.db import Base


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    units = Column(String(16))
    sex = Column(String(16))
    dob = Column(Date)
    height_cm = Column(Integer)
    weight_kg = Column(Numeric(5, 2))
    goal = Column(String(32))
    dietary_tags = Column(ARRAY(String), default=list)
    allergens = Column(ARRAY(String), default=list)
    cuisine_prefs = Column(ARRAY(String), default=list)
    meal_count = Column(Integer, default=4)
    protein_g_per_kg = Column(Numeric(4, 2), default=1.8)
    carb_periodization = Column(String(16), default="auto")
    fat_min_pct = Column(Integer, default=20)
    llm_meal_model = Column(String(128))


class ActivityDailySummary(Base):
    __tablename__ = "activity_daily_summary"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    kcal_exercise = Column(Integer, nullable=False, default=0)
    duration_min = Column(Integer, nullable=False, default=0)
    tss = Column(Integer)
    sport_breakdown = Column(JSONB, default=dict)
    intensity_hint = Column(String(16))


class PlanDay(Base):
    __tablename__ = "plan_day"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    locked = Column(Boolean, nullable=False, default=False)
    source = Column(String(32), nullable=False, default="generated")
    targets = Column(JSONB, default=dict)
    totals = Column(JSONB, default=dict)
    window_used = Column(Integer)
    algo_version = Column(String(16), default="v1.0")
    notes = Column(Text)

    meals = relationship("Meal", backref="plan_day", cascade="all, delete-orphan", order_by="Meal.rank")


class Meal(Base):
    __tablename__ = "meal"
    id = Column(Integer, primary_key=True)
    plan_day_id = Column(Integer, ForeignKey("plan_day.id", ondelete="CASCADE"), nullable=False)
    slot = Column(String(16), nullable=False)
    title = Column(String(255))
    kcal = Column(Integer)
    protein_g = Column(Integer)
    carbs_g = Column(Integer)
    fat_g = Column(Integer)
    instructions = Column(Text)
    source = Column(String(32), default="generated")
    rank = Column(Integer, default=0)

    items = relationship("MealItem", backref="meal", cascade="all, delete-orphan")


class MealItem(Base):
    __tablename__ = "meal_item"
    id = Column(Integer, primary_key=True)
    meal_id = Column(Integer, ForeignKey("meal.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    qty = Column(Numeric(8, 2))
    unit = Column(String(24))
    kcal = Column(Integer)
    protein_g = Column(Integer)
    carbs_g = Column(Integer)
    fat_g = Column(Integer)
    food_ref_id = Column(String(64))
    notes = Column(Text)


class PlanFeedback(Base):
    __tablename__ = "plan_feedback"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_day_id = Column(Integer, ForeignKey("plan_day.id", ondelete="CASCADE"), nullable=False)
    rating = Column(Integer)
    tags = Column(ARRAY(String), default=list)
    comment = Column(Text)
    created_at = Column(Date, default=None)
