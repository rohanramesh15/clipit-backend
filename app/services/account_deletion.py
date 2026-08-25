"""Shared, transaction-safe cleanup for a local ClipIt account."""

from sqlalchemy.orm import Session

from app.models.chat import ChatSavedWord, ChatSession
from app.models.chat_memory import ChatMemoryFact
from app.models.community_group import CommunityGroup
from app.models.community_membership import CommunityMembership
from app.models.converse_v2 import CV2Feedback, CV2Session, CV2Turn
from app.models.deck_settings import DeckSettings
from app.models.user import User
from app.models.user_anki_progress import UserAnkiProgress
from app.models.user_flashcard_progress import UserFlashcardProgress
from app.models.user_language_profile import UserLanguageProfile
from app.models.user_mined_word import UserMinedWord
from app.models.user_review_history import UserReviewHistory
from app.models.user_video_watch import UserVideoWatch
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_settings import UserVocabularySettings


def delete_local_user_data(db: Session, user: User) -> None:
    """Stage removal of every record owned by ``user`` in the active transaction.

    This deliberately does not commit. Callers can validate with ``flush()``
    and coordinate the operation with Supabase Auth before making it durable.
    """
    user_id = user.id
    cv2_session_ids = db.query(CV2Session.id).filter(CV2Session.user_id == user_id).subquery()
    db.query(CV2Feedback).filter(CV2Feedback.session_id.in_(cv2_session_ids)).delete(synchronize_session=False)
    db.query(CV2Turn).filter(CV2Turn.session_id.in_(cv2_session_ids)).delete(synchronize_session=False)
    db.query(CV2Session).filter(CV2Session.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatMemoryFact).filter(ChatMemoryFact.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatSavedWord).filter(ChatSavedWord.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.user_id == user_id).delete(synchronize_session=False)
    db.query(UserReviewHistory).filter(UserReviewHistory.user_id == user_id).delete(synchronize_session=False)
    db.query(UserFlashcardProgress).filter(UserFlashcardProgress.user_id == user_id).delete(synchronize_session=False)
    db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).delete(synchronize_session=False)
    db.query(UserAnkiProgress).filter(UserAnkiProgress.user_id == user_id).delete(synchronize_session=False)
    db.query(UserMinedWord).filter(UserMinedWord.user_id == user_id).delete(synchronize_session=False)
    db.query(DeckSettings).filter(DeckSettings.user_id == user_id).delete(synchronize_session=False)
    db.query(UserVocabularySettings).filter(UserVocabularySettings.user_id == user_id).delete(synchronize_session=False)
    db.query(UserVocabularyList).filter(UserVocabularyList.user_id == user_id).delete(synchronize_session=False)
    db.query(UserLanguageProfile).filter(UserLanguageProfile.user_id == user_id).delete(synchronize_session=False)
    db.query(CommunityMembership).filter(CommunityMembership.user_id == user_id).delete(synchronize_session=False)
    db.query(CommunityGroup).filter(CommunityGroup.creator_id == user_id).delete(synchronize_session=False)
    db.delete(user)
