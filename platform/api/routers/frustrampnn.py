"""
FrustraMPNN API Router - Energetic Frustration Analysis

Provides endpoint for running FrustraMPNN on PDB structures.
Returns per-residue frustration profiles for all amino acid mutations.
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
import subprocess
import pandas as pd
import tempfile
import json
from pathlib import Path
from typing import Optional
import logging

router = APIRouter(prefix="/api/frustrampnn", tags=["frustrampnn"])
logger = logging.getLogger(__name__)

# Container and weights paths
CONTAINER = Path.home() / "biomodstack/biomodstack/apptainer/frustrampnn.sif"
CHECKPOINT = "/opt/frustrampnn_weights/megascale.ckpt"

# Official thresholds from FrustraMPNN constants.py
THRESHOLDS = {
    "highly": -1.0,      # <= -1.0
    "minimally": 0.58,   # >= 0.58
}


def classify_frustration(value: float) -> str:
    """Classify frustration by official thresholds."""
    if value <= THRESHOLDS["highly"]:
        return "highly"
    elif value >= THRESHOLDS["minimally"]:
        return "minimally"
    return "neutral"


@router.post("/analyze")
async def analyze_frustration(
    pdb_file: UploadFile = File(..., description="PDB file to analyze"),
    chain: Optional[str] = Query(None, description="Specific chain to analyze (default: all)")
):
    """
    Run FrustraMPNN frustration analysis on uploaded PDB.
    
    Returns:
        - native_profile: Per-position frustration for wildtype residues
        - summary: Counts of highly/neutral/minimally frustrated positions
        - full_matrix: Complete N×20 mutation matrix (optional, for heatmaps)
    """
    if not CONTAINER.exists():
        raise HTTPException(500, f"FrustraMPNN container not found at {CONTAINER}")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        pdb_path = Path(tmpdir) / "input.pdb"
        csv_path = Path(tmpdir) / "results.csv"
        
        # Save uploaded PDB
        content = await pdb_file.read()
        pdb_path.write_bytes(content)
        
        # Build command
        cmd = [
            "apptainer", "run", "--nv", str(CONTAINER),
            "predict",
            "--pdb", str(pdb_path),
            "--checkpoint", CHECKPOINT,
            "--output", str(csv_path),
        ]
        if chain:
            cmd.extend(["--chains", chain])
        
        logger.info(f"Running FrustraMPNN: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"FrustraMPNN failed: {result.stderr}")
            raise HTTPException(500, f"FrustraMPNN failed: {result.stderr[:500]}")
        
        if not csv_path.exists():
            raise HTTPException(500, "FrustraMPNN produced no output")
        
        # Parse results
        df = pd.read_csv(csv_path)
        
        if df.empty:
            raise HTTPException(500, "FrustraMPNN returned empty results")
        
        # Extract native (wildtype) frustration per position
        native_df = df[df["mutation"] == df["wildtype"]].copy()
        native_df["class"] = native_df["frustration_pred"].apply(classify_frustration)
        
        # Build response
        native_profile = native_df[[
            "chain", "position", "wildtype", "frustration_pred", "class"
        ]].to_dict(orient="records")
        
        # Summary statistics
        summary = {
            "total_positions": len(native_df),
            "n_highly_frustrated": int((native_df["class"] == "highly").sum()),
            "n_neutral": int((native_df["class"] == "neutral").sum()),
            "n_minimally_frustrated": int((native_df["class"] == "minimally").sum()),
        }
        
        # Full matrix for heatmap visualization
        full_matrix = df[[
            "chain", "position", "wildtype", "mutation", "frustration_pred"
        ]].to_dict(orient="records")
        
        logger.info(f"FrustraMPNN completed: {summary}")
        
        return {
            "native_profile": native_profile,
            "summary": summary,
            "full_matrix": full_matrix,
            "thresholds": THRESHOLDS,
        }


@router.get("/health")
async def health_check():
    """Check if FrustraMPNN container is available."""
    return {
        "container_exists": CONTAINER.exists(),
        "container_path": str(CONTAINER),
        "checkpoint": CHECKPOINT,
        "thresholds": THRESHOLDS,
    }
