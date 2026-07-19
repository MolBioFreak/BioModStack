import { useState, useCallback, useEffect, useMemo, useRef, type MouseEvent as ReactMouseEvent } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import MolstarViewer from './MolstarViewer';
import ChainDetailsPanel from './ChainDetailsPanel';
import ReferenceSelector, { type ReferenceStructure } from './ReferenceSelector';
import { useThemeColors } from './useThemeColors';
import {
    buildFileDownloadUrl,
    buildFileStreamUrl,
    type ChainMetric,
    type ChainPairIptmData,
    type ContactMapData,
    type Design,
    type FampnnPsceChainMetric,
    type FampnnPsceProfile,
    type IpsaeInterfaceAnalysis,
    type Job,
    type PAEData,
    type PersistedAnalysisRun,
    type RfLoopMetric,
    type RfLoopMetrics,
    type RfScopeHeadlineMetrics,
    type RfScreeningScope,
    type StructureAnalysis,
} from '../lib/api';
import { inferDesignAnalysisLens, inferDesignOutputSource, getValidationOutputLabel } from './designOutputSource';
import { buildMetricLayerFromExplicitMaps } from '../lib/molstar-metrics';
import type { MolstarResidueMetricLayer } from '../lib/molstar-metrics';
import {
    buildConforNetsConformerNavigation,
    buildConforNetsConformerSet,
    buildPlddtResidueColorMap,
    buildStructureViewerQuickViews,
    buildStructureViewerSections,
    buildStructureViewerSummaryCards,
    getConforNetsDefaultChainId,
    getConforNetsScalarPlddt,
    resolveConforNetsOverlayIds,
    resolveEffectiveStructureViewerColorMode,
    resolveStructureViewerConfidenceSemantics,
    type StructureViewerColorMode,
    type StructureViewerOverlayView,
    type StructureViewerQuickViewSpec,
    type StructureViewerSectionId,
    type StructureViewerSummaryCardSpec,
    type PlddtResidueMaskPoint,
} from './structureViewerSemantics.js';
import { resolveStructureViewerFullscreenAnalyticsLayout, resolveStructureViewerLayout } from './structureViewerLayout.js';

interface Selection {
    chain_id?: string;
    start_residue_number?: number;
    end_residue_number?: number;
    color?: { r: number; g: number; b: number };
    focus?: boolean;
}

interface ViewerAnalysisBundle {
    structureAnalysisRun?: PersistedAnalysisRun<StructureAnalysis> | null;
    structureAnalysis?: StructureAnalysis | null;
    onRunStructureAnalysis?: () => void;
    structureAnalysisBusy?: boolean;
    chainMetricsRun?: PersistedAnalysisRun<Record<string, ChainMetric>> | null;
    chainMetrics?: Record<string, ChainMetric> | null;
    onRunChainMetrics?: () => void;
    chainMetricsBusy?: boolean;
    fampnnPsceProfileRun?: PersistedAnalysisRun<FampnnPsceProfile> | null;
    fampnnPsceProfile?: FampnnPsceProfile | null;
    onRunFampnnPsceProfile?: () => void;
    fampnnPsceBusy?: boolean;
    paeMatrixRun?: PersistedAnalysisRun<PAEData> | null;
    paeMatrixData?: PAEData | null;
    onRunPaeMatrix?: () => void;
    paeMatrixBusy?: boolean;
    ipsaeInterfaceRun?: PersistedAnalysisRun<IpsaeInterfaceAnalysis> | null;
    ipsaeInterface?: IpsaeInterfaceAnalysis | null;
    onRunIpsaeInterface?: () => void;
    ipsaeInterfaceBusy?: boolean;
    contactMapRun?: PersistedAnalysisRun<ContactMapData> | null;
    contactMap?: ContactMapData | null;
    onRunContactMap?: () => void;
    contactMapBusy?: boolean;
    chainPairIptm?: ChainPairIptmData | null;
    chainPairIptmLoading?: boolean;
}

interface Props {
    selectedDesignId: string | null;
    setSelectedDesignId: (id: string) => void;
    designs: Design[];
    selectedDesign: Design | null | undefined;
    colorMode: 'default' | 'plddt' | 'cdr' | 'frustration' | 'fampnn_psce';
    setColorMode: (mode: 'default' | 'plddt' | 'cdr' | 'frustration' | 'fampnn_psce') => void;
    structureFormat: 'pdb' | 'cif';
    antibodySelections?: Selection[];
    antibodyStructureUrl?: string;
    viewerAnalyses?: ViewerAnalysisBundle;
    activeJob: Job | null | undefined;
    getMetricColor: (field: string, value: number | null) => string;
    rfMetricScope?: RfScreeningScope;
    setRfMetricScope?: (scope: RfScreeningScope) => void;
}

type OverlayView = StructureViewerOverlayView;
type ReferenceDockMode = 'selector' | 'viewer';

interface ReferenceWindowState {
    x: number;
    y: number;
    width: number;
    height: number;
}

interface StructureMetricCard {
    label: string;
    value: string;
    accentClass: string;
}

const normalizeRfScreeningScope = (value: unknown): RfScreeningScope | null =>
    value === 'whole_antibody' ? 'whole_antibody' : (value === 'cdr_loops' ? 'cdr_loops' : null);

const RF_SCOPE_LABELS: Record<RfScreeningScope, { short: string; target: string; epitope: string; distance: string }> = {
    cdr_loops: {
        short: 'CDR Loops',
        target: 'CDR-Target Contacts',
        epitope: 'CDR-Epitope Contacts',
        distance: 'CDR-Target Dist',
    },
    whole_antibody: {
        short: 'Whole Antibody',
        target: 'Whole-Ab Target Contacts',
        epitope: 'Whole-Ab Epitope Contacts',
        distance: 'Whole-Ab Target Dist',
    },
};

const coerceRfLoopMetrics = (value: unknown): RfLoopMetrics | null => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    return value as RfLoopMetrics;
};

const getRfLoopSummary = (design: Design | null | undefined): Record<string, unknown> | null => {
    const metrics = coerceRfLoopMetrics(design?.rfa_loop_metrics);
    const summary = metrics?._screening;
    return summary && typeof summary === 'object' && !Array.isArray(summary) ? summary as Record<string, unknown> : null;
};

const getRfHeadlineMetricValue = (
    design: Design | null | undefined,
    scope: RfScreeningScope,
    key: keyof RfScopeHeadlineMetrics,
): number | null => {
    const summary = getRfLoopSummary(design);
    const headlineMetrics = summary?.headline_metrics_by_scope;
    if (headlineMetrics && typeof headlineMetrics === 'object' && !Array.isArray(headlineMetrics)) {
        const scopedMetrics = (headlineMetrics as Record<string, unknown>)[scope];
        if (scopedMetrics && typeof scopedMetrics === 'object' && !Array.isArray(scopedMetrics)) {
            const value = (scopedMetrics as RfScopeHeadlineMetrics)[key];
            if (typeof value === 'number' && Number.isFinite(value)) return value;
        }
    }
    const fallback = design ? (design as unknown as Record<string, unknown>)[key] : null;
    return typeof fallback === 'number' && Number.isFinite(fallback) ? fallback : null;
};

const getRfLoopEntries = (design: Design | null | undefined): Array<{ loopId: string; metrics: RfLoopMetric }> => {
    const metrics = coerceRfLoopMetrics(design?.rfa_loop_metrics);
    if (!metrics) return [];
    return Object.entries(metrics)
        .filter(([loopId, value]) => /^[HL][123]$/.test(loopId) && value && typeof value === 'object' && !Array.isArray(value))
        .map(([loopId, value]) => ({ loopId, metrics: value as RfLoopMetric }))
        .sort((a, b) => a.loopId.localeCompare(b.loopId));
};

function getDesignOriginLabel(design: Design | null | undefined): string | null {
    const source = inferDesignOutputSource(design || {});
    if (source === 'validation') {
        const validationLabel = getValidationOutputLabel(design || {});
        return validationLabel === 'Validation' ? 'Validation' : `${validationLabel} Validation`;
    }
    if (source === 'boltzgen') {
        return 'BoltzGen Candidate';
    }
    if (source === 'fampnn') {
        return 'FAMPNN Candidate';
    }
    if (source === 'esmfold2') {
        return 'ESMFold2 Prediction';
    }
    if (source === 'rfantibody') {
        return 'RFantibody Backbone';
    }
    return null;
}

function formatStructureValidationName(design: Design | null | undefined): string | null {
    const rawName = typeof design?.name === 'string' ? design.name.trim() : '';
    if (!rawName) return null;

    const sourceName = typeof design?.source_design_name === 'string' && design.source_design_name.trim()
        ? design.source_design_name.trim()
        : '';

    const base = sourceName || rawName.replace(/^(variant_\d+)_\1_model_\d+$/i, '$1');
    const variantMatch = base.match(/^variant_(\d+)$/i);
    const readableBase = variantMatch
        ? `Variant ${variantMatch[1]}`
        : base
            .replace(/^rbx1[_-]/i, 'RBX1 ')
            .replace(/[_-]+/g, ' ')
            .replace(/\b([a-z])/g, (match) => match.toUpperCase());

    const modelMatch = rawName.match(/_model_(\d+)$/i);
    if (modelMatch) return `${readableBase} • Model ${modelMatch[1]}`;

    const sampleMatch = rawName.match(/(?:_sample_(\d+)|_sample(\d+))$/i);
    if (sampleMatch) {
        const sampleIndex = sampleMatch[1] ?? sampleMatch[2];
        return `${readableBase} • Sample ${sampleIndex}`;
    }

    return readableBase;
}

function plddtColor(value: number): { r: number; g: number; b: number } {
    if (value >= 90) return { r: 59, g: 130, b: 246 };
    if (value >= 70) return { r: 34, g: 211, b: 238 };
    if (value >= 50) return { r: 250, g: 204, b: 21 };
    return { r: 249, g: 115, b: 22 };
}

function frustrationColor(value: number): { r: number; g: number; b: number } {
    if (value <= -1.0) return { r: 239, g: 68, b: 68 };
    if (value >= 0.58) return { r: 34, g: 197, b: 94 };
    return { r: 148, g: 163, b: 184 };
}

const CHAIN_ACCENT_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

const residueColorKey = (chainId: string, residueNumber: number): string => `${chainId}:${residueNumber}`;

function fampnnPsceColor(value: number): { r: number; g: number; b: number } {
    if (value <= 0.9) return { r: 52, g: 211, b: 153 };
    if (value <= 1.2) return { r: 56, g: 189, b: 248 };
    if (value <= 1.6) return { r: 245, g: 158, b: 11 };
    return { r: 244, g: 114, b: 182 };
}

function fampnnPsceColorHex(value: number): string {
    const color = fampnnPsceColor(value);
    return `rgb(${color.r}, ${color.g}, ${color.b})`;
}

function fampnnPsceTierLabel(value: number): string {
    if (value <= 0.9) return 'Excellent';
    if (value <= 1.2) return 'Good';
    if (value <= 1.6) return 'Moderate';
    return 'Review';
}

function formatMetricValue(value: number | null | undefined, digits = 1, suffix = ''): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    return `${value.toFixed(digits)}${suffix}`;
}

function formatAnalysisStatus(status: PersistedAnalysisRun<unknown>['status'] | undefined | null): string {
    if (status === 'completed') return 'Cached';
    if (status === 'running') return 'Running';
    if (status === 'queued') return 'Queued';
    if (status === 'failed') return 'Failed';
    return 'Not computed';
}

const asRecord = (value: unknown): Record<string, unknown> | null => (
    value && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null
);

const getFampnnPayload = (design: Design | null | undefined): Record<string, unknown> | null => (
    asRecord(asRecord(design?.provenance)?.fampnn)
    ?? asRecord(asRecord(asRecord(design?.provenance)?.ppiflow)?.fampnn)
    ?? asRecord(asRecord(design?.confidence_metrics)?.fampnn)
);

const getFampnnScalar = (payload: Record<string, unknown> | null, ...keys: string[]): number | null => {
    for (const key of keys) {
        const value = payload?.[key];
        if (typeof value === 'number' && Number.isFinite(value)) return value;
    }
    return null;
};

export default function StructureViewerPane({
    selectedDesignId,
    setSelectedDesignId,
    designs,
    selectedDesign,
    colorMode,
    setColorMode,
    structureFormat,
    antibodySelections,
    antibodyStructureUrl,
    viewerAnalyses,
    activeJob,
    getMetricColor,
    rfMetricScope,
    setRfMetricScope,
}: Props) {
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [viewportWidth, setViewportWidth] = useState(() => (
        typeof window === 'undefined' ? 1280 : window.innerWidth
    ));
    const [viewportHeight, setViewportHeight] = useState(() => (
        typeof window === 'undefined' ? 720 : window.innerHeight
    ));
    const [analyticsPanelOpen, setAnalyticsPanelOpen] = useState(true);
    const [overlayView, setOverlayView] = useState<OverlayView>('metrics');
    const [conforNetsOverlayIds, setConforNetsOverlayIds] = useState<string[]>([]);
    const [focusedMetricSection, setFocusedMetricSection] = useState<StructureViewerSectionId>('summary');
    const [showReferenceDock, setShowReferenceDock] = useState(false);
    const [selectedReference, setSelectedReference] = useState<ReferenceStructure | null>(null);
    const [referenceDockMode, setReferenceDockMode] = useState<ReferenceDockMode>('selector');
    const [referenceWindow, setReferenceWindow] = useState<ReferenceWindowState>({
        x: 28,
        y: 64,
        width: 430,
        height: 320,
    });

    const [plddtProfile, setPlddtProfile] = useState<number[]>([]);
    const [residueMetricNumbers, setResidueMetricNumbers] = useState<number[]>([]);
    const [selectedChain, setSelectedChain] = useState<string | null>(null);  // null = all chains
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerAreaRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const referenceDragRef = useRef<{ pointerX: number; pointerY: number; startX: number; startY: number } | null>(null);
    const referenceResizeRef = useRef<{ pointerX: number; pointerY: number; startWidth: number; startHeight: number } | null>(null);

    useEffect(() => {
        if (typeof window === 'undefined') {
            return undefined;
        }

        const handleViewportResize = () => {
            setViewportWidth(window.innerWidth);
            setViewportHeight(window.innerHeight);
        };
        handleViewportResize();
        window.addEventListener('resize', handleViewportResize);
        return () => window.removeEventListener('resize', handleViewportResize);
    }, []);

    // Theme-aware colors for Molstar viewer
    const themeColors = useThemeColors();
    const viewerLayout = useMemo(() => resolveStructureViewerLayout({
        viewportWidth,
        isFullscreen,
    }), [viewportWidth, isFullscreen]);
    const fullscreenAnalyticsLayout = useMemo(() => resolveStructureViewerFullscreenAnalyticsLayout({
        viewportWidth,
        viewportHeight,
    }), [viewportWidth, viewportHeight]);
    const designOrigin = getDesignOriginLabel(selectedDesign);
    const designLens = selectedDesign ? inferDesignAnalysisLens(selectedDesign as UntypedApiValue) : null;
    const selectedDesignPpiflowRecord = asRecord(asRecord(selectedDesign?.provenance)?.ppiflow);
    const fampnnPayload = useMemo(() => getFampnnPayload(selectedDesign), [selectedDesign]);
    const fampnnAvgPsce = useMemo(() => {
        const directValue = selectedDesign?.fampnn_psce;
        if (typeof directValue === 'number' && Number.isFinite(directValue)) return directValue;
        return getFampnnScalar(fampnnPayload, 'fampnn_avg_psce', 'avg_psce');
    }, [fampnnPayload, selectedDesign?.fampnn_psce]);
    const fampnnMaxResiduePsce = useMemo(() => {
        const directValue = selectedDesign?.fampnn_max_residue_psce;
        if (typeof directValue === 'number' && Number.isFinite(directValue)) return directValue;
        return getFampnnScalar(fampnnPayload, 'fampnn_max_residue_psce', 'max_residue_psce');
    }, [fampnnPayload, selectedDesign?.fampnn_max_residue_psce]);
    const sourceBackboneReference = useMemo<ReferenceStructure | null>(() => {
        if (designLens !== 'ppiflow') return null;
        const sourceName = typeof selectedDesignPpiflowRecord?.source_design_name === 'string' && selectedDesignPpiflowRecord.source_design_name.trim()
            ? selectedDesignPpiflowRecord.source_design_name.trim()
            : 'Source Backbone';
        return {
            url: `/api/designs/${selectedDesign?.id}/source-pdb`,
            format: 'pdb',
            name: `Source Backbone • ${sourceName}`,
        };
    }, [designLens, selectedDesign?.id, selectedDesignPpiflowRecord]);
    const conforNetsConformerSet = useMemo(
        () => buildConforNetsConformerSet(designs, selectedDesignId),
        [designs, selectedDesignId],
    );
    const conforNetsNavigation = useMemo(
        () => buildConforNetsConformerNavigation(conforNetsConformerSet),
        [conforNetsConformerSet],
    );
    const resolvedConforNetsOverlayIds = useMemo(
        () => resolveConforNetsOverlayIds(conforNetsConformerSet, conforNetsOverlayIds),
        [conforNetsConformerSet, conforNetsOverlayIds],
    );
    const conforNetsOverlaySignature = resolvedConforNetsOverlayIds.join('|');
    const conforNetsDefaultChainId = useMemo(
        () => getConforNetsDefaultChainId(selectedDesign ?? null) ?? (conforNetsConformerSet ? 'A' : null),
        [conforNetsConformerSet, selectedDesign],
    );
    const conforNetsScalarPlddt = getConforNetsScalarPlddt(selectedDesign ?? null);
    const conforNetsArtifactManifest = asRecord(asRecord(selectedDesign?.confidence_metrics)?.confornets_artifact_manifest);
    const conforNetsTensorCountRaw = conforNetsArtifactManifest?.full_confidence_tensor_count;
    const conforNetsFullConfidenceTensorCount = typeof conforNetsTensorCountRaw === 'number' && Number.isFinite(conforNetsTensorCountRaw)
        ? conforNetsTensorCountRaw
        : typeof conforNetsTensorCountRaw === 'string' && conforNetsTensorCountRaw.trim() && Number.isFinite(Number(conforNetsTensorCountRaw))
            ? Number(conforNetsTensorCountRaw)
            : null;
    const conforNetsUsesScalarPlddtFallback = Boolean(
        conforNetsConformerSet
        && conforNetsScalarPlddt !== null
        && conforNetsFullConfidenceTensorCount === 0,
    );
    useEffect(() => {
        const isAlreadyResolved = conforNetsOverlayIds.length === resolvedConforNetsOverlayIds.length
            && conforNetsOverlayIds.every((id, index) => id === resolvedConforNetsOverlayIds[index]);
        if (!isAlreadyResolved) {
            setConforNetsOverlayIds(resolvedConforNetsOverlayIds);
        }
    }, [conforNetsOverlayIds, resolvedConforNetsOverlayIds]);
    const selectConforNetsConformerByIndex = useCallback((nextIndex: number) => {
        if (!conforNetsConformerSet) return;
        const lastIndex = conforNetsConformerSet.conformers.length - 1;
        const clampedIndex = Math.min(Math.max(nextIndex, 0), lastIndex);
        const nextConformer = conforNetsConformerSet.conformers[clampedIndex];
        if (nextConformer && nextConformer.id !== selectedDesignId) {
            setSelectedDesignId(nextConformer.id);
        }
    }, [conforNetsConformerSet, selectedDesignId, setSelectedDesignId]);
    const toggleConforNetsOverlay = useCallback((conformerId: string) => {
        setConforNetsOverlayIds((currentIds) => (
            currentIds.includes(conformerId)
                ? currentIds.filter((id) => id !== conformerId)
                : [...currentIds, conformerId]
        ));
    }, []);
    const preferredRfMetricScope = normalizeRfScreeningScope(activeJob?.params?.rfantibody_screen_reference_scope) ?? 'cdr_loops';
    const effectiveRfMetricScope = rfMetricScope ?? preferredRfMetricScope;
    const rfMetricLabels = RF_SCOPE_LABELS[effectiveRfMetricScope];
    const rfLoopEntries = getRfLoopEntries(selectedDesign ?? null);
    const rfaConfidenceScope = useMemo(() => {
        if (!selectedDesign || designLens !== 'rfantibody') return null;
        const directScope = asRecord(selectedDesign.rfa_confidence_scope);
        if (directScope) return directScope;
        const confidenceMetrics = asRecord(selectedDesign.confidence_metrics);
        const nestedRfa = asRecord(confidenceMetrics?.rfantibody);
        const nestedScope = asRecord(nestedRfa?.confidence_scope);
        if (nestedScope) return nestedScope;
        return asRecord(confidenceMetrics?.confidence_scope);
    }, [designLens, selectedDesign]);
    const rfaModifiableResidueMask = useMemo<PlddtResidueMaskPoint[] | null>(() => {
        if (rfaConfidenceScope?.primary_scope !== 'modifiable_residues') return null;
        const rawResidues = Array.isArray(rfaConfidenceScope.modifiable_residues)
            ? rfaConfidenceScope.modifiable_residues
            : [];
        const residues = rawResidues
            .map((residue) => {
                const record = asRecord(residue);
                const chainId = String(record?.chain_id ?? '').trim();
                const residueNumber = typeof record?.residue_number === 'number' ? record.residue_number : Number(record?.residue_number);
                return chainId && Number.isFinite(residueNumber)
                    ? { chain_id: chainId, residue_number: residueNumber }
                    : null;
            })
            .filter((residue): residue is PlddtResidueMaskPoint => residue !== null);
        return residues.length > 0 ? residues : null;
    }, [rfaConfidenceScope]);
    const confidenceSemantics = useMemo(
        () => resolveStructureViewerConfidenceSemantics({
            activeJobModelId: activeJob?.model_id,
            designLens,
        }),
        [activeJob?.model_id, designLens],
    );
    const bfactorLabel = rfaModifiableResidueMask ? 'RFA Mod pLDDT' : confidenceSemantics.shortLabel;
    const headlineConfidenceLabel = rfaModifiableResidueMask ? 'RFA Modifiable pLDDT' : confidenceSemantics.headlineLabel;
    const structureAnalysisRun = viewerAnalyses?.structureAnalysisRun ?? null;
    const structureAnalysis = viewerAnalyses?.structureAnalysis ?? null;
    const structureAnalysisBusy = viewerAnalyses?.structureAnalysisBusy ?? false;
    const onRunStructureAnalysis = viewerAnalyses?.onRunStructureAnalysis;
    const structureAnalysisStatus = structureAnalysisRun?.status ?? 'missing';
    const structureAnalysisStatusCopy = formatAnalysisStatus(structureAnalysisStatus);

    const chainMetricsRun = viewerAnalyses?.chainMetricsRun ?? null;
    const chainMetrics = useMemo(() => viewerAnalyses?.chainMetrics ?? {}, [viewerAnalyses?.chainMetrics]);
    const chainMetricsBusy = viewerAnalyses?.chainMetricsBusy ?? false;
    const onRunChainMetrics = viewerAnalyses?.onRunChainMetrics;
    const chainMetricsStatus = chainMetricsRun?.status ?? 'missing';
    const chainMetricsStatusCopy = formatAnalysisStatus(chainMetricsStatus);

    const fampnnPsceProfileRun = viewerAnalyses?.fampnnPsceProfileRun ?? null;
    const fampnnPsceProfile = viewerAnalyses?.fampnnPsceProfile ?? null;
    const fampnnPsceChains = useMemo(() => (fampnnPsceProfile?.chains ?? {}) as Record<string, FampnnPsceChainMetric>, [fampnnPsceProfile?.chains]);
    const fampnnPsceBusy = viewerAnalyses?.fampnnPsceBusy ?? false;
    const onRunFampnnPsceProfile = viewerAnalyses?.onRunFampnnPsceProfile;
    const fampnnPsceStatus = fampnnPsceProfileRun?.status ?? 'missing';
    const fampnnPsceStatusCopy = formatAnalysisStatus(fampnnPsceStatus);

    const paeRun = viewerAnalyses?.paeMatrixRun ?? null;
    const paeData = viewerAnalyses?.paeMatrixData ?? null;
    const paeMatrix = paeData?.pae_matrix ?? null;
    const paeBusy = viewerAnalyses?.paeMatrixBusy ?? false;
    const onRunPaeMatrix = viewerAnalyses?.onRunPaeMatrix;
    const paeStatus = paeRun?.status ?? 'missing';
    const paeStatusCopy = formatAnalysisStatus(paeStatus);

    const ipsaeInterfaceRun = viewerAnalyses?.ipsaeInterfaceRun ?? null;
    const ipsaeInterface = viewerAnalyses?.ipsaeInterface ?? null;
    const ipsaeInterfaceBusy = viewerAnalyses?.ipsaeInterfaceBusy ?? false;
    const onRunIpsaeInterface = viewerAnalyses?.onRunIpsaeInterface;
    const ipsaeInterfaceStatus = ipsaeInterfaceRun?.status ?? 'missing';
    const ipsaeInterfaceStatusCopy = formatAnalysisStatus(ipsaeInterfaceStatus);

    const contactMapRun = viewerAnalyses?.contactMapRun ?? null;
    const contactMap = viewerAnalyses?.contactMap ?? null;
    const contactMapBusy = viewerAnalyses?.contactMapBusy ?? false;
    const onRunContactMap = viewerAnalyses?.onRunContactMap;
    const contactMapStatus = contactMapRun?.status ?? 'missing';
    const contactMapStatusCopy = formatAnalysisStatus(contactMapStatus);

    const chainPairIptm = viewerAnalyses?.chainPairIptm ?? null;
    const chainPairIptmLoading = viewerAnalyses?.chainPairIptmLoading ?? false;

    const clampReferenceWindow = useCallback((next: ReferenceWindowState): ReferenceWindowState => {
        const bounds = viewerAreaRef.current?.getBoundingClientRect();
        if (!bounds) return next;
        const minWidth = 280;
        const minHeight = 180;
        const width = Math.min(Math.max(next.width, minWidth), Math.max(minWidth, bounds.width - 24));
        const height = Math.min(Math.max(next.height, minHeight), Math.max(minHeight, bounds.height - 24));
        const maxX = Math.max(12, bounds.width - width - 12);
        const maxY = Math.max(12, bounds.height - height - 12);
        return {
            width,
            height,
            x: Math.min(Math.max(next.x, 12), maxX),
            y: Math.min(Math.max(next.y, 12), maxY),
        };
    }, []);

    const positionReferenceWindow = useCallback((mode: ReferenceDockMode) => {
        const bounds = viewerAreaRef.current?.getBoundingClientRect();
        if (!bounds) return;
        const width = mode === 'selector' ? 420 : 460;
        const height = mode === 'selector' ? 420 : 320;
        setReferenceWindow((current) => clampReferenceWindow({
            ...current,
            width,
            height,
            x: Math.max(12, bounds.width - width - 24),
            y: 64,
        }));
    }, [clampReferenceWindow]);

    // Per-residue confidence is already persisted on the design row, so fetching it
    // is cheap and does not kick off new analysis work.
    useEffect(() => {
        if (!selectedDesignId) return;

        const fetchResidueMetrics = async () => {
            try {
                const residueRes = await fetch(`/api/designs/${selectedDesignId}/residue-metrics`).catch(() => null);

                if (residueRes?.ok) {
                    const data = await residueRes.json();
                    setPlddtProfile(Array.isArray(data.plddt) ? data.plddt : []);
                    setResidueMetricNumbers(Array.isArray(data.residue_numbers) ? data.residue_numbers : []);
                } else {
                    setPlddtProfile([]);
                    setResidueMetricNumbers([]);
                }
            } catch {
                setPlddtProfile([]);
                setResidueMetricNumbers([]);
            }
        };

        fetchResidueMetrics();
    }, [selectedDesignId]);

    const chainBoundaries = useMemo(() => {
        const chainIds = Object.keys(chainMetrics).sort();
        let offset = 0;
        const boundaries: { id: string; start: number; end: number }[] = [];
        for (const chainId of chainIds) {
            const length = chainMetrics[chainId]?.length || 0;
            boundaries.push({ id: chainId, start: offset, end: offset + length });
            offset += length;
        }
        return boundaries;
    }, [chainMetrics]);

    const fampnnPsceChainIds = useMemo(() => Object.keys(fampnnPsceChains).sort(), [fampnnPsceChains]);
    const fampnnPsceBoundaries = useMemo(() => {
        let offset = 0;
        const boundaries: { id: string; start: number; end: number }[] = [];
        for (const chainId of fampnnPsceChainIds) {
            const length = fampnnPsceChains[chainId]?.length || 0;
            boundaries.push({ id: chainId, start: offset, end: offset + length });
            offset += length;
        }
        return boundaries;
    }, [fampnnPsceChainIds, fampnnPsceChains]);
    const fampnnPsceProfileValues = useMemo(
        () => fampnnPsceChainIds.flatMap((chainId) => fampnnPsceChains[chainId]?.psce || []),
        [fampnnPsceChainIds, fampnnPsceChains],
    );
    const fampnnDerivedSummary = useMemo(() => {
        if (!fampnnPsceProfileValues.length) return null;
        const total = fampnnPsceProfileValues.reduce((sum, value) => sum + value, 0);
        return {
            avg_psce: total / fampnnPsceProfileValues.length,
            max_psce: Math.max(...fampnnPsceProfileValues),
            min_psce: Math.min(...fampnnPsceProfileValues),
        };
    }, [fampnnPsceProfileValues]);
    const effectiveFampnnAvgPsce = fampnnAvgPsce ?? fampnnDerivedSummary?.avg_psce ?? null;
    const effectiveFampnnMaxResiduePsce = fampnnMaxResiduePsce ?? fampnnDerivedSummary?.max_psce ?? null;
    const hasResidueConfidence = plddtProfile.length > 0 || Object.keys(chainMetrics).length > 0;
    const hasFampnnPsceProfile = fampnnPsceChainIds.length > 0;
    const hasPae = Array.isArray(paeMatrix) && paeMatrix.length > 0;
    const hasStructureSummary = Boolean(structureAnalysis);
    const hasIpsaeInterface = Boolean(
        ipsaeInterface && (
            (typeof ipsaeInterface.ipsae === 'number' && Number.isFinite(ipsaeInterface.ipsae))
            || (ipsaeInterface.pair_scores?.length ?? 0) > 0
        ),
    );
    const hasContactMap = Array.isArray(contactMap?.distance_matrix) && contactMap.distance_matrix.length > 0;
    const hasChainPairIptm = Array.isArray(chainPairIptm?.iptm_matrix) && chainPairIptm.iptm_matrix.length > 0;
    const hasFampnnDesign = designLens === 'fampnn';
    const hasFrustrationSummary = selectedDesign?.frustration_high_count != null
        || selectedDesign?.frustration_min_count != null
        || !!selectedDesign?.frustration_residues?.length;
    const hasDesignabilitySection = hasFampnnDesign || hasFampnnPsceProfile || hasFrustrationSummary;
    const designabilityStatusCopy = hasFampnnDesign
        ? fampnnPsceStatusCopy
        : hasFrustrationSummary
            ? 'Available'
            : 'Not available';
    const viewerSections = useMemo(
        () => buildStructureViewerSections({
            hasResidueConfidence,
            hasPaeMatrix: hasPae,
            hasStructureSummary,
            hasIpsaeInterface,
            hasChainPairIptm,
            hasContactMap,
            hasFampnnDesign,
            hasFampnnPsceProfile,
            hasFrustrationSummary,
        }),
        [
            hasChainPairIptm,
            hasContactMap,
            hasFampnnDesign,
            hasFampnnPsceProfile,
            hasFrustrationSummary,
            hasIpsaeInterface,
            hasPae,
            hasResidueConfidence,
            hasStructureSummary,
        ],
    );
    const viewerQuickViews = useMemo(
        () => buildStructureViewerQuickViews({
            confidenceLabel: bfactorLabel,
            hasResidueConfidence,
            hasPaeMatrix: hasPae,
            hasStructureSummary,
            hasIpsaeInterface,
            hasChainPairIptm,
            hasContactMap,
            hasFampnnDesign,
            hasFampnnPsceProfile,
            hasFrustrationSummary,
            hasCdrOverlay: Boolean(antibodySelections?.length),
        }),
        [
            antibodySelections?.length,
            bfactorLabel,
            hasChainPairIptm,
            hasContactMap,
            hasFampnnDesign,
            hasFampnnPsceProfile,
            hasFrustrationSummary,
            hasIpsaeInterface,
            hasPae,
            hasResidueConfidence,
            hasStructureSummary,
        ],
    );
    const fampnnPsceChartMax = useMemo(() => {
        const maxValue = fampnnPsceProfileValues.length ? Math.max(...fampnnPsceProfileValues) : 0;
        return Math.max(2.0, Math.ceil(maxValue * 2) / 2);
    }, [fampnnPsceProfileValues]);

    useEffect(() => {
        if (designLens !== 'fampnn' || !selectedDesignId) return;
        if (hasFampnnPsceProfile || fampnnPsceBusy || fampnnPsceStatus !== 'missing' || !onRunFampnnPsceProfile) return;
        onRunFampnnPsceProfile();
    }, [designLens, fampnnPsceBusy, fampnnPsceStatus, hasFampnnPsceProfile, onRunFampnnPsceProfile, selectedDesignId]);

    const effectiveColorMode = resolveEffectiveStructureViewerColorMode({
        requestedMode: colorMode,
        hasResidueConfidence,
        hasFampnnPsceProfile,
        hasFrustrationResidues: Boolean(selectedDesign?.frustration_residues?.length),
    });
    const applyQuickView = useCallback((quickView: StructureViewerQuickViewSpec) => {
        setFocusedMetricSection(quickView.sectionId);
        setOverlayView(quickView.overlayView);
        setColorMode(quickView.colorMode);
    }, [setColorMode]);
    const handleOverlayTabClick = useCallback((nextOverlayView: OverlayView) => {
        setOverlayView(nextOverlayView);
        if (nextOverlayView === 'plddt') {
            setFocusedMetricSection('confidence');
            return;
        }
        if (nextOverlayView === 'psce') {
            setFocusedMetricSection('designability');
            return;
        }
        if (nextOverlayView === 'pae') {
            setFocusedMetricSection('geometry');
            return;
        }
        if (effectiveColorMode === 'frustration') {
            setFocusedMetricSection('designability');
            return;
        }
        if (effectiveColorMode === 'cdr') {
            setFocusedMetricSection('confidence');
        }
    }, [effectiveColorMode]);
    const handleColorModeChange = useCallback((nextColorMode: StructureViewerColorMode) => {
        setColorMode(nextColorMode);
        if (nextColorMode === 'plddt') {
            setOverlayView('plddt');
            setFocusedMetricSection('confidence');
            return;
        }
        if (nextColorMode === 'fampnn_psce') {
            if (hasFampnnPsceProfile) {
                setOverlayView('psce');
            }
            setFocusedMetricSection('designability');
            return;
        }
        if (nextColorMode === 'frustration') {
            setOverlayView('metrics');
            setFocusedMetricSection('designability');
            return;
        }
        if (nextColorMode === 'cdr') {
            setOverlayView('metrics');
            setFocusedMetricSection('confidence');
            return;
        }
        if (overlayView === 'metrics') {
            setFocusedMetricSection('summary');
        }
    }, [hasFampnnPsceProfile, overlayView, setColorMode]);
    const isQuickViewActive = useCallback((quickView: StructureViewerQuickViewSpec) => (
        focusedMetricSection === quickView.sectionId
        && overlayView === quickView.overlayView
        && effectiveColorMode === quickView.colorMode
    ), [effectiveColorMode, focusedMetricSection, overlayView]);
    const viewerStructureUrl = effectiveColorMode === 'cdr' && antibodyStructureUrl
        ? antibodyStructureUrl
        : (selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined);
    const viewerStructureFormat = effectiveColorMode === 'cdr' && antibodyStructureUrl ? 'pdb' : structureFormat;
    const conforNetsOverlayStructures = useMemo(() => {
        if (!conforNetsConformerSet || effectiveColorMode === 'cdr') return [];
        const conformerById = new Map(conforNetsConformerSet.conformers.map((conformer) => [conformer.id, conformer]));
        return resolvedConforNetsOverlayIds.map((id) => {
            const conformer = conformerById.get(id);
            return {
                id,
                structureUrl: `/api/designs/${id}/pdb`,
                format: viewerStructureFormat,
                label: conformer?.frameIndex == null
                    ? (conformer?.name ?? id)
                    : `Frame ${conformer.frameIndex} • ${conformer.name}`,
            };
        });
    }, [conforNetsConformerSet, effectiveColorMode, resolvedConforNetsOverlayIds, viewerStructureFormat]);

    useEffect(() => {
        if (selectedChain && !new Set([...Object.keys(chainMetrics), ...fampnnPsceChainIds]).has(selectedChain)) {
            setSelectedChain(null);
        }
    }, [chainMetrics, fampnnPsceChainIds, selectedChain]);

    useEffect(() => {
        if (
            (overlayView === 'plddt' && !hasResidueConfidence)
            || (overlayView === 'psce' && !hasFampnnPsceProfile)
            || (overlayView === 'pae' && !hasPae)
        ) {
            setOverlayView('metrics');
            setFocusedMetricSection('summary');
        }
    }, [hasFampnnPsceProfile, hasPae, hasResidueConfidence, overlayView]);

    const metricSectionTitle = (() => {
        if (designLens === 'rfantibody') return 'RFantibody Screen Metrics';
        if (designLens === 'boltzgen') return 'BoltzGen Metrics';
        if (designLens === 'fampnn') return 'FAMPNN Metrics';
        if (designLens === 'frustrampnn') return 'FrustraMPNN Metrics';
        if (designLens === 'ppiflow') return 'PPIFlow Metrics';
        return 'Confidence Metrics';
    })();

    const stageGuidance =
        designLens === 'rfantibody'
            ? (hasResidueConfidence
                ? 'RF confidence: stage-native output.'
                : 'RFantibody: stage-native coloring + engagement metrics.')
            : designLens === 'boltzgen'
                ? 'BoltzGen candidates: triage by conf_score, affinity priors, size.'
                : designLens === 'fampnn'
                    ? 'FA-MPNN pSCE: lower is better; worst residue flags outliers.'
                    : null;

    const structureMetricCards = (() => {
        if (!selectedDesign) return [] as StructureMetricCard[];

        if (designLens === 'rfantibody') {
            return [
                {
                    label: rfMetricLabels.target,
                    value: formatMetricValue(getRfHeadlineMetricValue(selectedDesign, effectiveRfMetricScope, 'target_contact_count'), 0),
                    accentClass: 'text-emerald-300',
                },
                {
                    label: rfMetricLabels.epitope,
                    value: formatMetricValue(getRfHeadlineMetricValue(selectedDesign, effectiveRfMetricScope, 'epitope_contact_count'), 0),
                    accentClass: 'text-cyan-300',
                },
                {
                    label: rfMetricLabels.distance,
                    value: formatMetricValue(getRfHeadlineMetricValue(selectedDesign, effectiveRfMetricScope, 'target_min_distance'), 1, ' A'),
                    accentClass: 'text-amber-300',
                },
                {
                    label: `${rfMetricLabels.short} Epi Dist`,
                    value: formatMetricValue(getRfHeadlineMetricValue(selectedDesign, effectiveRfMetricScope, 'epitope_min_distance'), 1, ' A'),
                    accentClass: 'text-violet-300',
                },
                {
                    label: 'Hotspots Covered',
                    value: formatMetricValue(selectedDesign.rfa_hotspot_covered_count ?? null, 0),
                    accentClass: 'text-rose-300',
                },
                {
                    label: 'Backbone ID',
                    value: formatMetricValue(selectedDesign.backbone_id ?? null, 0),
                    accentClass: 'text-slate-200',
                },
            ] satisfies StructureMetricCard[];
        }

        if (designLens === 'fampnn') {
            return [
                {
                    label: 'Avg PSCE',
                    value: formatMetricValue(effectiveFampnnAvgPsce, 3),
                    accentClass: getMetricColor('fampnn_psce', effectiveFampnnAvgPsce),
                },
                {
                    label: 'Worst Residue',
                    value: formatMetricValue(effectiveFampnnMaxResiduePsce, 3),
                    accentClass: getMetricColor('fampnn_max_residue_psce', effectiveFampnnMaxResiduePsce),
                },
                {
                    label: 'MPNN Score',
                    value: formatMetricValue(selectedDesign.mpnn_score ?? null, 3),
                    accentClass: 'text-cyan-300',
                },
                {
                    label: 'Binder Length',
                    value: formatMetricValue(selectedDesign.binder_length ?? null, 0),
                    accentClass: 'text-amber-300',
                },
            ] satisfies StructureMetricCard[];
        }

        if (designLens === 'boltzgen') {
            return [
                {
                    label: 'Confidence',
                    value: formatMetricValue(selectedDesign.conf_score ?? selectedDesign.plddt_overall ?? null, 2),
                    accentClass: getMetricColor('conf_score', selectedDesign.conf_score ?? null),
                },
                {
                    label: 'Affinity',
                    value: formatMetricValue(selectedDesign.affinity_score ?? null, 2),
                    accentClass: 'text-emerald-300',
                },
                {
                    label: 'iPTM',
                    value: formatMetricValue(selectedDesign.iptm ?? selectedDesign.protein_iptm ?? null, 3),
                    accentClass: 'text-amber-300',
                },
                {
                    label: 'Binder Length',
                    value: formatMetricValue(selectedDesign.binder_length ?? null, 0),
                    accentClass: 'text-slate-200',
                },
            ] satisfies StructureMetricCard[];
        }

        if (designLens === 'frustrampnn') {
            return [
                {
                    label: 'High Frustration',
                    value: formatMetricValue(selectedDesign.frustration_high_count ?? null, 0),
                    accentClass: 'text-rose-300',
                },
                {
                    label: 'High Frust. %',
                    value: formatMetricValue(selectedDesign.frustration_pct_high ?? null, 1, '%'),
                    accentClass: 'text-amber-300',
                },
                {
                    label: 'Minimal Frust.',
                    value: formatMetricValue(selectedDesign.frustration_min_count ?? null, 0),
                    accentClass: 'text-emerald-300',
                },
                {
                    label: 'Binder Length',
                    value: formatMetricValue(selectedDesign.binder_length ?? null, 0),
                    accentClass: 'text-slate-200',
                },
            ] satisfies StructureMetricCard[];
        }

        if (designLens === 'ppiflow') {
            return [
                {
                    label: 'Delta Interface Sel',
                    value: formatMetricValue(selectedDesign.maturation_selected_delta_interface ?? selectedDesign.maturation_delta_interface ?? null, 3),
                    accentClass: 'text-emerald-300',
                },
                {
                    label: 'Interface Score Sel',
                    value: formatMetricValue(selectedDesign.maturation_selected_interface_score ?? selectedDesign.maturation_interface_score ?? null, 3),
                    accentClass: 'text-cyan-300',
                },
                {
                    label: 'RMSD Sel',
                    value: formatMetricValue(selectedDesign.maturation_selected_rmsd ?? selectedDesign.maturation_rmsd ?? null, 2, ' A'),
                    accentClass: 'text-amber-300',
                },
                {
                    label: 'RMSD Rest',
                    value: formatMetricValue(selectedDesign.maturation_nonselected_rmsd ?? null, 2, ' A'),
                    accentClass: 'text-violet-300',
                },
            ] satisfies StructureMetricCard[];
        }

        return buildStructureViewerSummaryCards({
            confidenceLabel: headlineConfidenceLabel,
            designLens,
            selectedDesign,
        }).map((card: StructureViewerSummaryCardSpec) => ({
            label: card.label,
            value: formatMetricValue(card.value, card.decimals, card.suffix ?? ''),
            accentClass: card.accentClass ?? (card.accentField ? getMetricColor(card.accentField, card.value) : 'text-slate-200'),
        })) satisfies StructureMetricCard[];
    })();
    const chainMetricChainCount = Object.keys(chainMetrics).length;
    const confidenceResidueCount = plddtProfile.length > 0
        ? plddtProfile.length
        : Object.values(chainMetrics).reduce((sum, metric) => sum + (metric?.length || 0), 0);
    const confidenceOverlayReadyCopy = conforNetsUsesScalarPlddtFallback
        ? `Uniform scalar pLDDT ${formatMetricValue(conforNetsScalarPlddt, 1)}; no residue tensor.`
        : `${headlineConfidenceLabel} overlay ready: ${bfactorLabel} residue/chain map.`;
    const interfacePairScoreCount = ipsaeInterface?.pair_scores?.length ?? 0;
    const chainPairIptmChainCount = chainPairIptm?.chain_ids?.length ?? 0;

    const overlayTabs = [
        { id: 'metrics', label: 'Metrics' },
        ...(hasResidueConfidence ? [{ id: 'plddt', label: bfactorLabel }] : []),
        ...(hasFampnnPsceProfile ? [{ id: 'psce', label: 'PSCE' }] : []),
        ...(hasPae ? [{ id: 'pae', label: 'PAE' }] : []),
    ] as Array<{ id: OverlayView; label: string }>;

    const plddtResidueColors = useMemo(() => {
        if (effectiveColorMode !== 'plddt') return undefined;
        return buildPlddtResidueColorMap({
            chainMetrics,
            plddtProfile,
            residueNumbers: residueMetricNumbers,
            fallbackChainId: conforNetsDefaultChainId,
            scalarPlddtFallback: conforNetsScalarPlddt,
            preferScalarFallback: conforNetsUsesScalarPlddtFallback,
            residueMask: rfaModifiableResidueMask,
            maskMode: rfaModifiableResidueMask ? 'include_only' : 'none',
            colorForValue: plddtColor,
        });
    }, [chainMetrics, conforNetsDefaultChainId, conforNetsScalarPlddt, conforNetsUsesScalarPlddtFallback, effectiveColorMode, plddtProfile, residueMetricNumbers, rfaModifiableResidueMask]);

    const frustrationResidueColors = (() => {
        if (effectiveColorMode !== 'frustration' || !selectedDesign?.frustration_residues?.length) return undefined;
        const colorMap = new Map<string, { r: number; g: number; b: number }>();
        for (const residue of selectedDesign.frustration_residues) {
            const chainId = residue.chain;
            if (!chainId) continue;
            const residueNumbers = chainMetrics[chainId]?.residue_numbers || [];
            const actualResidueNumber =
                residueNumbers[residue.pos] ??
                residueNumbers[residue.pos - 1] ??
                (typeof residue.pos === 'number' ? residue.pos + 1 : null);
            if (actualResidueNumber == null) continue;
            colorMap.set(residueColorKey(chainId, actualResidueNumber), frustrationColor(residue.frust));
        }
        return colorMap.size > 0 ? colorMap : undefined;
    })();

    const fampnnPsceResidueColors = (() => {
        if (effectiveColorMode !== 'fampnn_psce' || !hasFampnnPsceProfile) return undefined;
        const colorMap = new Map<string, { r: number; g: number; b: number }>();
        for (const chainId of fampnnPsceChainIds) {
            const chain = fampnnPsceChains[chainId];
            if (!chain) continue;
            for (let idx = 0; idx < chain.psce.length; idx++) {
                const residueNumber = chain.residue_numbers[idx];
                const value = chain.psce[idx];
                if (residueNumber == null || typeof value !== 'number') continue;
                colorMap.set(residueColorKey(chainId, residueNumber), fampnnPsceColor(value));
            }
        }
        return colorMap.size > 0 ? colorMap : undefined;
    })();

    const residueMetricLayer = useMemo<MolstarResidueMetricLayer | undefined>(() => {
        const source = selectedDesign?.id ? `design:${selectedDesign.id}` : 'design:unknown';
        if (effectiveColorMode === 'plddt' && plddtResidueColors) {
            const values = new Map<string, number>();
            for (const [chainId, metrics] of Object.entries(chainMetrics)) {
                metrics.residue_numbers.forEach((residueNumber, index) => {
                    const value = metrics.plddt[index];
                    if (Number.isFinite(value)) values.set(residueColorKey(chainId, residueNumber), value);
                });
            }
            if (conforNetsDefaultChainId) {
                residueMetricNumbers.forEach((residueNumber, index) => {
                    const value = plddtProfile[index];
                    if (Number.isFinite(value)) values.set(residueColorKey(conforNetsDefaultChainId, residueNumber), value);
                });
            }
            for (const key of plddtResidueColors.keys()) {
                if (!values.has(key) && Number.isFinite(conforNetsScalarPlddt)) {
                    values.set(key, conforNetsScalarPlddt as number);
                }
            }
            return buildMetricLayerFromExplicitMaps({
                descriptor: {
                    id: 'plddt',
                    label: bfactorLabel,
                    semanticType: 'confidence',
                    units: 'score',
                    direction: 'higher_is_better',
                    range: [0, 100],
                    source: 'BioModStack design analysis',
                    provenance: { source, method: 'persisted chain_metrics/residue confidence' },
                },
                colors: plddtResidueColors,
                values,
            });
        }
        if (effectiveColorMode === 'frustration' && frustrationResidueColors && selectedDesign?.frustration_residues) {
            const values = new Map<string, number>();
            for (const residue of selectedDesign.frustration_residues) {
                const residueNumbers = chainMetrics[residue.chain]?.residue_numbers || [];
                const residueNumber = residueNumbers[residue.pos] ?? residueNumbers[residue.pos - 1] ?? residue.pos + 1;
                values.set(residueColorKey(residue.chain, residueNumber), residue.frust);
            }
            return buildMetricLayerFromExplicitMaps({
                descriptor: {
                    id: 'frustration-index',
                    label: 'Frustration index',
                    semanticType: 'energy',
                    units: 'dimensionless',
                    direction: 'higher_is_better',
                    source: 'BioModStack design analysis',
                    provenance: { source, method: 'FrustraMPNN' },
                },
                colors: frustrationResidueColors,
                values,
            });
        }
        if (effectiveColorMode === 'fampnn_psce' && fampnnPsceResidueColors) {
            const values = new Map<string, number>();
            for (const chainId of fampnnPsceChainIds) {
                const chain = fampnnPsceChains[chainId];
                chain?.residue_numbers.forEach((residueNumber, index) => {
                    const value = chain.psce[index];
                    if (Number.isFinite(value)) values.set(residueColorKey(chainId, residueNumber), value);
                });
            }
            return buildMetricLayerFromExplicitMaps({
                descriptor: {
                    id: 'fampnn-psce',
                    label: 'FAMPNN pSCE',
                    semanticType: 'distance',
                    units: 'Å',
                    direction: 'lower_is_better',
                    source: 'BioModStack design analysis',
                    provenance: { source, method: 'FAMPNN' },
                },
                colors: fampnnPsceResidueColors,
                values,
            });
        }
        return undefined;
    }, [bfactorLabel, chainMetrics, conforNetsDefaultChainId, conforNetsScalarPlddt, effectiveColorMode, fampnnPsceChainIds, fampnnPsceChains, fampnnPsceResidueColors, frustrationResidueColors, plddtProfile, plddtResidueColors, residueMetricNumbers, selectedDesign]);

    const topFrustratedResidues = (() => {
        if (!selectedDesign?.frustration_residues?.length) return [];
        return [...selectedDesign.frustration_residues]
            .sort((a, b) => a.frust - b.frust)
            .slice(0, 8)
            .map((residue) => {
                const residueNumbers = chainMetrics[residue.chain]?.residue_numbers || [];
                const actualResidueNumber =
                    residueNumbers[residue.pos] ??
                    residueNumbers[residue.pos - 1] ??
                    (typeof residue.pos === 'number' ? residue.pos + 1 : residue.pos);
                return {
                    ...residue,
                    actualResidueNumber,
                };
            });
    })();

    const fampnnPscePlot = useMemo<{ data: Data[]; layout: Partial<Layout> } | null>(() => {
        if (!selectedDesign || !hasFampnnPsceProfile) return null;
        const visibleChainIds = selectedChain && fampnnPsceChains[selectedChain]
            ? [selectedChain]
            : fampnnPsceChainIds;
        if (!visibleChainIds.length) return null;

        const traces: Data[] = visibleChainIds.map((chainId, idx) => {
            const chain = fampnnPsceChains[chainId];
            const residueNumbers = chain?.residue_numbers || [];
            const psce = chain?.psce || [];
            const residueNames = chain?.residue_names || [];
            return {
                type: 'scatter',
                mode: 'lines+markers',
                name: `Chain ${chainId}`,
                x: residueNumbers,
                y: psce,
                line: { color: CHAIN_ACCENT_COLORS[idx % CHAIN_ACCENT_COLORS.length], width: selectedChain ? 1.8 : 1.4 },
                marker: {
                    size: selectedChain ? 6 : 4,
                    color: psce.map((value) => fampnnPsceColorHex(value)),
                    line: { color: '#0f172a', width: 0.5 },
                },
                customdata: psce.map((value, residueIdx) => [chainId, residueNames[residueIdx] || '', fampnnPsceTierLabel(value)]),
                hovertemplate: 'Chain %{customdata[0]}<br>Residue %{x} %{customdata[1]}<br>pSCE: %{y:.2f} Å<br>Tier: %{customdata[2]}<extra></extra>',
            } as Data;
        });

        const allResidueNumbers = visibleChainIds.flatMap((chainId) => fampnnPsceChains[chainId]?.residue_numbers || []);
        const allScores = visibleChainIds.flatMap((chainId) => fampnnPsceChains[chainId]?.psce || []);
        const minResidue = allResidueNumbers.length ? Math.min(...allResidueNumbers) : 0;
        const maxResidue = allResidueNumbers.length ? Math.max(...allResidueNumbers) : 1;
        const maxScore = allScores.length ? Math.max(...allScores) : 0;
        const yMax = Math.max(2.0, Math.ceil(maxScore * 2) / 2);

        return {
            data: traces,
            layout: {
                paper_bgcolor: 'transparent',
                plot_bgcolor: '#0f172a',
                font: { color: '#e2e8f0' },
                margin: { l: 56, r: 20, t: 34, b: 46 },
                showlegend: visibleChainIds.length > 1,
                legend: { orientation: 'h', y: 1.12, font: { color: '#94a3b8' } },
                xaxis: {
                    title: { text: 'Residue Number', font: { color: '#94a3b8' } },
                    gridcolor: '#1e293b',
                    color: '#94a3b8',
                },
                yaxis: {
                    title: { text: 'pSCE (Å)', font: { color: '#94a3b8' } },
                    gridcolor: '#1e293b',
                    color: '#94a3b8',
                    range: [0, yMax],
                },
                shapes: [0.9, 1.2, 1.6].map((threshold, idx) => ({
                    type: 'line',
                    x0: minResidue,
                    x1: maxResidue,
                    y0: threshold,
                    y1: threshold,
                    line: {
                        color: ['#34d399', '#38bdf8', '#f59e0b'][idx],
                        width: 1,
                        dash: 'dash',
                    },
                })),
            },
        };
    }, [fampnnPsceChainIds, fampnnPsceChains, hasFampnnPsceProfile, selectedChain, selectedDesign]);

    // Draw PAE heatmap on canvas
    useEffect(() => {
        if (overlayView !== 'pae' || !paeMatrix || !canvasRef.current) return;

        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const size = Math.min(paeMatrix.length, 250);
        canvas.width = size;
        canvas.height = size;

        const scale = paeMatrix.length / size;

        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                const srcY = Math.floor(y * scale);
                const srcX = Math.floor(x * scale);
                const value = paeMatrix[srcY]?.[srcX] ?? 0;

                // Color: green (low) -> white (mid) -> red (high)
                const maxVal = 30;
                const norm = Math.min(value / maxVal, 1);
                const r = norm < 0.5 ? Math.round(norm * 2 * 255) : 255;
                const g = norm < 0.5 ? 255 : Math.round((1 - (norm - 0.5) * 2) * 255);
                const b = Math.round((1 - norm) * 100);

                ctx.fillStyle = `rgb(${r},${g},${b})`;
                ctx.fillRect(x, y, 1, 1);
            }
        }
    }, [overlayView, paeMatrix]);

    // Toggle fullscreen using native browser API
    const toggleFullscreen = useCallback(() => {
        if (!containerRef.current) return;

        if (!document.fullscreenElement) {
            containerRef.current.requestFullscreen().catch(err => {
                console.error('Failed to enter fullscreen:', err);
            });
        } else {
            document.exitFullscreen();
        }
    }, []);

    // Listen to fullscreen changes
    useEffect(() => {
        const handleFullscreenChange = () => {
            setIsFullscreen(!!document.fullscreenElement);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    useEffect(() => {
        const handlePointerMove = (event: MouseEvent) => {
            if (referenceDragRef.current) {
                const drag = referenceDragRef.current;
                setReferenceWindow((current) => clampReferenceWindow({
                    ...current,
                    x: drag.startX + (event.clientX - drag.pointerX),
                    y: drag.startY + (event.clientY - drag.pointerY),
                }));
            } else if (referenceResizeRef.current) {
                const resize = referenceResizeRef.current;
                setReferenceWindow((current) => clampReferenceWindow({
                    ...current,
                    width: resize.startWidth + (event.clientX - resize.pointerX),
                    height: resize.startHeight + (event.clientY - resize.pointerY),
                }));
            }
        };

        const handlePointerUp = () => {
            referenceDragRef.current = null;
            referenceResizeRef.current = null;
        };

        window.addEventListener('mousemove', handlePointerMove);
        window.addEventListener('mouseup', handlePointerUp);
        return () => {
            window.removeEventListener('mousemove', handlePointerMove);
            window.removeEventListener('mouseup', handlePointerUp);
        };
    }, [clampReferenceWindow]);

    const openReferenceSelector = useCallback(() => {
        setReferenceDockMode('selector');
        setShowReferenceDock(true);
        requestAnimationFrame(() => positionReferenceWindow('selector'));
    }, [positionReferenceWindow]);

    const showSelectedReference = useCallback((reference: ReferenceStructure) => {
        setSelectedReference(reference);
        setReferenceDockMode('viewer');
        setShowReferenceDock(true);
        requestAnimationFrame(() => positionReferenceWindow('viewer'));
    }, [positionReferenceWindow]);

    const closeReferenceDock = useCallback(() => {
        setShowReferenceDock(false);
        setSelectedReference(null);
        setReferenceDockMode('selector');
    }, []);

    const startReferenceDrag = useCallback((event: ReactMouseEvent<HTMLDivElement>) => {
        event.preventDefault();
        referenceDragRef.current = {
            pointerX: event.clientX,
            pointerY: event.clientY,
            startX: referenceWindow.x,
            startY: referenceWindow.y,
        };
    }, [referenceWindow.x, referenceWindow.y]);

    const startReferenceResize = useCallback((event: ReactMouseEvent<HTMLButtonElement>) => {
        event.preventDefault();
        event.stopPropagation();
        referenceResizeRef.current = {
            pointerX: event.clientX,
            pointerY: event.clientY,
            startWidth: referenceWindow.width,
            startHeight: referenceWindow.height,
        };
    }, [referenceWindow.height, referenceWindow.width]);
    const quickViewById = useMemo(
        () => new Map(viewerQuickViews.map((quickView) => [quickView.id, quickView] as const)),
        [viewerQuickViews],
    );
    const renderSectionButtons = useCallback((compact = false) => (
        <div className={`flex flex-wrap gap-1 ${compact ? '' : 'mb-3'}`}>
            {viewerSections.map((section) => {
                const quickView = quickViewById.get(section.id);
                const active = quickView ? isQuickViewActive(quickView) : false;
                return (
                    <button
                        key={section.id}
                        type="button"
                        onClick={() => quickView && applyQuickView(quickView)}
                        disabled={!quickView}
                        className={`rounded border px-2 py-1 text-[10px] uppercase tracking-wider transition-colors ${active
                            ? 'border-blue-500/50 bg-blue-500/15 text-blue-200'
                            : 'border-slate-700/60 bg-slate-900/50 text-slate-400 hover:border-slate-600 hover:text-slate-200'
                            }`}
                    >
                        {section.label}
                    </button>
                );
            })}
        </div>
    ), [applyQuickView, isQuickViewActive, quickViewById, viewerSections]);
    const renderQuickViewBar = useCallback((compact = false) => (
        <div className={`flex flex-wrap items-center gap-1 ${compact ? '' : 'ml-1'}`}>
            {viewerQuickViews.map((quickView) => {
                const active = isQuickViewActive(quickView);
                return (
                    <button
                        key={quickView.id}
                        type="button"
                        onClick={() => applyQuickView(quickView)}
                        className={`rounded-lg border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${active
                            ? 'border-blue-400/60 bg-blue-500/20 text-blue-100'
                            : compact
                                ? 'border-slate-700/70 bg-slate-900/75 text-slate-300 hover:border-slate-500 hover:text-white'
                                : 'border-slate-700 bg-slate-800 text-slate-300 hover:border-slate-500 hover:text-white'
                            }`}
                        title={`Focus ${quickView.label.toLowerCase()} signals`}
                    >
                        {quickView.label}
                    </button>
                );
            })}
        </div>
    ), [applyQuickView, isQuickViewActive, viewerQuickViews]);

    // Toggleable Analytics Panel for fullscreen
    const renderFullscreenOverlay = () => (
        <div
            data-structure-viewer-fullscreen-analytics-layout={fullscreenAnalyticsLayout.mode}
            className={fullscreenAnalyticsLayout.panelClassName}
            style={{ maxHeight: fullscreenAnalyticsLayout.panelMaxHeight }}
        >
            {/* Tab Header */}
            <div className="flex shrink-0 border-b border-slate-700/50">
                <div className="flex min-w-0 flex-1">
                    {overlayTabs.map(tab => (
                        <button
                            key={tab.id}
                            onClick={() => handleOverlayTabClick(tab.id as OverlayView)}
                            className={`min-w-0 flex-1 px-3 py-2 text-xs font-medium transition-colors ${overlayView === tab.id
                                ? 'bg-blue-500/20 text-blue-400 border-b-2 border-blue-400'
                                : 'text-slate-400 hover:text-white hover:bg-slate-800/50'
                                }`}
                        >
                            <span className="truncate">{tab.label}</span>
                        </button>
                    ))}
                </div>
                <button
                    type="button"
                    aria-label="Close analytics panel"
                    onClick={() => setAnalyticsPanelOpen(false)}
                    className="border-l border-slate-700/50 px-3 py-2 text-xs font-semibold text-slate-300 transition-colors hover:bg-slate-800/70 hover:text-white"
                    title="Close analytics panel"
                >
                    ✕
                </button>
            </div>

            {/* Content */}
            <div className={fullscreenAnalyticsLayout.contentClassName}>
                {overlayView === 'metrics' && (
                    <div className="space-y-3">
                        {/* Design Title */}
                        {selectedDesign && (
                            <div>
                                <h3 className="font-medium text-white/90 truncate text-sm">{selectedDesign.name}</h3>
                                <div className="text-xs text-slate-400/80 flex items-center gap-2 flex-wrap">
                                    {activeJob?.model_id} • {new Date(selectedDesign.created_at).toLocaleDateString()}
                                    {designOrigin && (
                                        <span className="px-1.5 py-0.5 rounded bg-slate-700/70 border border-slate-600/60 text-[10px] uppercase tracking-wider text-slate-300">
                                            {designOrigin}
                                        </span>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Key Metrics Grid */}
                        {selectedDesign && (
                            <>
                                {stageGuidance && (
                                    <div className="rounded border border-slate-700/70 bg-slate-900/60 px-2 py-2 text-[11px] leading-5 text-slate-300">
                                        {stageGuidance}
                                    </div>
                                )}
                                {renderSectionButtons(true)}
                                {designLens === 'rfantibody' && setRfMetricScope && (
                                    <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-violet-500/20 bg-violet-500/5 px-2 py-2">
                                        <div className="text-[10px] uppercase tracking-wider text-violet-200">RF Lens</div>
                                        <div className="inline-flex rounded-md border border-slate-700/70 bg-slate-950/60 p-1">
                                            {(['cdr_loops', 'whole_antibody'] as RfScreeningScope[]).map((scope) => (
                                                <button
                                                    key={scope}
                                                    type="button"
                                                    onClick={() => setRfMetricScope(scope)}
                                                    className={`rounded px-2 py-1 text-[10px] transition-colors ${effectiveRfMetricScope === scope ? 'bg-violet-500/20 text-violet-100' : 'text-slate-400 hover:text-slate-200'}`}
                                                >
                                                    {RF_SCOPE_LABELS[scope].short}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                                    {metricSectionTitle}
                                </div>
                                <div className="grid grid-cols-2 gap-2">
                                    {structureMetricCards.map((metric) => (
                                        <div key={metric.label} className="bg-slate-800/40 rounded p-2 text-center">
                                            <div className={`text-lg font-bold ${metric.accentClass}`}>
                                                {metric.value}
                                            </div>
                                            <div className="text-[10px] text-slate-500">{metric.label}</div>
                                        </div>
                                    ))}
                                </div>
                                {designLens === 'rfantibody' && rfLoopEntries.length > 0 && (
                                    <div className="space-y-2">
                                        <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Loop Triage</div>
                                        <div className="grid grid-cols-1 gap-2">
                                            {rfLoopEntries.map(({ loopId, metrics }) => (
                                                <div key={loopId} className={`rounded border px-2 py-2 text-[11px] ${metrics.redesign_candidate ? 'border-amber-500/30 bg-amber-500/10 text-amber-100' : 'border-slate-700/60 bg-slate-900/40 text-slate-300'}`}>
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="font-semibold text-white">{loopId}</span>
                                                        <span>{metrics.screening_status || metrics.screening_note || 'ok'}</span>
                                                    </div>
                                                    <div className="mt-1 text-slate-400">
                                                        {metrics.epitope_contact_count ?? '—'} epi cts • {metrics.target_contact_count ?? '—'} tgt cts • {metrics.target_min_distance != null ? `${metrics.target_min_distance.toFixed(1)} A` : '—'}
                                                    </div>
                                                    {metrics.screening_note && metrics.screening_note !== metrics.screening_status && (
                                                        <div className="mt-1 text-[10px] text-slate-500">{metrics.screening_note}</div>
                                                    )}
                                                    {metrics.redesign_candidate && (
                                                        <div className="mt-1 text-[10px] text-amber-200">Flagged for redesign triage.</div>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </>
                        )}

                        {/* Structure Analysis */}
                        {structureAnalysis && (
                            <div className="text-xs text-slate-400 space-y-1">
                                <div className="flex justify-between">
                                    <span>Residues</span>
                                    <span className="text-white font-mono">{structureAnalysis.residue_count}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span>Chains</span>
                                    <span className="text-white font-mono">{structureAnalysis.chain_ids?.length ?? 0}</span>
                                </div>
                                {structureAnalysis.secondary_structure && (
                                    <div className="flex justify-between">
                                        <span>Secondary</span>
                                        <span>
                                            <span className="text-accent-secondary">α{structureAnalysis.secondary_structure.helix?.toFixed(0)}%</span>
                                            {' '}
                                            <span className="text-yellow-400">β{structureAnalysis.secondary_structure.sheet?.toFixed(0)}%</span>
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}
                        {!structureAnalysis && (
                            <div className="rounded border border-slate-700/60 bg-slate-900/40 p-2 text-[11px] text-slate-400">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="font-semibold uppercase tracking-wider text-slate-500">Structure Analysis</div>
                                        <div className="mt-1">{structureAnalysisStatusCopy}</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={onRunStructureAnalysis}
                                        disabled={!onRunStructureAnalysis || !!structureAnalysisBusy}
                                        className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${structureAnalysisBusy
                                            ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                            : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20'
                                            }`}
                                    >
                                        {structureAnalysisBusy ? 'Running…' : 'Run'}
                                    </button>
                                </div>
                                {structureAnalysisRun?.error_message && (
                                    <div className="mt-2 text-[10px] text-rose-300">Last error: {structureAnalysisRun.error_message}</div>
                                )}
                            </div>
                        )}
                        <div className="rounded border border-slate-700/60 bg-slate-900/30 p-2 text-[11px] text-slate-400">
                            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Auxiliary Analyses</div>
                            <div className="mt-2 space-y-2">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-slate-200">Chain Metrics</div>
                                        <div className="text-[10px] text-slate-500">{chainMetricsStatusCopy}</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={onRunChainMetrics}
                                        disabled={!onRunChainMetrics || chainMetricsBusy}
                                        className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${chainMetricsBusy
                                            ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                            : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20'
                                            }`}
                                    >
                                        {chainMetricsBusy ? 'Running…' : Object.keys(chainMetrics).length ? 'Refresh' : 'Run'}
                                    </button>
                                </div>
                                {chainMetricsRun?.error_message && (
                                    <div className="text-[10px] text-rose-300">Last chain-metrics error: {chainMetricsRun.error_message}</div>
                                )}
                                {designLens === 'fampnn' && (
                                    <>
                                        <div className="flex items-center justify-between gap-3">
                                            <div>
                                                <div className="text-slate-200">FA-MPNN PSCE Profile</div>
                                                <div className="text-[10px] text-slate-500">{fampnnPsceStatusCopy}</div>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={onRunFampnnPsceProfile}
                                                disabled={!onRunFampnnPsceProfile || fampnnPsceBusy}
                                                className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${fampnnPsceBusy
                                                    ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                                    : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20'
                                                    }`}
                                            >
                                                {fampnnPsceBusy ? 'Running…' : hasFampnnPsceProfile ? 'Refresh' : 'Run'}
                                            </button>
                                        </div>
                                        {fampnnPsceProfileRun?.error_message && (
                                            <div className="text-[10px] text-rose-300">Last PSCE-profile error: {fampnnPsceProfileRun.error_message}</div>
                                        )}
                                    </>
                                )}
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-slate-200">PAE Matrix</div>
                                        <div className="text-[10px] text-slate-500">{paeStatusCopy}</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={onRunPaeMatrix}
                                        disabled={!onRunPaeMatrix || paeBusy}
                                        className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${paeBusy
                                            ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                            : 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-300 hover:bg-fuchsia-500/20'
                                            }`}
                                    >
                                        {paeBusy ? 'Running…' : paeMatrix ? 'Refresh' : 'Run'}
                                    </button>
                                </div>
                                {paeRun?.error_message && (
                                    <div className="text-[10px] text-rose-300">Last PAE error: {paeRun.error_message}</div>
                                )}
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-slate-200">Interface ipSAE</div>
                                        <div className="text-[10px] text-slate-500">{ipsaeInterfaceStatusCopy}</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={onRunIpsaeInterface}
                                        disabled={!onRunIpsaeInterface || ipsaeInterfaceBusy}
                                        className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${ipsaeInterfaceBusy
                                            ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                            : 'border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20'
                                            }`}
                                    >
                                        {ipsaeInterfaceBusy ? 'Running…' : hasIpsaeInterface ? 'Refresh' : 'Run'}
                                    </button>
                                </div>
                                {ipsaeInterfaceRun?.error_message && (
                                    <div className="text-[10px] text-rose-300">Last interface-ipSAE error: {ipsaeInterfaceRun.error_message}</div>
                                )}
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-slate-200">Contact Map</div>
                                        <div className="text-[10px] text-slate-500">{contactMapStatusCopy}</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={onRunContactMap}
                                        disabled={!onRunContactMap || contactMapBusy}
                                        className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${contactMapBusy
                                            ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                            : 'border-rose-500/40 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20'
                                            }`}
                                    >
                                        {contactMapBusy ? 'Running…' : hasContactMap ? 'Refresh' : 'Run'}
                                    </button>
                                </div>
                                {contactMapRun?.error_message && (
                                    <div className="text-[10px] text-rose-300">Last contact-map error: {contactMapRun.error_message}</div>
                                )}
                                {(chainPairIptmLoading || hasChainPairIptm) && (
                                    <div className="rounded border border-slate-700/60 bg-slate-900/40 px-2 py-2 text-[10px] text-slate-400">
                                        {chainPairIptmLoading
                                            ? 'Loading chain-pair iPTM matrix…'
                                            : `Chain-pair iPTM matrix ready for ${chainPairIptm?.chain_ids?.length ?? 0} chains.`}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}

                {overlayView === 'plddt' && (
                    <div>
                        {/* Chain Filter Toggle */}
                        <div className="flex items-center gap-1 mb-2 flex-wrap">
                            <span className="text-xs text-slate-400 mr-1">Chain:</span>
                            <button
                                onClick={() => setSelectedChain(null)}
                                className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === null
                                    ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                                    : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                    }`}
                            >
                                All
                            </button>
                            {Object.keys(chainMetrics).sort().map((chainId, idx) => (
                                <button
                                    key={chainId}
                                    onClick={() => setSelectedChain(chainId)}
                                    className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === chainId
                                        ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                        }`}
                                    style={{ borderLeft: `2px solid ${['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}` }}
                                >
                                    {chainId} ({chainMetrics[chainId]?.length || 0})
                                </button>
                            ))}
                        </div>

                        {/* Chart */}
                        {plddtProfile.length > 0 ? (
                            <div className="h-36 relative bg-slate-800/40 rounded overflow-hidden">
                                <svg viewBox={`0 0 ${plddtProfile.length} 100`} className="w-full h-full" preserveAspectRatio="none">
                                    {/* Grid lines */}
                                    <line x1="0" y1="10" x2={plddtProfile.length} y2="10" stroke="#334155" strokeWidth="0.5" />
                                    <line x1="0" y1="30" x2={plddtProfile.length} y2="30" stroke="#334155" strokeWidth="0.5" />
                                    <line x1="0" y1="50" x2={plddtProfile.length} y2="50" stroke="#334155" strokeWidth="0.5" />

                                    {/* Chain boundary lines */}
                                    {chainBoundaries.map((chain, idx) => (
                                        chain.start > 0 && (
                                            <line
                                                key={chain.id}
                                                x1={chain.start}
                                                y1="0"
                                                x2={chain.start}
                                                y2="100"
                                                stroke={['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}
                                                strokeWidth="1"
                                                strokeDasharray="2,2"
                                            />
                                        )
                                    ))}

                                    {/* Area fill - highlight selected chain or show all */}
                                    {selectedChain === null ? (
                                        <path
                                            d={`M0,100 ${plddtProfile.map((v, i) => `L${i},${100 - v}`).join(' ')} L${plddtProfile.length - 1},100 Z`}
                                            fill="url(#plddtGradient)"
                                            opacity="0.3"
                                        />
                                    ) : (
                                        chainBoundaries.filter(c => c.id === selectedChain).map(chain => (
                                            <path
                                                key={chain.id}
                                                d={`M${chain.start},100 ${plddtProfile.slice(chain.start, chain.end).map((v, i) => `L${chain.start + i},${100 - v}`).join(' ')} L${chain.end - 1},100 Z`}
                                                fill="url(#plddtGradient)"
                                                opacity="0.5"
                                            />
                                        ))
                                    )}

                                    {/* Line - dim non-selected chains */}
                                    <polyline
                                        points={plddtProfile.map((v, i) => `${i},${100 - v}`).join(' ')}
                                        fill="none"
                                        stroke="#3b82f6"
                                        strokeWidth="1"
                                        opacity={selectedChain === null ? 1 : 0.2}
                                    />

                                    {/* Highlighted chain line */}
                                    {selectedChain && chainBoundaries.filter(c => c.id === selectedChain).map((chain) => (
                                        <polyline
                                            key={chain.id}
                                            points={plddtProfile.slice(chain.start, chain.end).map((v, i) => `${chain.start + i},${100 - v}`).join(' ')}
                                            fill="none"
                                            stroke={['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][Object.keys(chainMetrics).sort().indexOf(selectedChain) % 5]}
                                            strokeWidth="1.5"
                                        />
                                    ))}

                                    <defs>
                                        <linearGradient id="plddtGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                                            <stop offset="0%" stopColor="#3b82f6" />
                                            <stop offset="100%" stopColor="#1e3a5f" />
                                        </linearGradient>
                                    </defs>
                                </svg>

                                {/* Y-axis labels */}
                                <div className="absolute left-1 top-0 text-[8px] text-slate-500">90</div>
                                <div className="absolute left-1 top-1/2 text-[8px] text-slate-500">50</div>
                                <div className="absolute left-1 bottom-0 text-[8px] text-slate-500">0</div>
                            </div>
                        ) : (
                            <div className="h-36 flex items-center justify-center text-slate-500 text-xs bg-slate-800/40 rounded">
                                No {bfactorLabel} profile data available
                            </div>
                        )}
                        <div className="text-[10px] text-slate-500 mt-1 text-center">
                            {selectedChain
                                ? `Chain ${selectedChain}: ${chainMetrics[selectedChain]?.length || 0} residues • Mean: ${chainMetrics[selectedChain]?.avg_plddt?.toFixed(1) || '—'}`
                                : `${plddtProfile.length} residues • Mean: ${plddtProfile.length > 0 ? (plddtProfile.reduce((a, b) => a + b, 0) / plddtProfile.length).toFixed(1) : '—'}`
                            }
                        </div>
                    </div>
                )}

                {overlayView === 'psce' && (
                    <div>
                        <div className="flex items-center gap-1 mb-2 flex-wrap">
                            <span className="text-xs text-slate-400 mr-1">Chain:</span>
                            <button
                                onClick={() => setSelectedChain(null)}
                                className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === null
                                    ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/40'
                                    : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                    }`}
                            >
                                All
                            </button>
                            {fampnnPsceChainIds.map((chainId, idx) => (
                                <button
                                    key={chainId}
                                    onClick={() => setSelectedChain(chainId)}
                                    className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === chainId
                                        ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/40'
                                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                        }`}
                                    style={{ borderLeft: `2px solid ${CHAIN_ACCENT_COLORS[idx % CHAIN_ACCENT_COLORS.length]}` }}
                                >
                                    {chainId} ({fampnnPsceChains[chainId]?.length || 0})
                                </button>
                            ))}
                        </div>
                        {fampnnPsceProfileValues.length > 0 ? (
                            <div className="h-36 relative bg-slate-800/40 rounded overflow-hidden">
                                <svg viewBox={`0 0 ${Math.max(fampnnPsceProfileValues.length, 1)} 100`} className="w-full h-full" preserveAspectRatio="none">
                                    {[0.9, 1.2, 1.6].map((threshold, idx) => (
                                        <line
                                            key={threshold}
                                            x1="0"
                                            y1={(threshold / fampnnPsceChartMax) * 100}
                                            x2={Math.max(fampnnPsceProfileValues.length, 1)}
                                            y2={(threshold / fampnnPsceChartMax) * 100}
                                            stroke={['#34d399', '#38bdf8', '#f59e0b'][idx]}
                                            strokeWidth="0.6"
                                            strokeDasharray="2,2"
                                        />
                                    ))}
                                    {fampnnPsceBoundaries.map((chain, idx) => (
                                        chain.start > 0 && (
                                            <line
                                                key={chain.id}
                                                x1={chain.start}
                                                y1="0"
                                                x2={chain.start}
                                                y2="100"
                                                stroke={CHAIN_ACCENT_COLORS[idx % CHAIN_ACCENT_COLORS.length]}
                                                strokeWidth="1"
                                                strokeDasharray="2,2"
                                            />
                                        )
                                    ))}
                                    <polyline
                                        points={fampnnPsceProfileValues.map((value, idx) => `${idx},${(value / fampnnPsceChartMax) * 100}`).join(' ')}
                                        fill="none"
                                        stroke="#38bdf8"
                                        strokeWidth="1"
                                        opacity={selectedChain === null ? 0.95 : 0.25}
                                    />
                                    {selectedChain && fampnnPsceBoundaries.filter((chain) => chain.id === selectedChain).map((chain) => {
                                        const chainIndex = fampnnPsceChainIds.indexOf(selectedChain);
                                        const chainValues = fampnnPsceChains[selectedChain]?.psce || [];
                                        return (
                                            <polyline
                                                key={chain.id}
                                                points={chainValues.map((value, idx) => `${chain.start + idx},${(value / fampnnPsceChartMax) * 100}`).join(' ')}
                                                fill="none"
                                                stroke={CHAIN_ACCENT_COLORS[(chainIndex + CHAIN_ACCENT_COLORS.length) % CHAIN_ACCENT_COLORS.length]}
                                                strokeWidth="1.8"
                                            />
                                        );
                                    })}
                                </svg>
                                <div className="absolute left-1 top-0 text-[8px] text-slate-500">0</div>
                                <div className="absolute left-1 top-1/2 text-[8px] text-slate-500">{(fampnnPsceChartMax / 2).toFixed(1)}</div>
                                <div className="absolute left-1 bottom-0 text-[8px] text-slate-500">{fampnnPsceChartMax.toFixed(1)}</div>
                            </div>
                        ) : (
                            <div className="h-36 flex items-center justify-center text-slate-500 text-xs bg-slate-800/40 rounded">
                                No PSCE profile data available
                            </div>
                        )}
                        <div className="text-[10px] text-slate-500 mt-1 text-center">
                            {selectedChain && fampnnPsceChains[selectedChain]
                                ? `Chain ${selectedChain}: ${fampnnPsceChains[selectedChain]?.length || 0} residues • Mean: ${fampnnPsceChains[selectedChain]?.avg_psce?.toFixed(2) || '—'} • Max: ${fampnnPsceChains[selectedChain]?.max_psce?.toFixed(2) || '—'}`
                                : `${fampnnPsceProfileValues.length} scored residues • Mean: ${fampnnPsceProfileValues.length > 0 ? (fampnnPsceProfileValues.reduce((sum, value) => sum + value, 0) / fampnnPsceProfileValues.length).toFixed(2) : '—'} • Worst: ${fampnnPsceProfileValues.length > 0 ? Math.max(...fampnnPsceProfileValues).toFixed(2) : '—'}`
                            }
                        </div>
                        <div className="text-[10px] text-slate-500 mt-1 text-center">Lower is better</div>
                    </div>
                )}

                {overlayView === 'pae' && (
                    <div>
                        <div className="text-xs text-slate-400 mb-2">Predicted Aligned Error Matrix</div>
                        {paeMatrix ? (
                            <div className="flex flex-col items-center">
                                {/* Canvas container with chain labels */}
                                <div className="relative">
                                    <canvas
                                        ref={canvasRef}
                                        className="rounded border border-slate-700"
                                        style={{ width: '220px', height: '220px', imageRendering: 'pixelated' }}
                                    />

                                    {/* Chain boundary labels on X-axis (bottom) */}
                                    <div className="absolute -bottom-4 left-0 right-0 flex" style={{ height: '16px' }}>
                                        {chainBoundaries.map((chain, idx) => {
                                            const totalResidues = paeMatrix.length;
                                            const leftPct = (chain.start / totalResidues) * 100;
                                            const widthPct = ((chain.end - chain.start) / totalResidues) * 100;
                                            return (
                                                <div
                                                    key={chain.id}
                                                    className="absolute text-[8px] text-slate-400 font-mono flex items-center justify-center"
                                                    style={{
                                                        left: `${leftPct}%`,
                                                        width: `${widthPct}%`,
                                                        borderTop: `2px solid ${['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}`,
                                                    }}
                                                    title={`Chain ${chain.id}: residues ${chain.start + 1}-${chain.end}`}
                                                >
                                                    {chain.id}
                                                </div>
                                            );
                                        })}
                                    </div>

                                    {/* Chain boundary labels on Y-axis (left) */}
                                    <div className="absolute -left-3 top-0 bottom-0 flex flex-col" style={{ width: '12px' }}>
                                        {chainBoundaries.map((chain, idx) => {
                                            const totalResidues = paeMatrix.length;
                                            const topPct = (chain.start / totalResidues) * 100;
                                            const heightPct = ((chain.end - chain.start) / totalResidues) * 100;
                                            return (
                                                <div
                                                    key={chain.id}
                                                    className="absolute text-[8px] text-slate-400 font-mono flex items-center justify-center"
                                                    style={{
                                                        top: `${topPct}%`,
                                                        height: `${heightPct}%`,
                                                        borderRight: `2px solid ${['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}`,
                                                        writingMode: 'vertical-lr',
                                                        transform: 'rotate(180deg)',
                                                    }}
                                                    title={`Chain ${chain.id}: residues ${chain.start + 1}-${chain.end}`}
                                                >
                                                    {chain.id}
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>

                                <div className="flex items-center gap-2 mt-5 text-[10px] text-slate-400">
                                    <span>Low</span>
                                    <div className="w-20 h-2 rounded" style={{ background: 'linear-gradient(to right, #00ff00, #ffffff, #ff0000)' }} />
                                    <span>High</span>
                                </div>

                                {/* Chain legend */}
                                <div className="flex items-center gap-2 mt-1 flex-wrap justify-center">
                                    {chainBoundaries.map((chain, idx) => (
                                        <span key={chain.id} className="text-[9px] text-slate-400">
                                            <span
                                                className="inline-block w-2 h-2 rounded-sm mr-0.5"
                                                style={{ backgroundColor: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5] }}
                                            />
                                            {chain.id}:{chain.end - chain.start}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <div className="h-40 flex flex-col items-center justify-center gap-3 text-slate-500 text-xs bg-slate-800/40 rounded px-4 text-center">
                                <div>No cached PAE matrix available.</div>
                                <div className="uppercase tracking-wider text-[10px]">{paeStatusCopy}</div>
                                <button
                                    type="button"
                                    onClick={onRunPaeMatrix}
                                    disabled={!onRunPaeMatrix || paeBusy}
                                    className={`rounded border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${paeBusy
                                        ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                        : 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-300 hover:bg-fuchsia-500/20'
                                        }`}
                                >
                                    {paeBusy ? 'Running…' : 'Run PAE'}
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );

    const analyticsSidebarSpacingClass = viewerLayout.isStacked ? 'space-y-3' : 'space-y-4';
    const analyticsSidebarWidthClass = viewerLayout.isStacked ? 'w-full min-w-0' : 'flex-1 min-w-[280px] max-w-[400px]';
    const analyticsPanelPaddingClass = viewerLayout.isStacked ? 'p-3' : 'p-4';
    const analyticsSectionGridClass = viewerLayout.isStacked ? 'grid grid-cols-2 gap-2' : 'grid grid-cols-2 gap-3';
    const analyticsStatCardClass = viewerLayout.isStacked
        ? 'rounded-lg bg-slate-900/50 px-2.5 py-2.5 text-center'
        : 'rounded-lg bg-slate-900/50 px-3 py-3 text-center';
    const analyticsHeadlineMetricClass = viewerLayout.isStacked ? 'text-xl font-bold' : 'text-2xl font-bold';
    const analyticsSecondaryValueClass = viewerLayout.isStacked ? 'text-base font-semibold' : 'text-lg font-semibold';

    // Full sidebar for normal mode
    const renderAnalyticsSidebar = () => (
        <div
            data-structure-viewer-analytics-layout={viewerLayout.isStacked ? 'stacked' : 'sidebar'}
            className={`${analyticsSidebarWidthClass} ${analyticsSidebarSpacingClass}`}
        >
            <div className="flex justify-end">
                <button
                    type="button"
                    aria-label="Close analytics panel"
                    onClick={() => setAnalyticsPanelOpen(false)}
                    className="rounded-lg border border-slate-700/70 bg-slate-900/50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 transition-colors hover:border-slate-500 hover:text-white"
                    title="Close analytics panel"
                >
                    Close Analytics
                </button>
            </div>
            {/* Design Title */}
            {selectedDesign && (
                <div className={`bg-slate-800/50 rounded-lg border border-slate-700/50 ${analyticsPanelPaddingClass}`}>
                    <h3 className="font-medium text-white truncate mb-2">{selectedDesign.name}</h3>
                    <div className="text-xs text-slate-400 flex items-center gap-2 flex-wrap">
                        {activeJob?.model_id} • {new Date(selectedDesign.created_at).toLocaleDateString()}
                        {designOrigin && (
                            <span className="px-1.5 py-0.5 rounded bg-slate-700/70 border border-slate-600/60 text-[10px] uppercase tracking-wider text-slate-300">
                                {designOrigin}
                            </span>
                        )}
                    </div>
                </div>
            )}

            {/* Key Metrics */}
            {selectedDesign && (
                <div className={`rounded-lg border ${analyticsPanelPaddingClass} ${focusedMetricSection === 'summary' ? 'border-blue-500/40 bg-blue-500/5' : 'border-slate-700/50 bg-slate-800/50'}`}>
                    <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Summary</h4>
                            <div className="mt-1 text-[11px] text-slate-500">{metricSectionTitle}</div>
                        </div>
                        <div className="rounded border border-slate-700/70 bg-slate-900/60 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">
                            Headline metrics
                        </div>
                    </div>
                    {stageGuidance && (
                        <div className="mb-3 rounded-lg border border-slate-700/70 bg-slate-900/60 px-3 py-2 text-[11px] leading-5 text-slate-300">
                            {stageGuidance}
                        </div>
                    )}
                    {renderSectionButtons(viewerLayout.isStacked)}
                    {designLens === 'rfantibody' && setRfMetricScope && (
                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-violet-500/20 bg-violet-500/5 px-3 py-2">
                            <div className="text-[10px] uppercase tracking-wider text-violet-200">RF Lens</div>
                            <div className="inline-flex rounded-md border border-slate-700/70 bg-slate-950/60 p-1">
                                {(['cdr_loops', 'whole_antibody'] as RfScreeningScope[]).map((scope) => (
                                    <button
                                        key={scope}
                                        type="button"
                                        onClick={() => setRfMetricScope(scope)}
                                        className={`rounded px-2 py-1 text-[10px] transition-colors ${effectiveRfMetricScope === scope ? 'bg-violet-500/20 text-violet-100' : 'text-slate-400 hover:text-slate-200'}`}
                                    >
                                        {RF_SCOPE_LABELS[scope].short}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                    <div className={analyticsSectionGridClass}>
                        {structureMetricCards.map((metric) => (
                            <div key={metric.label} className="bg-slate-900/50 rounded-lg p-3 text-center">
                                <div className={`${analyticsHeadlineMetricClass} ${metric.accentClass}`}>
                                    {metric.value}
                                </div>
                                <div className="text-xs text-slate-500 mt-1">{metric.label}</div>
                            </div>
                        ))}
                    </div>
                    {designLens === 'rfantibody' && rfLoopEntries.length > 0 && (
                        <div className="mt-3 space-y-2">
                            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Loop Triage</div>
                            <div className="grid grid-cols-1 gap-2">
                                {rfLoopEntries.map(({ loopId, metrics }) => (
                                    <div key={loopId} className={`rounded border px-3 py-2 text-xs ${metrics.redesign_candidate ? 'border-amber-500/30 bg-amber-500/10 text-amber-100' : 'border-slate-700/60 bg-slate-900/40 text-slate-300'}`}>
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="font-semibold text-white">{loopId}</span>
                                            <span>{metrics.screening_status || metrics.screening_note || 'ok'}</span>
                                        </div>
                                        <div className="mt-1 text-slate-400">
                                            {metrics.epitope_contact_count ?? '—'} epi cts • {metrics.target_contact_count ?? '—'} tgt cts • {metrics.target_min_distance != null ? `${metrics.target_min_distance.toFixed(1)} A` : '—'}
                                        </div>
                                        {metrics.screening_note && metrics.screening_note !== metrics.screening_status && (
                                            <div className="mt-1 text-[10px] text-slate-500">{metrics.screening_note}</div>
                                        )}
                                        {metrics.redesign_candidate && (
                                            <div className="mt-1 text-[10px] text-amber-200">Flagged for redesign triage.</div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Confidence Section */}
            {selectedDesign && (
                <div className={`rounded-lg border ${analyticsPanelPaddingClass} ${focusedMetricSection === 'confidence' ? 'border-blue-500/40 bg-blue-500/5' : 'border-slate-700/50 bg-slate-800/50'}`}>
                    <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Confidence</h4>
                            <div className="mt-1 text-[11px] text-slate-500">{confidenceSemantics.profileTitle} and chain-level residue coverage</div>
                        </div>
                        <div className="rounded border border-slate-700/70 bg-slate-900/60 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">
                            {hasResidueConfidence ? 'Ready' : chainMetricsStatusCopy}
                        </div>
                    </div>
                    <div className={analyticsSectionGridClass}>
                        <div className={analyticsStatCardClass}>
                            <div className={`${analyticsSecondaryValueClass} text-white`}>{confidenceResidueCount || '—'}</div>
                            <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">Residue Values</div>
                        </div>
                        <div className={analyticsStatCardClass}>
                            <div className={`${analyticsSecondaryValueClass} text-cyan-200`}>{chainMetricChainCount || '—'}</div>
                            <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">Chains With Metrics</div>
                        </div>
                    </div>
                    <div className="mt-3 space-y-3 border-t border-slate-700/50 pt-3">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Chain Metrics</div>
                                <div className="mt-1 text-sm text-slate-400">{chainMetricsStatusCopy}</div>
                            </div>
                            <button
                                type="button"
                                onClick={onRunChainMetrics}
                                disabled={!onRunChainMetrics || chainMetricsBusy}
                                className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${chainMetricsBusy
                                    ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                    : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20'
                                    }`}
                            >
                                {chainMetricsBusy ? 'Running…' : chainMetricChainCount ? 'Refresh' : 'Run'}
                            </button>
                        </div>
                        {chainMetricsRun?.error_message && (
                            <div className="text-xs text-rose-300">Last chain-metrics error: {chainMetricsRun.error_message}</div>
                        )}
                        {hasResidueConfidence ? (
                            <div className="rounded border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-xs text-slate-400">
                                {confidenceOverlayReadyCopy}
                            </div>
                        ) : (
                            <div className="rounded border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-xs text-slate-400">
                                Residue-level confidence will light up here once residue metrics or chain metrics are available for this design.
                            </div>
                        )}
                        {antibodySelections?.length ? (
                            <div className="text-xs text-violet-200">CDR overlay is available for this design.</div>
                        ) : null}
                    </div>
                </div>
            )}

            {/* Interface Section */}
            {selectedDesign && (
                <div className={`rounded-lg border ${analyticsPanelPaddingClass} ${focusedMetricSection === 'interface' ? 'border-blue-500/40 bg-blue-500/5' : 'border-slate-700/50 bg-slate-800/50'}`}>
                    <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Interface</h4>
                            <div className="mt-1 text-[11px] text-slate-500">ipSAE, chain-pair agreement, and interface-specific confidence</div>
                        </div>
                        <div className="rounded border border-slate-700/70 bg-slate-900/60 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">
                            {ipsaeInterfaceStatusCopy}
                        </div>
                    </div>
                    <div className={analyticsSectionGridClass}>
                        <div className={analyticsStatCardClass}>
                            <div className={`${analyticsSecondaryValueClass} text-amber-200`}>{formatMetricValue(ipsaeInterface?.ipsae ?? selectedDesign.ipsae ?? null, 3)}</div>
                            <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">Interface ipSAE</div>
                        </div>
                        <div className={analyticsStatCardClass}>
                            <div className={`${analyticsSecondaryValueClass} text-cyan-200`}>{chainPairIptmChainCount || interfacePairScoreCount || '—'}</div>
                            <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">Interface Pairs</div>
                        </div>
                    </div>
                    <div className="mt-3 space-y-3 border-t border-slate-700/50 pt-3">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Interface ipSAE</div>
                                <div className="mt-1 text-sm text-slate-400">{ipsaeInterfaceStatusCopy}</div>
                            </div>
                            <button
                                type="button"
                                onClick={onRunIpsaeInterface}
                                disabled={!onRunIpsaeInterface || ipsaeInterfaceBusy}
                                className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${ipsaeInterfaceBusy
                                    ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                    : 'border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20'
                                    }`}
                            >
                                {ipsaeInterfaceBusy ? 'Running…' : hasIpsaeInterface ? 'Refresh' : 'Run'}
                            </button>
                        </div>
                        {ipsaeInterfaceRun?.error_message && (
                            <div className="text-xs text-rose-300">Last interface-ipSAE error: {ipsaeInterfaceRun.error_message}</div>
                        )}
                        {(chainPairIptmLoading || hasChainPairIptm) ? (
                            <div className="rounded border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-xs text-slate-400">
                                {chainPairIptmLoading
                                    ? 'Loading chain-pair iPTM matrix…'
                                    : `Chain-pair iPTM matrix ready for ${chainPairIptmChainCount} chains.`}
                            </div>
                        ) : (
                            <div className="rounded border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-xs text-slate-400">
                                Run interface analysis to populate pairwise interface confidence and chain-pair agreement.
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Structure Analysis */}
            <div className={`rounded-lg border ${analyticsPanelPaddingClass} ${focusedMetricSection === 'geometry' ? 'border-blue-500/40 bg-blue-500/5' : 'border-slate-700/50 bg-slate-800/50'}`}>
                <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                        <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Geometry</h4>
                        <div className="mt-1 text-[11px] text-slate-500">Structure summary, PAE, and contact topology</div>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className={`text-[10px] uppercase tracking-wider ${structureAnalysisStatus === 'completed' ? 'text-emerald-300' : structureAnalysisStatus === 'failed' ? 'text-rose-300' : 'text-slate-500'}`}>
                            {structureAnalysisStatusCopy}
                        </span>
                        <button
                            type="button"
                            onClick={onRunStructureAnalysis}
                            disabled={!onRunStructureAnalysis || !!structureAnalysisBusy}
                            className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${structureAnalysisBusy
                                ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20'
                                }`}
                        >
                            {structureAnalysisBusy ? 'Running…' : structureAnalysis ? 'Refresh' : 'Run'}
                        </button>
                    </div>
                </div>
                {structureAnalysis ? (
                    <div className="space-y-2 text-sm">
                        <div className="flex justify-between">
                            <span className="text-slate-500">Total Residues</span>
                            <span className="text-white font-mono">{structureAnalysis.residue_count}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-500">Chains</span>
                            <span className="text-white font-mono">{structureAnalysis.chain_ids?.length ?? 0}</span>
                        </div>
                        {structureAnalysis.secondary_structure && (
                            <>
                                <div className="flex justify-between">
                                    <span className="text-slate-500">α-Helix</span>
                                    <span className="text-accent-secondary font-mono">{structureAnalysis.secondary_structure.helix?.toFixed(0)}%</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-slate-500">β-Sheet</span>
                                    <span className="text-yellow-400 font-mono">{structureAnalysis.secondary_structure.sheet?.toFixed(0)}%</span>
                                </div>
                            </>
                        )}
                    </div>
                ) : (
                    <div className="text-sm text-slate-500">
                        Structure summary is now on-demand and persisted. Run it once for this design to cache the result.
                        {structureAnalysisRun?.error_message && (
                            <div className="mt-2 text-xs text-rose-300">Last error: {structureAnalysisRun.error_message}</div>
                        )}
                    </div>
                )}
                <div className="mt-4 space-y-3 border-t border-slate-700/50 pt-3">
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">PAE Matrix</div>
                            <div className="mt-1 text-sm text-slate-400">{paeStatusCopy}</div>
                        </div>
                        <button
                            type="button"
                            onClick={onRunPaeMatrix}
                            disabled={!onRunPaeMatrix || paeBusy}
                            className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${paeBusy
                                ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                : 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-300 hover:bg-fuchsia-500/20'
                                }`}
                        >
                            {paeBusy ? 'Running…' : paeMatrix ? 'Refresh' : 'Run'}
                        </button>
                    </div>
                    {paeRun?.error_message && (
                        <div className="text-xs text-rose-300">Last PAE error: {paeRun.error_message}</div>
                    )}
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Contact Map</div>
                            <div className="mt-1 text-sm text-slate-400">{contactMapStatusCopy}</div>
                        </div>
                        <button
                            type="button"
                            onClick={onRunContactMap}
                            disabled={!onRunContactMap || contactMapBusy}
                            className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${contactMapBusy
                                ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                : 'border-rose-500/40 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20'
                                }`}
                        >
                            {contactMapBusy ? 'Running…' : hasContactMap ? 'Refresh' : 'Run'}
                        </button>
                    </div>
                    {contactMapRun?.error_message && (
                        <div className="text-xs text-rose-300">Last contact-map error: {contactMapRun.error_message}</div>
                    )}
                </div>
            </div>

            {/* Chain Details Panel (for multi-chain complexes) */}
            {selectedDesign && Object.keys(chainMetrics).length > 0 && (
                <ChainDetailsPanel
                    design={selectedDesign}
                    chainMetrics={chainMetrics as Record<string, ChainMetric> | null}
                />
            )}

            {selectedDesign && hasDesignabilitySection && (
                <div className={`rounded-lg border ${analyticsPanelPaddingClass} ${focusedMetricSection === 'designability' ? 'border-blue-500/40 bg-blue-500/5' : 'border-slate-700/50 bg-slate-800/50'}`}>
                    <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Designability</h4>
                            <div className="mt-1 text-[11px] text-slate-500">FA-MPNN PSCE and FrustraMPNN residue-level stress hotspots</div>
                        </div>
                        <div className="rounded border border-slate-700/70 bg-slate-900/60 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">
                            {designabilityStatusCopy}
                        </div>
                    </div>
                    <div className="space-y-4">
                        {hasFampnnDesign && (
                            <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
                                <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                            <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Per-Residue PSCE</h4>
                            <div className="mt-1 text-sm text-slate-400">{fampnnPsceStatusCopy}</div>
                        </div>
                        <button
                            type="button"
                            onClick={onRunFampnnPsceProfile}
                            disabled={!onRunFampnnPsceProfile || fampnnPsceBusy}
                            className={`rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors ${fampnnPsceBusy
                                ? 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20'
                                }`}
                        >
                            {fampnnPsceBusy ? 'Running…' : hasFampnnPsceProfile ? 'Refresh' : 'Run'}
                        </button>
                    </div>
                    {fampnnPsceProfileRun?.error_message && (
                        <div className="mb-3 text-xs text-rose-300">Last PSCE-profile error: {fampnnPsceProfileRun.error_message}</div>
                    )}
                    {fampnnPscePlot ? (
                        <>
                            <div className="mb-3 flex items-center gap-1 flex-wrap">
                                <span className="text-xs text-slate-400 mr-1">Chain:</span>
                                <button
                                    onClick={() => setSelectedChain(null)}
                                    className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === null
                                        ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/40'
                                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                        }`}
                                >
                                    All
                                </button>
                                {fampnnPsceChainIds.map((chainId, idx) => (
                                    <button
                                        key={chainId}
                                        onClick={() => setSelectedChain(chainId)}
                                        className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === chainId
                                            ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/40'
                                            : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                            }`}
                                        style={{ borderLeft: `2px solid ${CHAIN_ACCENT_COLORS[idx % CHAIN_ACCENT_COLORS.length]}` }}
                                    >
                                        {chainId} ({fampnnPsceChains[chainId]?.length || 0})
                                    </button>
                                ))}
                            </div>
                            <Plot
                                data={fampnnPscePlot.data}
                                layout={fampnnPscePlot.layout}
                                config={{
                                    responsive: true,
                                    displayModeBar: true,
                                    toImageButtonOptions: {
                                        format: 'svg',
                                        filename: `fampnn_psce_${selectedDesign.name.replace(/\s+/g, '_')}`,
                                    },
                                }}
                                style={{ width: '100%', height: '320px' }}
                            />
                            <div className="mt-2 text-[11px] text-slate-400">
                                {selectedChain && fampnnPsceChains[selectedChain]
                                    ? `Chain ${selectedChain}: ${fampnnPsceChains[selectedChain]?.length || 0} scored residues • Mean ${fampnnPsceChains[selectedChain]?.avg_psce?.toFixed(2) || '—'} Å • Worst ${fampnnPsceChains[selectedChain]?.max_psce?.toFixed(2) || '—'} Å`
                                    : `${fampnnPsceProfileValues.length} scored residues • Mean ${fampnnPsceProfileValues.length > 0 ? (fampnnPsceProfileValues.reduce((sum, value) => sum + value, 0) / fampnnPsceProfileValues.length).toFixed(2) : '—'} Å • Worst ${fampnnPsceProfileValues.length > 0 ? Math.max(...fampnnPsceProfileValues).toFixed(2) : '—'} Å`}
                            </div>
                        </>
                    ) : (
                        <div className="text-sm text-slate-500">
                            Run the FA-MPNN PSCE profile analysis to color the structure and inspect per-residue sidechain confidence.
                        </div>
                    )}
                </div>
            )}

            {/* Frustration Analysis (FrustraMPNN) */}
                        {hasFrustrationSummary && (
                            <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Frustration Analysis</h4>
                        {selectedDesign.frustration_csv_relpath && (
                            <div className="flex items-center gap-2">
                                <a
                                    href={buildFileStreamUrl(selectedDesign.frustration_csv_relpath)}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="rounded border border-slate-600 px-2 py-1 text-[11px] font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
                                >
                                    Open CSV
                                </a>
                                <a
                                    href={buildFileDownloadUrl(selectedDesign.frustration_csv_relpath)}
                                    className="rounded border border-slate-600 px-2 py-1 text-[11px] font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
                                >
                                    Download CSV
                                </a>
                            </div>
                        )}
                    </div>
                    <div className="space-y-2 text-sm">
                        <div className="flex justify-between">
                            <span className="text-slate-500">Highly Frustrated</span>
                            <span className={`font-mono ${(selectedDesign.frustration_high_count ?? 0) > 5 ? 'text-red-400' : 'text-green-400'}`}>
                                {selectedDesign.frustration_high_count} residues
                            </span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-500">Minimally Frustrated</span>
                            <span className="text-green-400 font-mono">
                                {selectedDesign.frustration_min_count} residues
                            </span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-500">% Highly Frustrated</span>
                            <span className={`font-mono ${(selectedDesign.frustration_pct_high ?? 0) > 10 ? 'text-orange-400' : 'text-green-400'}`}>
                                {selectedDesign.frustration_pct_high?.toFixed(1)}%
                            </span>
                        </div>
                    </div>
                    {/* Quick legend */}
                    <div className="flex items-center justify-center gap-3 mt-3 pt-2 border-t border-slate-700/50">
                        <span className="text-[10px] text-slate-500">
                            <span className="text-green-400 mr-1">●</span>min (≥0.58)
                        </span>
                        <span className="text-[10px] text-slate-500">
                            <span className="text-slate-400 mr-1">●</span>neutral
                        </span>
                        <span className="text-[10px] text-slate-500">
                            <span className="text-red-400 mr-1">●</span>high (≤-1.0)
                        </span>
                    </div>
                    {topFrustratedResidues.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-slate-700/50">
                            <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-2">Most Frustrated Positions</div>
                            <div className="grid grid-cols-2 gap-2 text-[11px]">
                                {topFrustratedResidues.map((residue) => (
                                    <div
                                        key={`${residue.chain}-${residue.actualResidueNumber}`}
                                        className="flex items-center justify-between rounded bg-slate-900/50 px-2 py-1"
                                    >
                                        <span className="text-slate-300">
                                            {residue.chain}{residue.actualResidueNumber}
                                        </span>
                                        <span className="font-mono text-red-300">{residue.frust.toFixed(2)}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* CDR Info */}
            {(selectedDesign as UntypedApiValue)?.cdr_h3 && (
                <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 p-4">
                    <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">CDR Loops</h4>
                    <div className="space-y-2 font-mono text-xs">
                        {['H1', 'H2', 'H3', 'L1', 'L2', 'L3'].map(cdr => {
                            const seq = (selectedDesign as UntypedApiValue)?.[`cdr_${cdr.toLowerCase()}`];
                            if (!seq) return null;
                            return (
                                <div key={cdr} className="flex justify-between gap-2">
                                    <span className="text-slate-500 font-bold">{cdr}</span>
                                    <span className="text-white truncate flex-1 text-right">{seq}</span>
                                    <span className="text-slate-600 w-6 text-right">{seq.length}</span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Favorite Button */}
            {selectedDesign && (
                <button
                    onClick={async () => {
                        await fetch(`/api/designs/${selectedDesign.id}/favorite`, { method: 'POST' });
                    }}
                    className={`w-full py-2 rounded-lg text-sm font-medium transition-colors ${selectedDesign.is_favorite
                        ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/40'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                >
                    {selectedDesign.is_favorite ? '★ Favorited' : '☆ Add to Favorites'}
                </button>
            )}
        </div>
    );

    // Shared toolbar for design/color selection
    const renderViewerToolbar = (isCompact = false) => (
        <div className={`flex items-center gap-2 ${isCompact ? 'flex-wrap' : 'mb-3 flex-wrap'}`}>
            {/* Design Selector */}
            <div className="relative">
                <select
                    value={selectedDesignId ?? ''}
                    onChange={(e) => setSelectedDesignId(e.target.value)}
                    className={`appearance-none border border-slate-700 rounded-lg px-3 py-1.5 pr-8 text-sm text-white cursor-pointer hover:bg-slate-700 transition-colors min-w-[200px] ${isCompact ? 'bg-slate-800/90 backdrop-blur-sm' : 'bg-slate-800'}`}
                >
                    {designs.map(d => (
                        <option key={d.id} value={d.id}>
                            {`${getDesignOriginLabel(d) ? `[${getDesignOriginLabel(d)}] ` : ''}${inferDesignOutputSource(d) === 'validation' ? (formatStructureValidationName(d) || d.name) : d.name}${inferDesignOutputSource(d) !== 'ppiflow' && d.plddt_overall ? ` (${d.plddt_overall.toFixed(0)})` : ''}`}
                        </option>
                    ))}
                </select>
                <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">▾</div>
            </div>

            {conforNetsNavigation && conforNetsConformerSet && conforNetsNavigation.totalCount > 1 && (
                <div
                    data-confornets-conformer-controls
                    className={`flex items-center gap-2 rounded-lg border border-violet-500/30 px-2 py-1.5 text-xs ${isCompact ? 'bg-slate-900/85 backdrop-blur-sm' : 'bg-violet-500/10'}`}
                >
                    <button
                        type="button"
                        onClick={() => conforNetsNavigation.previousId && setSelectedDesignId(conforNetsNavigation.previousId)}
                        disabled={!conforNetsNavigation.previousId}
                        className="rounded border border-violet-500/30 px-2 py-1 text-violet-100 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500 hover:bg-violet-500/20"
                        title="Previous conformation"
                    >
                        ‹
                    </button>
                    <label className="flex min-w-[220px] items-center gap-2 text-violet-100">
                        <span className="whitespace-nowrap">Conformation {conforNetsNavigation.selectedNumber}/{conforNetsNavigation.totalCount}</span>
                        <input
                            type="range"
                            min={conforNetsNavigation.sliderMin}
                            max={conforNetsNavigation.sliderMax}
                            value={conforNetsNavigation.sliderValue}
                            onChange={(event) => selectConforNetsConformerByIndex(Number(event.target.value))}
                            className="w-28 accent-violet-400"
                            aria-label="ConforNets conformation slider"
                        />
                    </label>
                    <button
                        type="button"
                        onClick={() => conforNetsNavigation.nextId && setSelectedDesignId(conforNetsNavigation.nextId)}
                        disabled={!conforNetsNavigation.nextId}
                        className="rounded border border-violet-500/30 px-2 py-1 text-violet-100 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500 hover:bg-violet-500/20"
                        title="Next conformation"
                    >
                        ›
                    </button>
                    <details className="relative">
                        <summary className="cursor-pointer select-none rounded border border-violet-500/30 px-2 py-1 text-violet-100 hover:bg-violet-500/20">
                            Overlay conformers ({resolvedConforNetsOverlayIds.length})
                        </summary>
                        <div className="absolute left-0 top-full z-50 mt-2 max-h-64 w-72 overflow-y-auto rounded-lg border border-violet-500/30 bg-slate-950/95 p-2 shadow-xl backdrop-blur-sm">
                            <div className="mb-2 text-[11px] text-slate-400">Overlay extra ConforNets frames on the active conformation.</div>
                            {conforNetsConformerSet.conformers.map((conformer, index) => {
                                const isActive = conformer.id === conforNetsNavigation.selectedId;
                                return (
                                    <label key={conformer.id} className={`flex items-center gap-2 rounded px-2 py-1 ${isActive ? 'text-slate-500' : 'text-slate-200 hover:bg-slate-800'}`}>
                                        <input
                                            type="checkbox"
                                            checked={resolvedConforNetsOverlayIds.includes(conformer.id)}
                                            disabled={isActive}
                                            onChange={() => toggleConforNetsOverlay(conformer.id)}
                                            className="accent-violet-400"
                                        />
                                        <span className="truncate">{conformer.frameIndex == null ? `Frame ${index}` : `Frame ${conformer.frameIndex}`} • {conformer.name}</span>
                                    </label>
                                );
                            })}
                        </div>
                    </details>
                    <span className="max-w-[240px] truncate text-[11px] text-violet-200" title={conforNetsNavigation.selectedLabel}>{conforNetsNavigation.selectedLabel}</span>
                </div>
            )}

            {/* Color Mode */}
            <select
                value={effectiveColorMode}
                onChange={(e) => handleColorModeChange(e.target.value as StructureViewerColorMode)}
                className={`appearance-none border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white cursor-pointer hover:bg-slate-700 ${isCompact ? 'bg-slate-800/90 backdrop-blur-sm' : 'bg-slate-800'}`}
            >
                <option value="default">Chain Colors</option>
                <option value="plddt" disabled={!hasResidueConfidence}>{bfactorLabel}</option>
                <option value="fampnn_psce" disabled={!hasFampnnPsceProfile}>FA-MPNN PSCE</option>
                <option value="frustration" disabled={!selectedDesign?.frustration_residues?.length}>
                    Frustration
                </option>
                <option value="cdr" disabled={!antibodySelections?.length}>
                    CDR Regions
                </option>
            </select>

            {renderQuickViewBar(isCompact)}

            <button
                type="button"
                onClick={() => setAnalyticsPanelOpen((open) => !open)}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${isCompact
                    ? (analyticsPanelOpen
                        ? 'bg-slate-800/90 text-slate-100 backdrop-blur-sm hover:bg-slate-700/90'
                        : 'bg-blue-500/80 text-white backdrop-blur-sm hover:bg-blue-500')
                    : (analyticsPanelOpen
                        ? 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        : 'bg-blue-500/10 text-blue-300 border border-blue-500/40 hover:bg-blue-500/20')}`}
                title={analyticsPanelOpen ? 'Hide the analytics panel' : 'Show the analytics panel'}
            >
                {analyticsPanelOpen ? 'Hide Analytics' : 'Show Analytics'}
            </button>

            {/* Color Legend */}
            {effectiveColorMode === 'plddt' && !isCompact && (
                <div className="flex items-center gap-1 text-xs text-slate-400">
                    <span className="text-blue-400">■</span>≥90
                    <span className="text-cyan-400 ml-1">■</span>≥70
                    <span className="text-yellow-400 ml-1">■</span>≥50
                    <span className="text-orange-400 ml-1">■</span>&lt;50
                </div>
            )}
            {effectiveColorMode === 'fampnn_psce' && !isCompact && (
                <div className="flex items-center gap-1 text-xs text-slate-400">
                    <span className="text-emerald-400">■</span>≤0.9
                    <span className="text-cyan-400 ml-1">■</span>≤1.2
                    <span className="text-amber-400 ml-1">■</span>≤1.6
                    <span className="text-rose-400 ml-1">■</span>&gt;1.6
                    <span className="ml-2 text-slate-500">Lower is better</span>
                </div>
            )}
            {effectiveColorMode === 'frustration' && !isCompact && (
                <div className="flex items-center gap-1 text-xs text-slate-400">
                    <span className="text-red-400">■</span>high
                    <span className="text-slate-400 ml-1">■</span>neutral
                    <span className="text-green-400 ml-1">■</span>minimal
                </div>
            )}

            {/* Fullscreen Toggle */}
            <button
                onClick={() => {
                    if (sourceBackboneReference) {
                        showSelectedReference(sourceBackboneReference);
                    }
                }}
                disabled={!sourceBackboneReference}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${isCompact
                    ? (sourceBackboneReference
                        ? 'bg-cyan-500/80 text-white backdrop-blur-sm hover:bg-cyan-400/80'
                        : 'bg-slate-800/60 text-slate-500 backdrop-blur-sm cursor-not-allowed')
                    : (sourceBackboneReference
                        ? 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/40 hover:bg-cyan-500/20'
                        : 'bg-slate-800 text-slate-500 border border-slate-700 cursor-not-allowed')}`}
                title={sourceBackboneReference ? 'Open the pre-refinement source backbone as the reference structure' : 'No source backbone reference is available for this output'}
            >
                Source Backbone
            </button>
            <button
                onClick={() => {
                    if (showReferenceDock) {
                        closeReferenceDock();
                    } else if (selectedReference) {
                        setReferenceDockMode('viewer');
                        setShowReferenceDock(true);
                        requestAnimationFrame(() => positionReferenceWindow('viewer'));
                    } else {
                        openReferenceSelector();
                    }
                }}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${isCompact
                    ? (showReferenceDock
                        ? 'bg-blue-500/80 text-white backdrop-blur-sm'
                        : 'bg-slate-800/90 text-slate-100 hover:bg-slate-700/90 backdrop-blur-sm')
                    : (showReferenceDock
                        ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600')}`}
            >
                {showReferenceDock ? 'Close Reference' : 'Reference'}
            </button>
            <button
                onClick={toggleFullscreen}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${isCompact
                    ? 'bg-red-500/80 hover:bg-red-500 text-white backdrop-blur-sm'
                    : 'bg-slate-700 text-slate-400 hover:bg-slate-600'}`}
            >
                {isCompact ? '✕ Exit Fullscreen' : '⛶ Fullscreen'}
            </button>
        </div>
    );

    return (
        <div
            ref={containerRef}
            data-structure-viewer-analytics-open={analyticsPanelOpen ? 'true' : 'false'}
            className={`${isFullscreen ? 'fixed inset-0 z-50 bg-slate-950' : 'p-4'}`}
        >
            {/* Main layout container - always present */}
            <div
                data-structure-viewer-layout={viewerLayout.isStacked ? 'stacked' : 'split'}
                className={isFullscreen ? 'h-full w-full relative' : viewerLayout.isStacked ? 'flex flex-col gap-4' : 'flex gap-4'}
            >
                {/* Left Column / Fullscreen: Viewer Area */}
                <div ref={viewerAreaRef} className={isFullscreen ? 'absolute inset-0' : viewerLayout.isStacked ? 'min-w-0' : 'flex-[2] min-w-0'}>
                    {/* Toolbar - positioned differently based on mode */}
                    <div className={isFullscreen ? 'absolute top-3 left-3 z-40' : ''}>
                        {renderViewerToolbar(isFullscreen || viewerLayout.isStacked)}
                    </div>

                    {/* Main Viewer - ALWAYS at this exact tree position */}
                    <div
                        className={isFullscreen
                            ? 'absolute inset-0'
                            : 'relative rounded-lg overflow-hidden border border-slate-700'
                        }
                        style={isFullscreen ? undefined : { height: viewerLayout.viewerHeight }}
                    >
                        <MolstarViewer
                            key={`${selectedDesignId}_${effectiveColorMode}_${conforNetsOverlaySignature}`}
                            structureUrl={viewerStructureUrl}
                            format={viewerStructureFormat}
                            alphafoldView={effectiveColorMode === 'plddt' && !plddtResidueColors}
                            selections={effectiveColorMode === 'cdr' ? antibodySelections : undefined}
                            overlayStructures={conforNetsOverlayStructures}
                            residueMetricLayer={residueMetricLayer}
                            height="100%"
                            backgroundColor={themeColors.bgPrimary}
                        />

                        {showReferenceDock && (
                            <div
                                className="absolute z-30 rounded-xl border border-slate-700/70 bg-slate-950/92 shadow-2xl backdrop-blur-sm overflow-hidden"
                                style={{
                                    left: referenceWindow.x,
                                    top: referenceWindow.y,
                                    width: referenceWindow.width,
                                    height: referenceWindow.height,
                                    maxWidth: 'calc(100% - 24px)',
                                    maxHeight: 'calc(100% - 24px)',
                                }}
                            >
                                <div
                                    onMouseDown={startReferenceDrag}
                                    className="flex cursor-move items-center justify-between border-b border-slate-700/60 px-3 py-2"
                                >
                                    <div className="min-w-0">
                                        <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                                            {referenceDockMode === 'viewer' ? 'Reference Structure' : 'Compare to Reference'}
                                        </div>
                                        <div className="truncate text-[11px] text-slate-300">
                                            {selectedReference ? selectedReference.name : 'Select a crystal or prior design reference'}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2 pl-3">
                                        <button
                                            onClick={closeReferenceDock}
                                            className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700"
                                        >
                                            Close
                                        </button>
                                    </div>
                                </div>

                                {referenceDockMode === 'viewer' && selectedReference ? (
                                    <div className="h-[calc(100%-45px)] p-2">
                                        <MolstarViewer
                                            key={`reference:${selectedReference.url}`}
                                            structureUrl={selectedReference.url}
                                            format={selectedReference.format}
                                            alphafoldView={false}
                                            height="100%"
                                            backgroundColor={themeColors.bgSecondary}
                                            label={selectedReference.name}
                                        />
                                    </div>
                                ) : (
                                    <div className="h-[calc(100%-45px)] overflow-y-auto p-2">
                                        <ReferenceSelector
                                            selectedRef={selectedReference}
                                            onSelect={(reference) => {
                                                if (reference) {
                                                    showSelectedReference(reference);
                                                } else {
                                                    setSelectedReference(null);
                                                }
                                            }}
                                            currentDesignId={selectedDesignId || undefined}
                                        />
                                    </div>
                                )}

                                <button
                                    onMouseDown={startReferenceResize}
                                    className="absolute bottom-2 right-2 h-5 w-5 cursor-se-resize rounded border border-slate-700/70 bg-slate-800/80 text-[10px] leading-none text-slate-400 hover:bg-slate-700"
                                    aria-label="Resize reference window"
                                    title="Resize"
                                >
                                    ◢
                                </button>
                            </div>
                        )}
                    </div>
                </div>

                {/* Right Column: Analytics Sidebar - hidden in fullscreen */}
                {!isFullscreen && analyticsPanelOpen && renderAnalyticsSidebar()}
            </div>

            {/* Fullscreen overlay panel - only in fullscreen mode */}
            {isFullscreen && analyticsPanelOpen && (
                <div className={fullscreenAnalyticsLayout.frameClassName}>
                    {renderFullscreenOverlay()}
                </div>
            )}
        </div>
    );
}
