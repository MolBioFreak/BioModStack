import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';

import { StructureWorkbench } from '../../structureViewer/StructureWorkbench';
import type { Job } from '../../lib/api';
import FrustraMpnnWorkbench from '../frustrampnn/FrustraMpnnWorkbench';
import type { ResidueRef } from '../../structureViewer/contracts/structureIdentity';

import {
    cancelCmRequest,
    cmApiError,
    cmArtifactUrl,
    getCmFailureReceipts,

    getCmLogs,
    getCmProgress,
    getCmRecordPage,
    getCmResults,
    getCmStateLandscapeAnalysis,
    getCmStateLandscapeAnalysisRows,
    getCmStatus,
    retryCmRequest,
    type CmStateLandscapeAnalysisRowsPage,
    type CmStateLandscapeRow,
    type CmSubmitReceipt,
} from './conformationalMappingApi';
import {
    APPROVED_CM_CONTRACTS,
    candidateLabel,
    candidateStructureArtifact,
    candidateStructureMap,
    canonicalAnalysis,
    canonicalEnsemble,
    CM_SCIENTIFIC_LIMIT,
    formatCoordinate,

    recordsByType,
    requireApprovedCmResults,
    type CmAnalysisResult,
    type CmStructureMapRow,
    validateCanonicalAnalysisRows,
    validateStructureMapRows,
} from './conformationalMappingSemantics';
import { canonicalStateLandscapeAnalysis } from './stateLandscapeSemantics';
import { StateLandscapeStatusAlert, StateLandscapeWorkspacePanel, type StateLandscapeMetricName } from './StateLandscapeWorkspacePanel';
import {
    initialStateLandscapeWorkspaceState,
    clearStateLandscapeResidueSelectionForCandidate,
    resolveStateLandscapeResidueRef,
    selectStateLandscapeWorkspacePair,
    stateLandscapeSummaryEnabled,
    stateLandscapeWorkspaceEnabled,
    stateLandscapeRowKey,
    stateLandscapeWorkspaceTabs,
    validateStateLandscapeWorkspaceRowsPage,
    validateStateLandscapeWorkspaceSummary,
    type StateLandscapeWorkspaceTab,
} from './stateLandscapeWorkspace';
import { ProjectAttachmentDialog } from '../project-manager/ProjectAttachmentDialog';

interface Props {
    requestId: string;
    title?: string;
    job?: Job;
    services?: {
        getStatus?: typeof getCmStatus;
        getProgress?: typeof getCmProgress;
        getFailureReceipts?: typeof getCmFailureReceipts;
        getResults?: typeof getCmResults;
        getLogs?: typeof getCmLogs;

        artifactUrl?: typeof cmArtifactUrl;
        cancelRequest?: typeof cancelCmRequest;
        retryRequest?: typeof retryCmRequest;
    };
    Workbench?: typeof StructureWorkbench;
    FrustraWorkbench?: typeof FrustraMpnnWorkbench;
}
type DetailTab = StateLandscapeWorkspaceTab;
type LifecycleTab = 'progress' | 'logs' | 'failures';

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const pct = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';
const scalar = (value: unknown): string => value == null ? '—' : typeof value === 'number' && Number.isFinite(value) ? value.toFixed(5).replace(/0+$/, '').replace(/\.$/, '') : String(value);
const shortHash = (value: unknown): string => typeof value === 'string' && value.length > 20 ? `${value.slice(0, 16)}…${value.slice(-8)}` : String(value ?? '—');
const json = (value: unknown) => <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-400">{JSON.stringify(value, null, 2)}</pre>;
const tabClass = (active: boolean) => `rounded-lg px-3 py-2 text-xs font-medium ${active ? 'bg-orange-500 text-slate-950' : 'border border-slate-700 text-slate-300 hover:border-slate-500'}`;


const analysisIdentity = (row: CmAnalysisResult): string => {
    const identity = row.identity;
    return `${String(identity.target_id)} · ${String(identity.auth_asym_id)}:${String(identity.auth_seq_id)}${String(identity.insertion_code || '')} · ${String(identity.validated_wt)}→${String(identity.substitution)}`;
};

export function ConformationalMappingViewer({
    requestId,
    title = 'Conformational Mapping',
    job,
    services,
    Workbench = StructureWorkbench,
    FrustraWorkbench = FrustraMpnnWorkbench,
}: Props) {
    const navigate = useNavigate();
    const location = useLocation();
    const queryClient = useQueryClient();
    const [selectedCandidateId, setSelectedCandidateId] = useState('');
    const [addToProjectOpen, setAddToProjectOpen] = useState(false);
    const [overlayIds, setOverlayIds] = useState<string[]>([]);
    const [detailTab, setDetailTab] = useState<DetailTab>('ensemble');
    const [lifecycleTab, setLifecycleTab] = useState<LifecycleTab>('progress');


    const [mappingFilter, setMappingFilter] = useState<'all' | 'mapped' | 'issues'>('all');
    const [expandedAnalysis, setExpandedAnalysis] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [selectedPairId, setSelectedPairId] = useState('');
    const [selectedStateRowKey, setSelectedStateRowKey] = useState<string | null>(null);
    const [stateAnalysisOffset, setStateAnalysisOffset] = useState(0);
    const [selectedStateMetric, setSelectedStateMetric] = useState<StateLandscapeMetricName>('native_score');
    const [stateInspectorMinimized, setStateInspectorMinimized] = useState(false);
    const [stateAnalysisRows, setStateAnalysisRows] = useState<CmStateLandscapeAnalysisRowsPage | null>(null);
    const [stateAnalysisResidueSelections, setStateAnalysisResidueSelections] = useState<ResidueRef[]>([]);
    const [pendingStateResidue, setPendingStateResidue] = useState<{ candidateId: string; row: CmStateLandscapeRow } | null>(null);
    const [stateResidueSelectionReason, setStateResidueSelectionReason] = useState<string | null>(null);
    const [analysisRows, setAnalysisRows] = useState<CmAnalysisResult[]>([]);
    const [analysisNextOffset, setAnalysisNextOffset] = useState<number | null>(null);
    const [analysisPageRequested, setAnalysisPageRequested] = useState(false);
    const [structureMapRows, setStructureMapRows] = useState<CmStructureMapRow[]>([]);
    const [structureMapNextOffset, setStructureMapNextOffset] = useState<number | null>(null);

    const [isViewerFullscreen, setIsViewerFullscreen] = useState(false);
    const viewerShellRef = useRef<HTMLElement | null>(null);

    useEffect(() => {
        const syncFullscreen = () => {
            setIsViewerFullscreen(document.fullscreenElement === viewerShellRef.current);
            window.setTimeout(() => window.dispatchEvent(new Event('resize')), 0);
        };
        document.addEventListener('fullscreenchange', syncFullscreen);
        return () => document.removeEventListener('fullscreenchange', syncFullscreen);
    }, []);

    const toggleViewerFullscreen = useCallback(async () => {
        const shell = viewerShellRef.current;
        if (!shell) return;
        if (document.fullscreenElement === shell) {
            await document.exitFullscreen();
            return;
        }
        if (document.fullscreenElement) await document.exitFullscreen();
        await shell.requestFullscreen();
    }, []);

    const locationReceipt = (location.state as { cmSubmissionReceipt?: CmSubmitReceipt } | null)?.cmSubmissionReceipt;
    const receipt = locationReceipt?.request_id === requestId ? locationReceipt : null;
    const status = useQuery({
        queryKey: ['cm-status', requestId], queryFn: () => (services?.getStatus || getCmStatus)(requestId),
        refetchInterval: (query) => TERMINAL.has(query.state.data?.status || '') ? false : 2000,
        retry: false,
    });
    const progress = useQuery({
        queryKey: ['cm-progress', requestId], queryFn: () => (services?.getProgress || getCmProgress)(requestId),
        enabled: Boolean(status.data),
        refetchInterval: TERMINAL.has(status.data?.status || '') ? false : 2000,
        retry: false,
    });
    const logs = useQuery({
        queryKey: ['cm-logs', status.data?.job_id], queryFn: () => (services?.getLogs || getCmLogs)(status.data!.job_id),
        enabled: Boolean(status.data?.job_id), refetchInterval: TERMINAL.has(status.data?.status || '') ? false : 4000,
        retry: false,
    });
    const failures = useQuery({
        queryKey: ['cm-failure-receipts', requestId], queryFn: () => (services?.getFailureReceipts || getCmFailureReceipts)(requestId),
        enabled: Boolean(status.data), retry: false,
    });
    const refetchFailureReceipts = failures.refetch;
    const previousStatus = useRef<string | null>(null);
    useEffect(() => {
        const nextStatus = status.data?.status;
        if (!nextStatus) return;
        const priorStatus = previousStatus.current;
        previousStatus.current = nextStatus;
        if (priorStatus && priorStatus !== nextStatus && TERMINAL.has(nextStatus)) {
            // A terminal transition is the authority boundary for durable
            // receipts. Refresh the receipt store even when the status object
            // itself does not carry a receipt.
            void refetchFailureReceipts();
        }
    }, [refetchFailureReceipts, status.data?.status]);
    const results = useQuery({
        queryKey: ['cm-results', requestId], queryFn: () => (services?.getResults || getCmResults)(requestId),
        enabled: status.data?.status === 'completed' && APPROVED_CM_CONTRACTS.has(status.data.result_contract_id), retry: false,
    });

    const parsed = useMemo(() => {
        if (!results.data) return { data: null, error: null as string | null };
        try {
            const value = requireApprovedCmResults(results.data);
            const ensemble = canonicalEnsemble(value);
            const candidates = ensemble.candidates;
            const analysis = canonicalAnalysis(value);
            candidates.forEach((candidate) => {
                candidateStructureArtifact(candidate, value.artifacts);
                candidateStructureMap(value, candidate.candidate_id);
            });
            return { data: { value, ensemble, candidates, analysis }, error: null };
        } catch (value) {
            return { data: null, error: value instanceof Error ? value.message : 'Canonical response validation failed.' };
        }
    }, [results.data]);

    const analysisRecord = parsed.data?.value.records.find((record) => record.type === 'analysis') || null;
    useEffect(() => {
        setAnalysisRows(parsed.data?.analysis.results || []);
        setAnalysisNextOffset(analysisRecord?.pages?.results?.next_offset ?? null);
        setAnalysisPageRequested(false);
    }, [analysisRecord, parsed.data]);
    const analysisPage = useQuery({
        queryKey: ['cm-analysis-results-page', requestId, analysisRecord?.key, analysisNextOffset],
        queryFn: () => getCmRecordPage(requestId, 'analysis', analysisRecord!.key, 'results', analysisNextOffset!, 100),
        enabled: detailTab === 'analysis' && analysisPageRequested && Boolean(analysisRecord && analysisNextOffset != null),
        retry: false,
    });
    useEffect(() => {
        if (!analysisPage.data) return;
        try {
            const rows = validateCanonicalAnalysisRows(analysisPage.data.rows);
            setAnalysisRows((current) => [...current, ...rows]);
            setAnalysisNextOffset(analysisPage.data.next_offset);
            setAnalysisPageRequested(false);
        } catch {
            setAnalysisPageRequested(false);
            setAnalysisNextOffset(null);
        }
    }, [analysisPage.data]);

    const stateLandscapeAuthority = useMemo(() => {
        if (!parsed.data) return { data: null, error: null as string | null };
        if (!parsed.data.value.records.some((record) => record.type === 'state_landscape_analysis')) return { data: null, error: null as string | null };
        try { return { data: canonicalStateLandscapeAnalysis(parsed.data.value), error: null as string | null }; }
        catch (value) { return { data: null, error: value instanceof Error ? value.message : 'Canonical state-analysis authority is malformed.' }; }
    }, [parsed.data]);
    const stateAnalysisSummary = useQuery({
        queryKey: ['cm-state-landscape-analysis', requestId],
        queryFn: () => getCmStateLandscapeAnalysis(requestId),
        enabled: stateLandscapeSummaryEnabled(stateLandscapeAuthority.data),
        retry: false,
    });
    const stateAnalysisSummaryParsed = useMemo(() => {
        if (!stateAnalysisSummary.data || stateLandscapeAuthority.error) return { data: null, error: stateLandscapeAuthority.error };
        try { return { data: validateStateLandscapeWorkspaceSummary(stateAnalysisSummary.data, stateLandscapeAuthority.data), error: null as string | null }; }
        catch (value) { return { data: null, error: value instanceof Error ? value.message : 'State-analysis projection is malformed.' }; }
    }, [stateAnalysisSummary.data, stateLandscapeAuthority.data, stateLandscapeAuthority.error]);
    const stateAnalysisSummaryError = stateAnalysisSummaryParsed.error
        || (stateAnalysisSummary.isError ? cmApiError(stateAnalysisSummary.error, 'State-analysis summary is unavailable.') : null);
    const stateAnalysisPage = useQuery({
        queryKey: ['cm-state-landscape-analysis-rows', requestId, stateAnalysisSummaryParsed.data?.analysis_id, selectedPairId, stateAnalysisOffset],
        queryFn: () => getCmStateLandscapeAnalysisRows(requestId, stateAnalysisSummaryParsed.data!.analysis_id, selectedPairId, stateAnalysisOffset, 50),
        enabled: stateLandscapeWorkspaceEnabled(stateAnalysisSummaryParsed.data) && Boolean(selectedPairId),
        retry: false,
    });
    const stateAnalysisPageParsed = useMemo(() => {
        if (!stateAnalysisPage.data || !stateAnalysisSummaryParsed.data || !selectedPairId) return { data: null, error: null as string | null };
        try { return { data: validateStateLandscapeWorkspaceRowsPage(stateAnalysisPage.data, stateAnalysisSummaryParsed.data, selectedPairId, stateAnalysisOffset), error: null as string | null }; }
        catch (value) { return { data: null, error: value instanceof Error ? value.message : 'State-analysis rows are malformed.' }; }
    }, [selectedPairId, stateAnalysisOffset, stateAnalysisPage.data, stateAnalysisSummaryParsed.data]);

    const selected = parsed.data?.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId)
        || parsed.data?.candidates[0] || null;
    const selectedStructureMapRecord = selected && parsed.data
        ? parsed.data.value.records.find((record) => record.type === 'structure_map' && record.key === selected.candidate_id) || null
        : null;
    const selectedStructureMapBase = selected && parsed.data ? candidateStructureMap(parsed.data.value, selected.candidate_id) : null;
    useEffect(() => {
        setStructureMapRows(selectedStructureMapBase?.rows || []);
        setStructureMapNextOffset(selectedStructureMapRecord?.pages?.rows?.next_offset ?? null);
    }, [selected?.candidate_id, selectedStructureMapBase, selectedStructureMapRecord]);
    const structureMapPage = useQuery({
        queryKey: ['cm-structure-map-page', requestId, selectedStructureMapRecord?.key, structureMapNextOffset],
        queryFn: () => getCmRecordPage(
            requestId,
            'structure_map',
            selectedStructureMapRecord!.key,
            'rows',
            structureMapNextOffset!,
            100,
        ),
        enabled: Boolean(
            selectedStructureMapRecord
            && structureMapNextOffset != null
            && (detailTab === 'mapping' || pendingStateResidue?.candidateId === selected?.candidate_id),
        ),
        retry: false,
    });
    useEffect(() => {
        if (!structureMapPage.data) return;
        try {
            const rows = validateStructureMapRows(structureMapPage.data.rows);
            setStructureMapRows((current) => [...current, ...rows]);
            setStructureMapNextOffset(structureMapPage.data.next_offset);
        } catch {
            setStructureMapNextOffset(null);
        }
    }, [structureMapPage.data]);
    const structureMap = selectedStructureMapBase
        ? { ...selectedStructureMapBase, rows: structureMapRows.length ? structureMapRows : selectedStructureMapBase.rows }
        : null;
    const clearStateAnalysisResidueSelection = (candidateId: string) => {
        const reset = clearStateLandscapeResidueSelectionForCandidate(candidateId);
        setStateAnalysisResidueSelections(reset.residueSelections);
        setPendingStateResidue(null);
        setStateResidueSelectionReason(reset.residueSelectionReason);
        return reset.selectedCandidateId;
    };
    const selectCandidateForStateAnalysis = (candidateId: string) => {
        setSelectedCandidateId(clearStateAnalysisResidueSelection(candidateId));
    };
    useEffect(() => {
        if (selected && selectedCandidateId !== selected.candidate_id) setSelectedCandidateId(clearStateAnalysisResidueSelection(selected.candidate_id));
    }, [selected, selectedCandidateId]);
    useEffect(() => {
        setOverlayIds((current) => current.filter((id) => id !== selected?.candidate_id));
    }, [selected?.candidate_id]);
    useEffect(() => {
        if (!parsed.data || !selected) return;
        setOverlayIds((current) => {
            const retained = current.filter((id) => id !== selected.candidate_id && parsed.data!.candidates.some((candidate) => candidate.candidate_id === id));
            if (retained.length) return retained;
            const firstAlternative = parsed.data!.candidates.find((candidate) => candidate.candidate_id !== selected.candidate_id);
            return firstAlternative ? [firstAlternative.candidate_id] : retained;
        });
    }, [parsed.data, selected]);
    useEffect(() => {
        setSelectedPairId('');
        setSelectedStateRowKey(null);
        setStateAnalysisOffset(0);
        setStateAnalysisRows(null);
        setStateAnalysisResidueSelections([]);
        setPendingStateResidue(null);
        setStateResidueSelectionReason(null);
    }, [requestId]);
    useEffect(() => {
        if (!stateAnalysisSummaryParsed.data) return;
        const initial = initialStateLandscapeWorkspaceState(stateAnalysisSummaryParsed.data);
        setSelectedPairId(initial.selectedPairId);
        setSelectedStateRowKey(initial.selectedStateRowKey);
        setStateAnalysisOffset(initial.pageOffset);
        setStateAnalysisRows(null);
        setStateAnalysisResidueSelections([]);
        setPendingStateResidue(null);
        setStateResidueSelectionReason(null);
    }, [stateAnalysisSummaryParsed.data]);
    useEffect(() => {
        const page = stateAnalysisPageParsed.data;
        if (!page) return;
        setStateAnalysisRows((current) => {
            if (page.offset === 0 || !current || current.selected_analysis_id !== page.selected_analysis_id || current.applied_filters.pair_id !== page.applied_filters.pair_id) return page;
            return { ...page, rows: [...current.rows, ...page.rows] };
        });
    }, [stateAnalysisPageParsed.data]);

    const selectedArtifact = selected && parsed.data ? candidateStructureArtifact(selected, parsed.data.value.artifacts) : null;
    useEffect(() => {
        if (!pendingStateResidue || selected?.candidate_id !== pendingStateResidue.candidateId) return;
        const residue = resolveStateLandscapeResidueRef(pendingStateResidue.row.identity, structureMap);
        if (!residue) {
            setStateAnalysisResidueSelections([]);
            setStateResidueSelectionReason('Exact author/entity/sequence identity is not mapped in this candidate structure.');
            return;
        }
        setStateAnalysisResidueSelections([residue]);
        setStateResidueSelectionReason(null);
    }, [pendingStateResidue, selected?.candidate_id, structureMap]);
    useEffect(() => {
        if (pendingStateResidue && selected?.candidate_id !== pendingStateResidue.candidateId) clearStateAnalysisResidueSelection(selected?.candidate_id || '');
    }, [pendingStateResidue, selected]);
    const overlays = useMemo(() => {
        if (!parsed.data || !selected) return [];
        return overlayIds.filter((id) => id !== selected.candidate_id).map((id) => {
            const candidate = parsed.data!.candidates.find((item) => item.candidate_id === id);
            if (!candidate) throw new Error('Overlay candidate is absent from API candidate order');
            const artifact = candidateStructureArtifact(candidate, parsed.data!.value.artifacts);
            return { id, structureUrl: (services?.artifactUrl || cmArtifactUrl)(requestId, artifact.artifact_id), format: 'cif' as const, label: candidateLabel(candidate) };
        });
    }, [overlayIds, parsed.data, requestId, selected, services?.artifactUrl]);


    const lifecycle = useMutation({
        mutationFn: async (action: 'cancel' | 'retry') => action === 'cancel'
            ? (services?.cancelRequest || cancelCmRequest)(requestId)
            : (services?.retryRequest || retryCmRequest)(requestId),
        onSuccess: async () => {
            setActionError(null);
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['cm-status', requestId] }),
                queryClient.invalidateQueries({ queryKey: ['cm-progress', requestId] }),
                queryClient.invalidateQueries({ queryKey: ['cm-failure-receipts', requestId] }),
            ]);
        },
        onError: (value) => setActionError(cmApiError(value, 'Lifecycle action failed.')),
    });

    const statusLabel = status.data?.status || (status.isLoading ? 'loading' : 'unavailable');
    const statusContractError = status.data && !APPROVED_CM_CONTRACTS.has(status.data.result_contract_id)
        ? 'Typed status returned an unknown result contract. Canonical rendering is disabled.'
        : null;
    const completedCoordinates = progress.data?.progress.completed_coordinates ?? status.data?.progress.completed_coordinates;
    const expectedCoordinates = progress.data?.progress.expected_coordinates ?? status.data?.progress.expected_coordinates;
    const numericProgress = typeof completedCoordinates === 'number' && typeof expectedCoordinates === 'number' && expectedCoordinates > 0
        ? Math.max(0, Math.min(100, completedCoordinates / expectedCoordinates * 100)) : null;
    const currentFailureReceipts = [
        ...(status.data?.failure_receipt ? [{ receipt_id: 'current', sha256: '', payload: status.data.failure_receipt }] : []),
        ...(failures.data || []),
    ];
    const supportRecords = parsed.data ? recordsByType(parsed.data.value, 'support') : [];
    const missingnessRecords = parsed.data ? recordsByType(parsed.data.value, 'missingness') : [];
    const supportRecord = supportRecords[0] || null;
    const missingnessRecord = missingnessRecords[0] || null;
    const evidenceSupportPage = useQuery({
        queryKey: ['cm-evidence-support-page', requestId, supportRecord?.key],
        queryFn: () => getCmRecordPage(requestId, 'support', supportRecord!.key, 'records', 0, 100),
        enabled: detailTab === 'evidence' && Boolean(supportRecord),
        retry: false,
    });
    const evidenceAnalysisSupportPage = useQuery({
        queryKey: ['cm-evidence-analysis-support-page', requestId, analysisRecord?.key],
        queryFn: () => getCmRecordPage(requestId, 'analysis', analysisRecord!.key, 'support_records', 0, 100),
        enabled: detailTab === 'evidence' && Boolean(analysisRecord),
        retry: false,
    });
    const evidenceAnalysisClashPage = useQuery({
        queryKey: ['cm-evidence-analysis-clash-page', requestId, analysisRecord?.key],
        queryFn: () => getCmRecordPage(requestId, 'analysis', analysisRecord!.key, 'clash_records', 0, 100),
        enabled: detailTab === 'evidence' && Boolean(analysisRecord),
        retry: false,
    });
    const evidenceMissingnessPage = useQuery({
        queryKey: ['cm-evidence-missingness-page', requestId, missingnessRecord?.key],
        queryFn: () => getCmRecordPage(requestId, 'missingness', missingnessRecord!.key, 'result_records', 0, 100),
        enabled: detailTab === 'evidence' && Boolean(missingnessRecord),
        retry: false,
    });
    const filteredMapRows = structureMap?.rows.filter((row) => mappingFilter === 'all' || (mappingFilter === 'mapped' ? row.status === 'mapped' : row.status !== 'mapped')) || [];
    const projectAdapterId = status.data?.backend === 'confornets'
        ? 'bms.cm.confornets.adapter.v1'
        : 'bms.cm.protenix_v2.adapter.v1';
    const frustraMpnnJob: Job = {
        ...(job ?? {
            id: requestId,
            name: title,
            status: statusLabel === 'failed' || statusLabel === 'cancelled' || statusLabel === 'running' || statusLabel === 'queued'
                ? statusLabel
                : 'completed',
            model_id: 'conformational_mapping',
            mode: 'analysis',
            params: { run_frustrampnn: true },
            created_at: '',
            design_count: 0,
            output_dir: null,
        }),
        id: status.data?.job_id || job?.id || requestId,
    };

    return (
        <div className="min-h-screen bg-slate-950 p-3 text-slate-200 sm:p-4 lg:p-6" data-bms-cm-viewer="canonical">
            <div className="mx-auto max-w-[1800px] space-y-4">
                <header className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4 sm:p-5">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                        <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-orange-300">Canonical ensemble lens</p><h1 className="mt-1 text-2xl font-semibold text-white">{title}</h1><p className="mt-1 break-all font-mono text-xs text-slate-500">{requestId}</p></div>
                        <div className="flex flex-wrap items-center gap-2"><button type="button" onClick={() => navigate('/designs')} className="rounded-lg border border-slate-700 px-3 py-2 text-xs hover:border-slate-500">All results</button><button type="button" onClick={() => setAddToProjectOpen(true)} className="rounded-lg border border-orange-500/40 px-3 py-2 text-xs text-orange-200 hover:border-orange-400">Add to Project / Experiment</button><button type="button" onClick={() => navigate('/submit')} className="rounded-lg border border-slate-700 px-3 py-2 text-xs hover:border-slate-500">New request</button><span className={`rounded-full border px-3 py-1.5 text-xs font-medium ${statusLabel === 'completed' ? 'border-emerald-500/40 text-emerald-200' : statusLabel === 'failed' ? 'border-red-500/40 text-red-200' : 'border-slate-700 text-slate-300'}`}>{statusLabel}</span>{['prepared', 'queued', 'running'].includes(statusLabel) && <button type="button" aria-label="Cancel request" disabled={lifecycle.isPending} onClick={() => { if (window.confirm('Cancel this conformational-mapping request?')) lifecycle.mutate('cancel'); }} className="rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-200 disabled:opacity-40">Cancel request</button>}{status.data?.retry_eligible && <button type="button" aria-label="Retry request" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate('retry')} className="rounded-lg border border-blue-500/40 px-3 py-2 text-xs text-blue-200 disabled:opacity-40">Retry request</button>}</div>
                    </div>
                    <p className="mt-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-xs leading-5 text-sky-100">{CM_SCIENTIFIC_LIMIT}</p>
                    {receipt && <div className="mt-3 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3"><div className="text-xs font-semibold text-emerald-200">Authenticated submission receipt</div><div className="mt-2 grid gap-2 text-[11px] sm:grid-cols-2 lg:grid-cols-4"><div>Backend: <span className="font-mono text-slate-300">{receipt.backend}</span></div><div>Cardinality: <span className="text-slate-300">{receipt.expected_cardinality}</span></div><div title={receipt.request_sha256}>Request: <span className="font-mono text-slate-300">{shortHash(receipt.request_sha256)}</span></div><div title={receipt.coordinate_plan_sha256}>Coordinate plan: <span className="font-mono text-slate-300">{shortHash(receipt.coordinate_plan_sha256)}</span></div></div></div>}
                    {(status.isError || actionError || statusContractError) && <div role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{actionError || statusContractError || cmApiError(status.error, 'Unable to open this authenticated typed request.')}</div>}
                </header>

                <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4" aria-labelledby="cm-lifecycle-heading">
                    <div className="flex flex-wrap items-center justify-between gap-3"><h2 id="cm-lifecycle-heading" className="font-semibold text-white">Lifecycle, progress, and diagnostics</h2><div className="flex flex-wrap gap-2">{(['progress', 'logs', 'failures'] as LifecycleTab[]).map((tab) => <button type="button" key={tab} aria-pressed={lifecycleTab === tab} onClick={() => setLifecycleTab(tab)} className={tabClass(lifecycleTab === tab)}>{tab === 'failures' ? `Failure receipts (${currentFailureReceipts.length})` : tab[0].toUpperCase() + tab.slice(1)}</button>)}</div></div>
                    {numericProgress != null && <div className="mt-4"><div className="mb-1 flex justify-between text-xs text-slate-400"><span>{String(progress.data?.progress.phase || status.data?.progress.phase || 'processing')}</span><span>{String(completedCoordinates)}/{String(expectedCoordinates)} coordinates</span></div><div className="h-2 overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-orange-500 transition-all" style={{ width: `${numericProgress}%` }} /></div></div>}
                    <div className="mt-4">
                        {lifecycleTab === 'progress' && <div className="grid gap-3 lg:grid-cols-2"><div><div className="mb-1 text-xs font-medium text-slate-400">Typed request progress</div>{json(progress.data?.progress || status.data?.progress || {})}</div><div><div className="mb-1 text-xs font-medium text-slate-400">Scheduler projection</div>{json({ job_status: status.data?.job_status, job_stage: progress.data?.job_stage, job_progress: progress.data?.job_progress, retry_eligible: status.data?.retry_eligible })}</div></div>}
                        {lifecycleTab === 'logs' && (logs.data ? <div className="grid gap-3 xl:grid-cols-3">{([['Command output', logs.data.command_log], ['Command errors', logs.data.command_err], ['Nextflow log', logs.data.nextflow_log]] as const).map(([label, value]) => <div key={label}><div className="mb-1 text-xs font-medium text-slate-400">{label}</div><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-3 font-mono text-[11px] text-slate-400">{value || 'No log content recorded.'}</pre></div>)}</div> : <p className="text-sm text-slate-500">{logs.isError ? cmApiError(logs.error, 'Job diagnostics are unavailable.') : 'Loading durable job diagnostics…'}</p>)}
                        {lifecycleTab === 'failures' && (failures.isError ? <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200"><div className="font-medium">Unable to retrieve failure receipts</div><div className="mt-1 text-xs">{cmApiError(failures.error, 'The durable receipt store could not be read.')}</div></div> : currentFailureReceipts.length ? <div className="space-y-3">{currentFailureReceipts.map((item, index) => <details key={`${item.receipt_id}:${index}`} className="rounded-lg border border-red-500/20 bg-red-500/5 p-3"><summary className="cursor-pointer text-xs font-medium text-red-200">{item.receipt_id}{item.sha256 ? ` · ${shortHash(item.sha256)}` : ''}</summary><div className="mt-2">{json(item.payload)}</div></details>)}</div> : <p className="text-sm text-slate-500">No failure receipt is recorded for this request.</p>)}
                    </div>
                </section>

                {status.data?.status === 'completed' && !statusContractError && results.isLoading && <div className="rounded-xl border border-slate-800 p-5 text-sm text-slate-400">Loading and validating canonical Phase 11 records…</div>}
                {(results.isError || parsed.error) && <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-red-200"><div className="font-semibold">Canonical result validation failed closed</div><p className="mt-1 text-sm">{parsed.error || cmApiError(results.error, 'Results could not be loaded through the approved contract.')}</p></div>}
                {!statusContractError && parsed.data && parsed.data.candidates.length === 0 && <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-amber-100"><div className="font-semibold">No canonical structural candidates were published</div><p className="mt-1 text-sm">This completed request has no governed candidate structure to display or overlay.</p></div>}

                {!statusContractError && parsed.data && selected && selectedArtifact && <>
                    <section className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
                        <aside className="max-h-[760px] overflow-auto rounded-2xl border border-slate-800 bg-slate-900/70 p-3" aria-label="Canonical structural hypotheses in API order"><div className="sticky top-0 z-10 mb-2 bg-slate-900 pb-2"><h2 className="text-sm font-semibold text-white">Structural hypotheses in API order</h2><p className="mt-1 text-[11px] text-slate-500">Choose the primary coordinate set, then compare immutable alternative candidate coordinates as overlays. These are predicted hypotheses, not time-resolved sampling or state populations.</p></div>{parsed.data.candidates.map((candidate, index) => <div key={candidate.candidate_id} className={`mb-2 rounded-lg border p-2 ${candidate.candidate_id === selected.candidate_id ? 'border-orange-400/60 bg-orange-500/10' : 'border-slate-800'}`}><button type="button" onClick={() => selectCandidateForStateAnalysis(candidate.candidate_id)} className="w-full text-left"><div className="text-xs font-medium text-white">{candidate.candidate_id === selected.candidate_id ? `Primary hypothesis · Candidate ${index + 1}` : `Candidate ${index + 1}`}</div><div className="mt-1 text-[11px] leading-4 text-slate-400">{candidateLabel(candidate)}</div><div className="mt-1 truncate font-mono text-[10px] text-slate-600">{candidate.candidate_id}</div></button><label className="mt-2 flex items-center gap-2 text-[11px] text-slate-400"><input type="checkbox" checked={overlayIds.includes(candidate.candidate_id)} disabled={candidate.candidate_id === selected.candidate_id || (!overlayIds.includes(candidate.candidate_id) && overlayIds.length >= 5)} onChange={(event) => setOverlayIds((current) => event.target.checked ? [...current, candidate.candidate_id] : current.filter((id) => id !== candidate.candidate_id))} />Compare as structural overlay</label></div>)}</aside>
                        <div className="space-y-3">
                            <section
                                ref={viewerShellRef}
                                data-cm-viewer-fullscreen={isViewerFullscreen ? 'true' : 'false'}
                                className={`overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70 ${isViewerFullscreen ? 'flex h-full min-h-0 flex-col rounded-none border-0 bg-slate-950 p-4 md:p-6' : 'min-h-[720px]'}`}
                            >
                                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 p-3">
                                    <div>
                                        <span className="text-sm font-medium text-white">Primary structural hypothesis</span>
                                        <span className="ml-2 text-xs text-slate-500">{overlays.length ? `${overlays.length} immutable alternative coordinate overlay${overlays.length === 1 ? '' : 's'}` : 'no alternative selected'}</span>
                                        <p className="mt-1 text-[11px] text-slate-500">Overlay visibility compares registered candidate coordinates only; it does not estimate conformer populations, kinetics, thermodynamics, or trajectories.</p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <button type="button" onClick={() => void toggleViewerFullscreen()} className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:border-orange-400" title={isViewerFullscreen ? 'Exit fullscreen' : 'Open fullscreen'}>{isViewerFullscreen ? 'Exit fullscreen' : 'Fullscreen'}</button>
                                    </div>
                                </div>
                                <div className={`min-h-0 ${isViewerFullscreen ? 'flex-1' : 'h-[680px]'}`}>
                                    <Workbench
                                        mode="standard"
                                        structureUrl={(services?.artifactUrl || cmArtifactUrl)(requestId, selectedArtifact.artifact_id)}
                                        format="cif"
                                        height="100%"
                                        label={candidateLabel(selected)}
                                        overlayStructures={overlays}
                                        showMetricWorkbench={false}
                                        showSequenceTrack={false}
                                        showComplexWorkbench={false}
                                        showM6Workbench={false}
                                        showMeasurements={false}
                                        residueSelections={stateAnalysisResidueSelections}
                                    />
                                </div>
                            </section>
                            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 text-xs"><div className="font-medium text-white">Backend coordinates</div><p className="mt-1 break-words text-slate-400">{formatCoordinate(selected.backend_coordinates)}</p></div>
                        </div>
                    </section>

                    <nav className="flex flex-wrap gap-2 rounded-2xl border border-slate-800 bg-slate-900/70 p-3" aria-label="Conformational mapping result lenses">{stateLandscapeWorkspaceTabs(stateLandscapeWorkspaceEnabled(stateAnalysisSummaryParsed.data)).map((tab) => <button type="button" key={tab} aria-pressed={detailTab === tab} onClick={() => setDetailTab(tab)} className={tabClass(detailTab === tab)}>{tab === 'mapping' ? 'Residue mapping' : tab === 'landscape' ? 'Exact-20 landscape' : tab === 'state-analysis' ? 'State analysis' : tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav>
                    {stateAnalysisSummaryError && <StateLandscapeStatusAlert error={stateAnalysisSummaryError} />}

                    {detailTab === 'ensemble' && <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4"><h2 className="font-semibold text-white">Canonical ensemble and provenance</h2><div className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4"><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Backend</span><div className="mt-1 font-mono text-white">{parsed.data.ensemble.backend}</div></div><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Runtime identity</span><div className="mt-1 break-words text-white">{parsed.data.ensemble.runtime_identity}</div></div><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Expected candidates</span><div className="mt-1 text-white">{parsed.data.ensemble.expected_cardinality}</div></div><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Terminal contract state</span><div className="mt-1 text-white">{parsed.data.ensemble.terminal_status}</div></div>{[['Request SHA-256', parsed.data.ensemble.request_sha256], ['Snapshot SHA-256', parsed.data.ensemble.source_snapshot_sha256], ['Feature policy SHA-256', parsed.data.ensemble.feature_policy_sha256], ['Native manifest SHA-256', parsed.data.ensemble.native_manifest_sha256], ['Container digest', parsed.data.ensemble.container_digest], ['Checkpoint SHA-256', parsed.data.ensemble.checkpoint_sha256]].map(([label, value]) => <div key={label} className="rounded-lg border border-slate-800 p-3" title={value}><span className="text-slate-500">{label}</span><div className="mt-1 font-mono text-white">{shortHash(value)}</div></div>)}</div><div className="mt-4 grid gap-3 lg:grid-cols-2"><div className="rounded-xl border border-amber-500/20 p-3"><h3 className="text-sm font-medium text-amber-100">Producer warnings</h3>{parsed.data.ensemble.warnings.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-200">{parsed.data.ensemble.warnings.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="mt-2 text-xs text-slate-500">No producer warning recorded.</p>}</div><div className="rounded-xl border border-slate-800 p-3"><h3 className="text-sm font-medium text-white">Explicit omissions</h3>{parsed.data.ensemble.omissions.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-400">{parsed.data.ensemble.omissions.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="mt-2 text-xs text-slate-500">No omission recorded.</p>}</div></div></section>}

                    {detailTab === 'state-analysis' && stateAnalysisSummaryParsed.data && <StateLandscapeWorkspacePanel
                        summary={stateAnalysisSummaryParsed.data}
                        page={stateAnalysisRows}
                        selectedPairId={selectedPairId}
                        selectedStateRowKey={selectedStateRowKey}
                        selectedMetric={selectedStateMetric}
                        inspectorMinimized={stateInspectorMinimized}
                        loading={stateAnalysisPage.isLoading}
                        error={stateAnalysisPageParsed.error || (stateAnalysisPage.isError ? cmApiError(stateAnalysisPage.error, 'Bounded state-analysis rows are unavailable.') : null)}
                        residueSelectionReason={stateResidueSelectionReason}
                        onSelectPair={(pairId) => {
                            const next = selectStateLandscapeWorkspacePair({ selectedPairId, selectedStateRowKey, pageOffset: stateAnalysisOffset }, pairId);
                            setSelectedPairId(next.selectedPairId);
                            setSelectedStateRowKey(next.selectedStateRowKey);
                            setStateAnalysisOffset(next.pageOffset);
                            setStateAnalysisRows(null);
                            setStateAnalysisResidueSelections([]);
                            setPendingStateResidue(null);
                            setStateResidueSelectionReason(null);
                        }}
                        onSelectRow={(row) => {
                            setSelectedStateRowKey(stateLandscapeRowKey(row));
                            clearStateAnalysisResidueSelection(row.candidate_a_id);
                            setPendingStateResidue({ candidateId: row.candidate_a_id, row });
                            setSelectedCandidateId(row.candidate_a_id);
                        }}
                        onInspectCandidate={selectCandidateForStateAnalysis}
                        onSelectMetric={setSelectedStateMetric}
                        onToggleInspector={() => setStateInspectorMinimized((current) => !current)}
                        onLoadMore={() => { if (stateAnalysisRows?.next_offset != null) setStateAnalysisOffset(stateAnalysisRows.next_offset); }}
                    />}

                    {detailTab === 'mapping' && structureMap && <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 p-4"><div><h2 className="font-semibold text-white">Structure-map identity and residue mapping</h2><p className="mt-1 text-xs text-slate-500">{structureMap.source_format} · source model {structureMap.selected_source_model} · {structureMap.normalizer_version} · {structureMap.altloc_policy}</p></div><select value={mappingFilter} onChange={(event) => setMappingFilter(event.target.value as typeof mappingFilter)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs"><option value="all">All rows</option><option value="mapped">Mapped</option><option value="issues">Issues only</option></select></div><div className="grid gap-2 border-b border-slate-800 p-3 text-[11px] sm:grid-cols-3"><div>Original CIF: <span className="font-mono">{shortHash(structureMap.original_cif_sha256)}</span></div><div>Source: <span className="font-mono">{shortHash(structureMap.source_sha256)}</span></div><div>Normalized PDB: <span className="font-mono">{shortHash(structureMap.normalized_pdb_sha256)}</span></div></div><div className="max-h-[560px] overflow-auto"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-slate-900 text-slate-400"><tr><th className="p-2">Sequence</th><th className="p-2">Source identity</th><th className="p-2">Author identity</th><th className="p-2">Normalized PDB</th><th className="p-2">Backbone</th><th className="p-2">Status / reason</th></tr></thead><tbody>{filteredMapRows.map((row) => <tr key={`${row.entity_instance_id}:${row.sequence_index}`} className="border-t border-slate-800 align-top"><td className="p-2">{row.sequence_index} · {row.residue_name}</td><td className="p-2">{row.source_entity_id} · {row.label_asym_id}:{row.label_seq_id}</td><td className="p-2">{row.auth_asym_id}:{row.auth_seq_id}{row.insertion_code}</td><td className="p-2">{row.pdb_chain_id}:{row.pdb_residue_id}{row.pdb_insertion_code}</td><td className="p-2 font-mono text-[10px]">{Object.entries(row.backbone_atoms).map(([atom, value]) => `${atom}:${value || 'missing'}`).join(' ')}</td><td className="p-2"><span className={row.status === 'mapped' ? 'text-emerald-300' : 'text-amber-200'}>{row.status}</span>{row.reason && <div className="mt-1 text-slate-500">{row.reason}</div>}</td></tr>)}</tbody></table></div>{!filteredMapRows.length && <p className="p-4 text-sm text-slate-500">No mapping rows match this filter.</p>}</section>}

                    {detailTab === 'landscape' && (
                        <FrustraWorkbench
                            job={frustraMpnnJob}
                            preferredInvocationId={selected ? `frustrampnn:${frustraMpnnJob.id}:${selected.candidate_id}` : undefined}
                            onBack={() => setDetailTab('ensemble')}
                            backLabel="CM ensemble"
                            onOpenJob={(jobId) => navigate(`/results/${jobId}`)}
                        />
                    )}

                    {detailTab === 'analysis' && <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70"><div className="border-b border-slate-800 p-4"><h2 className="font-semibold text-white">Canonical analysis ranking</h2><p className="mt-1 text-xs text-slate-500">Server-persisted ranking order. Each row retains its reconstructable components, sort keys, support, and robustness status.</p></div><div className="grid gap-2 border-b border-slate-800 p-3 text-[11px] sm:grid-cols-3"><div>Analysis: <span className="font-mono">{parsed.data.analysis.analysis_id}</span></div><div>Formula: <span className="font-mono">{parsed.data.analysis.formula_version}</span></div><div>Expected strata: {parsed.data.analysis.expected_strata.length}</div></div><div className="max-h-[680px] overflow-auto"><table className="w-full min-w-[1100px] text-left text-xs"><thead className="sticky top-0 bg-slate-900 text-slate-400"><tr><th className="p-2">Rank / identity</th><th className="p-2">Robustness</th><th className="p-2">Valid support</th><th className="p-2">Outer</th><th className="p-2">Coordinate</th><th className="p-2">Hierarchical mean</th><th className="p-2">Hotspot</th><th className="p-2">Switch</th><th className="p-2">Components</th></tr></thead><tbody>{analysisRows.map((row, index) => <><tr key={row.source_row_key} className="border-t border-slate-800 align-top"><td className="p-2"><div className="font-medium text-white">{index + 1}. {analysisIdentity(row)}</div><div className="mt-1 max-w-64 truncate font-mono text-[10px] text-slate-600">{row.source_row_key}</div>{row.failure_reason && <div className="mt-1 text-red-300">{row.failure_reason}</div>}</td><td className={`p-2 ${row.status === 'robust' ? 'text-emerald-300' : row.status === 'conditional' ? 'text-amber-200' : 'text-red-200'}`}>{row.status}</td><td className="p-2">{row.valid_coordinate_count}/{row.expected_coordinate_count}</td><td className="p-2">{pct(row.outer_support_fraction)}</td><td className="p-2">{pct(row.coordinate_support_fraction)}</td><td className="p-2 font-mono">{scalar(row.hierarchical_mean)}</td><td className="p-2 font-mono">{scalar(row.hotspot_score)}</td><td className="p-2 font-mono">{scalar(row.switch_score)}</td><td className="p-2"><button type="button" onClick={() => setExpandedAnalysis((current) => current === row.source_row_key ? null : row.source_row_key)} className="rounded border border-slate-700 px-2 py-1 text-[10px]">{expandedAnalysis === row.source_row_key ? 'Hide' : 'Inspect'}</button></td></tr>{expandedAnalysis === row.source_row_key && <tr key={`${row.source_row_key}:detail`} className="border-t border-slate-800 bg-slate-950/40"><td colSpan={9} className="p-3"><div className="grid gap-3 lg:grid-cols-3"><div><div className="mb-1 text-[11px] text-slate-500">Persisted components</div>{json(row.components)}</div><div><div className="mb-1 text-[11px] text-slate-500">Persisted sort keys</div>{json(row.sort_keys)}</div><div><div className="mb-1 text-[11px] text-slate-500">Identity</div>{json(row.identity)}</div></div></td></tr>}</>)}</tbody></table></div><div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800 p-3 text-xs text-slate-400"><span>Showing {analysisRows.length.toLocaleString()} of {(analysisRecord?.pages?.results?.total_count ?? analysisRows.length).toLocaleString()} ranking rows. Dense analysis data remains artifact-backed.</span>{analysisNextOffset != null && <button type="button" disabled={analysisPageRequested || analysisPage.isFetching} onClick={() => setAnalysisPageRequested(true)} className="rounded border border-slate-700 px-3 py-1.5 text-slate-200 disabled:opacity-40">{analysisPageRequested || analysisPage.isFetching ? 'Loading…' : 'Load next ranking page'}</button>}</div>{!analysisRows.length && <p className="p-4 text-sm text-slate-500">Canonical analysis is explicitly unavailable.</p>}<details className="border-t border-slate-800 p-4"><summary className="cursor-pointer text-sm font-medium text-slate-300">Ranking policy and exclusions</summary><div className="mt-3 grid gap-3 lg:grid-cols-2"><div>{json(parsed.data.analysis.ranking_policy)}</div><div>{json(parsed.data.analysis.exclusions)}</div></div></details></section>}

                    {detailTab === 'ensemble' && <section className="grid gap-3 lg:grid-cols-2"><details className="rounded-xl border border-slate-800 bg-slate-900/70 p-3"><summary className="cursor-pointer text-sm font-medium text-white">Selected candidate artifact provenance</summary><div className="mt-3">{json({ artifact_id: selectedArtifact.artifact_id, sha256: selectedArtifact.sha256, bytes: selectedArtifact.bytes, media_type: selectedArtifact.media_type, metadata: selectedArtifact.metadata })}</div></details><details className="rounded-xl border border-slate-800 bg-slate-900/70 p-3"><summary className="cursor-pointer text-sm font-medium text-white">Authoritative sidecar identities</summary><div className="mt-3">{json(selected.sidecar_paths)}</div></details></section>}

                    {detailTab === 'evidence' && <section className="grid gap-4 xl:grid-cols-2"><div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4"><h2 className="font-semibold text-white">Support authorities</h2><p className="mt-1 text-xs text-slate-500">Persisted canonical records; no support is reconstructed from metric shape or provenance text.</p><div className="mt-3 space-y-3">{supportRecords.length ? supportRecords.map((item) => <details key={`${item.type}:${item.key}`} className="rounded-lg border border-slate-800 p-3"><summary className="cursor-pointer text-xs">{item.key} · <span className="font-mono text-slate-500">{shortHash(item.sha256)}</span></summary><div className="mt-2">{json({ artifact: item.artifact, payload: item.payload, page_rows: item.type === 'support' ? evidenceSupportPage.data?.rows || [] : evidenceMissingnessPage.data?.rows || [], total_count: item.type === 'support' ? evidenceSupportPage.data?.total_count ?? item.pages?.records?.total_count ?? 0 : evidenceMissingnessPage.data?.total_count ?? item.pages?.result_records?.total_count ?? 0 })}</div></details>) : <p className="text-sm text-slate-500">No separate support record was persisted. Analysis-row support fields remain authoritative.</p>}</div><details className="mt-4 rounded-lg border border-slate-800 p-3"><summary className="cursor-pointer text-xs">Analysis support records ({analysisRecord?.pages?.support_records?.total_count ?? parsed.data.analysis.support_records.length})</summary><div className="mt-2">{json({ rows: evidenceAnalysisSupportPage.data?.rows || [], total_count: evidenceAnalysisSupportPage.data?.total_count ?? parsed.data.analysis.support_records.length })}</div></details><details className="mt-3 rounded-lg border border-slate-800 p-3"><summary className="cursor-pointer text-xs">Pair ledger ({parsed.data.analysis.pair_ledger.length})</summary><div className="mt-2">{json(parsed.data.analysis.pair_ledger)}</div></details></div><div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4"><h2 className="font-semibold text-white">Missingness and clash evidence</h2><p className="mt-1 text-xs text-slate-500">Missing values remain explicit and are never imputed in the browser.</p><div className="mt-3 space-y-3">{missingnessRecords.length ? missingnessRecords.map((item) => <details key={`${item.type}:${item.key}`} className="rounded-lg border border-slate-800 p-3"><summary className="cursor-pointer text-xs">{item.key} · <span className="font-mono text-slate-500">{shortHash(item.sha256)}</span></summary><div className="mt-2">{json({ artifact: item.artifact, payload: item.payload, page_rows: item.type === 'support' ? evidenceSupportPage.data?.rows || [] : evidenceMissingnessPage.data?.rows || [], total_count: item.type === 'support' ? evidenceSupportPage.data?.total_count ?? item.pages?.records?.total_count ?? 0 : evidenceMissingnessPage.data?.total_count ?? item.pages?.result_records?.total_count ?? 0 })}</div></details>) : <p className="text-sm text-slate-500">No separate missingness record was persisted. Landscape slot statuses and mapping reasons remain explicit.</p>}</div><details className="mt-4 rounded-lg border border-slate-800 p-3"><summary className="cursor-pointer text-xs">Clash records ({analysisRecord?.pages?.clash_records?.total_count ?? parsed.data.analysis.clash_records.length})</summary><div className="mt-2">{json({ rows: evidenceAnalysisClashPage.data?.rows || [], total_count: evidenceAnalysisClashPage.data?.total_count ?? parsed.data.analysis.clash_records.length })}</div></details></div></section>}

                    {detailTab === 'downloads' && <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4"><h2 className="font-semibold text-white">Native and canonical content-addressed downloads</h2><p className="mt-1 text-xs text-slate-500">Every link uses the authenticated artifact identity returned by the canonical API. Hash, byte count, role, and candidate binding are shown verbatim.</p><div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">{parsed.data.value.artifacts.map((artifact) => <a key={artifact.artifact_id} href={cmArtifactUrl(requestId, artifact.artifact_id)} className="rounded-lg border border-slate-800 p-3 text-xs hover:border-slate-600 focus:border-orange-400"><div className="truncate font-medium text-slate-200">{artifact.relative_path}</div><div className="mt-1 text-slate-500">{artifact.role} · {artifact.bytes.toLocaleString()} bytes</div><div className="mt-1 truncate font-mono text-[10px] text-slate-600" title={artifact.sha256}>{artifact.sha256}</div><div className="mt-1 truncate font-mono text-[10px] text-slate-600">{artifact.candidate_id || 'request-level'}</div></a>)}</div></section>}
                </>}
            </div>
            <ProjectAttachmentDialog
                open={addToProjectOpen}
                source={{ adapterId: projectAdapterId, entityId: requestId, label: title, availability: 'available' }}
                onClose={() => setAddToProjectOpen(false)}
            />
        </div>
    );
}

export default ConformationalMappingViewer;
