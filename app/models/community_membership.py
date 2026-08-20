from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from .base import BaseModel


class CommunityMembership(BaseModel):
    """Tracks user membership in community groups"""
    __tablename__ = "community_memberships"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), default="member", nullable=False)  # 'creator' | 'member'
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Unique constraint - user can only be in a group once
    __table_args__ = (
        UniqueConstraint('user_id', 'group_id', name='uq_user_group'),
    )

    # Relationships
    user = relationship("User", backref="community_memberships")
    group = relationship("CommunityGroup", back_populates="memberships")
