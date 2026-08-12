# app/models.py
"""
Authoritative SQLAlchemy ORM models aligned to the current database schema.

Tables:
- users
- activities
- recipes
- oauth_accounts
- plans                 (totals JSON, source VARCHAR)
- plan_meals            (tags JSON, updated_at)
- plan_items
- user_preferences
- energy_targets
- plan_lock
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.encrypted_types import EncryptedText

# -------------------------
# Users
# -------------------------


class User(Base):
    __tablename__ = "users"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # human-friendly name shown in the UI (editable on Profile)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # preferred units (“US”, “Metric”)
    units: Mapped[str | None] = mapped_column(String(16), nullable=True)

    sex: Mapped[str | None] = mapped_column(String, nullable=True)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    diet_pref: Mapped[str | None] = mapped_column(String, nullable=True)
    goal: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    activities: Mapped[list[Activity]] = relationship(
        "Activity", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    plans: Mapped[list[Plan]] = relationship(
        "Plan", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_occurred", "occurred_at"),
        Index("ix_security_audit_type_outcome", "event_type", "outcome"),
        Index("ix_security_audit_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# -------------------------
# Activities
# -------------------------


class Activity(Base):
    __tablename__ = "activities"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("id", name="uq_activities_id"),
        Index("ix_activities_user_time", "user_id", "start_time"),
        Index("ux_activities_source", "user_id", "source_provider", "source_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    sport: Mapped[str | None] = mapped_column(String, nullable=True)
    source_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="activities")

    def __repr__(self) -> str:
        return f"<Activity id={self.id} user_id={self.user_id} sport={self.sport!r}>"


# -------------------------
# Recipes
# -------------------------


class Recipe(Base):
    __tablename__ = "recipes"
    __allow_unmapped__ = True
    __table_args__ = (Index("ix_recipes_protein_group", "protein_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)

    # Stored as JSON array (e.g., ["pescatarian","gluten_free"])
    diet_tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    meal_type: Mapped[str | None] = mapped_column(String, nullable=True)

    # IMPORTANT: aligns ORM with DB + llm_recommend.py creation kwargs
    protein_group: Mapped[str | None] = mapped_column(String(32), nullable=True)

    kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protein_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carbs_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fat_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingredients: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    instructions: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<Recipe id={self.id} title={self.title!r}>"


# -------------------------
# OAuth Accounts
# -------------------------


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_athlete_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="oauth_accounts")

    def __repr__(self) -> str:
        return f"<OAuthAccount user_id={self.user_id} provider={self.provider!r}>"


# -------------------------
# Plans / Meals / Items
# -------------------------


class Plan(Base):
    __tablename__ = "plans"
    __allow_unmapped__ = True
    __table_args__ = (
        Index("ix_plan_user_date", "user_id", "date"),
        UniqueConstraint("user_id", "date", name="ux_plan_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)

    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="plans")
    meals: Mapped[list[PlanMeal]] = relationship(
        "PlanMeal",
        back_populates="plan",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PlanMeal.order_index",
        foreign_keys="PlanMeal.plan_id",
    )

    def __repr__(self) -> str:
        return f"<Plan id={self.id} user_id={self.user_id} date={self.date}>"


class PlanMeal(Base):
    __tablename__ = "plan_meals"
    __allow_unmapped__ = True
    __table_args__ = (Index("ix_plan_meal_plan", "plan_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # Track which catalog recipe this meal came from (nullable)
    recipe_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True)

    meal_type: Mapped[str] = mapped_column(String(24))
    title: Mapped[str | None] = mapped_column(String(160))
    kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    instructions: Mapped[str | None] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[dict | None] = mapped_column(JSON)
    # Per-meal metadata, including the explanation returned by the AI planner.
    meta: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    plan: Mapped[Plan] = relationship("Plan", back_populates="meals", foreign_keys=[plan_id])

    # Optional relationship to Recipe (convenience)
    recipe: Mapped[Recipe | None] = relationship("Recipe", lazy="joined")

    items: Mapped[list[PlanItem]] = relationship(
        "PlanItem",
        back_populates="meal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="PlanItem.meal_id",
        order_by="PlanItem.id",
    )

    def __repr__(self) -> str:
        return f"<PlanMeal id={self.id} plan_id={self.plan_id} title={self.title!r}>"


class PlanItem(Base):
    __tablename__ = "plan_items"
    __allow_unmapped__ = True
    __table_args__ = (Index("ix_plan_item_meal", "meal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan_meals.id", ondelete="CASCADE"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    meal: Mapped[PlanMeal] = relationship("PlanMeal", back_populates="items", foreign_keys=[meal_id])

    def __repr__(self) -> str:
        return f"<PlanItem id={self.id} meal_id={self.meal_id} name={self.name!r}>"


# -------------------------
# Preferences & Targets
# -------------------------


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Legacy JSON fields (still present in DB)
    dietary_tags: Mapped[dict | None] = mapped_column(JSON, default=list)
    banned_ingredients: Mapped[dict | None] = mapped_column(JSON, default=list)
    allergies: Mapped[dict | None] = mapped_column(JSON, default=list)
    settings: Mapped[dict | None] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Newer explicit preference columns (present in your SQLite schema)
    ingredient_exclusions: Mapped[str | None] = mapped_column(String, nullable=True)
    diet_type: Mapped[str | None] = mapped_column(String, nullable=True)

    def exclusions_list(self) -> list[str]:
        raw = (self.ingredient_exclusions or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]


class EnergyTarget(Base):
    __tablename__ = "energy_targets"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    date: Mapped[date] = mapped_column(Date, index=True)
    tdee_kcal: Mapped[float | None] = mapped_column(Float)
    training_kcal: Mapped[float | None] = mapped_column(Float)
    target_kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict | None] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


# -------------------------
# Plan Lock
# -------------------------


class PlanLock(Base):
    __tablename__ = "plan_lock"
    __allow_unmapped__ = True

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    locked: Mapped[int] = mapped_column(Integer)  # stored as INTEGER in DB
