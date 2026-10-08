import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    fetchPooledAssignmentManifest,
    fetchPooledAssignmentTargets,
    releasePooledAssignment,
    type Job,
    type PooledAssignmentManifest,
    type PooledAssignmentReleaseRequest,
    type PooledAssignmentReleaseResponse,
    type PooledAssignmentTarget,
    type PooledAssignmentTargetWorkflow,
} from '../../lib/api';

const POOLED_ASSIGNMENT_WORKFLOW_IDS = new Set([
    'pooled_reference_assignment',
    'ont_pooled_reference_assignment',
]);

const ALLOWED_ARTIFACT_ROOTS = [
    'bms_results/',
    'inputs/',
    'benchmarkdata/',
    'lib/',
    'rcsb/',
    'downloads/',
    'data/',
];

type ArtifactKey = 'assignment_summary' | 'per_read_assignment' | 'intended_pool_igv';

interface ArtifactLink {
    key: ArtifactKey;
    label: string;
    path: string;
    href: string;
}

export interface PooledAssignmentReviewPanelProps {
    jobId: string;
    jobStatus: Job['status'];
    mode?: string | null;
    ontWorkflowId?: unknown;
    stageOutputs?: Record<string, string[]> | null;
    files?: unknown;
    results?: unknown;
}

function isPooledAssignmentJob(mode?: string | null, ontWorkflowId?: unknown): boolean {
    const normalizedMode = typeof mode === 'string' ? mode.trim().toLowerCase() : '';
    const normalizedWorkflow = typeof ontWorkflowId === 'string' ? ontWorkflowId.trim().toLowerCase() : '';
    return POOLED_ASSIGNMENT_WORKFLOW_IDS.has(normalizedMode)
        || POOLED_ASSIGNMENT_WORKFLOW_IDS.has(normalizedWorkflow);
}

function newIdempotencyKey(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return `pooled-assignment-release-${crypto.randomUUID()}`;
    }
    return `pooled-assignment-release-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null;
}

function stringValue(value: unknown): string | null {
    return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function normalizeManifest(payload: unknown): PooledAssignmentManifest | null {
    if (!isRecord(payload)) return null;
    const nested = isRecord(payload.manifest) ? payload.manifest : payload;
    const assignmentJobId = stringValue(payload.assignment_job_id) || stringValue(nested.assignment_job_id);
    const referenceSetId = stringValue(payload.reference_set_id) || stringValue(nested.reference_set_id);
    const manifestId = stringValue(payload.manifest_id) || stringValue(nested.manifest_id);
    const manifestSha256 = stringValue(payload.manifest_sha256) || stringValue(nested.manifest_sha256);
    if (!assignmentJobId || !referenceSetId || !manifestId || !manifestSha256) return null;

    const execution = isRecord(nested.execution)
        ? { status: stringValue(nested.execution.status) }
        : null;
    return {
        schema: stringValue(payload.schema) || stringValue(nested.schema) || undefined,
        mode: stringValue(payload.mode) || stringValue(nested.mode) || undefined,
        assignment_job_id: assignmentJobId,
        reference_set_id: referenceSetId,
        manifest_id: manifestId,
        manifest_sha256: manifestSha256,
        scientific_status: payload.scientific_status === 'REVIEW' || nested.scientific_status === 'REVIEW' ? 'REVIEW' : undefined,
        execution_status: stringValue(payload.execution_status) || stringValue(nested.execution_status),
        execution,
    };
}

function normalizeTargets(payload: unknown): PooledAssignmentTarget[] {
    const rawTargets = Array.isArray(payload)
        ? payload
        : isRecord(payload) && Array.isArray(payload.targets)
            ? payload.targets
            : isRecord(payload) && Array.isArray(payload.entries)
                ? payload.entries
                : [];

    return rawTargets.flatMap((rawTarget) => {
        if (!isRecord(rawTarget)) return [];
        const targetId = stringValue(rawTarget.target_id);
        const label = stringValue(rawTarget.label) || targetId;
        const sequenceId = stringValue(rawTarget.sequence_id) || stringValue(rawTarget.molbio_sequence_id);
        const revisionId = stringValue(rawTarget.revision_id) || stringValue(rawTarget.molbio_revision_id);
        const revisionSha256 = stringValue(rawTarget.revision_sha256)
            || stringValue(rawTarget.revision_digest)
            || stringValue(rawTarget.digest);
        if (!targetId || !label || !sequenceId || !revisionId || !revisionSha256) return [];
        return [{
            target_id: targetId,
            label,
            sequence_id: sequenceId,
            revision_id: revisionId,
            revision_sha256: revisionSha256,
            revision_digest: stringValue(rawTarget.revision_digest),
            indistinguishable_group: stringValue(rawTarget.indistinguishable_group),
            selectable: typeof rawTarget.selectable === 'boolean' ? rawTarget.selectable : undefined,
            disposition: stringValue(rawTarget.disposition),
            status: stringValue(rawTarget.status),
        }];
    });
}

function collectStrings(value: unknown, output: string[], seen: Set<object> = new Set()): void {
    if (typeof value === 'string') {
        output.push(value);
        return;
    }
    if (!isRecord(value) && !Array.isArray(value)) return;
    if (seen.has(value)) return;
    seen.add(value);
    if (Array.isArray(value)) {
        value.forEach((item) => collectStrings(item, output, seen));
        return;
    }
    Object.values(value).forEach((item) => collectStrings(item, output, seen));
}

function normalizeArtifactPath(value: string): string | null {
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (trimmed.startsWith('/api/') || /^https?:\/\//i.test(trimmed)) return trimmed;

    const normalized = trimmed.split('?')[0].replace(/\\/g, '/').replace(/^\/+/, '');
    if (ALLOWED_ARTIFACT_ROOTS.some((root) => normalized.startsWith(root))) return normalized;
    for (const root of ALLOWED_ARTIFACT_ROOTS) {
        const marker = `/${root}`;
        const index = normalized.toLowerCase().lastIndexOf(marker.toLowerCase());
        if (index >= 0) return normalized.slice(index + 1);
    }
    return null;
}

function artifactHref(path: string, jobId: string): string | null {
    if (path.startsWith('/api/') || /^https?:\/\//i.test(path)) return path;
    const encoded = path.split('/').map((part) => encodeURIComponent(part)).join('/');
    return `/api/files/download/${encoded}?v=${encodeURIComponent(jobId)}`;
}

function artifactMatches(key: ArtifactKey, path: string): boolean {
    const candidate = path.split('?')[0].replace(/\\/g, '/');
    if (key === 'assignment_summary') return /(?:^|\/)assignment_summary(?:\.[^/]+)?$/i.test(candidate);
    if (key === 'per_read_assignment') return /(?:^|\/)per_read_assignment(?:\.[^/]+)?$/i.test(candidate);
    return /(?:^|\/)intended_pool(?:\.igv_session)?(?:\.[^/]+)?$/i.test(candidate);
}

function resolveArtifactLinks(jobId: string, sources: unknown[]): ArtifactLink[] {
    const values: string[] = [];
    sources.forEach((source) => collectStrings(source, values));
    const deduped = [...new Set(values)];
    const definitions: Array<{ key: ArtifactKey; label: string }> = [
        { key: 'assignment_summary', label: 'Assignment summary' },
        { key: 'per_read_assignment', label: 'Per-read assignment' },
        { key: 'intended_pool_igv', label: 'Intended-pool IGV session' },
    ];
    return definitions.flatMap(({ key, label }) => {
        const rawPath = deduped.find((value) => artifactMatches(key, value));
        const path = rawPath ? normalizeArtifactPath(rawPath) : null;
        const href = path ? artifactHref(path, jobId) : null;
        return path && href ? [{ key, label, path, href }] : [];
    });
}

function isSelectableTarget(target: PooledAssignmentTarget): boolean {
    const targetId = target.target_id.trim().toLowerCase();
    const state = `${target.disposition || ''} ${target.status || ''}`.toLowerCase();
    return target.selectable !== false
        && targetId !== 'ambiguous'
        && targetId !== 'unclassified'
        && !/(^|\s)(ambiguous|unclassified)(?:\s|$)/u.test(state);
}

function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : 'Pooled assignment release failed.';
}

export function PooledAssignmentReviewPanel({
    jobId,
    jobStatus,
    mode,
    ontWorkflowId,
    stageOutputs,
    files,
    results,
}: PooledAssignmentReviewPanelProps) {
    const queryClient = useQueryClient();
    const pooledAssignmentJob = isPooledAssignmentJob(mode, ontWorkflowId);
    const executionComplete = jobStatus === 'completed';
    const [selectedTargetIds, setSelectedTargetIds] = useState<string[]>([]);
    const [targetWorkflow, setTargetWorkflow] = useState<PooledAssignmentTargetWorkflow>('ont_plasmid_qc');
    const [namePrefix, setNamePrefix] = useState('');
    const [pinnedGpu, setPinnedGpu] = useState('');
    const [releaseResponse, setReleaseResponse] = useState<PooledAssignmentReleaseResponse | null>(null);
    const [idempotencyKey] = useState(newIdempotencyKey);

    const manifestQuery = useQuery({
        queryKey: ['pooled-assignment-manifest', jobId],
        queryFn: async () => normalizeManifest((await fetchPooledAssignmentManifest(jobId)).data),
        enabled: pooledAssignmentJob && executionComplete,
        retry: false,
    });
    const targetsQuery = useQuery({
        queryKey: ['pooled-assignment-targets', jobId],
        queryFn: async () => normalizeTargets((await fetchPooledAssignmentTargets(jobId)).data),
        enabled: pooledAssignmentJob && executionComplete,
        retry: false,
    });

    const targets = useMemo(() => targetsQuery.data || [], [targetsQuery.data]);
    const selectableTargetIds = useMemo(
        () => new Set(targets.filter(isSelectableTarget).map((target) => target.target_id)),
        [targets],
    );
    const artifactLinks = useMemo(
        () => resolveArtifactLinks(jobId, [stageOutputs, files, results]),
        [files, jobId, results, stageOutputs],
    );
    const executionStatus = manifestQuery.data?.execution_status
        || manifestQuery.data?.execution?.status
        || jobStatus;
    const scientificStatus = manifestQuery.data?.scientific_status || 'REVIEW';

    const releaseMutation = useMutation({
        mutationFn: async () => {
            const targetIds = selectedTargetIds.filter((targetId) => selectableTargetIds.has(targetId));
            if (targetIds.length === 0) {
                throw new Error('Select at least one target explicitly before release.');
            }
            const trimmedPinnedGpu = pinnedGpu.trim();
            let parsedPinnedGpu: number | undefined;
            if (trimmedPinnedGpu) {
                if (!/^\d+$/u.test(trimmedPinnedGpu)) {
                    throw new Error('Pinned GPU must be a non-negative integer.');
                }
                parsedPinnedGpu = Number.parseInt(trimmedPinnedGpu, 10);
            }
            const request: PooledAssignmentReleaseRequest = {
                idempotency_key: idempotencyKey,
                target_workflow: targetWorkflow,
                ...(namePrefix.trim() ? { name_prefix: namePrefix.trim() } : {}),
                ...(parsedPinnedGpu === undefined ? {} : { pinned_gpu: parsedPinnedGpu }),
                target_ids: targetIds,
            };
            return releasePooledAssignment(jobId, request);
        },
        onSuccess: (response) => {
            setReleaseResponse(response.data);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            queryClient.invalidateQueries({ queryKey: ['job-stages', jobId] });
        },
    });

    if (!pooledAssignmentJob) return null;

    return (
        <section
            className="w-full min-w-0 space-y-4 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4"
            data-testid="pooled-assignment-review-panel"
        >
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h4 className="text-base font-semibold text-[var(--text-primary)]">Pooled assignment review</h4>
                    <p className="mt-1 max-w-5xl text-xs leading-5 text-[var(--text-secondary)]">
                        Review first: execution can be complete while the scientific result remains REVIEW. Inspect the immutable manifest, exact target revisions, and assignment evidence before releasing any targets.
                    </p>
                </div>
                <div className="grid min-w-[17rem] grid-cols-2 gap-2 text-xs">
                    <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2" data-testid="pooled-assignment-execution-status">
                        <div className="text-[var(--text-secondary)]">Execution status</div>
                        <strong className="text-[var(--text-primary)]">{executionStatus}</strong>
                    </div>
                    <div className="rounded border border-amber-400/40 bg-amber-500/10 p-2" data-testid="pooled-assignment-scientific-status">
                        <div className="text-amber-200/80">Scientific status</div>
                        <strong className="text-amber-100">{scientificStatus}</strong>
                    </div>
                </div>
            </div>

            <p className="rounded border border-amber-400/30 bg-amber-500/10 p-3 text-xs leading-5 text-amber-100">
                Release is one explicit atomic request for all checked targets. There is no automatic release and no per-target child-job action; ambiguous and unclassified assignments are never release candidates.
            </p>

            {!executionComplete && (
                <p className="text-sm text-[var(--text-secondary)]">Immutable review targets become available after execution completes.</p>
            )}

            {executionComplete && (
                <>
                    {manifestQuery.isLoading && <p className="text-sm text-[var(--text-secondary)]">Loading immutable assignment manifest…</p>}
                    {targetsQuery.isLoading && <p className="text-sm text-[var(--text-secondary)]">Loading immutable assignment targets…</p>}
                    {manifestQuery.isError && <p role="alert" className="text-sm text-rose-400">Unable to load the immutable assignment manifest.</p>}
                    {targetsQuery.isError && <p role="alert" className="text-sm text-rose-400">Unable to load immutable assignment targets.</p>}

                    {manifestQuery.data && (
                        <div className="grid grid-cols-1 gap-3 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
                            <div>
                                <div className="text-[var(--text-secondary)]">Reference set</div>
                                <div className="break-all font-mono text-[var(--text-primary)]">{manifestQuery.data.reference_set_id}</div>
                            </div>
                            <div>
                                <div className="text-[var(--text-secondary)]">Manifest ID</div>
                                <div className="break-all font-mono text-[var(--text-primary)]">{manifestQuery.data.manifest_id}</div>
                            </div>
                            <div className="sm:col-span-2">
                                <div className="text-[var(--text-secondary)]">Immutable manifest digest</div>
                                <div className="break-all font-mono text-[var(--text-primary)]">{manifestQuery.data.manifest_sha256}</div>
                            </div>
                        </div>
                    )}

                    <div className="space-y-2">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                            <div>
                                <h5 className="text-sm font-semibold text-[var(--text-primary)]">Immutable target authority</h5>
                                <p className="mt-1 text-xs text-[var(--text-secondary)]">Check targets explicitly. The initial selection is empty.</p>
                            </div>
                            <span className="text-xs text-[var(--text-secondary)]" data-testid="pooled-assignment-selection-count">
                                {selectedTargetIds.filter((targetId) => selectableTargetIds.has(targetId)).length} selected
                            </span>
                        </div>
                        <div className="overflow-x-auto rounded border border-[var(--border-primary)]">
                            <table className="w-full min-w-[1180px] text-left text-xs">
                                <thead className="bg-[var(--bg-tertiary)] uppercase tracking-wide text-[var(--text-secondary)]">
                                    <tr>
                                        <th className="px-3 py-2">Release</th>
                                        <th className="px-3 py-2">Target ID</th>
                                        <th className="px-3 py-2">Label</th>
                                        <th className="px-3 py-2">Sequence ID</th>
                                        <th className="px-3 py-2">Exact revision ID</th>
                                        <th className="px-3 py-2">Revision digest</th>
                                        <th className="px-3 py-2">Indistinguishable group</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-[var(--border-primary)]">
                                    {targets.map((target) => {
                                        const selectable = isSelectableTarget(target);
                                        const checked = selectedTargetIds.includes(target.target_id);
                                        return (
                                            <tr key={target.target_id} data-testid={`pooled-assignment-target-${target.target_id}`}>
                                                <td className="px-3 py-2 align-top">
                                                    {selectable ? (
                                                        <label className="inline-flex items-center gap-2 text-[var(--text-primary)]">
                                                            <input
                                                                type="checkbox"
                                                                aria-label={`Explicitly select ${target.target_id}`}
                                                                checked={checked}
                                                                onChange={(event) => {
                                                                    const nextChecked = event.currentTarget.checked;
                                                                    setSelectedTargetIds((current) => (
                                                                        nextChecked
                                                                            ? [...current, target.target_id]
                                                                            : current.filter((id) => id !== target.target_id)
                                                                    ));
                                                                }}
                                                            />
                                                            <span>Select</span>
                                                        </label>
                                                    ) : (
                                                        <span className="text-[var(--text-secondary)]">Not releasable</span>
                                                    )}
                                                </td>
                                                <td className="px-3 py-2 align-top font-mono text-[var(--text-primary)]">{target.target_id}</td>
                                                <td className="px-3 py-2 align-top text-[var(--text-primary)]">{target.label}</td>
                                                <td className="px-3 py-2 align-top font-mono text-[var(--text-secondary)]">{target.sequence_id}</td>
                                                <td className="px-3 py-2 align-top font-mono text-[var(--text-secondary)]">{target.revision_id}</td>
                                                <td className="max-w-[20rem] break-all px-3 py-2 align-top font-mono text-[var(--text-secondary)]">{target.revision_sha256}</td>
                                                <td className="px-3 py-2 align-top text-[var(--text-secondary)]">{target.indistinguishable_group || '—'}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        {targetsQuery.isSuccess && targets.length === 0 && (
                            <p className="text-sm text-[var(--text-secondary)]">No immutable target rows are available for release.</p>
                        )}
                    </div>

                    {artifactLinks.length > 0 && (
                        <div className="space-y-2" data-testid="pooled-assignment-review-artifacts">
                            <h5 className="text-sm font-semibold text-[var(--text-primary)]">Assignment review artifacts</h5>
                            <div className="flex flex-wrap gap-2">
                                {artifactLinks.map((artifact) => (
                                    <a
                                        key={artifact.key}
                                        href={artifact.href}
                                        className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-xs text-sky-300 underline hover:bg-[var(--bg-tertiary)]"
                                        title={artifact.path}
                                    >
                                        {artifact.label}
                                    </a>
                                ))}
                            </div>
                        </div>
                    )}
                    {artifactLinks.length === 0 && (
                        <p className="text-xs text-[var(--text-secondary)]">No assignment summary, per-read assignment, or intended-pool IGV artifact is present in the job files/results.</p>
                    )}

                    <div className="grid grid-cols-1 gap-3 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 md:grid-cols-[minmax(15rem,1fr)_minmax(15rem,1fr)_minmax(12rem,0.7fr)_auto] md:items-end">
                        <label className="space-y-1 text-xs text-[var(--text-secondary)]">
                            Target workflow
                            <select
                                value={targetWorkflow}
                                onChange={(event) => setTargetWorkflow(event.target.value as PooledAssignmentTargetWorkflow)}
                                className="w-full rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-2 text-sm text-[var(--text-primary)]"
                            >
                                <option value="ont_plasmid_qc">ont_plasmid_qc</option>
                                <option value="ont_construct_screening">ont_construct_screening</option>
                            </select>
                        </label>
                        <label className="space-y-1 text-xs text-[var(--text-secondary)]">
                            Optional name prefix
                            <input
                                value={namePrefix}
                                onChange={(event) => setNamePrefix(event.target.value)}
                                className="w-full rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-2 text-sm text-[var(--text-primary)]"
                            />
                        </label>
                        <label className="space-y-1 text-xs text-[var(--text-secondary)]">
                            Optional pinned GPU
                            <input
                                value={pinnedGpu}
                                onChange={(event) => setPinnedGpu(event.target.value)}
                                inputMode="numeric"
                                className="w-full rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-2 text-sm text-[var(--text-primary)]"
                            />
                        </label>
                        <button
                            type="button"
                            disabled={releaseMutation.isPending || Boolean(releaseResponse) || selectedTargetIds.filter((targetId) => selectableTargetIds.has(targetId)).length === 0 || targetsQuery.isLoading || manifestQuery.isLoading}
                            onClick={() => releaseMutation.mutate()}
                            className="rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {releaseMutation.isPending ? 'Releasing selected targets…' : 'Release selected targets'}
                        </button>
                    </div>
                    {releaseMutation.isError && <p role="alert" className="text-sm text-rose-400">{errorMessage(releaseMutation.error)}</p>}
                    {releaseResponse && (
                        <div className="space-y-2 rounded border border-emerald-400/40 bg-emerald-500/10 p-3 text-sm text-emerald-100" data-testid="pooled-assignment-release-result">
                            <div>Release {releaseResponse.release_id} created for reference set {releaseResponse.reference_set_id}.</div>
                            <div className="text-xs">Assignment job: <span className="font-mono">{releaseResponse.assignment_job_id}</span></div>
                            <div className="text-xs font-semibold">Child job IDs</div>
                            <ul className="list-disc space-y-1 pl-5 text-xs font-mono">
                                {releaseResponse.child_job_ids.map((childJobId) => <li key={childJobId}>{childJobId}</li>)}
                            </ul>
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
