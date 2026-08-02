import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { Job } from '../lib/api.js';
import {
    fetchFrustraMpnnLandscape,
    fetchFrustraMpnnReceipt,
    fetchFrustraMpnnResult,
    fetchFrustraMpnnStructureMap,
    frustraMpnnArtifactUrl,
    listFrustraMpnnArtifacts,
    listFrustraMpnnResults,
    reanalyzeFrustraMpnn,
    type FrustraMpnnClassCounts,
    type FrustraMpnnClassFractions,
    type FrustraMpnnLandscapeFilters,
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

const PAGE_SIZE = 500;
const terminalJob = new Set(['completed', 'failed', 'cancelled']);
const fmt = (value: number | null) => value == null ? '—' : Number(value).toFixed(3);
const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;
const errorMessage = (value: unknown, fallback: string): string => value instanceof Error && value.message ? value.message : fallback;
const json = (value: unknown) => JSON.stringify(value, null, 2);

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
    const [selectedInvocation, setSelectedInvocation] = useState<string | null>(null);
    const [offset, setOffset] = useState(0);
    const [chainFilter, setChainFilter] = useState('');
    const [slotStatus, setSlotStatus] = useState<'' | 'ok' | 'missing'>('');
    const [mutationFilter, setMutationFilter] = useState('');
    const [selectedResidue, setSelectedResidue] = useState<ResidueRef | null>(null);

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
        setSelectedInvocation((current) => (
            current && items.some((item) => item.invocation_id === current)
                ? current
                : items[0]?.invocation_id ?? null
        ));
    }, [job.id, results.data?.items]);
    useEffect(() => {
        setOffset(0);
        setSelectedResidue(null);
    }, [selectedInvocation, chainFilter, slotStatus, mutationFilter]);

    const detail = useQuery({
        queryKey: ['frustrampnn-result', job.id, selectedInvocation],
        queryFn: ({ signal }) => fetchFrustraMpnnResult(job.id, selectedInvocation!, signal),
        enabled: Boolean(selectedInvocation),
    });
    const artifacts = useQuery({
        queryKey: ['frustrampnn-artifacts', job.id, selectedInvocation],
        queryFn: ({ signal }) => listFrustraMpnnArtifacts(job.id, selectedInvocation!, signal),
        enabled: Boolean(selectedInvocation),
    });
    const structureMapArtifact = artifacts.data?.items.find((item) => item.role === 'structure_map');
    const structureArtifact = artifacts.data?.items.find((item) => item.role === 'normalized_input');
    const identityAuthorityArtifact = artifacts.data?.items.find((item) => item.role === 'identity_authority');
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
        queryFn: ({ signal }) => fetchFrustraMpnnStructureMap(job.id, structureMapArtifact!.artifact_id, signal),
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
            if (terminalResult.source_artifact.sha256 !== detail.data.source_artifact_sha256) {
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

    const pageResidues = useMemo(() => {
        if (!landscape.data || slotStatus || mutationFilter) return [];
        try { return groupExact20Landscape(landscape.data.rows); } catch { return []; }
    }, [landscape.data, mutationFilter, slotStatus]);
    const reanalysis = useMutation({ mutationFn: () => reanalyzeFrustraMpnn(job.id) });
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
    const assignedGpu = detail.data?.assigned_gpu;
    const assignedGpuLabel = assignedGpu
        ? assignedGpu.physical_device_id == null
            ? assignedGpu.task_visible_device_index == null ? 'unassigned' : `visible GPU ${assignedGpu.task_visible_device_index}`
            : `GPU ${assignedGpu.physical_device_id}`
        : receipt.data?.assigned_gpu == null ? 'unassigned' : `GPU ${receipt.data.assigned_gpu}`;
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
                        {resultContext.canReanalyzePersistedInputs && <button type="button" disabled={state === 'queued' || state === 'running' || reanalysis.isPending} onClick={() => reanalysis.mutate()} className="rounded-lg border border-cyan-500/50 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-500/20 disabled:opacity-40">
                            {reanalysis.isPending ? 'Queueing…' : 'Reanalyze persisted inputs'}
                        </button>}
                    </div>
                </div>
            </header>
            <main className="mx-auto max-w-[1800px] space-y-4 p-6">
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
                {(detail.data?.diagnostic || receipt.data?.error_message) && <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100"><span className="font-semibold">Diagnostic:</span> {detail.data?.diagnostic || receipt.data?.error_message}</div>}

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
                            <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                                <h2 className="font-semibold">Support and missingness</h2>
                                <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
                                    {Object.entries(detail.data.summary.residue_support).map(([key, value]) => <div key={key} className="rounded-lg border border-slate-800 p-2"><div className="text-slate-500">{key}</div><div className="mt-1 text-lg">{value}</div></div>)}
                                </div>
                                <div className="mt-3 text-xs text-slate-400">Slots: {detail.data.summary.slot_support.scoreable.toLocaleString()} scoreable / {detail.data.summary.slot_support.observed.toLocaleString()} observed / {detail.data.summary.slot_support.expected.toLocaleString()} expected</div>
                                {Object.keys(detail.data.summary.missingness_by_reason).length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-amber-200">{Object.entries(detail.data.summary.missingness_by_reason).map(([reason, count]) => <li key={reason}>{reason}: {count}</li>)}</ul>}
                            </section>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <ClassSummary title="Native-slot classes" counts={detail.data.summary.native_slot_counts} fractions={detail.data.summary.native_slot_fractions} />
                                <ClassSummary title="Full-landscape classes" counts={detail.data.summary.complete_landscape_counts} fractions={detail.data.summary.complete_landscape_fractions} />
                            </div>
                        </section>

                        <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 p-3"><div><h2 className="font-semibold">Exact-authority structure coloring</h2><p className="mt-1 text-xs text-slate-500">Mol* colors only exact (auth_asym_id, auth_seq_id, insertion_code) identities validated against the persisted source and structure-map hashes.</p></div><div className="flex items-center gap-3"><span className="text-xs text-slate-400">{metricResult.bundle ? `${metricResult.bundle.residueProfiles.length} mapped residues` : 'coloring unavailable'}</span><a href="#frustrampnn-landscape" className="rounded-lg border border-cyan-500/50 bg-cyan-500/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 hover:bg-cyan-500/20">Open residue data ↓</a></div></div>
                            {metricResult.error && <div role="alert" className="m-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Typed mapping missingness: {metricResult.error}</div>}
                            {completeLandscape.isError && <div role="alert" className="m-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-100">{errorMessage(completeLandscape.error, 'Complete bounded landscape could not be validated.')}</div>}
                            {structureArtifact && selectedInvocation ? <MolstarViewer
                                structureUrl={frustraMpnnArtifactUrl(job.id, structureArtifact.artifact_id)}
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
                                <div><h2 className="font-semibold">Persisted exact residue landscape</h2><p className="mt-1 text-xs text-slate-500">Complete all-residue overview with a bounded exact-slot drill-down; legacy summary rows are never expanded into N×20 data.</p></div>
                                <div className="flex flex-wrap gap-2 text-xs">
                                    <label>Author chain <input aria-label="Filter by exact author chain" value={chainFilter} onChange={(event) => setChainFilter(event.target.value)} className="ml-1 w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1.5" /></label>
                                    <label>Slot status <select aria-label="Filter by FrustraMPNN slot status" value={slotStatus} onChange={(event) => setSlotStatus(event.target.value as typeof slotStatus)} className="ml-1 rounded border border-slate-700 bg-slate-950 px-2 py-1.5"><option value="">all</option><option value="ok">scoreable</option><option value="missing">missing</option></select></label>
                                    <label>Mutation <select aria-label="Filter by mutation amino acid" value={mutationFilter} onChange={(event) => setMutationFilter(event.target.value)} className="ml-1 rounded border border-slate-700 bg-slate-950 px-2 py-1.5"><option value="">all 20</option>{CANONICAL_AMINO_ACIDS.map((aa) => <option key={aa} value={aa}>{aa}</option>)}</select></label>
                                </div>
                            </div>
                            {allResidues.length > 0 ? <FrustraMpnnLandscapeOverview
                                residues={allResidues}
                                onSelectResidue={(residue) => {
                                    const profile = metricResult.bundle?.residueProfiles.find((item) => item.residue.key === residue.key);
                                    setSelectedResidue(profile?.identity ?? null);
                                }}
                            /> : completeLandscape.isLoading ? <div role="status" className="border-b border-slate-800 p-4 text-sm text-slate-400">Loading the complete persisted landscape…</div> : null}
                            {landscape.isError && <div role="alert" className="m-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-100">{errorMessage(landscape.error, 'Landscape page unavailable.')}</div>}
                            {pageResidues.length > 0 ? (
                                <div className="max-h-[650px] overflow-auto"><table className="min-w-[1100px] text-left text-[10px]"><thead className="sticky top-0 z-10 bg-slate-900 text-slate-400"><tr><th className="sticky left-0 z-20 bg-slate-900 p-2">Exact author residue</th>{CANONICAL_AMINO_ACIDS.map((aa) => <th key={aa} className="p-2 text-center">{aa}</th>)}</tr></thead><tbody>{pageResidues.map((residue) => <tr key={residue.key} className="border-t border-slate-800"><th className="sticky left-0 bg-slate-900 p-2"><button type="button" aria-label={`Select exact author residue ${residue.auth_asym_id} ${residue.auth_seq_id}${residue.insertion_code}, wild type ${residue.wt}`} className="text-left hover:text-cyan-300" onClick={() => { const profile = metricResult.bundle?.residueProfiles.find((item) => item.residue.key === residue.key); setSelectedResidue(profile?.identity ?? null); }}>{residue.auth_asym_id}:{residue.auth_seq_id}{residue.insertion_code} · WT {residue.wt}</button></th>{residue.slots.map((slot) => <td key={slot.mutation_aa} className="p-1"><div title={`${slot.status}${slot.reason ? ` · ${slot.reason}` : ''}`} className={`rounded border p-1.5 text-center ${classStyle(slot.class)}`}><div>{slot.mutation_aa}{slot.mutation_aa === residue.wt ? '*' : ''}</div><div>{fmt(slot.score)}</div></div></td>)}</tr>)}</tbody></table></div>
                            ) : (
                                <div className="max-h-[650px] overflow-auto"><table className="w-full min-w-[900px] text-left text-xs"><thead className="sticky top-0 bg-slate-900 text-slate-400"><tr><th className="p-2">Exact author residue</th><th className="p-2">WT→mutation</th><th className="p-2">Score</th><th className="p-2">Canonical class</th><th className="p-2">Support / reason</th></tr></thead><tbody>{landscape.data?.rows.map((row) => <tr key={`${row.entity_instance_id}:${row.sequence_index}:${row.mutation_aa}`} className="border-t border-slate-800"><td className="p-2">{row.auth_asym_id}:{row.auth_seq_id}{row.insertion_code}</td><td className="p-2">{row.wt}→{row.mutation_aa}</td><td className="p-2">{fmt(row.score)}</td><td className="p-2">{row.class ?? 'unavailable'}</td><td className="p-2">{row.status}{row.reason ? ` · ${row.reason}` : ''}</td></tr>)}</tbody></table></div>
                            )}
                            <div className="flex items-center justify-between border-t border-slate-800 p-3 text-xs"><span>{landscape.data ? (landscape.data.rows.length > 0 ? `${offset + 1}–${offset + landscape.data.rows.length} of ${landscape.data.total}` : `0 of ${landscape.data.total}`) : 'loading'} persisted slots</span><div className="flex gap-2"><button type="button" disabled={offset === 0 || landscape.isFetching} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="rounded border border-slate-700 px-3 py-1.5 disabled:opacity-30">Previous</button><button type="button" disabled={landscape.data?.next_offset == null || landscape.isFetching} onClick={() => setOffset(landscape.data!.next_offset!)} className="rounded border border-slate-700 px-3 py-1.5 disabled:opacity-30">Next</button></div></div>
                        </section>

                        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><h2 className="font-semibold">Governed artifacts</h2><p className="mt-1 text-xs text-slate-500">Authenticated content-addressed download routes; runtime filesystem paths are not exposed.</p><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{artifacts.data?.items.map((artifact) => <a key={artifact.artifact_id} href={frustraMpnnArtifactUrl(job.id, artifact.artifact_id)} className="rounded-lg border border-slate-800 p-3 text-xs hover:border-cyan-500 focus:border-cyan-400"><div className="font-medium text-slate-200">{artifact.relative_path}</div><div className="mt-1 text-slate-500">{artifact.role} · {artifact.size_bytes.toLocaleString()} bytes</div><div className="mt-1 font-mono text-[10px] text-slate-600" title={artifact.content_sha256}>{shortHash(artifact.content_sha256)}</div></a>)}</div></section>

                        <details className="rounded-xl border border-slate-800 bg-slate-900/60 p-3"><summary className="cursor-pointer text-sm font-medium">Persisted runtime and lineage identity</summary><pre className="mt-3 overflow-auto text-[10px] text-slate-400">{json({ runtime_identity: detail.data.runtime_identity, assigned_gpu: detail.data.assigned_gpu, lineage: receipt.data?.lineage })}</pre></details>
                    </>
                )}
                {detail.data && !canonicalSucceeded && <div role={canonicalAuthorityError ? 'alert' : 'status'} className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">{detail.data.status === 'failed' ? 'Failed: the selected persisted invocation has no canonical result matrix.' : detail.data.status === 'not_run' ? 'Not run: the selected invocation was explicitly skipped and has no canonical result matrix.' : `Typed result missingness: ${canonicalAuthorityError ?? 'canonical_result_unavailable'}`}</div>}
                {!detail.data && (state === 'queued' || state === 'running') && <div role="status" aria-live="polite" className="rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-5 text-sm text-cyan-100">{state === 'queued' ? 'Queued: waiting for scheduler admission.' : 'Running: the persisted child has not published terminal result authority yet.'}</div>}
                {!detail.data && terminalJob.has(state) && !results.isLoading && <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">{state === 'failed' || state === 'cancelled' ? 'Failed: no canonical result was persisted.' : 'Unavailable: the child is terminal but no canonical FrustraMPNN result exists.'}</div>}
            </main>
        </div>
    );
}
