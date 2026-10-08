# ProteinDJ Control Platform – UI Implementation Plan

> **Status**: Approved for implementation  
> **Date**: 2025-12-07  
> **Target Branch**: Phase-2-Test-Branch

## Executive Summary

Build a **modern graphical web interface** to control the ProteinDJ protein design pipeline remotely via Tailscale VPN.

| Requirement | Solution |
|-------------|----------|
| Remote access | Tailscale VPN (already configured) |
| Authentication | None needed (VPN provides security) |
| Job management | Queue, submit, cancel, monitor |
| Input handling | File browser, YAML/FASTA generation |
| Output database | SQLite with filtering/sorting |
| GPU monitoring | Real-time utilization, memory, temperature |
| 3D visualization | Mol* with measurements, superposition |
| Structure browsing | Grid/list views with thumbnails |

---

## Technology Stack

| Layer | Technology | Why |
|-------|------------|-----|
| Frontend | React + TypeScript + Vite | Modern, fast, Mol* compatible |
| UI Library | shadcn/ui (Tailwind) | Beautiful components, dark theme |
| 3D Viewer | Mol* | RCSB standard, handles large structures |
| Backend | FastAPI (Python) | Async, easy Nextflow integration |
| Database | SQLite | Single file, no server, perfect for 1 user |
| Job Queue | Redis + Celery | Robust background task handling |
| GPU Monitor | pynvml | Python nvidia-smi bindings |
| VPN | Tailscale | Already installed, peer-to-peer |

---

## Models Supported

### Backbone Generation
| Model | Framework | Notes |
|-------|-----------|-------|
| RFdiffusion | Native | Current default, 8 modes |
| RFdiffusion3 | Forge | Next-gen, via Forge container |
| Genie 2 | TBD | Experimental, higher designability |

### Sequence Design
| Model | Purpose |
|-------|---------|
| ProteinMPNN | Fast sequence design |
| FAMPNN | Full-atom with sidechains |
| LigandMPNN | Ligand/metal/DNA-aware |

### Structure Prediction
| Model | Framework | Notes |
|-------|-----------|-------|
| **Boltz-2** | Native | Primary predictor |
| AF2-IG | Native | AlphaFold2 initial guess |
| RosettaFold3 | Forge | 3rd option via Forge |

### Specialized Tools
| Model | Purpose | Status |
|-------|---------|--------|
| BoltzGen | All-atom binder generation | Planned |
| DiffDock | Protein-ligand docking | ✅ Confirmed |
| OpenMM | MD stability validation | Maybe (if needed) |

> **Note**: Forge is a container framework providing access to RFdiffusion3, RosettaFold3, and other emerging models. The UI will support selecting models from any available framework.

---

## Core Features

### Job Control
- Submit jobs via form wizard (no CLI)
- Real-time progress tracking
- Cancel running jobs
- View logs in browser

### Input Management
- Browse directories for PDB/FASTA files
- Upload new files via drag-and-drop
- Generate Boltz-2 YAML configs automatically
- Validate contig syntax before submission

### Output Database
- All designs stored with metrics
- Filter by pLDDT, PAE, RMSD thresholds
- Sort by any metric
- Favorite and annotate designs
- Export filtered sets

### GPU Analytics
- Live utilization per GPU
- Memory usage bars
- Temperature monitoring
- Task→GPU assignment view
- Historical usage graphs

### Visualization
- **Simple browser**: Grid of thumbnails with metrics
- **Advanced viewer**: Mol* with measurements, superposition, surface, binding interface

---

## Architecture

```
Browser (any device via Tailscale)
    │
    ▼
React Frontend (:3000)
    │
    ▼
FastAPI Backend (:8000)
    │
    ├──► SQLite (jobs, designs, metadata)
    ├──► Redis (job queue)
    └──► Celery Worker → Nextflow → GPUs
```

---

## Implementation Timeline

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| **1. Core Backend** | 2 weeks | API, database, job launching |
| **2. Job UI** | 2 weeks | Dashboard, wizard, status page |
| **3. I/O Management** | 2 weeks | File browser, generators, results DB |
| **4. Visualization** | 2 weeks | Thumbnails, Mol* viewer |
| **5. Real-time** | 2 weeks | WebSocket logs, GPU dashboard |

**Total: ~10 weeks** for full platform

---

## Directory Structure

```
Protein-De-Novo-Modification-and-Design-Platform/
├── platform/                 # NEW: Control platform
│   ├── api/                  # FastAPI backend
│   ├── frontend/             # React app
│   ├── docker-compose.yml
│   └── Caddyfile
├── main.nf                   # Existing pipeline
├── nextflow.config           # Existing config
└── pdj_results/              # Output directory
```

---

## Access Method

```bash
# Start platform
cd platform && docker-compose up -d

# Access via Tailscale
https://workstation.tailnet-xxx.ts.net/
```

Works from phone, laptop, anywhere on your Tailscale network.

---

## Database Schema

```sql
-- Jobs table
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,  -- queued, running, completed, failed, cancelled
    mode TEXT NOT NULL,    -- monomer_denovo, binder_denovo, etc.
    params JSON NOT NULL,
    created_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    output_dir TEXT,
    error_message TEXT
);

-- Designs table
CREATE TABLE designs (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id),
    name TEXT NOT NULL,
    pdb_path TEXT NOT NULL,
    plddt_overall REAL,
    plddt_binder REAL,
    pae_interaction REAL,
    rmsd_binder REAL,
    conf_score REAL,
    is_favorite BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMP
);

-- Input files table
CREATE TABLE input_files (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    directory TEXT NOT NULL,
    uploaded_at TIMESTAMP
);
```

---

## API Endpoints

### Jobs
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/jobs` | Submit new job |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/jobs/{id}` | Get job details |
| `DELETE` | `/api/jobs/{id}` | Cancel job |

### Inputs
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/inputs/browse` | List directory |
| `POST` | `/api/inputs/upload` | Upload file |
| `POST` | `/api/inputs/generate/yaml` | Generate Boltz-2 YAML |

### Outputs
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/designs` | List designs with filters |
| `PATCH` | `/api/designs/{id}` | Update notes/favorite |
| `POST` | `/api/designs/export` | Export filtered set |

### GPU
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/gpu/status` | Current GPU stats |
| `WS` | `/ws/gpu` | Live GPU stream |

---

## Next Steps

1. ✅ Plan approved
2. 🔲 Phase 1: Create `platform/` directory, build FastAPI skeleton
3. 🔲 Phase 2: Add React frontend with job wizard
4. 🔲 Phase 3-5: Layer in remaining features
