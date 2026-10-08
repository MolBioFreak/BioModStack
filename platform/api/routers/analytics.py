"""
Analytics API router - Aggregated metrics and batch comparisons.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import numpy as np
from pydantic import BaseModel

from database import get_session, Job, Design
from schemas import JobResponse

router = APIRouter()

# --- Schemas ---

class MetricDistribution(BaseModel):
    min: float
    max: float
    avg: float
    median: float
    std_dev: float
    histogram_bins: List[float]
    histogram_counts: List[int]

class ScatterPoint(BaseModel):
    x: float
    y: float
    id: str

class JobAnalytics(BaseModel):
    job_id: str
    design_count: int
    metrics: Dict[str, Optional[MetricDistribution]] | None
    correlations: Dict[str, List[ScatterPoint]] | None
    pipeline_summary: Dict[str, Any]

class DesignMetricPoint(BaseModel):
    id: str
    name: str
    metrics: Dict[str, float]

class BatchAnalytics(BaseModel):
    job_ids: List[str]
    metrics_summary: Dict[str, Dict[str, float]]  # metric -> {job_id -> avg}
    common_metrics: List[str]


# --- Helpers ---

def calculate_distribution(values: List[float], bins: int = 10) -> MetricDistribution:
    """Calculate statistical distribution for a list of values."""
    if not values:
        return None
    
    arr = np.array(values)
    hist, bin_edges = np.histogram(arr, bins=bins)
    
    return MetricDistribution(
        min=float(np.min(arr)),
        max=float(np.max(arr)),
        avg=float(np.mean(arr)),
        median=float(np.median(arr)),
        std_dev=float(np.std(arr)),
        histogram_bins=[float(b) for b in bin_edges],
        histogram_counts=[int(c) for c in hist]
    )

def extract_metrics(designs: List[Design]) -> Dict[str, List[float]]:
    """Extract all available numerical metrics from designs."""
    metrics = {
        "plddt_overall": [],
        "plddt_binder": [],
        "pae_overall": [],
        "pae_interaction": [],
        "rmsd_binder": [],
        "rmsd_overall": [],
        "mpnn_score": [],
        "conf_score": [],
        "ptm": [],
        "rog": [],
        "ligand_iptm": [],
        "affinity_score": [],
        "binder_probability": []
    }
    
    for d in designs:
        if d.plddt_overall is not None: metrics["plddt_overall"].append(d.plddt_overall)
        if d.plddt_binder is not None: metrics["plddt_binder"].append(d.plddt_binder)
        if d.pae_overall is not None: metrics["pae_overall"].append(d.pae_overall)
        if d.pae_interaction is not None: metrics["pae_interaction"].append(d.pae_interaction)
        if d.rmsd_binder is not None: metrics["rmsd_binder"].append(d.rmsd_binder)
        if d.rmsd_overall is not None: metrics["rmsd_overall"].append(d.rmsd_overall)
        if d.mpnn_score is not None: metrics["mpnn_score"].append(d.mpnn_score)
        if d.conf_score is not None: metrics["conf_score"].append(d.conf_score)
        if d.ptm is not None: metrics["ptm"].append(d.ptm)
        if d.rog is not None: metrics["rog"].append(d.rog)
        if d.ligand_iptm is not None: metrics["ligand_iptm"].append(d.ligand_iptm)
        if d.affinity_score is not None: metrics["affinity_score"].append(d.affinity_score)
        if d.binder_probability is not None: metrics["binder_probability"].append(d.binder_probability)
            
    return metrics


# --- Endpoints ---

@router.get("/job/{job_id}", response_model=JobAnalytics)
async def get_job_analytics(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get aggregated analytics for a single job."""
    # Fetch designs
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    if not designs:
        return JobAnalytics(
            job_id=job_id,
            design_count=0,
            metrics=None,
            correlations=None,
            pipeline_summary={}
        )

    # Extract raw values
    raw_metrics = extract_metrics(designs)
    
    # Calculate distributions
    analyzed_metrics = {}
    for name, values in raw_metrics.items():
        if values:
            analyzed_metrics[name] = calculate_distribution(values)
            
    # Calculate correlations for scatter plots (e.g. pLDDT vs PAE)
    correlations = {}
    if raw_metrics["plddt_overall"] and raw_metrics["pae_overall"]:
        correlations["plddt_vs_pae"] = [
            ScatterPoint(x=d.plddt_overall, y=d.pae_overall, id=d.id)
            for d in designs if d.plddt_overall is not None and d.pae_overall is not None
        ]

    return JobAnalytics(
        job_id=job_id,
        design_count=len(designs),
        metrics=analyzed_metrics,
        correlations=correlations,
        pipeline_summary={
            "total_designs": len(designs),
            "favorites": sum(1 for d in designs if d.is_favorite)
        }
    )

@router.get("/job/{job_id}/designs", response_model=List[DesignMetricPoint])
async def get_job_design_metrics(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get raw metrics for all designs in a job (for custom client-side charting)."""
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    return [
        DesignMetricPoint(
            id=d.id,
            name=d.name,
            metrics={
                "plddt_overall": d.plddt_overall or 0,
                "pae_overall": d.pae_overall or 0,
                "ptm": d.ptm or 0,
                "rmsd_binder": d.rmsd_binder or 0
            }
        )
        for d in designs
    ]

@router.post("/batch", response_model=BatchAnalytics)
async def get_batch_analytics(
    job_ids: List[str],
    session: AsyncSession = Depends(get_session)
):
    """Compare metrics across multiple jobs."""
    summary = {}
    
    # Simple loop for now - optimal would be single query with group_by
    for jid in job_ids:
        query = select(Design).where(Design.job_id == jid)
        result = await session.execute(query)
        designs = result.scalars().all()
        
        if not designs:
            continue
            
        metrics = extract_metrics(designs)
        
        # Calculate averages for comparison
        for m_name, values in metrics.items():
            if values:
                if m_name not in summary:
                    summary[m_name] = {}
                summary[m_name][jid] = float(np.mean(values))
                
    return BatchAnalytics(
        job_ids=job_ids,
        metrics_summary=summary,
        common_metrics=list(summary.keys())
    )


# === NEW ADVANCED ANALYTICS ENDPOINTS ===

class CorrelationPoint(BaseModel):
    metric_x: str
    metric_y: str
    r: float  # Pearson R
    n: int    # Sample size

class CorrelationMatrix(BaseModel):
    job_id: str
    metrics: List[str]
    matrix: List[List[float]]  # NxN Pearson R values
    sample_sizes: List[List[int]]  # NxN sample counts

class AACount(BaseModel):
    aa: str
    count: int
    frequency: float

class CDRComposition(BaseModel):
    cdr_name: str
    total_residues: int
    composition: List[AACount]

class AACompositionResponse(BaseModel):
    job_id: str
    overall: List[AACount]
    by_cdr: List[CDRComposition]

class PositionFrequency(BaseModel):
    position: int
    frequencies: Dict[str, float]  # AA -> frequency

class SequenceLogoData(BaseModel):
    cdr_name: str
    length: int
    positions: List[PositionFrequency]
    consensus: str
    sequence_count: int

class CDRAnalysisResponse(BaseModel):
    job_id: str
    logos: List[SequenceLogoData]


def pearson_r(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient."""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    x_arr = np.array(x)
    y_arr = np.array(y)
    # Filter to only paired non-null values
    mask = ~(np.isnan(x_arr) | np.isnan(y_arr))
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    if len(x_arr) < 3:
        return 0.0
    return float(np.corrcoef(x_arr, y_arr)[0, 1])


STANDARD_AAs = "ACDEFGHIKLMNPQRSTVWY"


@router.get("/job/{job_id}/correlation-matrix", response_model=CorrelationMatrix)
async def get_correlation_matrix(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Compute pairwise Pearson correlations between all numeric metrics."""
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    if not designs:
        raise HTTPException(status_code=404, detail="Job not found or no designs")
    
    raw_metrics = extract_metrics(designs)
    
    # Filter metrics with at least 5 data points
    valid_metrics = {k: v for k, v in raw_metrics.items() if len(v) >= 5}
    metric_names = list(valid_metrics.keys())
    n = len(metric_names)
    
    # Build correlation matrix
    matrix = []
    sample_sizes = []
    
    for i, m1 in enumerate(metric_names):
        row = []
        size_row = []
        for j, m2 in enumerate(metric_names):
            if i == j:
                row.append(1.0)
                size_row.append(len(valid_metrics[m1]))
            else:
                r = pearson_r(valid_metrics[m1], valid_metrics[m2])
                row.append(round(r, 4) if not np.isnan(r) else 0.0)
                size_row.append(min(len(valid_metrics[m1]), len(valid_metrics[m2])))
        matrix.append(row)
        sample_sizes.append(size_row)
    
    return CorrelationMatrix(
        job_id=job_id,
        metrics=metric_names,
        matrix=matrix,
        sample_sizes=sample_sizes
    )


@router.get("/job/{job_id}/aa-composition", response_model=AACompositionResponse)
async def get_aa_composition(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Compute amino acid composition from CDR sequences."""
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    if not designs:
        raise HTTPException(status_code=404, detail="Job not found or no designs")
    
    cdr_fields = ["cdr_h1", "cdr_h2", "cdr_h3", "cdr_l1", "cdr_l2", "cdr_l3"]
    
    overall_counts = {aa: 0 for aa in STANDARD_AAs}
    cdr_compositions = []
    
    for cdr_name in cdr_fields:
        cdr_counts = {aa: 0 for aa in STANDARD_AAs}
        total = 0
        
        for d in designs:
            seq = getattr(d, cdr_name, None)
            if seq:
                for aa in seq.upper():
                    if aa in cdr_counts:
                        cdr_counts[aa] += 1
                        overall_counts[aa] += 1
                        total += 1
        
        if total > 0:
            cdr_compositions.append(CDRComposition(
                cdr_name=cdr_name.upper().replace("_", "-"),
                total_residues=total,
                composition=[
                    AACount(aa=aa, count=cnt, frequency=round(cnt / total, 4))
                    for aa, cnt in sorted(cdr_counts.items()) if cnt > 0
                ]
            ))
    
    overall_total = sum(overall_counts.values())
    overall_list = [
        AACount(aa=aa, count=cnt, frequency=round(cnt / overall_total, 4) if overall_total > 0 else 0.0)
        for aa, cnt in sorted(overall_counts.items()) if cnt > 0
    ]
    
    return AACompositionResponse(
        job_id=job_id,
        overall=overall_list,
        by_cdr=cdr_compositions
    )


@router.get("/job/{job_id}/cdr-logos", response_model=CDRAnalysisResponse)
async def get_cdr_sequence_logos(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Generate sequence logo data for CDR regions."""
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    if not designs:
        raise HTTPException(status_code=404, detail="Job not found or no designs")
    
    cdr_fields = ["cdr_h1", "cdr_h2", "cdr_h3", "cdr_l1", "cdr_l2", "cdr_l3"]
    logos = []
    
    for cdr_name in cdr_fields:
        sequences = []
        for d in designs:
            seq = getattr(d, cdr_name, None)
            if seq and len(seq) > 0:
                sequences.append(seq.upper())
        
        if len(sequences) < 2:
            continue
        
        # Find modal length (most common CDR length)
        lengths = [len(s) for s in sequences]
        modal_length = max(set(lengths), key=lengths.count)
        
        # Filter to sequences of modal length
        aligned = [s for s in sequences if len(s) == modal_length]
        
        if len(aligned) < 2:
            continue
        
        # Calculate positional frequencies
        positions = []
        consensus = ""
        
        for pos in range(modal_length):
            counts = {aa: 0 for aa in STANDARD_AAs}
            for seq in aligned:
                aa = seq[pos]
                if aa in counts:
                    counts[aa] += 1
            
            total = sum(counts.values())
            freqs = {aa: round(cnt / total, 4) for aa, cnt in counts.items() if cnt > 0}
            positions.append(PositionFrequency(position=pos + 1, frequencies=freqs))
            
            # Consensus is most frequent AA at this position
            if freqs:
                consensus += max(freqs, key=freqs.get)
        
        logos.append(SequenceLogoData(
            cdr_name=cdr_name.upper().replace("_", "-"),
            length=modal_length,
            positions=positions,
            consensus=consensus,
            sequence_count=len(aligned)
        ))
    
    return CDRAnalysisResponse(job_id=job_id, logos=logos)

