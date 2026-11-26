"""Read-only, source-verified canonical scalar analytics for marked results."""
import hashlib
import json
import math
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
    source: Literal['canonical_artifact', 'retained_native_artifact', 'external_provider_artifact']
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
    std_dev: StrictFloat | StrictInt | None = None
    q1: StrictFloat | StrictInt | None = None
    q3: StrictFloat | StrictInt | None = None
    iqr: StrictFloat | StrictInt | None = None
    histogram_bins: list[StrictFloat | StrictInt] | None = None
    histogram_counts: list[StrictInt] | None = None


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
from services.boltz_scientific_consumer import SCALAR_DESCRIPTORS
_BOLTZ = {key: (unit, scope) for key, (unit, scope, _) in SCALAR_DESCRIPTORS.items()}


def metric_state(value):
    if value is None:
        return MetricState(state='unavailable', value=None, reason_code='not_reported')
    if type(value) not in (int, float) or not math.isfinite(value):
        return MetricState(state='invalid', value=None, reason_code='not_finite_real')
    return MetricState(state='ok', value=value, reason_code=None)


def projection(design, *, records=(), invalid_reason=None, model_id=None, source_kind='canonical_artifact'):
    """Project owner-verified records; never read legacy columns as canonical."""
    states, descriptors, sources = {}, {}, {}
    for record in records:
        record = validate_metric(record)
        key = record['metric_key']
        if key in states:
            raise ValueError('duplicate canonical metric')
        states[key] = MetricState(**{k:record[k] for k in ('state','value','reason_code')})
        descriptors[key] = MetricDescriptor(metric_id=key, source=source_kind,
            scope=record['scope'], unit=record['unit'],
            direction={'higher_is_better':'higher','lower_is_better':'lower','neutral':'none'}[record['direction']],
            producer_version=record['producer_version'], derivation_version=record['derivation_version'])
        sources[key] = MetricSource.model_validate(record['source']).model_dump()
    if invalid_reason:
        expected = _BOLTZ if model_id in ('boltz', 'boltz2', 'boltz_cp_experimental') else {}
        if model_id == 'boltz_api':
            expected = {k:v for k,v in _BOLTZ.items() if k != 'confidence_score'}
            expected['structure_confidence'] = ('dimensionless', 'provider_native_structure_confidence')
        if model_id == 'esmfold2':
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
        if model_id == 'protenix':
            from services.protenix_scientific_consumer import SCALAR_DESCRIPTORS
            expected = {d['metric_key']: (d['unit'], d['scope']) for d in SCALAR_DESCRIPTORS}
        if model_id == 'bindcraft2':
            from services.bindcraft2_scientific_consumer import DESCRIPTORS
            expected = {k: (unit, scope) for k, (unit, scope, _) in DESCRIPTORS.items()}
        for key, (unit, scope) in expected.items():
            states[key] = MetricState(state='unavailable' if invalid_reason in ('missing_scalar_source', 'missing_native_scalar_authority') else 'invalid', value=None, reason_code=invalid_reason)
            descriptors[key] = MetricDescriptor(metric_id=key, source=source_kind, scope=scope,
                unit=unit, direction='lower' if key in ('filter_rmsd', 'gpde', 'complex_pde', 'complex_ipde', 'i_pAE', 'Binder_RMSD') else 'higher',
                producer_version='unverified', derivation_version='unverified')
            sources[key] = None
    signature = json.dumps({k:d.model_dump() for k,d in sorted(descriptors.items())}, sort_keys=True, separators=(',',':'))
    key = hashlib.sha256(signature.encode()).hexdigest()
    return dict(contract_revision=1, source_job_id=design.job_id,
        publication_state=MetricState(state='unavailable' if invalid_reason in ('missing_canonical_publication', 'missing_scalar_source', 'missing_native_scalar_authority') else 'invalid', value=None, reason_code=invalid_reason) if invalid_reason else None,
        cohort_key=f'v1:{key}:{design.job_id}', metric_states=states,
        metric_descriptors=descriptors, metric_sources=sources,
        metrics={k:s.value for k,s in states.items() if s.state == 'ok'})


def native_scalar_owner(job):
    return job is not None and (revision_for_job(job) == 1
        or job.model_id in ('boltz_cp_experimental', 'bindcraft2')
        or ((job.provenance or {}).get('external_import') or {}).get('provider') == 'boltz_api')


def retained_scalar_projection(design, job, *, scalar_read=None, manifest_indexes=None):
    from services.boltz_scientific_consumer import retained_cp_snapshot, scalar_records
    external = ((job.provenance or {}).get('external_import') or {}).get('provider') == 'boltz_api'
    if external:
        from services.external_imports.boltz_api import retained_scalar_records
        records = retained_scalar_records(design, job, scalar_read=scalar_read, manifest_indexes=manifest_indexes)
        return projection(design, records=records, source_kind='external_provider_artifact')
    if scalar_read is not None:
        from services.boltz_scientific_consumer import retained_cp_paths
        root, keys, producer = retained_cp_paths(design, job)
        artifact, payload = scalar_read(root, keys['metrics'])
        source = dict(artifact_sha256=artifact['sha256'], candidate_id=design.id, document_id=producer['producer_output_key'])
        return projection(design, records=scalar_records(payload, source, 'boltz-cp:retained-output'), source_kind='retained_native_artifact')
    selected = retained_cp_snapshot(design, job, roles=('metrics',))
    source = dict(artifact_sha256=selected['artifacts']['metrics']['sha256'],
        candidate_id=design.id, document_id=selected['producer']['producer_output_key'])
    records = scalar_records(json.loads(selected['snapshots']['metrics']), source,
        'boltz-cp:retained-output')
    return projection(design, records=records, source_kind='retained_native_artifact')


async def persisted_projection(design, session):
    """Keep owning-Job lookup and publication verification in the same read."""
    with session.no_autoflush:
        job = await session.get(Job, design.job_id)
        if job is None or not native_scalar_owner(job):
            raise ValueError('native projection requires owning Job')
        try:
            if job.model_id == 'bindcraft2':
                from services.bindcraft2_scientific_consumer import scalar_projections
                return (await scalar_projections([design], job, session))[design.id]
            if revision_for_job(job) != 1:
                # Plotly's compact query defers structure/provenance columns.
                # Resolve them asynchronously, never trigger implicit ORM I/O.
                from sqlalchemy import inspect
                from starlette.concurrency import run_in_threadpool
                unloaded = inspect(design).unloaded.intersection({'pdb_path', 'provenance'})
                if unloaded:
                    await session.refresh(design, attribute_names=sorted(unloaded))
                return await run_in_threadpool(retained_scalar_projection, design, job)
            if job.model_id in ('boltz', 'boltz2'):
                from services.boltz_scientific_consumer import verified_boltz_design
                selected = await verified_boltz_design(design, session)
                return projection(design, records=selected['block']['metrics'])
            if job.model_id == 'esmfold2':
                from services.esmfold2_scientific_consumer import verified_esmfold2_design
                selected = await verified_esmfold2_design(design, session)
                return projection(design, records=selected['block']['metrics'])
            if job.model_id in ('boltzgen', 'boltzgen_child'):
                from services.boltzgen_candidate_publication import verified_boltzgen_design
                selected = await verified_boltzgen_design(session, design)
                return projection(design, records=selected['block']['metrics'].values())
            if job.model_id == 'protenix':
                from services.protenix_scientific_consumer import verified_scalar_metrics
                return projection(design, records=await verified_scalar_metrics(design, session))
            # Other model adapters must independently validate native source
            # bytes before contributing canonical scalar records here.
            return projection(design, invalid_reason='missing_canonical_publication', model_id=job.model_id)
        except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
            if revision_for_job(job) != 1:
                external = ((job.provenance or {}).get('external_import') or {}).get('provider') == 'boltz_api'
                return projection(design, invalid_reason='invalid_retained_native_evidence',
                    model_id='boltz_api' if external else job.model_id,
                    source_kind='external_provider_artifact' if external else 'retained_native_artifact')
            return projection(design, invalid_reason='invalid_canonical_publication', model_id=job.model_id)


async def bulk_scalar_projections(designs, owners, session):
    """One bounded scalar read per companion; metadata reuse is request-local.

    This is scalar-byte verification against committed publication/document
    metadata, NOT a fresh coordinate/PAE verification. Spatial readers are unchanged.
    """
    from pathlib import Path
    from sqlalchemy import inspect
    from database import Design
    from types import SimpleNamespace
    fields = ('id', 'job_id', 'name', 'confidence_metrics', 'provenance', 'pdb_path', 'json_path', 'source_stage', 'producer_model_id')
    deferred = [d.id for d in designs if inspect(d, raiseerr=False) is not None
                and inspect(d).unloaded.intersection(fields)]
    if deferred:
        loaded = {row['id']: SimpleNamespace(**row) for row in (await session.execute(
            select(*(getattr(Design, key) for key in fields)).where(Design.id.in_(deferred)))).mappings()}
        designs = [loaded.get(d.id, d) for d in designs]
    from starlette.concurrency import run_in_threadpool
    from services.boltz_scientific_persistence import _snapshot
    from services.protenix_scientific_consumer import _json, _return_artifacts
    from paths import get_data_root, resolve_runtime_data_path

    def collect():
        cache, result, manifest_indexes = {}, {}, {}
        def scalar_read(root, key):
            identity = ('retained', str(root), key)
            if identity not in cache:
                try:
                    artifact, raw = _snapshot(root, key)
                    cache[identity] = (artifact, _json(raw))
                except (OSError, ValueError) as exc:
                    cache[identity] = exc
            if isinstance(cache[identity], Exception):
                raise cache[identity]
            return cache[identity]
        def read(descriptor, *, index=None, returned=None):
            if returned is not None:
                return_job, root = returned
                key = ('return', return_job.id)
                if key not in cache:
                    try:
                        cache[key] = _return_artifacts(return_job, root)
                    except (OSError, ValueError) as exc:
                        cache[key] = exc
                if isinstance(cache[key], Exception):
                    raise cache[key]
                return cache[key]
            key = (str(job.output_dir), descriptor['path'], descriptor['sha256'])
            if key not in cache:
                path = Path(descriptor['path'])
                path = resolve_runtime_data_path(path) if path.is_absolute() else get_data_root() / path
                root = Path(job.output_dir)
                root = resolve_runtime_data_path(root) if root.is_absolute() else get_data_root() / root
                observed, value = scalar_read(root, path.relative_to(root).as_posix())
                if observed['sha256'] != descriptor['sha256']:
                    raise ValueError('changed scalar publication artifact')
                cache[key] = value
            value = cache[key]
            if index is None:
                return value
            indexed_key = (*key, *index)
            if indexed_key not in cache:
                rows = value[index[0]]
                indexed = {row[index[1]]: row for row in rows}
                if len(indexed) != len(rows):
                    raise ValueError('duplicate scalar publication identity')
                cache[indexed_key] = indexed
            return cache[indexed_key]

        for design in designs:
            job = owners.get(design.job_id)
            if not native_scalar_owner(job) or job.model_id == 'bindcraft2':
                continue
            try:
                if revision_for_job(job) != 1:
                    result[design.id] = retained_scalar_projection(design, job, scalar_read=scalar_read, manifest_indexes=manifest_indexes)
                    continue
                if job.model_id in ('boltz', 'boltz2'):
                    from services.boltz_scientific_consumer import scalar_records_from_publication
                elif job.model_id == 'esmfold2':
                    from services.esmfold2_scientific_consumer import scalar_records_from_publication
                elif job.model_id == 'protenix':
                    from services.protenix_scientific_consumer import scalar_records_from_publication
                else:
                    continue  # Unchanged non-prediction native owners below.
                result[design.id] = projection(design, records=scalar_records_from_publication(design, job, read))
            except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError) as exc:
                external = ((job.provenance or {}).get('external_import') or {}).get('provider') == 'boltz_api'
                result[design.id] = projection(design, invalid_reason=('missing_scalar_source' if isinstance(exc, FileNotFoundError) else
                        'scalar_read_failed' if isinstance(exc, OSError) else 'invalid_scalar_publication'),
                    model_id='boltz_api' if external else job.model_id,
                    source_kind='external_provider_artifact' if external else
                    'retained_native_artifact' if revision_for_job(job) != 1 else 'canonical_artifact')
        return result

    result = await run_in_threadpool(collect)
    from services.bindcraft2_scientific_consumer import scalar_projections
    for job in owners.values():
        selected = [d for d in designs if d.job_id == job.id]
        if job.model_id == 'bindcraft2' and selected:
            result.update(await scalar_projections(selected, job, session))
    for design in designs:
        if native_scalar_owner(owners.get(design.job_id)) and design.id not in result:
            result[design.id] = await persisted_projection(await session.get(Design, design.id), session)
    return result


def distribution_statistics_from_toolkit(column: dict | None) -> DistributionStatistics | None:
    if not isinstance(column, dict) or not column.get('count'):
        return None
    hist = column.get('histogram') if isinstance(column.get('histogram'), dict) else None
    return DistributionStatistics(
        min=float(column['min']),
        max=float(column['max']),
        avg=float(column['mean']),
        median=float(column['median']),
        std_dev=float(column['std']) if column.get('std') is not None else None,
        q1=float(column['q1']) if column.get('q1') is not None else None,
        q3=float(column['q3']) if column.get('q3') is not None else None,
        iqr=float(column['iqr']) if column.get('iqr') is not None else None,
        histogram_bins=[float(x) for x in hist['edges']] if hist and hist.get('edges') is not None else None,
        histogram_counts=[int(x) for x in hist['counts']] if hist and hist.get('counts') is not None else None,
    )


def cohort_structure(rows, *, include_pairs=True):
    """Extract cohort descriptors, observed counts, raw finite columns, and scatter pairs without computing stats."""
    ids = [identity for identity, _ in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate analytics candidate')
    metrics_meta = {}
    columns: dict[str, list[float]] = {}
    for key in sorted({key for _, proj in rows for key in proj['metric_states']}):
        descriptors = [proj['metric_descriptors'][key] for _, proj in rows]
        if any(d != descriptors[0] for d in descriptors):
            raise ValueError('incompatible metric descriptors')
        states = [proj['metric_states'][key] for _, proj in rows]
        values = [float(s.value) for s in states if s.state == 'ok']
        metrics_meta[key] = dict(
            observed_count=len(values),
            unavailable_count=sum(s.state == 'unavailable' for s in states),
            invalid_count=sum(s.state == 'invalid' for s in states),
            descriptor=descriptors[0],
            reason_code=None if values else 'no_observed_values',
        )
        if values:
            columns[key] = values
    metric_keys = sorted(metrics_meta) if include_pairs else []
    pair_specs = []
    for x, y in combinations(metric_keys, 2):
        points = [
            dict(id=row_id, x=proj['metrics'][x], y=proj['metrics'][y])
            for row_id, proj in rows if x in proj['metrics'] and y in proj['metrics']
        ]
        pair_specs.append((x, y, points))
    return dict(ids=ids, metrics_meta=metrics_meta, columns=columns, pair_specs=pair_specs)


def serialize_cohort_metadata(cohort_key: str, rows, *, include_pairs=True) -> dict:
    struct = cohort_structure(rows, include_pairs=include_pairs)
    metrics_meta = {}
    for key, meta in struct['metrics_meta'].items():
        desc = meta['descriptor']
        metrics_meta[key] = {
            'observed_count': meta['observed_count'],
            'unavailable_count': meta['unavailable_count'],
            'invalid_count': meta['invalid_count'],
            'descriptor': desc.model_dump(mode='json') if hasattr(desc, 'model_dump') else dict(desc),
            'reason_code': meta['reason_code'],
        }
    pairs_meta = []
    ids = struct['ids']
    for x, y, points in struct['pair_specs']:
        included = {pt['id'] for pt in points}
        pairs_meta.append({
            'x_metric': x,
            'y_metric': y,
            'points': points,
            'pair_count': len(points),
            'excluded_count': len(rows) - len(points),
            'excluded_ids': [row_id for row_id in ids if row_id not in included],
        })
    return {
        'cohort_key': cohort_key,
        'design_ids': ids,
        'metrics_meta': metrics_meta,
        'pairs_meta': pairs_meta,
    }


def assemble_cohort_from_metadata(
    cohort_meta: dict,
    *,
    summary_columns: dict[str, dict] | None = None,
    correlation_lookup: dict[tuple[str, str], MetricState] | None = None,
    column_prefix: str = '',
    unavailable_reason: str = 'stats_toolkit_unavailable',
) -> ScientificCohort:
    cohort_key = str(cohort_meta['cohort_key'])
    col_map = summary_columns if isinstance(summary_columns, dict) else {}
    metrics = {}
    for key, meta in (cohort_meta.get('metrics_meta') or {}).items():
        prefixed = f'{column_prefix}cohort::{cohort_key}::{key}'
        col = col_map.get(prefixed)
        if col is None and not column_prefix:
            col = col_map.get(key)
        stats = distribution_statistics_from_toolkit(col) if meta['observed_count'] else None
        reason = meta['reason_code']
        if meta['observed_count'] and stats is None:
            reason = unavailable_reason
        desc = meta['descriptor']
        if not isinstance(desc, MetricDescriptor):
            desc = MetricDescriptor.model_validate(desc)
        metrics[key] = DistributionSummary(
            observed_count=meta['observed_count'],
            unavailable_count=meta['unavailable_count'],
            invalid_count=meta['invalid_count'],
            descriptor=desc,
            statistics=stats,
            reason_code=reason,
        )
    pairs = {}
    for pmeta in (cohort_meta.get('pairs_meta') or []):
        x, y = pmeta['x_metric'], pmeta['y_metric']
        if pmeta['pair_count'] < 3:
            correlation = MetricState(state='unavailable', value=None, reason_code='insufficient_pairs')
        elif correlation_lookup is not None and (x, y) in correlation_lookup:
            correlation = correlation_lookup[(x, y)]
        elif correlation_lookup is not None and (y, x) in correlation_lookup:
            correlation = correlation_lookup[(y, x)]
        else:
            correlation = MetricState(state='unavailable', value=None, reason_code=unavailable_reason)
        pairs[f'{x}_vs_{y}'] = PairSummary(
            x_metric=x,
            y_metric=y,
            points=pmeta['points'],
            pair_count=pmeta['pair_count'],
            excluded_count=pmeta['excluded_count'],
            excluded_ids=pmeta['excluded_ids'],
            correlation=correlation,
        )
    return ScientificCohort(
        cohort_key=cohort_key,
        design_ids=list(cohort_meta.get('design_ids') or []),
        metrics=metrics,
        pairs=pairs,
    )


def assemble_cohort_from_toolkit(
    cohort_key: str,
    rows,
    *,
    summary_columns: dict[str, dict] | None = None,
    correlation_lookup: dict[tuple[str, str], MetricState] | None = None,
    include_pairs: bool = True,
    unavailable_reason: str = 'stats_toolkit_unavailable',
) -> ScientificCohort:
    meta = serialize_cohort_metadata(cohort_key, rows, include_pairs=include_pairs)
    return assemble_cohort_from_metadata(
        meta,
        summary_columns=summary_columns,
        correlation_lookup=correlation_lookup,
        unavailable_reason=unavailable_reason,
    )


def summarize(rows, *, include_pairs=True, summary_columns=None, correlation_lookup=None):
    """Assemble cohort summary from Stats Toolkit outputs (no local statistical computation)."""
    struct = cohort_structure(rows, include_pairs=include_pairs)
    columns = struct['columns']
    pair_specs = struct['pair_specs']
    resolved_columns = summary_columns
    resolved_corrs = correlation_lookup

    if resolved_columns is None and columns:
        from services.stats_toolkit_client import StatsToolkitUnavailableError, get_stats_toolkit_client
        try:
            res = get_stats_toolkit_client().execute_tool(
                'descriptive.summary',
                {'columns': columns, 'histogram_bins': 10, 'shared_bins': False},
            )
            resolved_columns = res.get('columns') or {}
        except StatsToolkitUnavailableError:
            resolved_columns = {}

    eligible_specs = [(x, y, pts) for x, y, pts in pair_specs if len(pts) >= 3]
    if resolved_corrs is None and eligible_specs:
        from services.stats_toolkit_client import StatsToolkitUnavailableError, get_stats_toolkit_client
        try:
            client = get_stats_toolkit_client()
            metric_keys = sorted(struct['metrics_meta'])
            complete_rows = [proj for _, proj in rows if all(k in proj['metrics'] for k in metric_keys)]
            if (
                len(metric_keys) >= 2
                and len(metric_keys) <= 50
                and len(complete_rows) >= 3
                and all(len(pts) == len(complete_rows) for _, _, pts in pair_specs)
            ):
                res = client.execute_tool(
                    'inference.correlation_matrix',
                    {
                        'columns': {k: [float(proj['metrics'][k]) for proj in complete_rows] for k in metric_keys},
                        'method': 'pearson',
                        'p_adjust_method': 'benjamini_hochberg',
                    },
                )
                lookup = {
                    (str(item['var_a']), str(item['var_b'])): item.get('statistic')
                    for item in (res.get('pairs') or [])
                    if isinstance(item, dict)
                }
                resolved_corrs = {}
                for x, y, _pts in eligible_specs:
                    stat = lookup.get((x, y))
                    if stat is None:
                        stat = lookup.get((y, x))
                    resolved_corrs[(x, y)] = (
                        metric_state(float(stat))
                        if stat is not None
                        else MetricState(state='unavailable', value=None, reason_code='constant_metric')
                    )
            else:
                resolved_corrs = {}
                for x, y, pts in eligible_specs:
                    res = client.execute_tool(
                        'inference.correlation_matrix',
                        {
                            'columns': {x: [float(pt['x']) for pt in pts], y: [float(pt['y']) for pt in pts]},
                            'method': 'pearson',
                            'p_adjust_method': 'none',
                        },
                    )
                    stat = (res.get('pairs') or [{}])[0].get('statistic')
                    resolved_corrs[(x, y)] = (
                        metric_state(float(stat))
                        if stat is not None
                        else MetricState(state='unavailable', value=None, reason_code='constant_metric')
                    )
        except StatsToolkitUnavailableError:
            resolved_corrs = {}

    cohort = assemble_cohort_from_toolkit(
        '',
        rows,
        summary_columns=resolved_columns or {},
        correlation_lookup=resolved_corrs,
        include_pairs=include_pairs,
    )
    metrics_out = {}
    for k, v in cohort.metrics.items():
        metrics_out[k] = {
            'observed_count': v.observed_count,
            'unavailable_count': v.unavailable_count,
            'invalid_count': v.invalid_count,
            'descriptor': v.descriptor,
            'statistics': v.statistics.model_dump() if v.statistics is not None else None,
            'reason_code': v.reason_code,
        }
    pairs_out = {}
    for k, v in cohort.pairs.items():
        pairs_out[k] = {
            'x_metric': v.x_metric,
            'y_metric': v.y_metric,
            'points': [pt.model_dump() for pt in v.points],
            'pair_count': v.pair_count,
            'excluded_count': v.excluded_count,
            'excluded_ids': list(v.excluded_ids),
            'correlation': v.correlation,
        }
    return dict(metrics=metrics_out, pairs=pairs_out)


async def partition_rows(designs, owners, session, projections=None):
    if projections is None:
        projections = await bulk_scalar_projections(designs, owners, session)
    legacy, grouped = [], {}
    for design in designs:
        if not native_scalar_owner(owners.get(design.job_id)):
            legacy.append(design)
        else:
            value = projections[design.id] if projections is not None else await persisted_projection(design, session)
            grouped.setdefault(value['cohort_key'], []).append((design.id, value))
    return legacy, grouped


async def partition(designs, owners, session, projections=None, *, summary_columns=None, correlation_lookup=None, include_pairs=True):
    legacy, grouped = await partition_rows(designs, owners, session, projections=projections)
    if summary_columns is not None:
        return legacy, [
            assemble_cohort_from_toolkit(
                key,
                rows,
                summary_columns=summary_columns,
                correlation_lookup=correlation_lookup,
                include_pairs=include_pairs,
            )
            for key, rows in grouped.items()
        ]
    return legacy, [
        ScientificCohort(cohort_key=key, design_ids=[row_id for row_id, _ in rows], **summarize(rows, include_pairs=include_pairs))
        for key, rows in grouped.items()
    ]


async def owning_jobs(session, designs):
    ids = {d.job_id for d in designs}
    if not ids:
        return {}
    rows = (await session.execute(select(Job).where(Job.id.in_(ids)))).scalars().all()
    return {job.id: job for job in rows}
