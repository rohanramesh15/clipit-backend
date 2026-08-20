"""
Isolated SQLAlchemy models for the Converse V2 prototype.

These live in their own `cv2_*` tables and have no foreign keys into the
production schema, so the prototype can be dropped in/out without migrations
and shares the existing database engine harmlessly. Login is skipped for the
prototype; everything is keyed by a single fixed `user_key` string.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class CV2Profile(Base):
    __tablename__ = "cv2_profile"

    id = Column(Integer, primary_key=True)
    user_key = Column(String, unique=True, index=True)  # fixed "prototype" for now
    level = Column(String, default="beginner")          # beginner | intermediate | advanced
    reason = Column(String, default="general")          # travel | work | family | partner | show | general
    english_support = Column(String, default="some")    # lots | some | minimal
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CV2Session(Base):
    __tablename__ = "cv2_session"

    id = Column(Integer, primary_key=True)
    user_key = Column(String, index=True)
    seed_type = Column(String, default="due_words")     # due_words | video | free
    seed_label = Column(String, nullable=True)
    seed_video_id = Column(String, nullable=True)
    level = Column(String)
    reason = Column(String)
    english_support = Column(String)
    due_words_json = Column(Text)                        # JSON list of {lemma, gloss}
    difficulty_nudge = Column(Integer, default=0)        # -n easier / +n harder (Phase 3)
    started_at = Column(DateTime, default=datetime.utcnow)


class CV2Turn(Base):
    __tablename__ = "cv2_turn"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, index=True)
    idx = Column(Integer)
    role = Column(String)                                # user | assistant
    text = Column(Text)
    meta_json = Column(Text, nullable=True)             # JSON: correction, suggestions, used words, translation
    created_at = Column(DateTime, default=datetime.utcnow)


class CV2Feedback(Base):
    __tablename__ = "cv2_feedback"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, index=True)
    turn_id = Column(Integer, nullable=True)
    kind = Column(String)                                # session: too_easy|too_hard ; correction: fine|wrong
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
