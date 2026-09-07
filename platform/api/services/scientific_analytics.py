"""Read-only, source-verified canonical scalar analytics for marked results."""
import hashlib
import json
import math
import statistics
from itertools import combinations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator
from sqlalchemy import select
from database import Job
from services.core_protein_scientific_contract import revision_for_job, validate_metric


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MetricState(ClosedModel):
    state: Literal['ok', 'unavailable', 'invalid']
    value: StrictFloat | StrictInt | None
    reason_code: str | None

    @model_validator(mode='after')
    def coherent(self):
        if self.state == 'ok':
            if self.value is None or not math.isfinite(self.value) or self.reason_code is not None:
                raise ValueError('ok requires finite real value and null reason')
        elif self.value is not None or not self.reason_code or not self.reason_code.strip():
            raise ValueError('missing/invalid requires null value and reason')
        return self


class MetricDescriptor(ClosedModel):
    metric_id: str
    source: Literal['canonical_artifact']
    scope: str
    unit: str
    direction: Literal['higher', 'lower', 'none']
    producer_version: str
    derivation_version: str


class MetricSource(ClosedModel):
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)


class DistributionStatistics(ClosedModel):
    min: StrictFloat | StrictInt
    max: StrictFloat | StrictInt
    avg: StrictFloat | StrictInt
    median: StrictFloat | StrictInt
    std_dev: StrictFloat | StrictInt


class DistributionSummary(ClosedModel):
    observed_count: StrictInt = Field(ge=0)
    unavailable_count: StrictInt = Field(ge=0)
    invalid_count: StrictInt = Field(ge=0)
    descriptor: MetricDescriptor
    statistics: DistributionStatistics | None
    reason_code: str | None


class PairPoint(ClosedModel):
    id: str
    x: StrictFloat | StrictInt
    y: StrictFloat | StrictInt


class PairSummary(ClosedModel):
    x_metric: str
    y_metric: str
    pair_count: StrictInt = Field(ge=0)
    excluded_count: StrictInt = Field(ge=0)
    excluded_ids: list[str]
    points: list[PairPoint]
    correlation: MetricState


class ScientificCohort(ClosedModel):
    cohort_key: str
    design_ids: list[str]
    metrics: dict[str, DistributionSummary]
    pairs: dict[str, PairSummary]


# Owner-fixed native definitions also describe unreadable evidence without
# trusting a tampered compact block for its descriptor or supported metric list.
_BOLTZ = {'ptm': ('dimensionless', 'overall'), 'complex_plddt': ('fraction', 'complex')}


def metric_state(value):
    if value is None:
        return MetricState(state='unavailable', value=None, reason_code='not_reported')
    if type(value) not in (int, float) or not math.isfinite(value):
        return MetricState(state='invalid', value=None, reason_code='not_finite_real')
    return MetricState(state='ok', value=value, reason_code=None)


def projection(design, *, records=(), invalid_reason=None, model_id=None):
    """Project owner-verified records; never read legacy columns as canonical."""
    states, descriptors, sources = {}, {}, {}
    for record in records:
        record = validate_metric(record)
        key = record['metric_key']
        if key in states:
            raise ValueError('duplicate canonical metric')
        states[key] = MetricState(**{k:record[k] for k in ('state','value','reason_code')})
        descriptors[key] = MetricDescriptor(metric_id=key, source='canonical_artifact',
            scope=record['scope'], unit=record['unit'],
            direction={'higher_is_better':'higher','lower_is_better':'lower','neutral':'none'}[record['direction']],
            producer_version=record['producer_version'], derivation_version=record['derivation_version'])
        sources[key] = MetricSource.model_validate(record['source']).model_dump()
    if invalid_reason:
        expected = _BOLTZ if model_id in ('boltz', 'boltz2') else {}
        if model_id in ('esmfold2', 'esmfold2_experimental'):
            from services.esmfold2_scientific_consumer import DESCRIPTORS
            expected = {d['metric_key']: (d['unit'], d['scope']) for d in DESCRIPTORS}
        if model_id in ('boltzgen', 'boltzgen_child'):
            # Fixed native semantics only; no artifact or alignment subtype is
            # claimed when the publication cannot be independently verified.
            expected = {
                'design_ptm': ('fraction', 'native_design_chain_tokens'),
                'affinity_probability': ('fraction', 'native_affinity_binary1_complex'),
                'filter_rmsd': ('angstrom', 'native_filter_complex_alignment'),
            }
        for key, (unit, scope) in expected.items():
            states[key] = MetricState(state='invalid', value=None, reason_code=invalid_reason)
            descriptors[key] = MetricDescriptor(metric_id=key, source='canonical_artifact', scope=scope,
                unit=unit, direction='lower' if key == 'filter_rmsd' else 'higher',
                producer_version='unverified', derivation_version='unverified')
            sources[key] = None
    signature = json.dumps({k:d.model_dump() for k,d in sorted(descriptors.items())}, sort_keys=True, separators=(',',':'))
    key = hashlib.sha256(signature.encode()).hexdigest()
    return dict(contract_revision=1, source_job_id=design.job_id,
        cohort_key=f'v1:{key}:{design.job_id}', metric_states=states,
        metric_descriptors=descriptors, metric_sources=sources,
        metrics={k:s.value for k,s in states.items() if s.state == 'ok'})


async def persisted_projection(design, session):
    """Keep owning-Job lookup and publication verification in the same read."""
    with session.no_autoflush:
        job = await session.get(Job, design.job_id)
        if job is None or revision_for_job(job) != 1:
            raise ValueError('canonical projection requires marked owning Job')
        try:
            if job.model_id in ('boltz', 'boltz2'):
                from services.boltz_scientific_consumer import verified_boltz_design
                selected = await verified_boltz_design(design, session)
                return projection(design, records=selected['block']['metrics'])
            if job.model_id in ('esmfold2', 'esmfold2_experimental'):
                from services.esmfold2_scientific_consumer import verified_esmfold2_design
                selected = await verified_esmfold2_design(design, session)
                return projection(design, records=selected['block']['metrics'])
            if job.model_id in ('boltzgen', 'boltzgen_child'):
                from services.boltzgen_candidate_publication import verified_boltzgen_design
                selected = await verified_boltzgen_design(session, design)
                return projection(design, records=selected['block']['metrics'].values())
            # Other model adapters must independently validate native source
            # bytes before contributing canonical scalar records here.
            return projection(design, invalid_reason='missing_canonical_publication', model_id=job.model_id)
        except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
            return projection(design, invalid_reason='invalid_canonical_publication', model_id=job.model_id)


def summarize(rows):
    """Rows are (Design ID, verified projection) pairs from one descriptor cohort."""
    ids = [identity for identity, _ in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate analytics candidate')
    metrics = {}
    for key in sorted({key for _, p in rows for key in p['metric_states']}):
        descriptors = [p['metric_descriptors'][key] for _,p in rows]
        if any(d != descriptors[0] for d in descriptors):
            raise ValueError('incompatible metric descriptors')
        states = [p['metric_states'][key] for _,p in rows]
        values = [s.value for s in states if s.state == 'ok']
        metrics[key] = dict(observed_count=len(values),
            unavailable_count=sum(s.state == 'unavailable' for s in states),
            invalid_count=sum(s.state == 'invalid' for s in states), descriptor=descriptors[0],
            statistics=dict(min=min(values), max=max(values), avg=statistics.mean(values),
                median=statistics.median(values), std_dev=statistics.pstdev(values)) if values else None,
            reason_code=None if values else 'no_observed_values')
    pairs = {}
    for x, y in combinations(sorted(metrics), 2):
        points = [dict(id=id, x=p['metrics'][x], y=p['metrics'][y])
            for id,p in rows if x in p['metrics'] and y in p['metrics']]
        included = {p['id'] for p in points}
        correlation = MetricState(state='unavailable', value=None, reason_code='insufficient_pairs')
        if len(points) >= 3:
            try:
                correlation = metric_state(statistics.correlation([p['x'] for p in points], [p['y'] for p in points]))
            except statistics.StatisticsError:
                correlation = MetricState(state='unavailable', value=None, reason_code='constant_metric')
        pairs[f'{x}_vs_{y}'] = dict(x_metric=x, y_metric=y, points=points, pair_count=len(points),
            excluded_count=len(rows)-len(points), excluded_ids=[i for i in ids if i not in included], correlation=correlation)
    return dict(metrics=metrics, pairs=pairs)


async def partition(designs, owners, session, projections=None):
    legacy, grouped = [], {}
    for design in designs:
        if revision_for_job(owners.get(design.job_id)) != 1:
            legacy.append(design)
        else:
            value = projections[design.id] if projections is not None else await persisted_projection(design, session)
            grouped.setdefault(value['cohort_key'], []).append((design.id, value))
    return legacy, [ScientificCohort(cohort_key=key, design_ids=[id for id,_ in rows], **summarize(rows))
        for key, rows in grouped.items()]


async def owning_jobs(session, designs):
    ids = {d.job_id for d in designs}
    if not ids:
        return {}
    rows = (await session.execute(select(Job).where(Job.id.in_(ids)))).scalars().all()
    return {job.id: job for job in rows}
