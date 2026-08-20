"""
Anki .apkg import service.

.apkg files are ZIP archives containing:
- collection.anki2 or collection.anki21 or collection.anki21b (SQLite database)
- media folder with numbered files
- media JSON mapping

Key SQLite tables (schema varies by Anki version):
Old schema (pre-2.1.50):
- col: Contains decks, models as JSON
- notes: Card content (fields separated by \x1f)
- cards: Scheduling info
- revlog: Review history

New schema (2.1.50+):
- decks: Separate table for deck info
- notetypes: Separate table for note types
- fields, templates: Note type components
- notes, cards, revlog: Same as old
"""

import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import os
import shutil


class AnkiImportError(Exception):
    """Custom exception for Anki import errors."""
    pass


def extract_apkg(apkg_path: str) -> Tuple[str, Path]:
    """
    Extract .apkg file to a temporary directory.
    Returns (temp_dir, db_path).
    """
    temp_dir = tempfile.mkdtemp(prefix="anki_import_")

    try:
        with zipfile.ZipFile(apkg_path, 'r') as zf:
            zf.extractall(temp_dir)
    except zipfile.BadZipFile:
        raise AnkiImportError("Invalid .apkg file - not a valid ZIP archive")

    temp_path = Path(temp_dir)
    db_path = None

    # Check for different database file formats (in order of preference)
    # .anki21b = zstd compressed (newest)
    # .anki21 = uncompressed SQLite (common)
    # .anki2 = older format
    for db_name in ['collection.anki21b', 'collection.anki21', 'collection.anki2']:
        potential_path = temp_path / db_name
        if potential_path.exists():
            if db_name == 'collection.anki21b':
                # Try to decompress zstd file
                try:
                    import zstandard as zstd
                    decompressed_path = temp_path / 'collection.anki21'
                    with open(potential_path, 'rb') as compressed:
                        dctx = zstd.ZstdDecompressor()
                        with open(decompressed_path, 'wb') as decompressed:
                            dctx.copy_stream(compressed, decompressed)
                    db_path = decompressed_path
                    print(f"[Anki Extract] Decompressed .anki21b file")
                except ImportError:
                    # zstandard not installed, try reading as regular SQLite anyway
                    print(f"[Anki Extract] Warning: zstandard not installed, trying direct read")
                    db_path = potential_path
                except Exception as e:
                    print(f"[Anki Extract] Warning: Failed to decompress .anki21b: {e}")
                    # Try to read it directly in case it's not actually compressed
                    db_path = potential_path
            else:
                db_path = potential_path
            break

    if not db_path:
        # List what files are in the archive for debugging
        files = list(temp_path.iterdir())
        print(f"[Anki Extract] Files in archive: {[f.name for f in files]}")
        raise AnkiImportError("No Anki database found in .apkg file")

    print(f"[Anki Extract] Using database: {db_path.name}")
    return temp_dir, db_path


def unicase_collation(s1: str, s2: str) -> int:
    """
    Case-insensitive Unicode collation for Anki compatibility.
    Anki uses this for sorting cards/notes.
    """
    s1_lower = s1.lower() if s1 else ""
    s2_lower = s2.lower() if s2 else ""
    if s1_lower < s2_lower:
        return -1
    elif s1_lower > s2_lower:
        return 1
    return 0


def parse_anki_database(db_path: Path, target_language: str = 'ko') -> Dict[str, Any]:
    """
    Parse the Anki SQLite database and extract cards with their progress.

    Returns:
        {
            'deck_name': str,
            'cards': [
                {
                    'front': str,
                    'back': str,
                    'due': datetime,
                    'interval': int,
                    'ease_factor': float,
                    'reps': int,
                    'lapses': int,
                    'state': int,  # 0=New, 1=Learning, 2=Review, 3=Relearning
                    'last_review': datetime | None,
                    'reviews': [...]  # Review history
                }
            ],
            'total_reviews': int
        }
    """
    conn = sqlite3.connect(str(db_path))
    # Register Anki's custom collations
    conn.create_collation("unicase", unicase_collation)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    result = {
        'deck_name': 'Imported Deck',
        'cards': [],
        'total_reviews': 0
    }

    try:
        # Check which tables exist (different Anki versions have different schemas)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row['name'] for row in cursor.fetchall()]
        print(f"[Anki Parse] Tables found: {tables}")

        # Check for newer Anki 2.1.50+ format (uses 'notetypes' instead of storing in 'col')
        is_new_schema = 'notetypes' in tables
        print(f"[Anki Parse] Schema type: {'new (2.1.50+)' if is_new_schema else 'legacy'}")

        # Get deck names - try multiple approaches
        deck_name_found = False

        if 'decks' in tables:
            # Newer schema - decks in separate table
            # Check what columns exist in decks table
            cursor.execute("PRAGMA table_info(decks)")
            deck_cols = [row[1] for row in cursor.fetchall()]
            print(f"[Anki Parse] Decks table columns: {deck_cols}")

            if 'name' in deck_cols:
                cursor.execute("SELECT name FROM decks WHERE name != 'Default' LIMIT 1")
                deck_row = cursor.fetchone()
                if deck_row:
                    result['deck_name'] = deck_row['name']
                    deck_name_found = True
                    print(f"[Anki Parse] Deck name from decks table: {result['deck_name']}")

        if not deck_name_found and 'col' in tables:
            # Try older schema - decks stored as JSON in col table
            cursor.execute("PRAGMA table_info(col)")
            col_cols = [row[1] for row in cursor.fetchall()]
            print(f"[Anki Parse] Col table columns: {col_cols}")

            if 'decks' in col_cols:
                cursor.execute("SELECT decks FROM col")
                col_row = cursor.fetchone()
                if col_row and col_row['decks']:
                    try:
                        decks_json = json.loads(col_row['decks'])
                        for deck_id, deck_info in decks_json.items():
                            if deck_info.get('name') and deck_info['name'] != 'Default':
                                result['deck_name'] = deck_info['name']
                                deck_name_found = True
                                print(f"[Anki Parse] Deck name from col.decks JSON: {result['deck_name']}")
                                break
                    except json.JSONDecodeError:
                        print("[Anki Parse] Warning: Could not parse decks JSON")

        # Get cards with their notes
        # Anki card types: 0=new, 1=learning, 2=review, 3=relearning
        # Anki queue: -3=user buried, -2=sched buried, -1=suspended, 0=new, 1=learning, 2=review, 3=day learning, 4=preview

        # First check what columns exist in cards table
        cursor.execute("PRAGMA table_info(cards)")
        card_cols = [row[1] for row in cursor.fetchall()]
        print(f"[Anki Parse] Cards table columns: {card_cols}")

        cursor.execute("PRAGMA table_info(notes)")
        note_cols = [row[1] for row in cursor.fetchall()]
        print(f"[Anki Parse] Notes table columns: {note_cols}")

        # Build query based on available columns
        select_cols = [
            "c.id as card_id",
            "c.nid as note_id",
            "c.did as deck_id" if 'did' in card_cols else "0 as deck_id",
            "c.type",
            "c.queue",
            "c.due",
            "c.ivl",
            "c.factor",
            "c.reps",
            "c.lapses",
            "c.odue" if 'odue' in card_cols else "0 as odue",
            "n.flds",
            "n.tags" if 'tags' in note_cols else "'' as tags",
        ]

        query = f"""
            SELECT {', '.join(select_cols)}
            FROM cards c
            JOIN notes n ON c.nid = n.id
            WHERE c.queue >= 0
        """

        cursor.execute(query)
        cards_data = cursor.fetchall()
        print(f"[Anki Parse] Found {len(cards_data)} cards with queue >= 0")

        # Get creation time for calculating absolute due dates
        collection_created = datetime.now()
        if 'col' in tables:
            cursor.execute("PRAGMA table_info(col)")
            col_cols = [row[1] for row in cursor.fetchall()]
            if 'crt' in col_cols:
                cursor.execute("SELECT crt FROM col")
                col_crt = cursor.fetchone()
                if col_crt and col_crt['crt']:
                    collection_created = datetime.fromtimestamp(col_crt['crt'])
                    print(f"[Anki Parse] Collection created: {collection_created}")

        for card in cards_data:
            # Parse fields (separated by \x1f - unit separator)
            fields = card['flds'].split('\x1f')
            front = fields[0] if len(fields) > 0 else ''
            back = fields[1] if len(fields) > 1 else ''

            # Strip HTML tags for cleaner text
            front = strip_html(front)
            back = strip_html(back)

            if not front:
                continue

            # Calculate due date
            # For new cards (type=0): due is position in new queue
            # For learning cards (type=1): due is timestamp in seconds
            # For review cards (type=2): due is days since collection creation
            due_date = datetime.now()

            if card['type'] == 0:  # New
                due_date = datetime.now()
            elif card['type'] == 1:  # Learning
                # Due is timestamp in seconds
                due_date = datetime.fromtimestamp(card['due'])
            elif card['type'] in (2, 3):  # Review or Relearning
                # Due is days since collection creation
                due_date = collection_created + timedelta(days=card['due'])

            # Map Anki state to FSRS state
            # Anki type: 0=new, 1=learning, 2=review, 3=relearning
            # FSRS state: 0=New, 1=Learning, 2=Review, 3=Relearning
            state = card['type']  # Direct mapping works

            # Ease factor (Anki stores as integer, e.g., 2500 = 2.5)
            ease_factor = card['factor'] / 1000.0 if card['factor'] else 2.5

            # Get last review date from revlog
            cursor.execute("""
                SELECT id, ease, ivl, lastIvl, factor, time, type
                FROM revlog
                WHERE cid = ?
                ORDER BY id DESC
                LIMIT 1
            """, (card['card_id'],))
            last_review_row = cursor.fetchone()

            last_review = None
            if last_review_row:
                # revlog.id is timestamp in milliseconds
                last_review = datetime.fromtimestamp(last_review_row['id'] / 1000)

            # Get review count from revlog
            cursor.execute("SELECT COUNT(*) as cnt FROM revlog WHERE cid = ?", (card['card_id'],))
            review_count = cursor.fetchone()['cnt']
            result['total_reviews'] += review_count

            card_data = {
                'front': front,
                'back': back,
                'due': due_date,
                'interval': card['ivl'] if card['ivl'] else 0,
                'ease_factor': ease_factor,
                'reps': card['reps'] if card['reps'] else 0,
                'lapses': card['lapses'] if card['lapses'] else 0,
                'state': state,
                'last_review': last_review,
                'anki_card_id': card['card_id'],
            }

            # Debug: log first few cards
            if len(result['cards']) < 5:
                print(f"[Anki Parse] Card {len(result['cards'])+1}: front='{front[:50]}...', back='{back[:50] if back else 'empty'}...'")

            result['cards'].append(card_data)

    finally:
        conn.close()

    return result


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    import re
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    clean = clean.replace('&nbsp;', ' ')
    clean = clean.replace('&lt;', '<')
    clean = clean.replace('&gt;', '>')
    clean = clean.replace('&amp;', '&')
    clean = clean.replace('&quot;', '"')
    clean = clean.replace('&#39;', "'")
    # Clean up whitespace
    clean = ' '.join(clean.split())
    return clean.strip()


def convert_to_fsrs_params(anki_card: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Anki card parameters to FSRS parameters.

    FSRS uses:
    - stability: Expected retention interval (similar to Anki interval)
    - difficulty: 0-10 scale (derived from ease factor)
    - state: 0=New, 1=Learning, 2=Review, 3=Relearning
    """
    # Stability is similar to interval
    stability = float(anki_card['interval']) if anki_card['interval'] > 0 else 0.0

    # Convert ease factor (typically 1.3-2.5+) to difficulty (0-10)
    # Higher ease = lower difficulty
    # Anki default ease is 2.5, minimum is usually 1.3
    ease = anki_card['ease_factor']
    # Map 1.3-3.0 ease to 10-0 difficulty
    difficulty = max(0, min(10, (3.0 - ease) / 0.17))

    return {
        'stability': stability,
        'difficulty': round(difficulty, 2),
        'elapsed_days': anki_card['interval'],
        'scheduled_days': anki_card['interval'],
        'reps': anki_card['reps'],
        'lapses': anki_card['lapses'],
        'state': anki_card['state'],
        'due': anki_card['due'],
        'last_review': anki_card['last_review'],
    }


def cleanup_temp_dir(temp_dir: str) -> None:
    """Remove temporary directory and its contents."""
    import shutil
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass  # Best effort cleanup
