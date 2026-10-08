# BioModStack

BioModStack is a protein modification and design platform that combines
Nextflow-driven workflows, a FastAPI control plane, and a React UI for running
and inspecting protein design pipelines.

## Quick Start (Local)

```bash
# From the repo root
./start_ui.sh

# Or launch the GUI control panel
./start_ui_gui.sh
```

Service URLs:
- UI: `http://localhost:5173/bms/`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Documentation

- Installation: `docs/installation.md`
- Parameters: `docs/parameters.md`
- Modes: `docs/modes.md`
- DB rules: `docs/ai_guidance/Database_Instructions.md`
- Pathing rules: `docs/ai_guidance/Centralization_and_Standardization.md`
- Model inventory: `docs/ai_guidance/Model_Integrations.md`
- FrustraMPNN plan: `docs/FrustraMPNN_Integration_Plan.md`

## UI Base Path (`/bms/`)

The frontend is served at `/bms/` (configured in `platform/frontend/vite.config.ts`).
If you use a reverse proxy or Tailscale Serve, keep the `/bms/` base path
consistent with your proxy configuration.

## Desktop Launcher (Optional)

If you install a desktop launcher, the `.desktop` entries live here:
- App menu: `~/.local/share/applications/biomodstack.desktop`
- Autostart: `~/.config/autostart/biomodstack-panel.desktop`

If you move the repo, update the `Exec` and `Icon` paths in those files to
point at the new location (they should reference `biomodstack_panel.py` and the
icon in `platform/assets/icons/`).

## Pathing & Portability

BioModStack is path-agnostic. These environment variables override defaults:
- `BMS_HOME`, `BMS_DATA`
- `BMS_WEIGHTS`, `BMS_COLABFOLD_DB`, `BMS_MSA_CACHE`, `BMS_SABDAB_CACHE`
- `DATABASE_URL` or `BMS_DB_PATH`
- `BMS_FAN_CONTROL_BACKEND` (`nvidia-settings` or `coolercontrol`)

See `docs/installation.md` for examples.
