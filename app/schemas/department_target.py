from typing import List, Literal, Optional
from pydantic import BaseModel, Field

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class DepartmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    hod_id: Optional[int] = None
    member_ids: List[int] = Field(default_factory=list, max_length=500)


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    hod_id: Optional[int] = None
    member_ids: Optional[List[int]] = Field(None, max_length=500)


class CompanyGoalUpdate(BaseModel):
    headline: Optional[str] = Field(None, max_length=500)


class QuarterTargetUpdate(BaseModel):
    message: Optional[str] = Field(None, max_length=20000)
    outcome: Optional[Literal["achieved", "met_partly", "not_met"]] = None
    outcome_note: Optional[str] = Field(None, max_length=5000)


class ObjectiveCreate(BaseModel):
    department_id: int
    fiscal_year: int
    quarter_number: int
    parent_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = Field(None, max_length=5000)
    owner_id: Optional[int] = None
    deadline: Optional[str] = Field(None, pattern=DATE_PATTERN)


class ObjectiveUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    description: Optional[str] = Field(None, max_length=5000)
    owner_id: Optional[int] = None
    deadline: Optional[str] = Field(None, pattern=DATE_PATTERN)
    status: Optional[Literal["not_started", "in_progress", "done"]] = None
    progress: Optional[float] = Field(None, ge=0, le=100)


class ObjectiveUpdatePost(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


WIDGET_KINDS = Literal["number", "money", "percent", "milestone"]


class WidgetCreate(BaseModel):
    fiscal_year: int
    quarter_number: int
    title: str = Field(..., min_length=1, max_length=200)
    kind: WIDGET_KINDS = "number"
    unit: Optional[str] = Field(None, max_length=30)
    base: float = 0
    current: float = 0
    target: float = 0
    lower_is_better: bool = False
    department_id: Optional[int] = None
    note: Optional[str] = Field(None, max_length=1000)


class WidgetUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[WIDGET_KINDS] = None
    unit: Optional[str] = Field(None, max_length=30)
    base: Optional[float] = None
    current: Optional[float] = None
    target: Optional[float] = None
    lower_is_better: Optional[bool] = None
    department_id: Optional[int] = None
    note: Optional[str] = Field(None, max_length=1000)


class WidgetOrder(BaseModel):
    ids: List[int] = Field(..., max_length=100)


class MonthlyTargetCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    kind: WIDGET_KINDS = "number"
    unit: Optional[str] = Field(None, max_length=30)
    target: float = 0
    current: float = 0
    due_date: str = Field(..., pattern=DATE_PATTERN)
    department_id: Optional[int] = None
    note: Optional[str] = Field(None, max_length=1000)


class MonthlyTargetUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[WIDGET_KINDS] = None
    unit: Optional[str] = Field(None, max_length=30)
    target: Optional[float] = None
    current: Optional[float] = None
    due_date: Optional[str] = Field(None, pattern=DATE_PATTERN)
    department_id: Optional[int] = None
    note: Optional[str] = Field(None, max_length=1000)


class TaskLink(BaseModel):
    task_id: int
