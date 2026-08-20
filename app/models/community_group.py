from sqlalchemy import Column, String, Integer, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from .base import BaseModel


class CommunityGroup(BaseModel):
    """A community group for sharing vocabulary lists"""
    __tablename__ = "community_groups"

    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    language = Column(String(10), nullable=False, default="ko")
    is_public = Column(Boolean, default=True, nullable=False)
    invite_code = Column(String(10), unique=True, nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    member_permission = Column(String(20), default="all", nullable=False)  # 'all' | 'creator_only'
    member_count = Column(Integer, default=1, nullable=False)

    # Relationships
    creator = relationship("User", backref="created_groups")
    memberships = relationship("CommunityMembership", back_populates="group", cascade="all, delete-orphan")
    vocab_lists = relationship("CommunityVocabList", back_populates="group", cascade="all, delete-orphan")
