# BioModStack UI Phase 1 - Status Summary

> **Branch**: `UI-phase-1`  
> **Latest Commit**: `52948c2` (pushed)  
> **Date**: 2025-12-07

---

## ✅ Completed

### Branding & Rebrand
- [x] Renamed from **ProteinDJ** → **BioModStack**
- [x] Updated backend API title/description/version (v0.2.0)
- [x] Updated frontend Dashboard header
- [x] Updated browser tab title

---

### Model Registry (Extensible Architecture)
- [x] [model_registry.py](file:///home/dalab/ProteinDJ_UI/platform/api/model_registry.py) - Core YAML loader + validation
- [x] [routers/models.py](file:///home/dalab/ProteinDJ_UI/platform/api/routers/models.py) - `/api/models` endpoints

#### Configured Models (in `config/models/`)

| Model | Category | Status | NTP Templates |
|-------|----------|--------|---------------|
| [rfdiffusion.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/rfdiffusion.yaml) | backbone_generation | ✅ enabled | - |
| [proteinmpnn.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/proteinmpnn.yaml) | sequence_design | ✅ enabled | - |
| [fampnn.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/fampnn.yaml) | sequence_design | ✅ enabled | - |
| [ligandmpnn.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/ligandmpnn.yaml) | sequence_design | ✅ enabled | 8 NTPs |
| [boltz2.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/boltz2.yaml) | structure_prediction | ✅ enabled | - |
| [af2.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/af2.yaml) | structure_prediction | ✅ enabled | - |
| [diffdock.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/diffdock.yaml) | docking | 🔲 experimental | 4 dNTPs |
| [boltzgen.yaml](file:///home/dalab/ProteinDJ_UI/platform/api/config/models/boltzgen.yaml) | generative_design | 🔲 experimental | 4 dNTPs |

---

### Enhanced System Monitoring
- [x] [routers/gpu.py](file:///home/dalab/ProteinDJ_UI/platform/api/routers/gpu.py) - Complete rewrite with:
  - GPU: power draw, fan speed, core/memory clocks, running processes
  - CPU: model, cores, frequency, per-core utilization
  - RAM: total/used/available with utilization %

---

### Frontend (React + Vite + TypeScript + Tailwind)
- [x] [Dashboard.tsx](file:///home/dalab/ProteinDJ_UI/platform/frontend/src/components/Dashboard.tsx) - Main dashboard with:
  - System Overview (CPU + RAM cards)
  - Enhanced GPU cards (power, temp/fan, clocks, VRAM, processes)
  - Jobs table (ready for data)
- [x] [api.ts](file:///home/dalab/ProteinDJ_UI/platform/frontend/src/lib/api.ts) - API client with types

---

## 🔲 Not Yet Implemented

### Phase B: Schema Integration
- [ ] Update `schemas.py` - replace hardcoded `PipelineMode` enum with dynamic model validation
- [ ] Update `database.py` - add `model_id` column to jobs table
- [ ] Update `routers/jobs.py` - validate job params against model registry
- [ ] Database migration for existing records

### Phase D: Job Wizard UI
- [ ] Model selector component (cards or dropdown)
- [ ] Mode selector (based on selected model)
- [ ] Dynamic parameter form (generated from model's param schema)
- [ ] File picker for PDB/FASTA inputs
- [ ] Contig builder for RFdiffusion
- [ ] Submit to `/api/jobs`

### Testing
- [ ] pytest tests for backend API
- [ ] End-to-end job submission test

---

## Files Changed (This Session)

```
platform/api/
├── config/models/           # NEW - 8 model YAML configs
├── model_registry.py        # NEW - extensible model loader
├── routers/models.py        # NEW - /api/models endpoints
├── routers/gpu.py           # MODIFIED - enhanced monitoring
├── main.py                  # MODIFIED - branding + models router
└── pyproject.toml           # MODIFIED - added pyyaml, psutil

platform/frontend/           # NEW - entire directory
├── src/components/Dashboard.tsx
├── src/lib/api.ts
└── [vite/tailwind config files]
```

---

## How to Run

```bash
# Backend
cd platform/api
uv run uvicorn main:app --reload --port 8000

# Frontend
cd platform/frontend
npm run dev
# Opens at http://localhost:5173
```

## API Endpoints Added

| Endpoint | Description |
|----------|-------------|
| `GET /api/models` | List all enabled models |
| `GET /api/models?include_experimental=true` | Include experimental models |
| `GET /api/models/{id}` | Full model details with param schema |
| `GET /api/models/{id}/modes` | Available modes for a model |
| `GET /api/models/{id}/ntp-templates` | NTP SMILES for nucleotide-aware models |
| `GET /api/gpu/status` | Enhanced: GPU + CPU + RAM |
