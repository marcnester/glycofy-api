# app/schemas.py
from datetime import date
from typing import Literal

from pydantic import BaseModel


class UserUpdate(BaseModel):
    sex: Literal["male", "female", "unspecified"] | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    diet_pref: str | None = None
    goal: str | None = None
    timezone: str | None = None
    dob: date | None = None  # <-- actual date type

    class Config:
        orm_mode = True
