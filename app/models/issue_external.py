from sqlalchemy import Column, Integer, String, ForeignKey, JSON, DateTime, Text
from sqlalchemy.orm import relationship, backref
from app.db.base_class import Base


class CosIssueExternal(Base):
    """Where an Issue really came from when it was raised by another system (today: Build-Track "Report a problem").

    One row per such issue; the `issue` table itself is untouched. external_id is the sender's own code
    (e.g. BR-1042) and is unique, which is what makes the intake endpoint idempotent.
    """
    __tablename__ = "cos_issue_external"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issue.id"), nullable=False, unique=True)
    source = Column(String(40), nullable=False, default="build_track")
    external_id = Column(String(80), nullable=False, unique=True)
    client_report_id = Column(String(80))
    reporter = Column(JSON)
    screen = Column(JSON)
    breadcrumbs = Column(JSON)
    device = Column(JSON)
    project = Column(JSON)
    occurred_at = Column(DateTime)
    received_at = Column(DateTime)
    screenshot_key = Column(String(300))
    screenshot_mime = Column(String(40))
    callback_url = Column(Text)
    last_callback_status = Column(String(300))
    last_callback_at = Column(DateTime)

    # `Issue.external` (one-to-one) without touching the Issue model.
    issue = relationship("Issue", backref=backref("external", uselist=False))
