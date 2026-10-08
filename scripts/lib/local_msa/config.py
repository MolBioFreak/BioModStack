from __future__ import annotations

import os
from pathlib import Path

_default_data_root = Path(os.path.expanduser(os.getenv("BMS_DATA") or "~/.biomodstack"))

DEFAULT_DB_PATH = os.getenv("BMS_COLABFOLD_DB") or str(_default_data_root / "colabfold_db")

DEFAULT_CACHE_DIR = os.getenv("BMS_MSA_CACHE") or str(_default_data_root / "msa_cache")

DEFAULT_COLABFOLD_API_HOST = os.getenv("BMS_COLABFOLD_API_HOST") or "https://api.colabfold.com"

DEFAULT_COLABFOLD_API_USER_AGENT = os.getenv("BMS_COLABFOLD_API_USER_AGENT") or "biomodstack-msa/1.0"

DEFAULT_SMALL_MAX_TASKS = 1

DEFAULT_SMALL_MAX_PROTEIN_CHAINS = 4

DEFAULT_SMALL_MAX_TOTAL_RESIDUES = 1500

MSA_PRESETS = {
    "maximum": {
        "num_iterations": 3,
        "use_env": True,
        "use_expand": True,   # Re-enabled: _aln files verified valid (8.7GB)
        "use_filter": True,
        "sensitivity": 8.0,
        "evalue": 0.1,       # ColabFold uses 0.1 for initial search
        "max_seqs": 10000,
        "qsc": -20.0,        # ColabFold default - score per aligned residue
        "max_seq_id": 0.95,
        "description": "Full ColabFold workflow - highest MSA depth and diversity (~15-30s)"
    },
    "balanced": {
        "num_iterations": 2,
        "use_env": True,
        "use_expand": False,
        "use_filter": True,
        "sensitivity": 8.0,
        "evalue": 0.1,
        "max_seqs": 300,
        "qsc": -20.0,        # ColabFold default
        "max_seq_id": 0.95,
        "description": "Environmental search without expansion (~8-15s)"
    },
    "fast": {
        "num_iterations": 1,
        "use_env": False,
        "use_expand": False,
        "use_filter": False,
        "sensitivity": 7.0,
        "evalue": 0.001,
        "max_seqs": 300,
        "qsc": -20.0,
        "max_seq_id": 1.0,
        "description": "UniRef30 only - quick screening (~3-5s)"
    }
}
