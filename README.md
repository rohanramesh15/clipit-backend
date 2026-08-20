# Deadbird Backend

A FastAPI backend for the Deadbird language learning application. Learn languages through video content with spaced repetition flashcards.

## Repository Structure

```
.
├── app/                  # Main FastAPI application
├── alembic/              # Database migrations
├── data/                 # Local backend data
├── chrome-extension/     # Browser extension for Netflix integration
└── docker-compose.yml    # Docker configuration
```

## Features

- **User Authentication**: JWT-based auth with signup/login
- **Video Tracking**: Track watched videos from YouTube and Netflix
- **Subtitle Management**: Fetch and cache subtitles with multi-language support (Ukrainian, Serbian, Bulgarian)
- **Vocabulary System**: Save and organize vocabulary from videos
- **Flashcards**: Spaced repetition using FSRS algorithm
- **Translation**: DeepL integration for word lookups
- **Deck Management**: Organize flashcards into custom decks
- **Chrome Extension**: Netflix subtitle integration

## Project Structure

```
.
├── app/
│   ├── api/routes/       # API endpoints
│   │   ├── auth.py       # Authentication
│   │   ├── videos.py     # Video tracking
│   │   ├── subtitles.py  # Subtitle fetching
│   │   ├── vocabulary.py # Vocabulary management
│   │   ├── flashcards.py # Flashcard operations
│   │   ├── fsrs.py       # Spaced repetition
│   │   ├── lookup.py     # Word lookups
│   │   ├── netflix.py    # Netflix integration
│   │   └── decks.py      # Deck management
│   ├── core/
│   │   ├── config.py     # App configuration
│   │   └── database.py   # Database setup
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   └── services/         # Business logic
├── alembic/              # Database migrations
├── data/                 # Local data storage
├── subtitles_cache/      # Cached subtitle files
├── main.py               # Application entry point
└── requirements.txt      # Python dependencies
```

## Setup

### Prerequisites

- Python 3.8+
- pip

### Installation

1. From the monorepo root, navigate to the backend directory:
```bash
cd clipit-backend
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # macOS/Linux
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file:
```env
PROJECT_NAME=Deadbird API
DEBUG=True
HOST=0.0.0.0
PORT=8000
DATABASE_URL=sqlite:///./deadbird.db
SECRET_KEY=your-secret-key
DEEPL_API_KEY=your-deepl-key
```

## Running

### Development
```bash
cd clipit-backend
python main.py
```

### Production
```bash
cd clipit-backend
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker
```bash
docker-compose up
```

## API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/auth/*` | User authentication |
| `/api/videos/*` | Video tracking |
| `/api/subtitles/*` | Subtitle management |
| `/api/vocabulary/*` | Vocabulary operations |
| `/api/flashcards/*` | Flashcard CRUD |
| `/api/fsrs/*` | Spaced repetition scheduling |
| `/api/lookup/*` | Word translation/lookup |
| `/api/netflix/*` | Netflix integration |
| `/api/decks/*` | Deck management |

## Chrome Extension

The `chrome-extension/` directory contains a browser extension for capturing subtitles from Netflix. See the extension's README for installation instructions.
# Trigger redeploy Thu Mar 19 00:03:58 EDT 2026
