from typing import Optional, List
from pydantic import BaseModel
from app.models.finance_record import FinanceRecordTypeEnum, FinanceRecordStatusEnum


class FinanceRecordBase(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    record_type: Optional[FinanceRecordTypeEnum] = None
    status: Optional[FinanceRecordStatusEnum] = None
    amount: Optional[float] = 0.0
    currency: Optional[str] = "USD"
    vendor: Optional[str] = None
    department: Optional[str] = None
    cost_category: Optional[str] = None
    project_id: Optional[int] = None
    user_id: Optional[int] = None
    due_date: Optional[str] = None
    transaction_date: Optional[str] = None
    notes: Optional[str] = None


class FinanceRecordCreate(FinanceRecordBase):
    title: str
    amount: float


class FinanceRecordUpdate(FinanceRecordBase):
    pass


class PerformanceReviewCreate(BaseModel):
    user_id: int
    quarter_id: Optional[int] = None
    score: float
    comments: Optional[str] = None
    increment_pct: Optional[float] = None
