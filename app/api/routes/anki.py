"""
Anki deck import routes.

Imports Anki vocabulary with progress data into a "vocabulary bank".
When users encounter these words while watching videos, flashcards
are created with the video source and Anki progress is applied.
"""

import os
import tempfile
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_anki_progress import UserAnkiProgress
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_word import UserVocabularyWord
from app.services.anki_import_service import (
    extract_apkg,
    parse_anki_database,
    convert_to_fsrs_params,
    cleanup_temp_dir,
    AnkiImportError,
)

router = APIRouter()


@router.post("/import")
async def import_anki_deck(
    file: UploadFile = File(...),
    language: str = Form(default="ko"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Import an Anki .apkg deck file.

    Imports vocabulary words with their scheduling progress into a vocabulary bank.
    These words won't become flashcards until the user encounters them in a video.
    When that happens, the Anki progress (reps, interval, due date) is applied.

    Args:
        file: The .apkg file to import
        language: Target language code (ko, uk)
    """
    if not file.filename.endswith('.apkg'):
        raise HTTPException(status_code=400, detail="File must be an .apkg file")

    temp_apkg = None
    temp_dir = None

    try:
        # Save the uploaded file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.apkg') as tmp:
            content = await file.read()
            tmp.write(content)
            temp_apkg = tmp.name

        # Extract and parse the Anki package
        temp_dir, db_path = extract_apkg(temp_apkg)
        anki_data = parse_anki_database(db_path, target_language=language)

        deck_name = anki_data['deck_name']

        # Create a vocabulary list for this Anki deck
        vocab_list = UserVocabularyList(
            user_id=current_user.id,
            name=f"Anki: {deck_name}",
            language=language,
            word_count=0,
        )
        db.add(vocab_list)
        db.flush()  # Get the list ID

        # Import vocabulary
        imported_count = 0
        skipped_count = 0
        updated_count = 0
        skipped_reasons = []
        batch_size = 500

        for i, card in enumerate(anki_data['cards']):
            word = card['front']
            translation = card['back'] or ""

            # Skip empty cards
            if not word or not word.strip():
                skipped_count += 1
                continue

            # Skip cards with very long "words" (likely sentences)
            if len(word) > 100:
                skipped_count += 1
                skipped_reasons.append(f"Too long ({len(word)} chars): {word[:30]}...")
                continue

            # Get FSRS parameters
            fsrs_params = convert_to_fsrs_params(card)

            # Check if word already exists in progress bank
            existing_progress = (
                db.query(UserAnkiProgress)
                .filter(
                    UserAnkiProgress.user_id == current_user.id,
                    UserAnkiProgress.word == word,
                    UserAnkiProgress.language == language,
                )
                .first()
            )

            if existing_progress:
                # Update if Anki has more reviews
                if card['reps'] > existing_progress.reps:
                    existing_progress.due = fsrs_params['due']
                    existing_progress.stability = fsrs_params['stability']
                    existing_progress.difficulty = fsrs_params['difficulty']
                    existing_progress.elapsed_days = fsrs_params['elapsed_days']
                    existing_progress.scheduled_days = fsrs_params['scheduled_days']
                    existing_progress.reps = fsrs_params['reps']
                    existing_progress.lapses = fsrs_params['lapses']
                    existing_progress.state = fsrs_params['state']
                    existing_progress.last_review = fsrs_params['last_review']
                    existing_progress.deck_name = deck_name
                    updated_count += 1
                else:
                    skipped_count += 1
            else:
                # Create new progress entry
                new_progress = UserAnkiProgress(
                    user_id=current_user.id,
                    word=word,
                    language=language,
                    deck_name=deck_name,
                    due=fsrs_params['due'],
                    stability=fsrs_params['stability'],
                    difficulty=fsrs_params['difficulty'],
                    elapsed_days=fsrs_params['elapsed_days'],
                    scheduled_days=fsrs_params['scheduled_days'],
                    reps=fsrs_params['reps'],
                    lapses=fsrs_params['lapses'],
                    state=fsrs_params['state'],
                    last_review=fsrs_params['last_review'],
                )
                db.add(new_progress)
                imported_count += 1

            # Add to vocabulary list (for display in "My Word Lists")
            vocab_word = UserVocabularyWord(
                list_id=vocab_list.id,
                word=word,
                translation=translation[:500] if translation else "",
                sort_order=i,
            )
            db.add(vocab_word)

            # Batch commit to avoid timeout
            if (i + 1) % batch_size == 0:
                db.commit()
                print(f"[Anki Import] Committed batch {(i + 1) // batch_size}")

        # Update word count and final commit
        vocab_list.word_count = imported_count + updated_count
        db.commit()

        # Log skipped reasons for debugging
        if skipped_reasons:
            print(f"[Anki Import] Skipped {len(skipped_reasons)} cards:")
            for reason in skipped_reasons[:10]:
                print(f"  - {reason}")

        return {
            "status": "ok",
            "deck_name": deck_name,
            "list_name": f"Anki: {deck_name}",
            "total_cards_in_deck": len(anki_data['cards']),
            "imported": imported_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "total_reviews_in_anki": anki_data['total_reviews'],
            "message": f"Imported {imported_count} words from '{deck_name}'. "
                       f"You can find them in 'My Word Lists'. "
                       f"When you encounter these words in videos, your Anki progress will be applied.",
        }

    except AnkiImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Anki Import] Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        if temp_apkg and os.path.exists(temp_apkg):
            os.unlink(temp_apkg)
        if temp_dir:
            cleanup_temp_dir(temp_dir)


@router.get("/progress")
async def get_anki_progress(
    language: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get summary of imported Anki vocabulary progress."""
    progress = (
        db.query(UserAnkiProgress)
        .filter(
            UserAnkiProgress.user_id == current_user.id,
            UserAnkiProgress.language == language,
        )
        .all()
    )

    total = len(progress)
    applied = sum(1 for p in progress if p.applied_to_flashcard is not None)
    pending = total - applied

    # Group by deck
    decks = {}
    for p in progress:
        deck = p.deck_name or "Unknown"
        if deck not in decks:
            decks[deck] = {"total": 0, "applied": 0}
        decks[deck]["total"] += 1
        if p.applied_to_flashcard:
            decks[deck]["applied"] += 1

    return {
        "total_words": total,
        "applied_to_flashcards": applied,
        "pending": pending,
        "decks": decks,
    }


@router.post("/import/preview")
async def preview_anki_deck(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Preview an Anki deck without importing it.
    Returns deck stats and sample cards.
    """
    if not file.filename.endswith('.apkg'):
        raise HTTPException(status_code=400, detail="File must be an .apkg file")

    temp_apkg = None
    temp_dir = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.apkg') as tmp:
            content = await file.read()
            tmp.write(content)
            temp_apkg = tmp.name

        temp_dir, db_path = extract_apkg(temp_apkg)
        anki_data = parse_anki_database(db_path)

        cards = anki_data['cards']
        new_cards = sum(1 for c in cards if c['state'] == 0)
        learning_cards = sum(1 for c in cards if c['state'] == 1)
        review_cards = sum(1 for c in cards if c['state'] == 2)
        relearning_cards = sum(1 for c in cards if c['state'] == 3)

        sample_cards = [
            {
                'front': c['front'][:100],
                'back': c['back'][:100],
                'reps': c['reps'],
                'state': c['state'],
            }
            for c in cards[:10]
        ]

        return {
            "deck_name": anki_data['deck_name'],
            "total_cards": len(cards),
            "new_cards": new_cards,
            "learning_cards": learning_cards,
            "review_cards": review_cards,
            "relearning_cards": relearning_cards,
            "total_reviews": anki_data['total_reviews'],
            "sample_cards": sample_cards,
        }

    except AnkiImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")
    finally:
        if temp_apkg and os.path.exists(temp_apkg):
            os.unlink(temp_apkg)
        if temp_dir:
            cleanup_temp_dir(temp_dir)
