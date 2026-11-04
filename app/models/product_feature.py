"""Product features: every idea the team raises, and the record of why it was built (or not).

A feature is one story: what someone noticed (the problem), the idea, Sir's decision and the
reason for it, and the Task Center tasks that built it. Everything that happens to a feature is
written to cos_feature_event, which is append-only: rows are never updated or deleted, so years
later anyone can search "animations" and read why Reduce motion exists.

Features are never deleted either; "Not doing" with a reason is how an idea is closed.
Tables start with cos_ so they never collide with the hand-made tables in production.
"""
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
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


# ----------------------------------------------------------------- products, attachments, costs (4 Nov)

class AppProduct(Base):
    """A product the company builds (GMDI, Build-Track, Chief of Staff). Features are tagged with it.
    Kept apart from Runway's money products, which are private to the CEO."""
    __tablename__ = "cos_product"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


class ProductFeatureProduct(Base):
    __tablename__ = "cos_feature_product"
    __table_args__ = (UniqueConstraint("feature_id", "product_id", name="uix_cos_feature_product"),)

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("cos_product.id"), nullable=False, index=True)


class ProductFeatureAttachment(Base):
    """A screenshot or supporting document on a feature, or on one of its ideas.
    Stored with the app's S3 helper (local disk when S3 is not configured)."""
    __tablename__ = "cos_feature_attachment"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    solution_id = Column(Integer, ForeignKey("cos_feature_solution.id"), nullable=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(120), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    storage_key = Column(String(400), nullable=False)
    uploaded_by_id = Column(Integer, ForeignKey("user.id"), nullable=False)


class ProductFeatureCost(Base):
    """One line of a feature's cost and profit estimate.

    side = 'cost' (what it takes: people's time, tools, hosting) or 'benefit' (what it brings:
    new revenue, money saved). kind = 'one_time' or 'monthly'. People's time can be entered as
    days x rate; amount is then days * rate.
    """
    __tablename__ = "cos_feature_cost"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("cos_feature.id"), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    label = Column(String(200), nullable=False)
    kind = Column(String(10), nullable=False, default="one_time")
    amount = Column(Numeric(14, 2), nullable=False, default=0)
    days = Column(Numeric(8, 2), nullable=True)
    rate = Column(Numeric(14, 2), nullable=True)
    note = Column(String(300), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)


FEATURE_EXTRA_TABLES = ["cos_product", "cos_feature_product", "cos_feature_attachment", "cos_feature_cost"]
DEFAULT_PRODUCTS = ["GMDI", "Build-Track", "Chief of Staff"]
