from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.base_class import Base


class IssueFollowup(Base):
    __tablename__ = "issuefollowup"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issue.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    message = Column(Text, nullable=False)

    issue = relationship("Issue", back_populates="followups")
    user = relationship("User")
