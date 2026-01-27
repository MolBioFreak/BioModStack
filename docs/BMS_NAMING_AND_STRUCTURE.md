# BioModStack Naming and Structure

This document defines the canonical naming and directory structure for the BioModStack repository and runtime artifacts.

## Naming Standards
- **Product name:** BioModStack (BMS)
- **Default results directory:** `bms_results`
- **Database file:** `biomodstack.db`
- **API package name:** `biomodstack-api`
- **Repository root:** `biomodstack/` (recommended)

## Default Paths
- Results: `${BMS_DATA:-<repo_root>}/bms_results`
- Work: `${BMS_DATA:-<repo_root>}/work`
- Weights: `${BMS_WEIGHTS:-/mnt/BioModStack/weights}`
- MSA DB: `${BMS_COLABFOLD_DB:-/mnt/BioModStack/colabfold_db}`
- MSA Cache: `${BMS_MSA_CACHE:-/mnt/BioModStack/msa_cache}`
- Cache root: `${XDG_CACHE_HOME:-$HOME/.cache}`

## Recommended Repo Layout
```
biomodstack/
├── biomodstack/          # main code (Nextflow + platform)
├── data/                 # db, models, caches
├── results/              # bms_results + work symlink
├── .nextflow/            # Nextflow cache
├── .nextflow.log
└── docs/
```

## Migration Notes
- Existing results directories should be moved to `bms_results`.
- Update any external scripts or cron jobs to use the new paths.
