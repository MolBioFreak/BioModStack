# ProteinDJ Control Platform API

FastAPI backend for the ProteinDJ web interface.

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload --port 8000
```

## API Endpoints

- `GET /api/health` - Health check
- `GET /api/jobs` - List all jobs
- `POST /api/jobs` - Submit new job
- `GET /api/jobs/{id}` - Get job details
- `DELETE /api/jobs/{id}` - Cancel job
