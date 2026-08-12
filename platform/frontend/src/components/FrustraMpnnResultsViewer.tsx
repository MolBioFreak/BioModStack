import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import type { Job } from '../lib/api.js';
import {
    fetchFrustraMpnnComparisonById,
    fetchFrustraMpnnGuidance,
    fetchFrustraMpnnLandscape,
    fetchFrustraMpnnReceipt,
    fetchFrustraMpnnResult,
    fetchFrustraMpnnStructureMap,
    listFrustraMpnnArtifacts,
    listFrustraMpnnResults,
    reanalyzeFrustraMpnn,
    selectFrustraMpnnArtifactByIdentity,
    validateFrustraMpnnOwnedSettings,
    type FrustraMpnnClassCounts,
    type FrustraMpnnClassFractions,
    type FrustraMpnnLandscapeFilters,
    type FrustraMpnnRequestedSettings,
} from '../lib/frustraMpnnApi.js';
import MolstarViewer from './MolstarViewer.js';
import {
    collectCompleteFrustraMpnnLandscape,
    createFrustraMpnnViewerMetrics,
} from './conformationalMapping/frustraMpnnViewerMetrics.js';
import {
    CANONICAL_AMINO_ACIDS,
    groupExact20Landscape,
} from './conformationalMapping/conformationalMappingSemantics.js';
import type { ResidueRef } from '../structureViewer/contracts/structureIdentity.js';
import { getFrustraMpnnResultContext } from './frustraMpnnResultSurface.js';
import FrustraMpnnLandscapeOverview from './FrustraMpnnLandscapeOverview.js';
import FrustraMpnnPlotlyAnalytics from './FrustraMpnnPlotlyAnalytics.js';
import FrustraMpnnCrossDatasetExplorer from './FrustraMpnnCrossDatasetExplorer.js';
import FrustraMpnnComparisonWorkbench from './FrustraMpnnComparisonWorkbench.js';
import FrustraMpnnCandidateHandoffPanel from './FrustraMpnnCandidateHandoffPanel.js';
import { buildFrustraMpnnCoverageReadiness } from './frustraMpnnCoverageModel.js';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from './frustrampnn/frustraMpnnSettingsState.js';
import { FrustraMpnnResultAuthoritySurface } from './FrustraMpnnResultAuthoritySurface.js';

const PAGE_SIZE = 500;
const terminalJob = new Set(['completed', 'failed', 'cancelled']);
const fmt = (value: number | null) => value == null ? '—' : Number(value).toFixed(3);
const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;
const errorMessage = (value: unknown, fallback: string): string => value instanceof Error && value.message ? value.message : fallback;


const classStyle = (value: string | null): string => {
    if (value === 'high') return 'border-red-500/30 bg-red-500/10 text-red-100';
    if (value === 'neutral') return 'border-amber-500/30 bg-amber-500/10 text-amber-100';
    if (value === 'minimal') return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-100';
    return 'border-slate-700 bg-slate-900 text-slate-500';
};

function ClassSummary({ title, counts, fractions }: { title: string; counts: FrustraMpnnClassCounts; fractions: FrustraMpnnClassFractions }) {
    return (
        <section className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
            <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-300">{title}</h3>
            <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                {([
                    ['high', 'Highly frustrated'],
                    ['neutral', 'Neutral'],
                    ['minimal', 'Minimally frustrated'],
                ] as const).map(([key, label]) => (
                    <div key={key} className={`rounded-lg border p-2 ${classStyle(key)}`}>
                        <div className="font-semibold">{label}</div>
                        <div className="mt-1 text-lg">{counts[key].toLocaleString()}</div>
                        <div className="opacity-75">{pct(fractions[key])}</div>
                    </div>
                ))}
            </div>
        </section>
    );
}

export default function FrustraMpnnResultsViewer({
    job,
    onBack,
    backLabel = 'Jobs',
    onOpenJob,
}: {
    job: Job;
    onBack: () => void;
    backLabel?: string;
    onOpenJob: (jobId: string) => void;
}) {
    const resultContext = getFrustraMpnnResultContext(job)!;
    const [searchParams] = useSearchParams();
    const requestedInvocationId = searchParams.get('frustrampnn_invocation_id');
    const requestedComparisonId = searchParams.get('frustrampnn_comparison_id');
    const requestedGuidanceId = searchParams.get('frustrampnn_guidance_id');
    const [selectedInvocation, setSelectedInvocation] = useState<string | null>(requestedInvocationId);
    const [offset, setOffset] = useState(0);
    const [chainFilter, setChainFilter] = useState('');
    const [slotStatus, setSlotStatus] = useState<'' | 'ok' | 'missing'>('');
    const [mutationFilter, setMutationFilter] = useState('');
    const [selectedResidue, setSelectedResidue] = useState<ResidueRef | null>(null);
    const [frustrampnnSettings, setFrustrampnnSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);

    const receipt = useQuery({
        queryKey: ['frustrampnn-receipt', job.id],
        queryFn: ({ signal }) => fetchFrustraMpnnReceipt(job.id, signal),
        refetchInterval: (query) => {
            const status = query.state.data?.status ?? job.status;
            return status === 'queued' || status === 'running' ? 3000 : false;
        },
        enabled: resultContext.usesChildReceipt,
    });
    const results = useQuery({
        queryKey: ['frustrampnn-results', job.id],
        queryFn: ({ signal }) => listFrustraMpnnResults(job.id, 200, 0, signal),
        refetchInterval: () => terminalJob.has(resultContext.usesChildReceipt ? (receipt.data?.status ?? job.status) : job.status) ? false : 3000,
    });
    useEffect(() => {
        const items = results.data?.items ?? [];
        setSelectedInvocation((current) => {
            if (requestedInvocationId) {
                return items.some((item) => item.invocation_id === requestedInvocationId)
                    ? requestedInvocationId
                    : null;
            }
            return current && items.some((item) => item.invocation_id === current)
                ? current
                : (items[0]?.invocation_id ?? null);
        });
    }, [job.id, requestedInvocationId, results.data?.items]);
    const persistedComparison = useQuery({
        queryKey: ['frustrampnn-comparison-id', requestedComparisonId],
        queryFn: ({ signal }) => fetchFrustraMpnnComparisonById(requestedComparisonId as string, signal),
        enabled: Boolean(requestedComparisonId),
    });
    const persistedGuidance = useQuery({
        queryKey: ['frustrampnn-guidance-id', requestedGuidanceId],
        queryFn: ({ signal }) => fetchFrustraMpnnGuidance(requestedGuidanceId as string, signal),
        enabled: Boolean(requestedGuidanceId),
    });
    useEffect(() => {
        const comparison = persistedComparison.data;
        if (!comparison) return;
        if (
            comparison.reference.parent_job_id !== job.id
            || (requestedInvocationId && comparison.reference.invocation_id !== requestedInvocationId)
        ) {
            setSelectedInvocation(null);
            return;
        }
        setSelectedInvocation(comparison.reference.invocation_id);
    }, [job.id, persistedComparison.data, requestedInvocationId]);
    useEffect(() => {
        setOffset(0);
        setSelectedResidue(null);
    }, [selectedInvocation, chainFilter, slotStatus, mutationFilter]);

    const detail = useQuery({
        queryKey: ['frustrampnn-result', job.id, selectedInvocation],
        queryFn: ({ signal }) => fetchFrustraMpnnResult(job.id, selectedInvocation!, signal),
        enabled: Boolean(selectedInvocation),
    });
    useEffect(() => {
        const persisted = detail.data?.effective_settings_json?.requested_settings;
        if (persisted) setFrustrampnnSettings(persisted);
    }, [detail.data?.invocation_id]);
    const artifacts = useQuery({
        queryKey: ['frustrampnn-artifacts', job.id, selectedInvocation],
        queryFn: ({ signal }) => listFrustraMpnnArtifacts(job.id, selectedInvocation!, signal),
        enabled: Boolean(selectedInvocation),
    });
    const structureMapArtifact = selectFrustraMpnnArtifactByIdentity(artifacts.data?.items ?? [], {
        role: 'structure_map',
        schema_name: 'frustrampnn_structure_map',
        schema_version: 1,
        media_type: 'application/json',
    });
    const structureArtifact = selectFrustraMpnnArtifactByIdentity(artifacts.data?.items ?? [], {
        role: 'normalized_input',
        schema_name: null,
        schema_version: null,
        media_type: 'chemical/x-pdb',
    });
    const identityAuthorityArtifact = selectFrustraMpnnArtifactByIdentity(artifacts.data?.items ?? [], {
        role: 'identity_authority',
        schema_name: 'producer_manifest',
        schema_version: 1,
        media_type: 'application/json',
    });
    const canonicalSucceeded = Boolean(
        detail.data
        && detail.data.status === 'succeeded'
        && detail.data.terminal_result?.status === 'succeeded'
        && detail.data.terminal_result.invocation_id === detail.data.invocation_id
        && detail.data.terminal_result.parent_job_id === job.id
        && detail.data.parent_job_id === job.id
        && detail.data.summary?.schema_name === 'frustrampnn_summary'
        && detail.data.summary.parent_job_id === job.id
        && detail.data.summary.candidate_id === detail.data.candidate_id
    );
    const canonicalAuthorityError = detail.data?.status === 'succeeded' && !canonicalSucceeded
        ? 'canonical_result_authority_conflict: terminal result, summary, invocation, candidate, or result-job scope is incomplete or inconsistent.'
        : null;
    const structureMap = useQuery({
        queryKey: ['frustrampnn-structure-map', job.id, selectedInvocation, structureMapArtifact?.artifact_id],
        queryFn: ({ signal }) => fetchFrustraMpnnStructureMap(structureMapArtifact!.download_url, signal),
        enabled: Boolean(canonicalSucceeded && structureMapArtifact),
    });
    const filters: FrustraMpnnLandscapeFilters = {
        ...(chainFilter.trim() ? { auth_asym_id: chainFilter.trim() } : {}),
        ...(slotStatus ? { status: slotStatus } : {}),
        ...(mutationFilter ? { mutation_aa: mutationFilter } : {}),
    };
    const landscape = useQuery({
        queryKey: ['frustrampnn-landscape-page', job.id, selectedInvocation, offset, filters],
        queryFn: ({ signal }) => fetchFrustraMpnnLandscape(job.id, selectedInvocation!, offset, PAGE_SIZE, filters, signal),
        enabled: Boolean(selectedInvocation && canonicalSucceeded),
    });
    const completeLandscape = useQuery({
        queryKey: ['frustrampnn-landscape-complete', job.id, selectedInvocation],
        queryFn: ({ signal }) => collectCompleteFrustraMpnnLandscape(
            (pageOffset, limit) => fetchFrustraMpnnLandscape(job.id, selectedInvocation!, pageOffset, limit, {}, signal),
        ),
        enabled: Boolean(selectedInvocation && canonicalSucceeded),
        staleTime: Infinity,
    });

    const allResidues = useMemo(() => {
        if (!completeLandscape.data) return [];
        try { return groupExact20Landscape(completeLandscape.data); } catch { return []; }
    }, [completeLandscape.data]);

    const metricResult = useMemo(() => {
        if (!canonicalSucceeded || !detail.data) return { bundle: null, error: null as string | null };
        if (!structureArtifact) return { bundle: null, error: 'normalized_structure_artifact_missing: governed normalized PDB authority is absent.' };
        if (!structureMapArtifact) return { bundle: null, error: 'structure_map_artifact_missing: governed structure-map authority is absent.' };
        if (!structureMap.data || !completeLandscape.data) return { bundle: null, error: null as string | null };
        const terminalResult = detail.data.terminal_result;
        if (!terminalResult) return { bundle: null, error: 'terminal_result_missing: canonical terminal result authority is absent.' };
        try {
            if (structureArtifact.content_sha256 !== structureMap.data.normalized_pdb_sha256) {
                throw new Error('normalized_structure_hash_conflict: normalized structure artifact SHA-256 does not match structure-map authority.');
            }
            if (detail.data.source_artifact_sha256 !== structureMap.data.source_sha256) {
                throw new Error('source_hash_conflict: result source SHA-256 does not match structure-map source authority.');
            }
            if (!terminalResult.source_artifact || terminalResult.source_artifact.sha256 !== detail.data.source_artifact_sha256) {
                throw new Error('terminal_source_hash_conflict: result and terminal source SHA-256 authorities disagree.');
            }
            const sourceIsIdentityAuthority = structureMap.data.identity_authority === 'pdb_self_identity_v1'
                || structureMap.data.identity_authority === 'mmcif_atom_site_v1';
            if (sourceIsIdentityAuthority) {
                if (structureMap.data.authority_artifact_sha256 !== detail.data.source_artifact_sha256) {
                    throw new Error('identity_authority_hash_conflict: self-authoritative source SHA-256 does not match the structure map.');
                }
            } else if (!identityAuthorityArtifact) {
                throw new Error('identity_authority_artifact_missing: external residue-identity authority is absent.');
            } else if (identityAuthorityArtifact.content_sha256 !== structureMap.data.authority_artifact_sha256) {
                throw new Error('identity_authority_hash_conflict: external residue-identity artifact SHA-256 does not match the structure map.');
            }
            if (detail.data.candidate_id !== structureMap.data.candidate_id) {
                throw new Error('candidate_identity_conflict: result candidate does not match structure-map authority.');
            }
            if (structureMap.data.parent_job_id !== job.id || structureMap.data.target_id !== detail.data.summary.target_id) {
                throw new Error('structure_map_scope_conflict: structure-map job or target authority does not match the selected result.');
            }
            return {
                bundle: createFrustraMpnnViewerMetrics({
                    requestId: job.id,
                    candidateId: detail.data.candidate_id,
                    residues: allResidues,
                    structureMap: structureMap.data,
                }),
                error: null,
            };
        } catch (error) {
            return { bundle: null, error: errorMessage(error, 'Exact FrustraMPNN residue mapping failed closed.') };
        }
    }, [allResidues, canonicalSucceeded, completeLandscape.data, detail.data, identityAuthorityArtifact, job.id, structureArtifact, structureMap.data, structureMapArtifact]);

    const authorChains = useMemo(() => Array.from(new Set(allResidues.map((residue) => residue.auth_asym_id))), [allResidues]);
    const pageResidues = useMemo(() => {
        if (!landscape.data || slotStatus || mutationFilter) return [];
        try { return groupExact20Landscape(landscape.data.rows); } catch { return []; }
    }, [landscape.data, mutationFilter, slotStatus]);
    const coverageReadiness = detail.data ? buildFrustraMpnnCoverageReadiness(
        detail.data.summary.residue_support,
        detail.data.summary.slot_support,
        detail.data.summary.missingness_by_reason,
    ) : null;
    const reanalysis = useMutation({
        mutationFn: async () => {
            if (!selectedInvocation) throw new Error('A governed FrustraMPNN invocation is required for reanalysis.');
            await validateFrustraMpnnOwnedSettings(frustrampnnSettings, {
                job_id: job.id,
                invocation_id: selectedInvocation,
            });
            return reanalyzeFrustraMpnn(job.id, frustrampnnSettings);
        },
    });
    const newChildId = reanalysis.data?.child_job_id ?? null;
    const nextReceipt = useQuery({
        queryKey: ['frustrampnn-reanalysis-receipt', newChildId],
        queryFn: ({ signal }) => fetchFrustraMpnnReceipt(newChildId!, signal),
        enabled: Boolean(newChildId),
        refetchInterval: (query) => {
            const status = query.state.data?.status;
            return status === 'queued' || status === 'running' ? 3000 : false;
        },
    });
    const state = resultContext.usesChildReceipt ? (receipt.data?.status ?? job.status) : job.status;
    const assignedGpu = detail.data?.gpu_provenance
        ?? receipt.data?.results.find((item) => item.invocation_id === selectedInvocation)?.gpu_provenance
        ?? null;
    const assignedGpuLabel = assignedGpu
        ? assignedGpu.task_visible_device_index == null
            ? `GPU ${assignedGpu.physical_device_id}`
            : `GPU ${assignedGpu.physical_device_id} · visible ${assignedGpu.task_visible_device_index}`
        : 'unassigned';
    const nextResultJobId = nextReceipt.data?.result_job_id ?? null;
    const explicitState = state === 'queued'
        ? 'queued'
        : state === 'running'
            ? 'running'
            : detail.data?.status === 'succeeded'
                ? (canonicalSucceeded ? 'succeeded' : 'unavailable')
                : detail.data?.status === 'failed'
                    ? 'failed'
                    : detail.data?.status === 'not_run'
                        ? 'not_run'
                        : state === 'failed' || state === 'cancelled'
                            ? 'failed'
                            : 'unavailable';

    return (
        <div className="min-h-screen bg-slate-950 text-slate-100">
            <header className="border-b border-slate-800 bg-slate-900/80 px-6 py-4">
                <div className="mx-auto flex max-w-[1800px] flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-4">
                        <button type="button" onClick={onBack} className="rounded-lg border border-slate-700 px-3 py-2 text-sm hover:border-slate-500">← {backLabel}</button>
                        <div>
                            <h1 className="text-lg font-semibold">FrustraMPNN Results Viewer</h1>
                            <p className="mt-1 text-xs text-slate-500">{resultContext.executionLabel} <span className="font-mono">{job.id}</span></p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <span role="status" aria-live="polite" className="rounded-full border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs">{explicitState}</span>
                        {resultContext.canReanalyzePersistedInputs && <button type="button" disabled={!selectedInvocation || state === 'queued' || state === 'running' || reanalysis.isPending} onClick={() => reanalysis.mutate()} className="rounded-lg border border-cyan-500/50 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-500/20 disabled:opacity-40">
                            {reanalysis.isPending ? 'Queueing…' : 'Reanalyze persisted inputs'}
                        </button>}
                    </div>
                </div>
            </header>
            <main className="mx-auto max-w-[1800px] space-y-4 p-6">
                {(requestedInvocationId || requestedComparisonId || requestedGuidanceId) && (
                    <aside className="rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-3 text-xs text-cyan-100" aria-label="Exact FrustraMPNN source context">
                        {requestedInvocationId && <span className="mr-3">Invocation <code>{requestedInvocationId}</code> <strong>{selectedInvocation === requestedInvocationId ? 'loaded' : 'unavailable'}</strong></span>}
                        {requestedComparisonId && <span className="mr-3">Comparison <code>{requestedComparisonId}</code> <strong>{persistedComparison.data ? 'loaded' : persistedComparison.isError ? 'unavailable' : 'loading'}</strong></span>}
                        {requestedGuidanceId && <span>Guidance <code>{requestedGuidanceId}</code> <strong>{persistedGuidance.data && (!requestedComparisonId || persistedGuidance.data.source_comparison_id === requestedComparisonId) ? 'loaded' : persistedGuidance.isError || persistedGuidance.data ? 'unavailable' : 'loading'}</strong></span>}
                    </aside>
                )}
                {((resultContext.usesChildReceipt && receipt.isError) || results.isError) && <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-100">{errorMessage((resultContext.usesChildReceipt ? receipt.error : null) || results.error, 'Persisted FrustraMPNN state is unavailable.')}</div>}
                {(detail.isError || artifacts.isError || structureMap.isError) && <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-100">{errorMessage(detail.error || artifacts.error || structureMap.error, 'Governed FrustraMPNN result authority is unavailable.')}</div>}
                {resultContext.canReanalyzePersistedInputs && reanalysis.isError && <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-100">{errorMessage(reanalysis.error, 'Reanalysis child could not be queued.')}</div>}
                {resultContext.canReanalyzePersistedInputs && nextReceipt.data && <div role="status" aria-live="polite" className="rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-3 text-sm text-cyan-100">Reanalysis child {nextReceipt.data.status}.{nextResultJobId && <button type="button" onClick={() => onOpenJob(nextResultJobId)} className="ml-2 underline">Open child results</button>}</div>}

                <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5" aria-label="Persisted execution state">
                    {[
                        ['Requested', receipt.data?.created_at ?? job.created_at],
                        ['Status', explicitState],
                        ['Runtime identity', detail.data ? shortHash(String(detail.data.runtime_identity.sif_sha256 ?? detail.data.request_sha256)) : 'pending'],
                        ['Assigned GPU', assignedGpuLabel],
                        ['Failure class', detail.data?.failure_class ?? (canonicalSucceeded ? 'none' : state === 'failed' ? 'scheduler_failure' : 'none')],
                    ].map(([label, value]) => <div key={label} className="rounded-xl border border-slate-800 bg-slate-900/60 p-3"><div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{label}</div><div className="mt-1 break-words text-sm text-slate-200">{value}</div></div>)}
                </section>
                {detail.data?.failure_class && <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100"><span className="font-semibold">Failure class persisted:</span> {detail.data.failure_class}. Unsafe runtime internals are not exposed by this response.</div>}

                {detail.data && <FrustraMpnnResultAuthoritySurface detail={detail.data} />}

                {detail.data && <section aria-label="FrustraMPNN reanalysis settings" className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                    <h2 className="font-semibold">Reanalysis settings</h2>
                    <p className="mt-1 text-xs text-slate-500">Edit a complete requested-settings document for the next governed child.</p>
                    <FrustraMpnnSettingsPanel
                        value={frustrampnnSettings}
                        onChange={setFrustrampnnSettings}
                        governedSource={selectedInvocation ? {
                            kind: 'owned',
                            reference: { job_id: job.id, invocation_id: selectedInvocation },
                        } : undefined}
                    />
                </section>}

                {results.data && results.data.items.length > 1 && (
                    <label className="block rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-300">Persisted invocation
                        <select value={selectedInvocation ?? ''} onChange={(event) => setSelectedInvocation(event.target.value)} className="ml-3 rounded border border-slate-700 bg-slate-950 px-3 py-2">
                            {results.data.items.map((item) => <option key={item.invocation_id} value={item.invocation_id}>{item.candidate_id} · {item.status}</option>)}
                        </select>
                    </label>
                )}

                {canonicalSucceeded && detail.data && (
                    <>
                        <section className="grid gap-4 xl:grid-cols-2">
                            <section className="rounded-xl border border-emerald-500/25 bg-gradient-to-br from-emerald-950/25 to-slate-900/60 p-4">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div><h2 className="font-semibold">Landscape analysis readiness</h2><p className="mt-1 text-xs text-slate-400">Can this persisted result support complete residue- and mutation-level interpretation?</p></div>
                                    <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${coverageReadiness?.status === 'Complete' ? 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200' : 'border-amber-400/40 bg-amber-400/10 text-amber-100'}`}>{coverageReadiness?.status}</span>
                                </div>
                                <div className="mt-4 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
                                    <div className="rounded-lg border border-slate-700/80 bg-slate-950/35 p-3"><div className="text-slate-400">Residues analyzed</div><div className="mt-1 text-xl font-semibold text-slate-100">{detail.data.summary.residue_support.scoreable.toLocaleString()} <span className="text-sm font-normal text-slate-500">/ {detail.data.summary.residue_support.expected.toLocaleString()}</span></div><div className="mt-1 text-emerald-300">{pct(coverageReadiness?.residueCoverage ?? 0)} coverage</div></div>
                                    <div className="rounded-lg border border-slate-700/80 bg-slate-950/35 p-3"><div className="text-slate-400">Mutation scores available</div><div className="mt-1 text-xl font-semibold text-slate-100">{detail.data.summary.slot_support.scoreable.toLocaleString()} <span className="text-sm font-normal text-slate-500">/ {detail.data.summary.slot_support.expected.toLocaleString()}</span></div><div className="mt-1 text-emerald-300">{pct(coverageReadiness?.slotCoverage ?? 0)} coverage</div></div>
                                    <div className="rounded-lg border border-slate-700/80 bg-slate-950/35 p-3"><div className="text-slate-400">Unresolved data</div><div className="mt-1 text-xl font-semibold text-slate-100">{coverageReadiness?.missingSlots.toLocaleString()} <span className="text-sm font-normal text-slate-500">missing slots</span></div><div className="mt-1 text-slate-400">{coverageReadiness?.missingResidues.toLocaleString()} residues · {coverageReadiness?.issueCount.toLocaleString()} mapping issues</div></div>
                                </div>
                                {coverageReadiness && (coverageReadiness.issueCount > 0 || coverageReadiness.missingness.length > 0) && <details className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs"><summary className="cursor-pointer font-medium text-amber-100">Review coverage diagnostics</summary><ul className="mt-2 list-disc pl-5 text-amber-200">{coverageReadiness.missingness.map(([reason, count]) => <li key={reason}>{reason.replaceAll('_', ' ')}: {count.toLocaleString()}</li>)}</ul></details>}
                            </section>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <ClassSummary title="Native-slot classes" counts={detail.data.summary.native_slot_counts} fractions={detail.data.summary.native_slot_fractions} />
                                <ClassSummary title="Full-landscape classes" counts={detail.data.summary.complete_landscape_counts} fractions={detail.data.summary.complete_landscape_fractions} />
                            </div>
                        </section>

                        <FrustraMpnnCrossDatasetExplorer currentDatasetId={job.id} />

                        {selectedInvocation && <FrustraMpnnComparisonWorkbench
                            referenceJobId={job.id}
                            referenceInvocationId={selectedInvocation}
                        />}

                        {selectedInvocation && <FrustraMpnnCandidateHandoffPanel
                            parentJobId={job.id}
                            parentInvocationId={selectedInvocation}
                            parentLandscapeSha256={detail.data.summary.landscape_sha256}
                        />}

                        {allResidues.length > 0 && <FrustraMpnnPlotlyAnalytics
                            residues={allResidues}
                            highMax={detail.data.summary.threshold_policy.high_max}
                            minimalMin={detail.data.summary.threshold_policy.minimal_min}
                            thresholdPolicyId={detail.data.summary.schema_version === 2
                                ? detail.data.summary.threshold_policy_id
                                : detail.data.summary.threshold_policy.id}
                            sourceSha256={detail.data.source_artifact_sha256}
                        />}

                        <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 p-3"><div><h2 className="font-semibold">Exact-authority structure coloring</h2><p className="mt-1 text-xs text-slate-500">Mol* colors only exact (auth_asym_id, auth_seq_id, insertion_code) identities validated against the persisted source and structure-map hashes.</p></div><div className="flex items-center gap-3"><span className="text-xs text-slate-400">{metricResult.bundle ? `${metricResult.bundle.residueProfiles.length} mapped residues` : 'coloring unavailable'}</span><a href="#frustrampnn-landscape" className="rounded-lg border border-cyan-500/50 bg-cyan-500/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 hover:bg-cyan-500/20">Open residue data ↓</a></div></div>
                            {metricResult.error && <div role="alert" className="m-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Typed mapping missingness: {metricResult.error}</div>}
                            {completeLandscape.isError && <div role="alert" className="m-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-100">{errorMessage(completeLandscape.error, 'Complete bounded landscape could not be validated.')}</div>}
                            {structureArtifact && selectedInvocation ? <MolstarViewer
                                structureUrl={structureArtifact.download_url}
                                format="pdb"
                                height={440}
                                label={detail.data.candidate_id}
                                metricLayers={metricResult.bundle?.layers}
                                activeMetricId={metricResult.bundle ? 'frustrampnn-native-index' : undefined}
                                showMetricWorkbench={Boolean(metricResult.bundle)}
                                showSequenceTrack={Boolean(metricResult.bundle)}
                                residueSelections={selectedResidue ? [selectedResidue] : []}
                            /> : <div role="status" className="p-6 text-sm text-slate-500">Normalized structure artifact unavailable.</div>}
                        </section>

                        <section id="frustrampnn-landscape" className="scroll-mt-4 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
                            <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-800 p-3">
                                <div><h2 className="font-semibold">Exact residue explorer</h2><p className="mt-1 text-xs text-slate-500">Navigate the complete mutation map, select an exact author residue, then inspect its canonical 20-substitution profile below.</p></div>
                                <div className="flex flex-wrap items-end gap-2 text-xs">
                                    <label className="text-slate-400">Author chain<select aria-label="Filter by exact author chain" value={chainFilter} onChange={(event) => { setChainFilter(event.target.value); setOffset(0); }} className="mt-1 block min-w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200"><option value="">All chains</option>{authorChains.map((chain) => <option key={chain} value={chain}>{chain}</option>)}</select></label>
                                    <label className="text-slate-400">Slot status<select aria-label="Filter by FrustraMPNN slot status" value={slotStatus} onChange={(event) => { setSlotStatus(event.target.value as typeof slotStatus); setOffset(0); }} className="mt-1 block rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200"><option value="">All statuses</option><option value="ok">Scoreable</option><option value="missing">Missing</option></select></label>
                                    <label className="text-slate-400">Mutation<select aria-label="Filter by mutation amino acid" value={mutationFilter} onChange={(event) => { setMutationFilter(event.target.value); setOffset(0); }} className="mt-1 block rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200"><option value="">All 20</option>{CANONICAL_AMINO_ACIDS.map((aa) => <option key={aa} value={aa}>{aa}</option>)}</select></label>
                                    {(chainFilter || slotStatus || mutationFilter) && <button type="button" onClick={() => { setChainFilter(''); setSlotStatus(''); setMutationFilter(''); setOffset(0); }} className="rounded border border-slate-700 px-3 py-1.5 text-slate-300 hover:border-cyan-500/60 hover:text-cyan-200">Reset</button>}
                                </div>
                            </div>
                            {allResidues.length > 0 ? <FrustraMpnnLandscapeOverview
                                residues={allResidues}
                                selectedResidue={selectedResidue}
                                onSelectResidue={(residue) => {
                                    const profile = metricResult.bundle?.residueProfiles.find((item) => item.residue.key === residue.key);
                                    setSelectedResidue(profile?.identity ?? null);
                                }}
                            /> : completeLandscape.isLoading ? <div role="status" className="border-b border-slate-800 p-4 text-sm text-slate-400">Loading the complete persisted landscape…</div> : null}
                            <aside aria-label="Planned frustration-guided mutation workflow" className="m-3 rounded-xl border border-violet-500/30 bg-violet-500/5 p-3">
                                <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="text-sm font-semibold text-violet-100">Future: frustration-guided mutation reorchestration</h3><p className="mt-1 max-w-5xl text-xs text-slate-400">Planned workflow: select exact persisted residue/mutation evidence from this map, create an explicit mutation set, and submit a new provenance-linked sample through the single scheduler for fresh structure and FrustraMPNN analysis. FrustraMPNN remains the analysis authority; it will not silently redesign or overwrite the source Design.</p></div><span className="rounded-full border border-violet-400/30 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-200">Planned · not active</span></div>
                            </aside>
                            {landscape.isError && <div role="alert" className="m-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-100">{errorMessage(landscape.error, 'Landscape page unavailable.')}</div>}
                            {pageResidues.length > 0 ? (
                                <section aria-label="Exact 20-substitution residue profiles" className="border-t border-slate-800">
                                    <div className="flex flex-wrap items-center justify-between gap-2 bg-slate-950/35 px-3 py-2"><div><h3 className="text-sm font-medium text-slate-200">Exact 20-substitution profiles</h3><p className="text-[11px] text-slate-500">Scores are persisted authority. A cyan row marks the residue currently synchronized with the structure.</p></div>{selectedResidue && <span className="rounded border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-1 font-mono text-[11px] text-cyan-200">Selected {selectedResidue.authAsymId}:{selectedResidue.authSeqId}{selectedResidue.insertionCode}</span>}</div>
                                    <div className="max-h-[650px] overflow-auto"><table className="min-w-[1100px] text-left text-[10px]"><thead className="sticky top-0 z-10 bg-slate-950 text-slate-400 shadow-sm"><tr><th className="sticky left-0 z-20 min-w-36 bg-slate-950 p-2">Author residue</th>{CANONICAL_AMINO_ACIDS.map((aa) => <th key={aa} className="p-2 text-center font-mono text-xs">{aa}</th>)}</tr></thead><tbody>{pageResidues.map((residue, rowIndex) => { const isSelected = selectedResidue?.authAsymId === residue.auth_asym_id && String(selectedResidue.authSeqId) === residue.auth_seq_id && (selectedResidue.insertionCode ?? '') === residue.insertion_code; return <tr key={residue.key} className={`border-t border-slate-800/80 ${isSelected ? 'bg-cyan-500/8' : rowIndex % 2 === 0 ? 'bg-slate-950/20' : ''}`}><th className={`sticky left-0 p-2 ${isSelected ? 'bg-cyan-950/90' : 'bg-slate-900'}`}><button type="button" aria-label={`Select exact author residue ${residue.auth_asym_id} ${residue.auth_seq_id}${residue.insertion_code}, wild type ${residue.wt}`} className={`w-full rounded px-2 py-1.5 text-left hover:bg-cyan-500/10 hover:text-cyan-200 ${isSelected ? 'font-semibold text-cyan-200' : 'text-slate-300'}`} onClick={() => { const profile = metricResult.bundle?.residueProfiles.find((item) => item.residue.key === residue.key); setSelectedResidue(profile?.identity ?? null); }}><span className="font-mono">{residue.auth_asym_id}:{residue.auth_seq_id}{residue.insertion_code}</span><span className="ml-2 text-slate-500">WT {residue.wt}</span></button></th>{residue.slots.map((slot) => <td key={slot.mutation_aa} className="p-1"><div title={`${residue.wt}→${slot.mutation_aa} · ${slot.status}${slot.reason ? ` · ${slot.reason}` : ''}`} className={`relative rounded border px-1 py-2 text-center font-mono ${classStyle(slot.class)} ${slot.mutation_aa === residue.wt ? 'ring-1 ring-inset ring-white/70' : ''}`}><span className="text-[11px]">{fmt(slot.score)}</span>{slot.mutation_aa === residue.wt && <span className="absolute right-0.5 top-0 text-[7px] text-white">WT</span>}</div></td>)}</tr>; })}</tbody></table></div>
                                </section>
                            ) : (
                                <div className="max-h-[650px] overflow-auto"><table className="w-full min-w-[900px] text-left text-xs"><thead className="sticky top-0 bg-slate-900 text-slate-400"><tr><th className="p-2">Exact author residue</th><th className="p-2">WT→mutation</th><th className="p-2">Score</th><th className="p-2">Canonical class</th><th className="p-2">Support / reason</th></tr></thead><tbody>{landscape.data?.rows.map((row) => <tr key={`${row.entity_instance_id}:${row.sequence_index}:${row.mutation_aa}`} className="border-t border-slate-800"><td className="p-2">{row.auth_asym_id}:{row.auth_seq_id}{row.insertion_code}</td><td className="p-2">{row.wt}→{row.mutation_aa}</td><td className="p-2">{fmt(row.score)}</td><td className="p-2">{row.class ?? 'unavailable'}</td><td className="p-2">{row.status}{row.reason ? ` · ${row.reason}` : ''}</td></tr>)}</tbody></table></div>
                            )}
                            <div className="flex items-center justify-between border-t border-slate-800 p-3 text-xs"><span>{landscape.data ? (landscape.data.rows.length > 0 ? `${offset + 1}–${offset + landscape.data.rows.length} of ${landscape.data.total}` : `0 of ${landscape.data.total}`) : 'loading'} persisted slots</span><div className="flex gap-2"><button type="button" disabled={offset === 0 || landscape.isFetching} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="rounded border border-slate-700 px-3 py-1.5 disabled:opacity-30">Previous</button><button type="button" disabled={landscape.data?.next_offset == null || landscape.isFetching} onClick={() => setOffset(landscape.data!.next_offset!)} className="rounded border border-slate-700 px-3 py-1.5 disabled:opacity-30">Next</button></div></div>
                        </section>

                        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><h2 className="font-semibold">Governed artifacts</h2><p className="mt-1 text-xs text-slate-500">Authenticated content-addressed downloads. Runtime filesystem paths and storage topology are not exposed.</p><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{artifacts.data?.items.map((artifact) => <a key={artifact.artifact_id} href={artifact.download_url} className="rounded-lg border border-slate-800 p-3 text-xs hover:border-cyan-500 focus:border-cyan-400"><div className="font-medium text-slate-200">{artifact.role.replaceAll('_', ' ')}</div><div className="mt-1 text-slate-500">{artifact.media_type} · {artifact.size_bytes.toLocaleString()} bytes</div><div className="mt-1 font-mono text-[10px] text-slate-600" title={artifact.content_sha256}>{shortHash(artifact.content_sha256)}</div></a>)}</div></section>
                    </>
                )}
                {detail.data && !canonicalSucceeded && <div role={canonicalAuthorityError ? 'alert' : 'status'} className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">{detail.data.status === 'failed' ? 'Failed: the selected persisted invocation has no canonical result matrix.' : detail.data.status === 'not_run' ? 'Not run: the selected invocation was explicitly skipped and has no canonical result matrix.' : `Typed result missingness: ${canonicalAuthorityError ?? 'canonical_result_unavailable'}`}</div>}
                {!detail.data && (state === 'queued' || state === 'running') && <div role="status" aria-live="polite" className="rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-5 text-sm text-cyan-100">{state === 'queued' ? 'Queued: waiting for scheduler admission.' : 'Running: the persisted child has not published terminal result authority yet.'}</div>}
                {!detail.data && terminalJob.has(state) && !results.isLoading && <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">{state === 'failed' || state === 'cancelled' ? 'Failed: no canonical result was persisted.' : 'Unavailable: the child is terminal but no canonical FrustraMPNN result exists.'}</div>}
            </main>
        </div>
    );
}
