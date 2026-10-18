from typing import Literal, Optional
from pydantic import BaseModel, Field

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
DAY = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
Money = Field(..., ge=0, le=1e12)


class CategoryIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sort_order: Optional[int] = None


class ExpenseIn(BaseModel):
    category_id: Optional[int] = None
    new_category: Optional[str] = Field(None, min_length=1, max_length=120)
    name: str = Field(..., min_length=1, max_length=160)
    how: Literal["monthly", "varies", "once"]
    amount: float = Field(0, ge=0, le=1e12)            # per month (monthly / varies)
    start_month: Optional[str] = Field(None, pattern=MONTH)
    end_month: Optional[str] = Field(None, pattern=MONTH)
    raise_pct: float = Field(0, ge=0, le=100)
    once_month: Optional[str] = Field(None, pattern=MONTH)   # one-time: the month it is paid
    once_amount: Optional[float] = Field(None, ge=0, le=1e12)


class MonthAmount(BaseModel):
    amount: Optional[float] = Field(None, ge=0, le=1e12)    # null clears the figure for that month


class FillMonth(BaseModel):
    month: str = Field(..., pattern=MONTH)
    only_missing: bool = True


class ProductIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    tracking: Literal["figures", "fixed"]
    cost_label: Optional[str] = Field(None, max_length=80)
    amount: float = Field(0, ge=0, le=1e12)
    start_month: str = Field(..., pattern=MONTH)
    end_month: Optional[str] = Field(None, pattern=MONTH)


class FigureIn(BaseModel):
    day: str = Field(..., pattern=DAY)
    revenue: float = Money
    cost: Optional[float] = Field(None, ge=0, le=1e12)
    forecast: Optional[float] = Field(None, ge=0, le=1e12)


class ProductMonthIn(BaseModel):
    revenue: float = Money
    cost: Optional[float] = Field(None, ge=0, le=1e12)


class CapitalIn(BaseModel):
    day: str = Field(..., pattern=DAY)
    amount: float = Field(..., gt=0, le=1e12)
    note: Optional[str] = Field(None, max_length=200)


class BankIn(BaseModel):
    day: str = Field(..., pattern=DAY)
    amount: float = Field(..., ge=-1e12, le=1e12)


class SettingsIn(BaseModel):
    start_month: Optional[str] = Field(None, pattern=MONTH)
    buffer_pct: Optional[float] = Field(None, ge=0, le=50)
    green_months: Optional[int] = Field(None, ge=1, le=60)
    amber_months: Optional[int] = Field(None, ge=1, le=60)
