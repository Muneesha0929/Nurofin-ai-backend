from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base

class TargetScore(Base):
    __tablename__ = "target_score"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, ForeignKey("target.id"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    score = Column(Numeric(5, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('target_id', 'reviewer_id', name='uix_target_reviewer'),
    )

    # Relationships
    target = relationship("Target", back_populates="scores")
    reviewer = relationship("User")
