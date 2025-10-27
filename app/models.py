# app/models.py
"""
SQLAlchemy models: User, Activity, Recipe, OAuthAccount, Plan, PlanMeal.
Compatible with SQLAlchemy 2.x Annotated Declarative (Mapped[] + mapped_column()).
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    Integer, String, Float, Date, DateTime, ForeignKey,
    UniqueConstraint, Index, Text, Boolean
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    sex: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    dob: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diet_pref: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    goal: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    activities: Mapped[List["Activity"]] = relationship(
        "Activity", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    oauth_accounts: Mapped[List["OAuthAccount"]] = relationship(
        "OAuthAccount", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    plans: Mapped[List["Plan"]] = relationship(
        "Plan", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


class Activity(Base):
    __tablename__ = "activities"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("id", name="uq_activities_id"),
        Index("ix_activities_user_time", "user_id", "start_time"),
        Index("ux_activities_source", "user_id", "source_provider", "source_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    sport: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    kcal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    source_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="activities")

    def __repr__(self) -> str:
        return f"<Activity id={self.id} user_id={self.user_id} sport={self.sport!r}>"


class Recipe(Base):
    __tablename__ = "recipes"
    __allow_unmapped__ = True
    __table_args__ = (
        Index("ix_recipes_meal_type", "meal_type"),
        Index("ix_recipes_first_tag", "diet_tags"),
        Index("ix_recipes_kcal", "kcal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    diet_tags: Mapped[str] = mapped_column(String(120), nullable=False, default="omnivore")
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False)  # breakfast|lunch|dinner|snack
    kcal: Mapped[int] = mapped_column(Integer, nullable=False)
    protein_g: Mapped[int] = mapped_column(Integer, nullable=False)
    carbs_g: Mapped[int] = mapped_column(Integer, nullable=False)
    fat_g: Mapped[int] = mapped_column(Integer, nullable=False)
    ingredients: Mapped[str] = mapped_column(Text, nullable=False)  # newline-separated
    instructions: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<Recipe id={self.id} title={self.title!r}>"


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    __allow_unmapped__ = True
    __table_args__ = (
        Index("ix_oauth_user_provider", "user_id", "provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., 'strava'
    external_athlete_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    access_token: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    expires_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # epoch seconds
    scope: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="oauth_accounts")

    def __repr__(self) -> str:
        return f"<OAuthAccount user_id={self.user_id} provider={self.provider!r}>"


class Plan(Base):
    __tablename__ = "plans"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="ux_plan_user_date"),
        Index("ix_plan_user_date", "user_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    diet_pref: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # targets at time of generation
    tdee_kcal: Mapped[int] = mapped_column(Integer, nullable=False)
    training_kcal: Mapped[int] = mapped_column(Integer, nullable=False)
    protein_g: Mapped[int] = mapped_column(Integer, nullable=False)
    carbs_g: Mapped[int] = mapped_column(Integer, nullable=False)
    fat_g: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="plans")
    meals: Mapped[List["PlanMeal"]] = relationship(
        "PlanMeal", back_populates="plan", cascade="all, delete-orphan", passive_deletes=True, order_by="PlanMeal.order_index"
    )


class PlanMeal(Base):
    __tablename__ = "plan_meals"
    __allow_unmapped__ = True
    __table_args__ = (
        Index("ix_plan_meal_plan", "plan_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # denormalized recipe snapshot (so edits to recipe library don’t rewrite old plans)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False)   # breakfast|lunch|dinner|snack
    diet_tags: Mapped[str] = mapped_column(String(120), nullable=False)
    kcal: Mapped[int] = mapped_column(Integer, nullable=False)
    protein_g: Mapped[int] = mapped_column(Integer, nullable=False)
    carbs_g: Mapped[int] = mapped_column(Integer, nullable=False)
    fat_g: Mapped[int] = mapped_column(Integer, nullable=False)
    ingredients: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    plan: Mapped[Plan] = relationship("Plan", back_populates="meals")