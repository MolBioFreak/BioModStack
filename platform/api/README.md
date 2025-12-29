# ProteinDJ Control Platform API

FastAPI backend for the ProteinDJ web interface.

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

## Interactive Docs

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
