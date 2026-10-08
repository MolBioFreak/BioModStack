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

## GPU Fan Backend

Fan control backend is selected with `BMS_FAN_CONTROL_BACKEND`:

- `nvidia-settings` (default): direct NVIDIA target mapping (`nvidia-settings`)
- `coolercontrol`: uses CoolerControl daemon API (`/devices/.../settings/...`) for per-GPU, per-channel writes

When using `coolercontrol`, required runtime env:

- `BMS_COOLERCONTROL_DAEMON_ADDRESS` (default `127.0.0.1`)
- `BMS_COOLERCONTROL_DAEMON_PORT` (default `11987`)
- `BMS_COOLERCONTROL_USERNAME` (default `CCAdmin`)
- `BMS_COOLERCONTROL_PASSWORD` (default `coolAdmin`)
- `BMS_COOLERCONTROL_CLI` (absolute path to `cctv` if not discoverable in `PATH`)

Notes:
- `cctv` is used for mode discovery (`available_modes`) and operational diagnostics.
- Mode names (`BMS_COOLERCONTROL_MODE_AUTO`, `BMS_COOLERCONTROL_MODE_MANUAL`) are optional and only used by helper scripts that bootstrap mode entries in CoolerControl.
- API-level fallback is `nvidia-settings` when `BMS_FAN_CONTROL_BACKEND` is unset, but project launcher scripts (`start_ui.sh`, `restart_api.sh`) currently default this env to `coolercontrol`.

## Interactive Docs

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
