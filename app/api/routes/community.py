import secrets
import string
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.community_group import CommunityGroup
from app.models.community_membership import CommunityMembership
from app.models.community_vocab_list import CommunityVocabList
from app.models.community_vocab_word import CommunityVocabWord
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_word import UserVocabularyWord

router = APIRouter()


# ── Pydantic Schemas ─────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    language: str = "ko"
    is_public: bool = True
    member_permission: str = "all"  # 'all' | 'creator_only'


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    member_permission: Optional[str] = None


class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    language: str
    is_public: bool
    invite_code: str
    creator_id: int
    member_permission: str
    member_count: int
    created_at: str

    class Config:
        from_attributes = True


class GroupListResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    language: str
    is_public: bool
    member_count: int
    word_count: int
    list_count: int

    class Config:
        from_attributes = True


class MyGroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    language: str
    is_public: bool
    invite_code: str
    member_count: int
    word_count: int
    list_count: int
    role: str
    last_synced_at: str

    class Config:
        from_attributes = True


class VocabListCreate(BaseModel):
    name: str


class VocabWordCreate(BaseModel):
    word: str
    translation: str
    example: Optional[str] = None
    example_translation: Optional[str] = None


class CommunityVocabListResponse(BaseModel):
    id: int
    name: str
    word_count: int
    added_by: Optional[int]
    created_at: str

    class Config:
        from_attributes = True


class CommunityVocabWordResponse(BaseModel):
    id: int
    word: str
    translation: str
    example: Optional[str]
    example_translation: Optional[str]
    sort_order: int

    class Config:
        from_attributes = True


class JoinRequest(BaseModel):
    invite_code: Optional[str] = None
    group_id: Optional[int] = None


class SyncResponse(BaseModel):
    words_synced: int
    groups_synced: int


# ── Helper Functions ─────────────────────────────────────────────────────────

def generate_invite_code(length: int = 6) -> str:
    """Generate a random invite code"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def get_group_stats(db: Session, group_id: int) -> tuple:
    """Get list count and word count for a group"""
    list_count = db.query(func.count(CommunityVocabList.id)).filter(
        CommunityVocabList.group_id == group_id
    ).scalar() or 0

    word_count = db.query(func.count(CommunityVocabWord.id)).join(
        CommunityVocabList
    ).filter(
        CommunityVocabList.group_id == group_id
    ).scalar() or 0

    return list_count, word_count


# ── Public Group Discovery ───────────────────────────────────────────────────

@router.get("/groups", response_model=List[GroupListResponse])
async def list_public_groups(
    language: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all public groups, optionally filtered by language or search term"""
    query = db.query(CommunityGroup).filter(CommunityGroup.is_public == True)

    if language:
        query = query.filter(CommunityGroup.language == language)

    if search:
        query = query.filter(CommunityGroup.name.ilike(f"%{search}%"))

    groups = query.order_by(CommunityGroup.member_count.desc()).all()

    result = []
    for group in groups:
        list_count, word_count = get_group_stats(db, group.id)
        result.append(GroupListResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            language=group.language,
            is_public=group.is_public,
            member_count=group.member_count,
            word_count=word_count,
            list_count=list_count,
        ))

    return result


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get group details"""
    group = db.query(CommunityGroup).filter(CommunityGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check access - public groups or member
    if not group.is_public:
        membership = db.query(CommunityMembership).filter(
            CommunityMembership.group_id == group_id,
            CommunityMembership.user_id == current_user.id
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Access denied")

    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        language=group.language,
        is_public=group.is_public,
        invite_code=group.invite_code,
        creator_id=group.creator_id,
        member_permission=group.member_permission,
        member_count=group.member_count,
        created_at=group.created_at.isoformat(),
    )


@router.get("/groups/{group_id}/lists", response_model=List[CommunityVocabListResponse])
async def get_group_lists(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all vocab lists in a group"""
    group = db.query(CommunityGroup).filter(CommunityGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check access
    if not group.is_public:
        membership = db.query(CommunityMembership).filter(
            CommunityMembership.group_id == group_id,
            CommunityMembership.user_id == current_user.id
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Access denied")

    lists = db.query(CommunityVocabList).filter(
        CommunityVocabList.group_id == group_id
    ).order_by(CommunityVocabList.created_at.desc()).all()

    return [CommunityVocabListResponse(
        id=lst.id,
        name=lst.name,
        word_count=lst.word_count,
        added_by=lst.added_by,
        created_at=lst.created_at.isoformat(),
    ) for lst in lists]


@router.get("/lists/{list_id}/words", response_model=List[CommunityVocabWordResponse])
async def get_list_words(
    list_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all words in a community vocab list"""
    vocab_list = db.query(CommunityVocabList).filter(CommunityVocabList.id == list_id).first()
    if not vocab_list:
        raise HTTPException(status_code=404, detail="List not found")

    # Check access via group
    group = db.query(CommunityGroup).filter(CommunityGroup.id == vocab_list.group_id).first()
    if not group.is_public:
        membership = db.query(CommunityMembership).filter(
            CommunityMembership.group_id == vocab_list.group_id,
            CommunityMembership.user_id == current_user.id
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Access denied")

    words = db.query(CommunityVocabWord).filter(
        CommunityVocabWord.list_id == list_id
    ).order_by(CommunityVocabWord.sort_order).all()

    return [CommunityVocabWordResponse(
        id=w.id,
        word=w.word,
        translation=w.translation,
        example=w.example,
        example_translation=w.example_translation,
        sort_order=w.sort_order,
    ) for w in words]


# ── Group Management ─────────────────────────────────────────────────────────

@router.post("/groups", response_model=GroupResponse)
async def create_group(
    data: GroupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new community group"""
    # Generate unique invite code
    invite_code = generate_invite_code()
    while db.query(CommunityGroup).filter(CommunityGroup.invite_code == invite_code).first():
        invite_code = generate_invite_code()

    group = CommunityGroup(
        name=data.name,
        description=data.description,
        language=data.language,
        is_public=data.is_public,
        invite_code=invite_code,
        creator_id=current_user.id,
        member_permission=data.member_permission,
        member_count=1,
    )
    db.add(group)
    db.flush()

    # Add creator as member
    membership = CommunityMembership(
        user_id=current_user.id,
        group_id=group.id,
        role="creator",
        last_synced_at=datetime.utcnow(),
    )
    db.add(membership)
    db.commit()

    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        language=group.language,
        is_public=group.is_public,
        invite_code=group.invite_code,
        creator_id=group.creator_id,
        member_permission=group.member_permission,
        member_count=group.member_count,
        created_at=group.created_at.isoformat(),
    )


@router.put("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    data: GroupUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update group settings (creator only)"""
    group = db.query(CommunityGroup).filter(CommunityGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the creator can update the group")

    if data.name is not None:
        group.name = data.name
    if data.description is not None:
        group.description = data.description
    if data.member_permission is not None:
        group.member_permission = data.member_permission

    db.commit()

    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        language=group.language,
        is_public=group.is_public,
        invite_code=group.invite_code,
        creator_id=group.creator_id,
        member_permission=group.member_permission,
        member_count=group.member_count,
        created_at=group.created_at.isoformat(),
    )


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a group (creator only)"""
    group = db.query(CommunityGroup).filter(CommunityGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the creator can delete the group")

    db.delete(group)
    db.commit()

    return {"message": "Group deleted"}


# ── Membership ───────────────────────────────────────────────────────────────

@router.post("/join")
async def join_group(
    data: JoinRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Join a group by invite code or group_id (for public groups)"""
    group = None

    if data.invite_code:
        group = db.query(CommunityGroup).filter(
            CommunityGroup.invite_code == data.invite_code.upper()
        ).first()
        if not group:
            raise HTTPException(status_code=404, detail="Invalid invite code")
    elif data.group_id:
        group = db.query(CommunityGroup).filter(CommunityGroup.id == data.group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        if not group.is_public:
            raise HTTPException(status_code=403, detail="This group requires an invite code")
    else:
        raise HTTPException(status_code=400, detail="Provide invite_code or group_id")

    # Check if already a member
    existing = db.query(CommunityMembership).filter(
        CommunityMembership.group_id == group.id,
        CommunityMembership.user_id == current_user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already a member of this group")

    # Add membership
    membership = CommunityMembership(
        user_id=current_user.id,
        group_id=group.id,
        role="member",
        last_synced_at=datetime.utcnow(),
    )
    db.add(membership)

    # Update member count
    group.member_count += 1

    db.commit()

    return {"message": f"Joined {group.name}", "group_id": group.id}


@router.post("/leave")
async def leave_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Leave a group"""
    membership = db.query(CommunityMembership).filter(
        CommunityMembership.group_id == group_id,
        CommunityMembership.user_id == current_user.id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="Not a member of this group")

    group = db.query(CommunityGroup).filter(CommunityGroup.id == group_id).first()

    # Creator cannot leave (must delete group instead)
    if membership.role == "creator":
        raise HTTPException(status_code=400, detail="Creator cannot leave. Delete the group instead.")

    db.delete(membership)
    group.member_count -= 1
    db.commit()

    return {"message": "Left the group"}


@router.get("/my-groups", response_model=List[MyGroupResponse])
async def get_my_groups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all groups the user is a member of"""
    memberships = db.query(CommunityMembership).filter(
        CommunityMembership.user_id == current_user.id
    ).all()

    result = []
    for membership in memberships:
        group = db.query(CommunityGroup).filter(CommunityGroup.id == membership.group_id).first()
        if group:
            list_count, word_count = get_group_stats(db, group.id)
            result.append(MyGroupResponse(
                id=group.id,
                name=group.name,
                description=group.description,
                language=group.language,
                is_public=group.is_public,
                invite_code=group.invite_code,
                member_count=group.member_count,
                word_count=word_count,
                list_count=list_count,
                role=membership.role,
                last_synced_at=membership.last_synced_at.isoformat(),
            ))

    return result


@router.post("/sync", response_model=SyncResponse)
async def sync_community_vocab(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sync vocabulary from all enrolled community groups to user's personal vocab lists"""
    memberships = db.query(CommunityMembership).filter(
        CommunityMembership.user_id == current_user.id
    ).all()

    total_words_synced = 0
    groups_synced = 0

    for membership in memberships:
        group = db.query(CommunityGroup).filter(CommunityGroup.id == membership.group_id).first()
        if not group:
            continue

        # Get community vocab lists added after last sync
        community_lists = db.query(CommunityVocabList).filter(
            CommunityVocabList.group_id == group.id,
            CommunityVocabList.created_at > membership.last_synced_at
        ).all()

        # Also get words added to existing lists after last sync
        new_words = db.query(CommunityVocabWord).join(CommunityVocabList).filter(
            CommunityVocabList.group_id == group.id,
            CommunityVocabWord.created_at > membership.last_synced_at
        ).all()

        words_synced = 0

        # For each community list, create/update user's personal list
        for community_list in community_lists:
            # Check if user already has this list (by name + group reference)
            list_name = f"[{group.name}] {community_list.name}"
            existing_list = db.query(UserVocabularyList).filter(
                UserVocabularyList.user_id == current_user.id,
                UserVocabularyList.name == list_name
            ).first()

            if not existing_list:
                # Create new user list
                user_list = UserVocabularyList(
                    user_id=current_user.id,
                    name=list_name,
                    language=group.language,
                    word_count=0,
                )
                db.add(user_list)
                db.flush()

                # Copy all words from community list
                community_words = db.query(CommunityVocabWord).filter(
                    CommunityVocabWord.list_id == community_list.id
                ).all()

                for cw in community_words:
                    user_word = UserVocabularyWord(
                        list_id=user_list.id,
                        word=cw.word,
                        translation=cw.translation,
                        example=cw.example,
                        example_translation=cw.example_translation,
                        sort_order=cw.sort_order,
                    )
                    db.add(user_word)
                    words_synced += 1

                user_list.word_count = len(community_words)

        # Sync new words to existing lists
        for cw in new_words:
            community_list = db.query(CommunityVocabList).filter(
                CommunityVocabList.id == cw.list_id
            ).first()
            if not community_list:
                continue

            list_name = f"[{group.name}] {community_list.name}"
            user_list = db.query(UserVocabularyList).filter(
                UserVocabularyList.user_id == current_user.id,
                UserVocabularyList.name == list_name
            ).first()

            if user_list:
                # Check if word already exists
                existing_word = db.query(UserVocabularyWord).filter(
                    UserVocabularyWord.list_id == user_list.id,
                    UserVocabularyWord.word == cw.word
                ).first()

                if not existing_word:
                    user_word = UserVocabularyWord(
                        list_id=user_list.id,
                        word=cw.word,
                        translation=cw.translation,
                        example=cw.example,
                        example_translation=cw.example_translation,
                        sort_order=cw.sort_order,
                    )
                    db.add(user_word)
                    user_list.word_count += 1
                    words_synced += 1

        # Update last synced time
        membership.last_synced_at = datetime.utcnow()
        total_words_synced += words_synced
        if words_synced > 0:
            groups_synced += 1

    db.commit()

    return SyncResponse(words_synced=total_words_synced, groups_synced=groups_synced)


# ── Vocabulary Management ────────────────────────────────────────────────────

@router.post("/groups/{group_id}/lists", response_model=CommunityVocabListResponse)
async def create_vocab_list(
    group_id: int,
    data: VocabListCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a vocabulary list to a group"""
    group = db.query(CommunityGroup).filter(CommunityGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check membership
    membership = db.query(CommunityMembership).filter(
        CommunityMembership.group_id == group_id,
        CommunityMembership.user_id == current_user.id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Must be a member to add lists")

    # Check permission
    if group.member_permission == "creator_only" and group.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the creator can add lists to this group")

    vocab_list = CommunityVocabList(
        group_id=group_id,
        name=data.name,
        added_by=current_user.id,
        word_count=0,
    )
    db.add(vocab_list)
    db.commit()

    return CommunityVocabListResponse(
        id=vocab_list.id,
        name=vocab_list.name,
        word_count=vocab_list.word_count,
        added_by=vocab_list.added_by,
        created_at=vocab_list.created_at.isoformat(),
    )


@router.post("/lists/{list_id}/words", response_model=CommunityVocabWordResponse)
async def add_word_to_list(
    list_id: int,
    data: VocabWordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a word to a community vocab list"""
    vocab_list = db.query(CommunityVocabList).filter(CommunityVocabList.id == list_id).first()
    if not vocab_list:
        raise HTTPException(status_code=404, detail="List not found")

    group = db.query(CommunityGroup).filter(CommunityGroup.id == vocab_list.group_id).first()

    # Check membership
    membership = db.query(CommunityMembership).filter(
        CommunityMembership.group_id == vocab_list.group_id,
        CommunityMembership.user_id == current_user.id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Must be a member to add words")

    # Check permission
    if group.member_permission == "creator_only" and group.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the creator can add words to this group")

    # Check for duplicate
    existing = db.query(CommunityVocabWord).filter(
        CommunityVocabWord.list_id == list_id,
        CommunityVocabWord.word == data.word
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Word already exists in this list")

    # Get next sort order
    max_order = db.query(func.max(CommunityVocabWord.sort_order)).filter(
        CommunityVocabWord.list_id == list_id
    ).scalar() or 0

    word = CommunityVocabWord(
        list_id=list_id,
        word=data.word,
        translation=data.translation,
        example=data.example,
        example_translation=data.example_translation,
        sort_order=max_order + 1,
        added_by=current_user.id,
    )
    db.add(word)

    # Update word count
    vocab_list.word_count += 1

    db.commit()

    return CommunityVocabWordResponse(
        id=word.id,
        word=word.word,
        translation=word.translation,
        example=word.example,
        example_translation=word.example_translation,
        sort_order=word.sort_order,
    )


@router.delete("/lists/{list_id}")
async def delete_vocab_list(
    list_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a vocab list (creator or list adder only)"""
    vocab_list = db.query(CommunityVocabList).filter(CommunityVocabList.id == list_id).first()
    if not vocab_list:
        raise HTTPException(status_code=404, detail="List not found")

    group = db.query(CommunityGroup).filter(CommunityGroup.id == vocab_list.group_id).first()

    # Check permission - must be group creator or list adder
    if vocab_list.added_by != current_user.id and group.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the list creator or group owner can delete this list")

    db.delete(vocab_list)
    db.commit()

    return {"message": "List deleted"}


# ── Seed Korean 3 Community Group ─────────────────────────────────────────────

# Korean 3 vocab organized by lesson and conversation
# Format: (word, translation, example_sentence)
KOREAN3_VOCAB = {
    "L11_C1": {
        "name": "Lesson 11 - Conversation 1",
        "words": [
            ("갈비", "ribs (Korean BBQ)", "제가 좋아하는 한국 음식은 갈비예요."),
            ("물", "water", "깨끗한 물을 마시세요!"),
            ("바닷가", "beach", "주말에 친구하고 바닷가에서 놀 거예요."),
            ("밴쿠버", "Vancouver", "밴쿠버는 캐나다에 있는 큰 도시예요."),
            ("불고기", "bulgogi", "제 동생은 불고기를 무척 좋아해서 매일 먹어요."),
            ("생활", "life; living", "대학교 생활이 바쁘지만 재미있어요."),
            ("어젯밤", "last night", "어젯밤에 잠을 잘 못 잤어요."),
            ("차", "tea; car", "지금 너무 더워서 차가운 차를 마시고 싶어요."),
            ("청바지", "jeans", "백화점에서 청바지하고 스웨터를 샀어요."),
            ("캐나다", "Canada", "캐나다에 한국 사람들이 많이 살아요?"),
            ("잔", "counter for cups/glasses", "아침에 커피 두 잔을 마셨어요."),
            ("어떤", "what kind of", "어떤 사람을 좋아해요?"),
            ("되다", "to become", "저는 의사가 되고 싶어요."),
            ("눈이 오다", "to snow", "밤에 눈이 많이 왔어요."),
            ("사귀다", "to make friends; to date", "한국 친구를 사귀고 싶어요."),
            ("쓰다", "to use", "제니는 친구와 같이 부엌을 써요."),
            ("착하다", "to be kind-hearted", "저는 착한 사람이 좋아요."),
            ("친절하다", "to be friendly; kind", "미나는 친절한 사람이에요."),
            ("-(으)ㄹ래요", "Do you want to...? / I want to...", "이번 주말에 같이 한국 식당에 갈래요?"),
            ("-고 있다", "to be doing (progressive)", "지금 앤디가 음악을 듣고 있어요."),
            ("-고 계시다", "to be doing (honorific progressive)", "선생님께서 케이크를 만들고 계세요."),
            ("잘 됐네요", "That's great / It sounds good", "시험이 끝났어요. 잘 됐네요!"),
            ("1이 어떻게 됩니까/돼요/되세요?", "What is your [noun]? (polite inquiry form)", "성함이 어떻게 되세요?"),
        ]
    },
    "L11_C2": {
        "name": "Lesson 11 - Conversation 2",
        "words": [
            ("골프", "golf", "저는 골프를 못 쳐요. 하지만 샘은 골프를 잘 쳐요."),
            ("기차", "train", "기차를 타고 놀러 갔어요."),
            ("연극", "play (theater)", "저녁에 같이 연극을 볼래요?"),
            ("인터넷", "internet", "인터넷으로 콘서트 표를 샀어요."),
            ("입구", "entrance", "미나하고 제니를 지하철역 입구에서 만났어요."),
            ("끝나다", "to be over; finished", "오늘 수업이 일찍 끝났어요."),
            ("쉬다", "to rest", "피곤해서 오늘은 좀 쉬고 싶어요."),
            ("알아보다", "to find out; check out", "지금 한국 비행기 가격을 인터넷으로 알아보고 있어요."),
            ("찾다", "to find; look for", "뭐 찾으세요? 남자 모자를 찾고 있어요."),
            ("춤을 추다", "to dance", "수지는 춤을 잘 추는데 미나는 못 춰요."),
            ("힘이 들다", "to be hard; difficult", "요즘 일이 많아서 피곤하고 힘들어요."),
            ("다", "all", "배고파서 음식을 다 먹고 싶어요."),
            ("벌써", "already", "벌써 12시예요? 몰랐어요."),
            ("N까지", "to / until / through (time)", "집에서 학교까지 한 시간이 걸려요."),
            ("N밖에", "nothing but; only", "집에서 학교까지 걸어서 5분밖에 안 걸려요."),
            ("N부터", "from (time)", "매일 7시 45분부터 8시 35분까지 한국어 수업을 들어요."),
            ("N이나", "as much/many as", "지난 학기에 다섯 과목이나 들었어요?"),
            ("V-(으)ㄹ까요?", "Shall I/we...? / Do you think...?", "내일 같이 영화 볼까요?"),
            ("글쎄요", "Well; It's hard to say", "글쎄요. 잘 모르겠어요."),
            ("몇 과목", "how many subjects", "이번 학기에 몇 과목을 들어요?"),
            ("스무 명", "20 people", "우리 반에 스무 명이 있어요."),
            ("스물 한 명", "21 people", "파티에 스물 한 명이 왔어요."),
            ("공연하다", "to perform", "토요일 저녁에 저희 밴드가 Hop에서 공연합니다."),
            ("소극장", "small theater", "우리는 소극장에서 공연할 거예요."),
            ("골프장", "golf course", "골프장에서 골프를 쳤어요."),
        ]
    },
    "L12_C1": {
        "name": "Lesson 12 - Conversation 1",
        "words": [
            ("데", "place", "항상 서울 식당만 갔는데 오늘은 다른 데에 가고 싶어요."),
            ("동부", "East Coast", "제 할머니께서 미국 동부에 살고 계세요."),
            ("막내", "youngest child", "저는 막내예요. 그래서 동생이 없어요."),
            ("바지", "pants", "제니가 바지를 입었어요."),
            ("밤", "night", "밤에 안 자고 공부했어요."),
            ("부엌", "kitchen", "부엌에서 음식을 만들어서 친구하고 같이 먹었어요."),
            ("셔츠", "shirt", "셔츠를 입고 있는 사람이 마이클이에요."),
            ("형제", "siblings", "저는 형제가 없어요. 외동이에요."),
            ("첫", "first (pre-noun)", "첫눈이 왔어요."),
            ("다르다", "to be different", "한국이 미국하고 문화가 달라요."),
            ("피곤하다", "to be tired", "어제 늦게 자서 지금 피곤해요."),
            ("결혼하다", "to get married", "저희 부모님이 22년 전에 결혼하셨어요."),
            ("기다리다", "to wait", "친구를 기다리고 있어요."),
            ("자라다", "to grow up", "아이가 자라서 어른이 되었어요."),
            ("태어나다", "to be born", "제가 태어난 곳은 한국이에요."),
            ("아직", "still; yet", "숙제를 아직 못 했어요."),
            ("N까지", "including (particle)", "1시부터 2시까지 카페에서 아르바이트해요."),
            ("째", "ordinal number counter", "저는 셋째예요."),
            ("번째", "ordinal number counter", "첫 번째 문제가 어려웠어요."),
            ("-겠-", "may/will (conjecture)", "늦게 자서 피곤하겠어요."),
            ("-아서/어서", "clausal connective (sequential)", "도서관에 가서 공부했어요."),
            ("자매", "sisters; female siblings", "저는 언니만 있어요. 우리는 자매예요."),
            ("배고프다", "to be hungry", "아침을 못 먹어서 너무 배고파요."),
            ("배부르다", "to be full", "많이 먹어서 배불러요."),
            ("기분이 좋다", "to be in a good mood", "숙제를 다 해서 기분이 좋아요."),
        ]
    },
    "L12_C2": {
        "name": "Lesson 12 - Conversation 2",
        "words": [
            ("눈", "1) eyes  2) snow", "제 눈은 갈색이에요."),
            ("색", "color", "저는 흰색을 좋아해요."),
            ("색깔", "color", "무슨 색깔을 좋아해요?"),
            ("안경", "eyeglasses", "미나는 안경을 샀어요. 눈이 나빠요."),
            ("한복", "traditional Korean dress", "설날에 한복을 많이 입어요."),
            ("형님", "older brother (honorific)", "형님께 선물을 드렸어요."),
            ("끼다", "to wear (glasses/gloves/rings)", "수지는 손에 반지를 꼈어요."),
            ("나오다", "to come out", "아침 일찍 집에서 나왔어요."),
            ("다니다", "to attend", "앤디는 다트머스 대학에 다녀요."),
            ("닮다", "to resemble", "미나하고 수지는 얼굴이 닮았어요."),
            ("쓰다", "to wear headgear", "모자를 쓴 사람이 누구예요?"),
            ("입다", "to wear; put on (clothes)", "마이클은 오늘 멋있는 옷을 입었어요."),
            ("벗다", "to take off (clothes)", "더워서 패딩을 벗었어요."),
            ("N이랑", "with; and", "유미랑 미나는 베프예요."),
            ("까맣다", "to be black", "제 눈은 까만 색이에요."),
            ("노랗다", "to be yellow", "노란 옷을 입은 사람이 제니예요."),
            ("빨갛다", "to be red", "빨간 사과를 먹고 싶어요."),
            ("파랗다", "to be blue", "파란 하늘이 참 예뻐요."),
            ("하얗다", "to be white", "하얀 눈이 많이 왔어요."),
            ("키가 크다", "to be tall", "톰은 키가 커요."),
            ("키가 작다", "to be short (height)", "제리는 키가 작아요."),
            ("오래", "for a long time", "저는 한국에서 오래 살았어요."),
            ("어머", "Oh my! Dear me!", "어머! 정말요?"),
            ("V/A-네요", "sentence ending indicating speaker's reaction", "날씨가 정말 좋네요!"),
            ("V-(으)ㄴ", "noun-modifying form (past tense verb)", "어제 먹은 음식이 뭐예요?"),
        ]
    },
    "L13_C1": {
        "name": "Lesson 13 - Conversation 1",
        "words": [
            ("도시", "city", "저는 도시에서 살고 싶어요."),
            ("몸조리", "taking care of one's health (esp. while recovering)", "몸조리 잘 하세요!"),
            ("엄마", "mom", "엄마가 보고 싶어서 전화를 했어요."),
            ("감기에 걸리다", "to catch a cold", "요즘 피곤해서 감기에 걸렸어요."),
            ("돕다", "to help", "저를 좀 도와 주세요!"),
            ("바꾸다", "to change (something)", "제니가 헤어스타일을 바꿨어요."),
            ("빨래하다", "to do laundry", "저는 주말마다 빨래하고 청소해요."),
            ("부치다", "to send / to mail", "부모님께 발렌타인스 카드를 부쳤어요."),
            ("비(가) 오다", "to rain", "지금 비가 많이 오네요."),
            ("빌리다", "to borrow", "도서관에서 책을 빌렸어요."),
            ("빌려주다", "to lend", "펜 좀 빌려주세요."),
            ("실례하다", "to excuse oneself / excuse me", "실례하지만, 누구세요?"),
            ("배(가) 고프다", "to be hungry", "아침을 못 먹어서 배고파요."),
            ("나중에", "later", "나중에 다시 전화 주세요."),
            ("다시", "again", "다시 말씀해 주세요."),
            ("여보세요", "hello (on the phone)", "여보세요?"),
            ("이따가", "in a little while / later (today)", "이따가 만나요."),
            ("이젠", "now (contracted from 이제는)", "이젠 한국어가 어렵지 않아요."),
            ("-(으)ㄹ게요", "I'll ~ (speaker's promise/intention)", "제가 도와줄게요."),
            ("-아/어 주다", "to do (something) for someone", "여기에 이름을 좀 써 주세요."),
            ("-아/어야 되다", "have to / must (do something)", "주말에 빨래해야 돼요."),
            ("따르릉", "ring ring (phone sound)", "따르릉, 따르릉"),
            ("허리", "lower back / waist", "허리가 아파서 병원에 갔어요."),
            ("어깨", "shoulder", "어제 어깨 운동을 많이 했어요."),
            ("푹 쉬다", "to rest well / get plenty of rest", "주말에 푹 쉬세요!"),
        ]
    },
    "L13_C2": {
        "name": "Lesson 13 - Conversation 2",
        "words": [
            ("그동안", "in the meantime / during that time / lately", "그동안 보고 싶었어요. 잘 지냈어요?"),
            ("뉴스", "news", "어제 뉴스를 봤어요?"),
            ("메시지", "message", "친구한테 메시지를 받았어요."),
            ("물가", "(cost of) prices / cost of living", "요즘 물가가 비싸요."),
            ("반", "half", "아니요. 반밖에 못 했어요."),
            ("발", "foot", "발이 아파서 하이힐을 못 신었어요."),
            ("비", "rain", "비가 많이 오네요!"),
            ("신발", "shoes", "신발을 백화점에서 샀는데 좀 비쌌어요."),
            ("인터뷰", "interview", "내일 인터뷰가 있어서 오늘 준비할 거예요."),
            ("새", "new (modifier)", "새 신발을 사고 싶어요."),
            ("시끄럽다", "to be loud / noisy", "헤비 메탈 음악은 좀 시끄러워요."),
            ("남기다", "to leave (something) behind / leave over", "배가 불러서 음식을 남겼어요."),
            ("돈이 들다", "to cost money / take money", "레고 때문에 돈이 많이 들었어요."),
            ("들어가다", "to enter / go in", "동생이 MIT에 들어갔어요."),
            ("부탁하다", "to ask a favor", "친구가 저한테 부탁했어요."),
            ("그만", "stop (doing) / no more", "그만 하세요."),
            ("때문에", "because of (noun)", "눈 때문에 차가 많이 막혀요."),
            ("잠깐만", "just a moment / hold on", "잠깐만 기다려 주세요."),
            ("-겠-", "would (intention/will)", "제가 하겠습니다!"),
            ("잘 먹겠습니다", "I'll enjoy the meal (lit. I will eat well)", "잘 먹겠습니다!"),
            ("00 좀 부탁합니다", "May I speak to 00?", "여보세요? 제니 씨 좀 부탁합니다."),
            ("생활비", "living expenses", "부모님께 생활비를 받았어요."),
            ("돈을 벌다", "to earn money", "아르바이트해서 돈을 많이 벌었어요."),
            ("통화(하다)", "to talk on the phone", "누구하고 통화하고 있어요?"),
            ("이", "tooth / teeth", "이가 아파요."),
        ]
    },
    "L14_C1": {
        "name": "Lesson 14 - Conversation 1",
        "words": [
            ("공항", "airport", "공항에 비행기도 많고 사람들도 많아요."),
            ("기사", "driver (taxi, bus, etc.)", "버스 기사님이 친절하세요."),
            ("길", "road / street / way", "오늘이 토요일이라서 길이 많이 막히네요."),
            ("모레", "the day after tomorrow", "내일 모레가 토요일이에요."),
            ("손님", "guest / customer", "식당에 손님이 많이 왔어요."),
            ("아저씨", "mister / middle-aged man", "아저씨께서 길을 가르쳐 주셨어요."),
            ("안부", "regards / greetings (to pass along)", "부모님께 제 안부 좀 전해 주세요."),
            ("연락", "contact / getting in touch", "서울에서 연락이 왔어요?"),
            ("연락하다", "to contact / to get in touch", "동생이 오랜만에 저한테 연락했어요."),
            ("인천", "Incheon (city west of Seoul)", "인천에 공항이 있어요."),
            ("전", "before", "수업 전에 화장실에 갔어요."),
            ("후", "after", "수업 후에 아침을 먹으러 카페에 갔어요."),
            ("휴일", "day off / holiday", "휴일에도 공부하세요?"),
            ("공휴일", "public holiday / national holiday", "공휴일은 모두가 좋아하는 날이에요."),
            ("적어도", "at least", "적어도 오늘까지 숙제를 다 하세요."),
            ("빨리", "quickly / fast", "빨리 먹고 수업에 가야 돼요."),
            ("건너다", "to cross (a street, river, etc.)", "저기에서 길을 건너세요."),
            ("(돈을) 내다", "to pay (money)", "어제 식당에서 누가 돈을 냈어요?"),
            ("수고하다", "to work hard / put in effort", "수고하셨습니다."),
            ("운전하다", "to drive", "택시 기사 아저씨께서 운전을 잘 하세요."),
            ("전하다", "to pass on / convey / deliver (a message)", "제 말을 전해 주세요."),
            ("N(이)라서", "because (it) is N", "학생이라서 돈이 많이 없어요."),
            ("V/A-지 못하다", "cannot / was unable to (do something)", "어제 너무 바빠서 전화하지 못했어요."),
            ("내일 모레", "the day after tomorrow", "내일 모레 만나요."),
            ("전화비", "phone bill", "7월 전화비가 많이 나왔어요."),
        ]
    },
    "L14_C2": {
        "name": "Lesson 14 - Conversation 2",
        "words": [
            ("게임", "game", "비디오 게임을 좋아하세요?"),
            ("계단", "stairs", "계단 옆에 교실이 있어요."),
            ("곳", "place / spot", "보스턴에서 좋아하는 곳이 어디예요?"),
            ("노래방", "karaoke (room)", "노래방에 가서 친구하고 놀았어요."),
            ("목소리", "voice", "제니는 목소리가 작아요."),
            ("엘리베이터", "elevator", "엘리베이터에 4명이 탔어요."),
            ("웬일", "what's the matter? / what brings you here?", "웬일이에요?"),
            ("정류장", "(bus) stop", "버스 정류장이 어디예요?"),
            ("출구", "exit", "지하철 출구에서 만나요."),
            ("큰아버지", "uncle (father's older brother)", "큰아버지께서 키가 아주 크세요."),
            ("택시비", "taxi fare", "택시비가 많이 나왔어요."),
            ("휴게실", "lounge / break room", "휴게실에서 커피를 마셨어요."),
            ("더", "more", "밥을 더 주세요."),
            ("배(가) 부르다", "to be full (after eating)", "밥을 많이 먹어서 배불러요."),
            ("졸리다", "to be drowsy / sleepy", "어제 늦게 자서 지금 너무 졸려요."),
            ("(노래) 부르다", "to sing (a song)", "노래방에서 노래를 많이 불렀어요."),
            ("도착하다", "to arrive", "한국 인천 공항에 도착했어요."),
            ("마중 나오다", "to come out to greet/pick someone up (toward speaker)", "친구가 공항에 마중 나왔어요."),
            ("마중 나가다", "to go out to greet/pick someone up (away from speaker)", "내일 부모님을 마중 나갈 거예요."),
            ("복잡하다", "to be crowded / complicated", "뉴욕은 차가 많아서 교통이 복잡해요."),
            ("(목이) 마르다", "to be thirsty", "목이 말라서 물을 마시고 싶어요."),
            ("졸다", "to doze off / nod off", "수업 시간에 졸지 마세요!"),
            ("A-게", "adverbial suffix (turns adjective into adverb)", "어제 늦게 자서 오늘 아침에도 일찍 못 일어났어요."),
            ("-지 말다 (마세요)", "don't (do something)", "수업 시간에 늦지 마세요."),
            ("-지 마세요", "don't (do something) — polite command form", "핸드폰을 오래 보지 마세요. 눈이 아파요."),
        ]
    },
    "L15_C1": {
        "name": "Lesson 15 - Conversation 1",
        "words": [
            ("까만색", "black (color)", "까만색 고양이가 귀여워요."),
            ("사이즈", "size", "사이즈가 큰 셔츠를 샀어요."),
            ("세일", "sale", "세일이라서 옷을 싸게 샀어요."),
            ("양말", "socks", "날씨가 추워서 따뜻한 양말을 신었어요."),
            ("운동화", "sneakers / athletic shoes", "운동화를 사러 백화점에 갔어요."),
            ("점원", "store clerk / salesperson", "백화점 점원이 친절했어요."),
            ("금방", "soon / in a moment", "수업이 금방 시작할 거예요."),
            ("어서", "quickly / please (welcoming)", "어서 오세요."),
            ("번 (counter)", "times (counter for occurrences)", "책을 두 번 읽었어요."),
            ("켤레 (counter)", "pair (counter for shoes/socks)", "양말 한 켤레를 샀어요."),
            ("V-(으)ㄹ 수 있다", "can (do something)", "컴퓨터 좀 쓸 수 있을까요?"),
            ("V-(으)ㄹ 수 없다", "cannot (do something)", "수업이 있어서 지금 갈 수 없어요."),
            ("갈아입다", "to change (clothes)", "아침에 옷을 갈아입었어요."),
            ("갈아타다", "to transfer (transportation)", "뉴욕에서 비행기를 갈아타야 돼요."),
            ("갖고 가다/오다", "to take/bring (something) along", "친구가 선물을 갖고 왔어요."),
            ("갖고 다니다", "to carry around (regularly)", "가방에 물을 갖고 다녀요."),
            ("갖다 놓다", "to put / place (something somewhere)", "이 케이크를 책상 위에 갖다 놓으세요."),
            ("갖다 주다", "to bring (something) to someone", "케이크를 미나한테 갖다 주세요."),
            ("걸어가다", "to go on foot / walk (away)", "학교에 걸어갔어요."),
            ("걸어오다", "to come on foot / walk (toward)", "학교에 걸어왔어요."),
            ("걸어다니다", "to walk around / commute on foot", "저는 학교에 걸어다녀요."),
            ("돌아오다", "to come back / return", "언제 한국에서 돌아왔어요?"),
            ("타고 가다/오다", "to go/come by (vehicle)", "버스를 타고 학교에 갔어요."),
            ("타고 다니다", "to commute by (vehicle)", "저는 학교에 버스를 타고 다녀요."),
            ("N에", "for / per (price/quantity)", "사과 세 개에 삼 천원이에요."),
        ]
    },
}


@router.post("/seed-korean3")
async def seed_korean3_community(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create the 'Korean 3 황선생님' community group with all vocabulary.
    Only creates if it doesn't already exist.
    """
    group_name = "Korean 3 황선생님"

    # Check if already exists
    existing = db.query(CommunityGroup).filter(CommunityGroup.name == group_name).first()
    if existing:
        return {
            "message": "Group already exists",
            "group_id": existing.id,
            "invite_code": existing.invite_code,
        }

    # Generate unique invite code
    invite_code = generate_invite_code()
    while db.query(CommunityGroup).filter(CommunityGroup.invite_code == invite_code).first():
        invite_code = generate_invite_code()

    # Create the group
    group = CommunityGroup(
        name=group_name,
        description="Korean 3 vocabulary from Dartmouth. Lessons 11-15.",
        language="ko",
        is_public=True,
        invite_code=invite_code,
        creator_id=current_user.id,
        member_permission="creator_only",
        member_count=1,
    )
    db.add(group)
    db.flush()

    # Add creator as member
    membership = CommunityMembership(
        user_id=current_user.id,
        group_id=group.id,
        role="creator",
        last_synced_at=datetime.utcnow(),
    )
    db.add(membership)

    # Create vocab lists for each lesson
    total_words = 0
    lists_created = 0

    for lesson_key, lesson_data in KOREAN3_VOCAB.items():
        vocab_list = CommunityVocabList(
            group_id=group.id,
            name=lesson_data["name"],
            added_by=current_user.id,
            word_count=len(lesson_data["words"]),
        )
        db.add(vocab_list)
        db.flush()

        # Add words
        for idx, (word, translation, example) in enumerate(lesson_data["words"]):
            vocab_word = CommunityVocabWord(
                list_id=vocab_list.id,
                word=word,
                translation=translation,
                example=example,
                sort_order=idx,
                added_by=current_user.id,
            )
            db.add(vocab_word)
            total_words += 1

        lists_created += 1

    db.commit()

    return {
        "message": "Korean 3 황선생님 community created successfully",
        "group_id": group.id,
        "invite_code": invite_code,
        "lists_created": lists_created,
        "words_added": total_words,
    }
