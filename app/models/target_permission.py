from sqlalchemy import Column, Integer, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base_class import Base

class TargetPermission(Base):
    __tablename__ = "target_permission"

    id = Column(Integer, primary_key=True, index=True)
    grantee_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    target_user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    
    can_score = Column(Boolean, default=False)
    can_add_targets = Column(Boolean, default=False)
    
    granted_by_id = Column(Integer, ForeignKey("user.id"), nullable=False)

    # Relationships
    grantee = relationship("User", foreign_keys=[grantee_id])
    target_user = relationship("User", foreign_keys=[target_user_id])
    granted_by = relationship("User", foreign_keys=[granted_by_id])
