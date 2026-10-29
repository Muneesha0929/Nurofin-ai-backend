"""Product features: every idea the team raises, and the record of why it was built (or not).

A feature is one story: what someone noticed (the problem), the idea, Sir's decision and the
reason for it, and the Task Center tasks that built it. Everything that happens to a feature is
written to cos_feature_event, which is append-only: rows are never updated or deleted, so years
later anyone can search "animations" and read why Reduce motion exists.

Features are never deleted either; "Not doing" with a reason is how an idea is closed.
Tables start with cos_ so they never collide with the hand-made tables in production.
"""
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from app.db.base_class import Base

FEATURE_STATUSES = ("suggested", "planned", "building", "shipped", "not_doing")
FEATURE_SOURCES = ("team", "customer", "director", "other")


class ProductFeature(Base):
    __tablename__ = "cos_feature"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    problem = Column(Text, nullable=False)            # what was noticed, in the words of whoever saw it
    idea = Column(Text, nullable=True)                # what we could build
    area = Column(String(80), nullable=True)          # where in the product: "Task Center", "Overview" ...
    source = Column(String(20), nullable=False, default="team")
    source_note = Column(String(200), nullable=True)  # "GimBooks call", "Sir in Monday review"
    status = Column(String(20), nullable=False, default="suggested", index=True)
    decision_note = Column(Text, nullable=True)       # the latest reason; every earlier one stays in the events
    decided_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    chosen_solution_id = Column(Integer, nullable=True)  # the solution Sir picked; the pick and its reason are in the events
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=False)


class ProductFeatureEvent(Base):
    """Append-only history. kind: created, edited, status, comment, me_too, task_linked, task_unlinked,
    solution_added, solution_edited, solution_chosen.
    data holds the details: {"from": .., "to": ..} for status, {"changes": {field: [old, new]}} for edits,
    {"task_id": .., "title": ..} for task links (the title is copied so it survives the task being deleted)."""
    __tablename__ = "cos_feature_event"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    text = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)


class ProductFeatureSolution(Base):
    """One way to fix the problem. Anyone can add one; the team likes the ones they prefer and the
    Director picks the one to build, so the person who thought of it gets the credit."""
    __tablename__ = "cos_feature_solution"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=False)


class ProductFeatureSolutionVote(Base):
    """A like on a solution. One per person per solution (enforced by the database)."""
    __tablename__ = "cos_feature_solution_vote"
    __table_args__ = (UniqueConstraint("solution_id", "user_id", name="uix_cos_feature_solution_vote"),)

    id = Column(Integer, primary_key=True, index=True)
    solution_id = Column(Integer, ForeignKey("cos_feature_solution.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)


class ProductFeatureMeToo(Base):
    """'I noticed this too'. One per person per feature (enforced by the database, so a fast
    double click can't count twice). The matching me_too event keeps it in the history."""
    __tablename__ = "cos_feature_me_too"
    __table_args__ = (UniqueConstraint("feature_id", "user_id", name="uix_cos_feature_me_too"),)

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)


class ProductFeatureTask(Base):
    """A Task Center task that builds this feature. The task table itself is not changed."""
    __tablename__ = "cos_feature_task"
    __table_args__ = (UniqueConstraint("feature_id", "task_id", name="uix_cos_feature_task"),)

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    linked_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)


FEATURE_TABLES = ["cos_feature", "cos_feature_event", "cos_feature_solution", "cos_feature_solution_vote", "cos_feature_me_too", "cos_feature_task"]
