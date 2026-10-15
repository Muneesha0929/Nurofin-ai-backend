"""Director-set quarter targets for departments.

Kept separate from `department` / `role` (which User Management uses as a role catalogue):
these departments are created by the Director and start empty.

Table names start with cos_ ("Chief of Staff"): production already had hand-made tables
called department_objective etc., so generic names are not safe in this database.

Fiscal quarters follow the Indian financial year: Q1 = Apr–Jun ... Q4 = Jan–Mar,
and `fiscal_year` is the calendar year the FY starts in (FY 2026-27 -> 2026).
"""
from sqlalchemy import Boolean, Column, Integer, String, Text, Float, ForeignKey, Table, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base_class import Base


target_department_members = Table(
    "cos_department_member",
    Base.metadata,
    Column("department_id", Integer, ForeignKey("cos_department.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
)


class TargetDepartment(Base):
    __tablename__ = "cos_department"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    hod_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    hod = relationship("User", foreign_keys=[hod_id])
    members = relationship("User", secondary=target_department_members)


class CompanyQuarterGoal(Base):
    """The one-line company target for a quarter (shown in bold on the Overview)."""
    __tablename__ = "cos_company_goal"
    __table_args__ = (UniqueConstraint("fiscal_year", "quarter_number", name="uix_cos_company_goal"),)

    id = Column(Integer, primary_key=True, index=True)
    fiscal_year = Column(Integer, nullable=False)
    quarter_number = Column(Integer, nullable=False)
    headline = Column(Text, nullable=True)
    updated_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)


class DepartmentQuarterTarget(Base):
    """The Director's message to one department for one quarter, and the quarter result he marks."""
    __tablename__ = "cos_quarter_target"
    __table_args__ = (UniqueConstraint("department_id", "fiscal_year", "quarter_number", name="uix_cos_quarter_target"),)

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("cos_department.id"), nullable=False, index=True)
    fiscal_year = Column(Integer, nullable=False)
    quarter_number = Column(Integer, nullable=False)
    message = Column(Text, nullable=True)
    message_updated_at = Column(DateTime, nullable=True)
    message_updated_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    hod_seen_at = Column(DateTime, nullable=True)
    outcome = Column(String, nullable=True)  # achieved | met_partly | not_met
    outcome_note = Column(Text, nullable=True)
    outcome_set_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    outcome_set_at = Column(DateTime, nullable=True)


class DepartmentObjective(Base):
    """An objective the HOD sets from the Director's message. `parent_id` makes it a sub-objective."""
    __tablename__ = "cos_objective"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("cos_department.id"), nullable=False, index=True)
    fiscal_year = Column(Integer, nullable=False)
    quarter_number = Column(Integer, nullable=False)
    parent_id = Column(Integer, ForeignKey("cos_objective.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    deadline = Column(String, nullable=True)  # YYYY-MM-DD, like the rest of the codebase
    status = Column(String, default="not_started")  # not_started | in_progress | done
    progress = Column(Float, default=0.0)
    sort_order = Column(Integer, default=0)
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    owner = relationship("User", foreign_keys=[owner_id])


class DepartmentObjectiveUpdate(Base):
    """A short progress update posted on an objective or sub-objective."""
    __tablename__ = "cos_objective_update"

    id = Column(Integer, primary_key=True, index=True)
    objective_id = Column(Integer, ForeignKey("cos_objective.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    text = Column(Text, nullable=False)

    user = relationship("User", foreign_keys=[user_id])


class CompanyWidget(Base):
    """One company target card (T1, T2 ...) on the Overview. The Director adds, edits and orders them per quarter."""
    __tablename__ = "cos_company_widget"

    id = Column(Integer, primary_key=True, index=True)
    fiscal_year = Column(Integer, nullable=False)
    quarter_number = Column(Integer, nullable=False)
    sort_order = Column(Integer, default=0)
    title = Column(String, nullable=False)
    kind = Column(String, default="number")  # number | money | percent | milestone
    unit = Column(String, nullable=True)      # e.g. "Cr", "L", "merchants"
    base = Column(Float, default=0.0)         # value at the start of the quarter
    current = Column(Float, default=0.0)
    target = Column(Float, default=0.0)
    lower_is_better = Column(Boolean, default=False)
    department_id = Column(Integer, ForeignKey("cos_department.id"), nullable=True)
    note = Column(Text, nullable=True)
    updated_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)


class MonthlyTarget(Base):
    """A dated target on the Overview's right side, e.g. "10 lakh transactions by 30 Nov".
    The Director sets it; the Director or the responsible department's HOD updates the current value."""
    __tablename__ = "cos_monthly_target"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    kind = Column(String, default="number")  # number | money | percent | milestone
    unit = Column(String, nullable=True)
    target = Column(Float, default=0.0)
    current = Column(Float, default=0.0)
    due_date = Column(String, nullable=False)  # YYYY-MM-DD
    department_id = Column(Integer, ForeignKey("cos_department.id"), nullable=True)
    note = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    updated_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
