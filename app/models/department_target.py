"""Director-set quarter targets for departments.

Kept separate from `department` / `role` (which User Management uses as a role catalogue):
these departments are created by the Director and start empty.

Table names start with cos_ ("Chief of Staff"): production already had hand-made tables
called department_objective etc., so generic names are not safe in this database.

Fiscal quarters follow the Indian financial year: Q1 = Apr–Jun ... Q4 = Jan–Mar,
and `fiscal_year` is the calendar year the FY starts in (FY 2026-27 -> 2026).
"""
from sqlalchemy import Boolean, Column, Integer, String, Text, Float, ForeignKey, Table, DateTime, UniqueConstraint, Index, text
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
    short_code = Column(String, nullable=True)
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
    # Next free number for a top-level objective of this department + quarter. Only ever goes up.
    next_objective_seq = Column(Integer, nullable=False, default=1, server_default=text("1"))


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
    progress_manual = Column(Boolean, default=False)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    sort_order = Column(Integer, default=0)
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    # Stored code number: top level numbers within (department, FY, quarter), a sub-objective within its parent.
    # Handed out once and never reused, so codes like PROD-1.2 keep pointing at the same item.
    seq = Column(Integer, nullable=False)
    # One shared counter for the numbers of this objective's sub-objectives and linked tasks.
    next_child_seq = Column(Integer, nullable=False, default=1, server_default=text("1"))
    # CEO-set priority: P1 | P2 | P3 | null (top-level and sub-objectives only)
    priority = Column(String, nullable=True)
    priority_set_at = Column(DateTime, nullable=True)
    priority_set_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    owner = relationship("User", foreign_keys=[owner_id])
    project = relationship("Project", foreign_keys=[project_id])


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


class ObjectiveTask(Base):
    """Links a Task Center task to one objective or sub-objective. The task table itself is not changed."""
    __tablename__ = "cos_objective_task"
    __table_args__ = (UniqueConstraint("task_id", name="uix_cos_objective_task_task"),)

    id = Column(Integer, primary_key=True, index=True)
    objective_id = Column(Integer, ForeignKey("cos_objective.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    linked_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    # Stored code number within the objective (shared counter with its sub-objectives). Re-linking gets a new one.
    seq = Column(Integer, nullable=False)


class TimelineEntry(Base):
    """One line on the timeline of a task or an objective: a deadline change (with the reason), an update, or a handover request.
    Status changes, transfers and objective updates are read from their own tables when the timeline is shown."""
    __tablename__ = "cos_timeline_entry"

    id = Column(Integer, primary_key=True, index=True)
    entity = Column(String, nullable=False)      # task | objective
    entity_id = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)        # date_change | update | handover
    old_date = Column(String, nullable=True)     # YYYY-MM-DD
    new_date = Column(String, nullable=True)
    text = Column(Text, nullable=True)
    person_id = Column(Integer, ForeignKey("user.id"), nullable=True)  # handover: who was asked to look after it
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    __table_args__ = (Index("ix_cos_timeline_entry_entity", "entity", "entity_id"),)


class PrioritySeen(Base):
    """The popup for a priority was shown to this user (and acknowledged) on `seen_on`."""
    __tablename__ = "cos_priority_seen"
    __table_args__ = (UniqueConstraint("user_id", "objective_id", name="uix_cos_priority_seen"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    objective_id = Column(Integer, ForeignKey("cos_objective.id"), nullable=False)
    priority = Column(String, nullable=False)
    seen_on = Column(String, nullable=False)  # YYYY-MM-DD
