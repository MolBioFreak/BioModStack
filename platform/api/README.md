# BioModStack Control Platform API

FastAPI backend for the BioModStack web interface.

## Pathing and Database

DB resolution is centralized in `platform/api/paths.py`. Defaults:
- `platform/api/biomodstack.db` (local)
- `${BMS_DATA}/biomodstack.db` when `BMS_DATA` is set

Overrides (highest priority first):
- `DATABASE_URL` (SQLAlchemy URL)
- `BMS_DB_PATH` (absolute sqlite path)

If you change any of these, keep them consistent across API, scripts, and
workflows to avoid writing to multiple databases.

## Setup (using uv)

```bash
# Install uv if not installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies
uv sync

# Run development server
uv run uvicorn main:app --reload --port 8000
```

## Setup (alternative: pip)

```bash
pip install -e .
uvicorn main:app --reload --port 8000
```

## API Endpoints

- `GET /api/health` - Health check
- `GET /api/jobs` - List all jobs
- `POST /api/jobs` - Submit new job
- `GET /api/jobs/{id}` - Get job details
- `DELETE /api/jobs/{id}` - Cancel job
- `GET /api/gpu/status` - GPU monitoring
- `GET /api/files/browse` - Browse directories
- `GET /api/system/db-info` - DB path + health info

## Interactive Docs

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
