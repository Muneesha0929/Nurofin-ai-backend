from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base_class import Base

class Target(Base):
    __tablename__ = "target"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    month = Column(String, index=True, nullable=False)  # e.g., '2026-09'
    is_global = Column(Boolean, default=False)
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    
    # Scoring - visible only to CEO and delegated scorers
    score = Column(Float, nullable=True)
    scored_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    scored_by = relationship("User", foreign_keys=[scored_by_id])
