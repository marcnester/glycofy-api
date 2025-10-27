# app/schemas.py
from datetime import date
from typing import Optional, Literal
from pydantic import BaseModel

class UserUpdate(BaseModel):
    sex: Optional[Literal["male", "female", "unspecified"]] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    diet_pref: Optional[str] = None
    goal: Optional[str] = None
    timezone: Optional[str] = None
    dob: Optional[date] = None  # <-- actual date type

    class Config:
        orm_mode = True