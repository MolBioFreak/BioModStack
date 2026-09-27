"""Read-only binder-round lineage projection; native readers own numerical evidence.

Only persisted round-step IDs join jobs to candidates. Names, rank, filenames and
sequence similarity are deliberately not lineage mechanisms.
"""
from collections import defaultdict
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AnalysisRun, Design, Job
from services.analysis_runs import serialize_analysis_run


def _step(job):
    value = (job.provenance or {}).get("binder_round_step")
    return value if isinstance(value, dict) else {}


def _sequence(design, job):
    return {
        "job_id": job.id, "design_id": design.id if design else None,
        "status": job.status, "model_id": job.model_id,
        # Retain producer identity, rather than inventing a sequence ordinal.
        "native_identity": design.provenance if design else None,
        "name": design.name if design else None,
        "predictions": [],
    }


def _prediction(job, design, analyses):
    step = _step(job)
    base = f"/api/designs/{quote(design.id, safe='')}" if design else None
    runs = [serialize_analysis_run(run, analysis_type=run.analysis_type,
                subject_kind=run.subject_kind, subject_id=run.subject_id)
            for run in analyses]
    return {
        "job_id": job.id, "design_id": design.id if design else None,
        "name": design.name if design else None,
        "status": job.status, "model_id": job.model_id,
        "target_state": step.get("target_state"),
        "binder_chains": step.get("binder_chains", []),
        "target_chains": step.get("target_chains", []),
        "step": step, "native_identity": design.provenance if design else None,
        # Native Protenix/Boltz PAE lives in producer artifacts, not legacy columns.
        # A readback URL advertises access, not the presence of a measurement.
        "pae_url": f"{base}/pae" if base else None,
        "chain_metrics_url": f"{base}/chain-metrics" if base else None,
        "design_url": base,
        "analyses": runs,
        "ipsae": [run for run in runs if run["analysis_type"] == "ipsae_interface"],
        # All native wrappers remain accessible, including comparison revisions.
        "error_message": getattr(job, "error_message", None),
    }


async def binder_evidence(session: AsyncSession, job: Job, *, offset: int, limit: int):
    """Paginate source Designs before querying their explicitly joined descendants."""
    with session.no_autoflush:
        total = await session.scalar(select(func.count()).select_from(Design).where(Design.job_id == job.id))
        sources = list((await session.scalars(select(Design).where(Design.job_id == job.id)
            .order_by(Design.id).offset(offset).limit(limit))).all())
        envelope = {"schema_version": 1, "job_id": job.id, "offset": offset,
                    "limit": limit, "total": total, "records": []}
        if not sources:
            return envelope
        ids = [source.id for source in sources]
        step = Job.provenance["binder_round_step"]
        children = list((await session.scalars(select(Job).where(
            step["root_job_id"].as_string() == job.id,
            (step["backbone_design_id"].as_string().in_(ids)) |
            (step["source_design_id"].as_string().in_(ids)),
        ).order_by(Job.created_at, Job.id))).all())
        child_ids = [child.id for child in children]
        designs = list((await session.scalars(select(Design).where(Design.job_id.in_(child_ids))
            .order_by(Design.id))).all()) if child_ids else []
        by_job = defaultdict(list)
        for design in designs:
            by_job[design.job_id].append(design)
        design_ids = [design.id for design in designs]
        runs = list((await session.scalars(select(AnalysisRun).where(
            AnalysisRun.subject_kind == "design", AnalysisRun.subject_id.in_(design_ids)
        ).order_by(AnalysisRun.queued_at, AnalysisRun.id))).all()) if design_ids else []
        by_design = defaultdict(list)
        for run in runs:
            by_design[run.subject_id].append(run)
        for source in sources:
            provenance = source.provenance or {}
            record = {"source_design_id": source.id, "candidate_key": provenance.get("candidate_key", provenance.get("producer_candidate_key")), "sequences": []}
            sequences = {}
            for child in children:
                binding = _step(child)
                if binding.get("stage") != "sequence_design" or binding.get("source_design_id") != source.id:
                    continue
                for design in by_job[child.id] or [None]:
                    entry = _sequence(design, child)
                    record["sequences"].append(entry)
                    if design:
                        sequences[design.id] = entry
            for child in children:
                binding = _step(child)
                if binding.get("stage") != "prediction":
                    continue
                source_id = binding.get("source_design_id")
                if source_id == source.id and source_id not in sequences:
                    sequences[source_id] = _sequence(source, job)
                    record["sequences"].append(sequences[source_id])
                sequence = sequences.get(source_id)
                if sequence is None:
                    continue
                for design in by_job[child.id] or [None]:
                    sequence["predictions"].append(_prediction(child, design, by_design[design.id] if design else []))
            envelope["records"].append(record)
        return envelope
