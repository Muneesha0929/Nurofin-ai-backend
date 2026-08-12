from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.base_class import Base


class PerformanceReview(Base):
    __tablename__ = "performancereview"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    quarter_id = Column(Integer, ForeignKey("quarter.id"), nullable=True)

    # CEO given performance mark (0 - 100)
    score = Column(Float, default=0.0)
    rating = Column(String, default="Needs Improvement")
    comments = Column(Text)

    # Salary derived from performance
    salary_before = Column(Float, default=0.0)
    salary_after = Column(Float, default=0.0)
    increment_pct = Column(Float, default=0.0)

    reviewed_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])
