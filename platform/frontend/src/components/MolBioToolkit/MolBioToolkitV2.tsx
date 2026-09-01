/**
 * MolBioToolkit - Seqviz-based sequence editor
 *
 * Clean rewrite replacing OVE with modern component architecture.
 */

import { useState, useEffect, useCallback, useMemo, useRef, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { ngsResultHref } from '../../lib/ngsResultRouting';
import { createLatestAsyncResourceController } from '../../lib/latestAsyncResource';
import {
    fetchRestrictionAnalysis,
    fetchRestrictionCatalog,
    simulateRestrictionDigest,
    type RestrictionAnalysisResponse,
    type RestrictionCatalogReceipt,
    type RestrictionDigestSimulation,
    type RestrictionRecord,
    type RestrictionSource,
} from '../../lib/restrictionAnalysis';
import { anyToJson } from '@teselagen/bio-parsers';
import { SequenceViewer, type ColorPaletteName } from './SequenceViewer';
import { DEFAULT_VISIBILITY } from './sequenceViewerConstants';
import { SequenceHeader } from './SequenceHeader';
import {
    MobileMolBioWorkspace,
    MobileMolBioReadPanel,
    parseMobileMolBioWorkups,
    type MobileMolBioWorkupStatus,
} from './MobileMolBioWorkspace';
import { VisibilityPanel } from './VisibilityPanel';
import { createHistoryState, useSequenceHistory, type HistoryState } from './hooks/useSequenceHistory';
import { useSequenceOperations } from './hooks/useSequenceOperations';
import { AlignmentPanel, AssemblyPanel, DigestPanel, HistoryPanel, PCRPanel, PrimerPanel, RnaStructurePanel, FeaturePanel, EditPanel, SearchPanel } from './panels';
import { AutoAnnotatePanel, type AutoAnnotateSettings } from './AutoAnnotatePanel';
import { GCContentTrack } from './GCContentTrack';
import {
    SelectionActionDialog,
    type SelectionActionKind,
    type SelectionFeatureInput,
    type SelectionPrimerInput,
} from './SelectionActionDialog';
import { MolecularInputModal } from './MolecularInputModal';
import { RnaStructureViewer, type RnaStructureDisplayMode } from './RnaStructureViewer';
import { getFeatureColor, normalizeFeatureType } from './featureCatalog';
import {
    didFocusLeaveContainer,
    moveMenuFocus,
    type MenuNavigationKey,
} from './utils/focusManagement';
import { clearFeatureAnnotations } from './utils/annotations';
import {
    assertAnnotationTopology,
    resolveAnnotationAlignmentPolicy,
    resolveAnnotationSequenceAlignment,
    transformFeatureForAlignment,
} from './utils/annotationTransfer';
import {
    assertAnnotationArtifactChecksum,
    fetchAnnotationSourceStatus,
    retrieveAddgeneAnnotationSource,
    retrieveNcbiAnnotationSource,
    type AnnotationSourceProvenance,
    type AnnotationSourceStatus,
} from './utils/annotationSources';
import { loadDemoPlasmids } from './demoConstructs';
import {
    calculatePrimerTm,
    fetchProjectHub,
    fetchMolecularRevision,
    fetchNucleotideSequence,
    fetchPrimerTmOptions,
    type MolecularRevision,
    type SequenceAnalysisTrack,
    type PrimerTmOptionsResponse,
    type PrimerTmSettings,
    type RnaStructureResult,
    type NucleotideSequenceCreate,
} from '../../lib/api';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';
import { projectHubDNASequencesToConstructShelf } from './utils/projectConstructShelf';
import type {
    AnalysisTrack,
    SequenceData,
    VisibilityState,
    SelectionInfo,
    NucleotideSequenceListItem,
    NucleotideSequenceResponse,
    HighlightedRegion,
    ActivePanel,
    Feature,
    Primer
} from './types';
import { EMPTY_SEQUENCE } from './types';
import {
    calculateGcPercent,
    displayStrandForMoleculeOrientation,
    hasExplicitNucleotideStrandednessMetadata,
    inferNucleotideMoleculeMetadataFromParsedRecord,
    inferSequenceTypeFromSequence,
    moleculeLabelForNucleotide,
    normalizeSequenceForType,
    sequenceUnitLabel,
    type NucleotideDisplayStrand,
} from './utils/nucleotides';
import { findOpenReadingFrames } from './utils/orfs';
import {
    dedupeFeatures,
    featureBounds,
    featureLength,
    featureOverlapLength,
} from './utils/features';
import {
    buildSelectionPrimer,
    buildPrimerTmRequest,
    createSelectionSnapshot,
    getPrimerHighlightRegions,
    normalizeStoredPrimerPlacement,
    prepareSelectionPrimer,
    type SelectionSnapshot,
} from './utils/selectionActions';
import { applyImportedTopology, type ImportTopology } from './utils/topology';
import {
    MOLBIO_LIBRARY_PANEL_DEFAULT_WIDTH,
    clampMolBioPanelWidth,
    getDefaultMolBioToolPanelWidth,
    resolveMolBioViewerLayout,
    shouldCollapseMolBioPanelsForViewport,
} from './utils/viewerLayout';
import {
    MOLECULAR_WORKSPACE_STORAGE_KEY,
    deserializeMolecularWorkspaceIdentity,
    loadMolecularWorkspaceCurrentSequence,
    molecularWorkspaceId,
    resolveExactMolecularAuthority,
    resolveMolecularOpenRequest,
    runDirtyWorkspaceTransition,
    serializeMolecularWorkspaceIdentity,
    upsertStableMolecularWorkspace,
    useMolecularWorkspaceRestoreEffect,
    type DirtyWorkspaceChoice,
    type MolecularOpenRequest,
    type PersistedMolecularWorkspace,
} from './utils/molecularWorkspaceState';
import {
    activateMobileMolBioSequence,
    detectMolBioCordovaShell,
    detectMolBioPrimaryCoarsePointer,
    resolveMolBioMobileBackAction,
    resolveMolBioMobileSequenceIntent,
    shouldUseMolBioMobileLayout,
    type MolBioMobileSequenceIntent,
    type MolBioMobileSurface,
} from './utils/mobileLayout';
import { useMolBioBodyScrollLock } from './useMolBioBodyScrollLock';

// ═══════════════════════════════════════════════════════════════════════════════
// SEQUENCE LIBRARY SIDEBAR WITH IMPORT
// ═══════════════════════════════════════════════════════════════════════════════

interface SequenceLibraryProps {
    mobile?: boolean;
    sequences: NucleotideSequenceListItem[];
    demos: SequenceData[];
    demoLoading: boolean;
    selectedId: string | null;
    onSelect: (sequence: NucleotideSequenceListItem) => void;
    onRefresh: () => void;
    onLoadDemo: (demo: SequenceData) => void;
    loading: boolean;
    width: number;
    projectScoped: boolean;
    showAllConstructs: boolean;
    onToggleAllConstructs: () => void;
}

export function SequenceLibrary({
    mobile = false,
    sequences,
    demos,
    demoLoading,
    selectedId,
    onSelect,
    onRefresh,
    onLoadDemo,
    loading,
    width,
    projectScoped,
    showAllConstructs,
    onToggleAllConstructs,
}: SequenceLibraryProps) {
    const [showDemos, setShowDemos] = useState(false);

    return (
        <div
            data-molbio-construct-library={mobile ? 'true' : undefined}
            className={mobile
                ? 'sequence-library flex h-full min-h-0 w-full flex-col overflow-hidden border-r border-slate-700 bg-slate-900'
                : 'sequence-library flex flex-shrink-0 flex-col overflow-hidden border-r border-slate-700 bg-slate-900'}
            style={{ width: mobile ? '100%' : `${width}px` }}
        >
            <div className="flex items-center justify-between p-3 border-b border-slate-700">
                <div>
                    <h3 className="font-semibold text-slate-200">DNA Sequence Shelf</h3>
                    <p className="text-xs text-slate-500">{projectScoped && !showAllConstructs ? 'DNA sequences in this Project' : 'All recent DNA sequences'}</p>
                </div>
                <button
                    onClick={onRefresh}
                    disabled={loading}
                    data-molbio-mobile-touch-target={mobile ? 'true' : undefined}
                    className={mobile
                        ? 'inline-flex min-h-12 min-w-12 items-center justify-center rounded transition-colors hover:bg-slate-700 disabled:opacity-50'
                        : 'rounded p-1.5 transition-colors hover:bg-slate-700 disabled:opacity-50'}
                    title="Refresh recent constructs"
                >
                    <svg className={`w-4 h-4 text-slate-400 ${loading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                </button>
            </div>
            {projectScoped && <button type="button" onClick={onToggleAllConstructs} className="border-b border-slate-700 px-3 py-2 text-left text-xs font-semibold text-cyan-300 hover:bg-slate-800">{showAllConstructs ? 'Show Project DNA sequences' : 'All DNA sequences'}</button>}

            <div
                data-molbio-scroll-region="construct-shelf"
                className="flex-1 min-h-0 overflow-y-auto overscroll-contain"
            >
                {(!projectScoped || showAllConstructs) && <div className="border-b border-slate-700">
                    <button
                        onClick={() => setShowDemos(!showDemos)}
                        data-molbio-mobile-touch-target={mobile ? 'true' : undefined}
                        className={`flex w-full items-center justify-between p-2 text-xs text-slate-400 hover:bg-slate-800 ${mobile ? 'min-h-12' : ''}`}
                    >
                        <span>Demo Plasmids ({demoLoading ? '…' : demos.length})</span>
                        <svg className={`w-3 h-3 transition-transform ${showDemos ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>
                    {showDemos && (
                        <div className="bg-slate-800/50">
                            {demoLoading ? (
                                <div className="px-4 py-3 text-xs text-slate-500">Loading demo dataset…</div>
                            ) : demos.map((demo, i) => (
                                <button
                                    key={i}
                                    onClick={() => onLoadDemo(demo)}
                                    data-molbio-mobile-touch-target={mobile ? 'true' : undefined}
                                    className={`w-full p-2 pl-4 text-left text-sm text-slate-300 transition-colors hover:bg-slate-700 ${mobile ? 'min-h-12' : ''}`}
                                >
                                    <span className="mr-2">{demo.circular ? '○' : '─'}</span>
                                    {demo.name}
                                </button>
                            ))}
                        </div>
                    )}
                </div>}

                <div className="border-b border-slate-700 px-3 py-2 text-xs font-semibold text-slate-400">
                    {projectScoped && !showAllConstructs ? `Project DNA sequences (${sequences.length})` : `Recent DNA sequences (${sequences.length})`}
                </div>

                {sequences.length === 0 ? (
                    <div className="p-4 text-center text-slate-500 text-sm">
                        <p>No recent constructs</p>
                        <p className="mt-1 text-xs">Use Acquire in the sequence toolbar to search or create one</p>
                    </div>
                ) : (
                    sequences.map((seq) => (
                        <button
                            key={seq.id}
                            onClick={() => onSelect(seq)}
                            data-molbio-mobile-touch-target={mobile ? 'true' : undefined}
                            className={`w-full border-b border-slate-800 p-3 text-left transition-colors hover:bg-slate-800 ${mobile ? 'min-h-12' : ''} ${selectedId === seq.id ? 'bg-slate-700' : ''}`}
                        >
                            <div className="font-medium text-slate-200 truncate">{seq.name}</div>
                            <div className="flex items-center gap-2 mt-1 text-xs text-slate-400">
                                <span>{seq.length.toLocaleString()} {sequenceUnitLabel(seq.sequence_type === 'rna' ? 'rna' : 'dna')}</span>
                                <span>•</span>
                                <span className="uppercase">{seq.sequence_type}</span>
                                {seq.is_circular && (
                                    <>
                                        <span>•</span>
                                        <span className="text-emerald-400">○</span>
                                    </>
                                )}
                            </div>
                            <div className="mt-1 text-[11px] text-slate-500">
                                {seq.feature_count} features{seq.updated_at ? ` • ${new Date(seq.updated_at).toLocaleDateString()}` : ''}
                            </div>
                        </button>
                    ))
                )}
            </div>
        </div>
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TOOL PANEL TABS
// ═══════════════════════════════════════════════════════════════════════════════

interface PanelTabsProps {
    active: ActivePanel;
    onChange: (panel: ActivePanel) => void;
    sequenceType: SequenceData['sequenceType'];
    readOnlyAuthority?: boolean;
}

const BASE_PANELS: { id: ActivePanel; label: string }[] = [
    { id: 'view', label: 'View' },
    { id: 'history', label: 'History' },
    { id: 'search', label: 'Find' },
    { id: 'align', label: 'Align' },
    { id: 'assembly', label: 'Assembly' },
    { id: 'edit', label: 'Edit' },
    { id: 'digest', label: 'Digest' },
    { id: 'pcr', label: 'PCR' },
    { id: 'primers', label: 'Primers' },
    { id: 'features', label: 'Features' },
];

const DEFAULT_DNA_TM_SETTINGS: PrimerTmSettings = {
    algorithm: 'nn_santalucia_hicks_2004',
    salt_correction: 'owczarzy_2008',
    primer_concentration_nM: 250,
    template_concentration_nM: 0,
    na_mM: 50,
    k_mM: 0,
    tris_mM: 0,
    mg_mM: 1.5,
    dntps_mM: 0.6,
    dmso_percent: 0,
    formamide_percent: 0,
    self_complementary: false,
};

const EXACT_REVISION_ALLOWED_PANELS = new Set<ActivePanel>(['view', 'history', 'search']);

function PanelTabs({ active, onChange, sequenceType, readOnlyAuthority = false }: PanelTabsProps) {
    const panels = sequenceType === 'rna'
        ? [...BASE_PANELS.slice(0, 6), { id: 'rna' as const, label: 'RNA' }, ...BASE_PANELS.slice(6)]
        : BASE_PANELS;

    return (
        <div className="panel-tabs flex flex-wrap border-b border-slate-700 bg-slate-800">
            {panels.map(({ id, label }) => {
                const disabled = readOnlyAuthority && !EXACT_REVISION_ALLOWED_PANELS.has(id);
                return (
                    <button
                        key={id}
                        onClick={() => onChange(active === id ? 'view' : id)}
                        disabled={disabled}
                        title={disabled ? 'Exact immutable molecular revisions are read-only.' : undefined}
                        className={`flex items-center gap-1 px-3 py-1.5 text-xs transition-colors ${active === id
                            ? 'bg-slate-700 text-slate-100 border-b-2 border-blue-500'
                            : disabled
                                ? 'cursor-not-allowed text-slate-600'
                                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                            }`}
                    >
                        <span>{label}</span>
                    </button>
                );
            })}
        </div>
    );
}

function normalizeFeatureList(features: Feature[]): Feature[] {
    return dedupeFeatures(features);
}

function normalizeFeatureRecord(feature: Partial<Feature> & Record<string, UntypedApiValue>, fallbackId: string): Feature {
    const rawSegments = Array.isArray(feature.segments)
        ? feature.segments
        : Array.isArray(feature.locations)
            ? feature.locations
            : [];

    const segments = rawSegments
        .map((segment: UntypedApiValue) => ({
            start: Number(segment?.start ?? segment?.startIndex ?? segment?.rangeBegin ?? feature.start ?? 0),
            end: Number(segment?.end ?? segment?.endIndex ?? segment?.rangeEnd ?? feature.end ?? 0),
        }))
        .filter((segment: { start: number; end: number }) => Number.isFinite(segment.start) && Number.isFinite(segment.end) && segment.end > segment.start);

    const start = typeof feature.start === 'number'
        ? feature.start
        : (segments[0]?.start ?? 0);
    const end = typeof feature.end === 'number'
        ? feature.end
        : (segments[segments.length - 1]?.end ?? 0);

    const normalizedType = normalizeFeatureType(feature.type || 'misc_feature');
    const normalized: Feature = {
        id: feature.id || fallbackId,
        name: feature.name || feature.type || 'feature',
        type: normalizedType,
        start,
        end,
        strand: feature.strand === -1 ? -1 : 1,
        color: feature.color || getFeatureColor(normalizedType),
        description: feature.description,
        notes: feature.notes,
        qualifiers: feature.qualifiers || feature.notes,
        provenance: feature.provenance,
        segments: segments.length > 0 ? segments : undefined,
    };

    const bounds = featureBounds(normalized);
    return {
        ...normalized,
        start: bounds.start,
        end: bounds.end,
    };
}

function normalizePrimerRecord(
    primer: Partial<Primer> & Record<string, UntypedApiValue>,
    fallbackId: string,
    sequenceLength: number,
    circular: boolean,
): Primer {
    const placement = normalizeStoredPrimerPlacement(primer, sequenceLength, circular);
    return {
        id: primer.id || fallbackId,
        name: primer.name || 'Primer',
        sequence: (primer.sequence || '').toUpperCase(),
        sequenceType: primer.sequenceType || primer.sequence_type || inferSequenceTypeFromSequence(primer.sequence || ''),
        start: placement.start,
        end: placement.end,
        strand: placement.strand,
        tm: primer.tm,
        gc_percent: primer.gc_percent,
        tm_algorithm: primer.tm_algorithm,
        tm_salt_correction: primer.tm_salt_correction,
        tm_settings: primer.tm_settings,
        notes: primer.notes,
        provenance: primer.provenance,
        sites: placement.sites,
    };
}

function sequenceDataFromApiRecord(seq: NucleotideSequenceResponse): SequenceData {
    return {
        name: seq.name,
        description: seq.description ?? undefined,
        sequence: seq.sequence,
        circular: seq.is_circular,
        sequenceType: seq.sequence_type,
        moleculeStrandedness: seq.molecule_strandedness,
        moleculeOrientation: seq.molecule_orientation,
        moleculeLabel: seq.molecule_label,
        features: normalizeFeatureList((seq.features || []).map((feature: Feature, index: number) => normalizeFeatureRecord(
            feature as Feature & Record<string, UntypedApiValue>,
            feature.id || `loaded_feature_${index}`,
        ))),
        primers: (seq.primers || []).map((primer: Primer, index: number) => normalizePrimerRecord(
            primer as Primer & Record<string, UntypedApiValue>,
            primer.id || `loaded_primer_${index}`,
            seq.sequence.length,
            seq.is_circular,
        )),
        translations: [],
        analysisTracks: (seq.analysis_tracks || []).map(trackFromApi),
        organism: seq.organism ?? undefined,
        accession: seq.accession ?? undefined,
        sourceFile: seq.source_file ?? undefined,
        parentId: seq.parent_id ?? null,
        operation: seq.operation ?? null,
        operationParams: seq.operation_params ?? null,
        version: seq.version ?? null,
    };
}

function sequenceDataFromMolecularRevision(revision: MolecularRevision): SequenceData {
    const snapshot = revision.snapshot;
    const sequence = snapshot.sequence;
    const sequenceType = snapshot.sequence_type;
    const isCircular = snapshot.is_circular;
    if (typeof sequence !== 'string' || !sequence) {
        throw new Error('Immutable molecular revision snapshot has no nucleotide sequence.');
    }
    if (sequenceType !== 'dna' && sequenceType !== 'rna') {
        throw new Error(`Immutable molecular revision has unsupported sequence type: ${String(sequenceType)}.`);
    }
    if (typeof isCircular !== 'boolean') {
        throw new Error('Immutable molecular revision snapshot has no exact topology.');
    }

    const moleculeStrandedness = snapshot.molecule_strandedness;
    const moleculeOrientation = snapshot.molecule_orientation;
    const normalizedStrandedness = moleculeStrandedness === 'single'
        || moleculeStrandedness === 'double'
        || moleculeStrandedness === 'unknown'
        ? moleculeStrandedness
        : (sequenceType === 'rna' ? 'single' : 'double');
    const normalizedOrientation = moleculeOrientation === 'positive'
        || moleculeOrientation === 'negative'
        || moleculeOrientation === 'ambisense'
        || moleculeOrientation === 'not_applicable'
        || moleculeOrientation === 'unknown'
        ? moleculeOrientation
        : (sequenceType === 'rna' ? 'unknown' : 'not_applicable');

    return sequenceDataFromApiRecord({
        id: revision.sequence_id,
        name: typeof snapshot.name === 'string' ? snapshot.name : revision.document_name,
        description: typeof snapshot.description === 'string' ? snapshot.description : null,
        sequence,
        sequence_type: sequenceType,
        molecule_strandedness: normalizedStrandedness,
        molecule_orientation: normalizedOrientation,
        molecule_label: typeof snapshot.molecule_label === 'string'
            ? snapshot.molecule_label
            : moleculeLabelForNucleotide(sequenceType, normalizedStrandedness, normalizedOrientation),
        is_circular: isCircular,
        length: sequence.length,
        features: Array.isArray(snapshot.features) ? snapshot.features as Feature[] : [],
        primers: Array.isArray(snapshot.primers) ? snapshot.primers as Primer[] : [],
        analysis_tracks: Array.isArray(snapshot.analysis_tracks) ? snapshot.analysis_tracks as SequenceAnalysisTrack[] : [],
        organism: typeof snapshot.organism === 'string' ? snapshot.organism : null,
        accession: typeof snapshot.accession === 'string' ? snapshot.accession : null,
        source_file: typeof snapshot.source_file === 'string' ? snapshot.source_file : null,
        gc_content: typeof snapshot.gc_content === 'number' ? snapshot.gc_content : null,
        parent_id: typeof snapshot.parent_id === 'string' ? snapshot.parent_id : null,
        operation: typeof snapshot.operation === 'string' ? snapshot.operation : null,
        operation_params: snapshot.operation_params && typeof snapshot.operation_params === 'object' && !Array.isArray(snapshot.operation_params)
            ? snapshot.operation_params as Record<string, unknown>
            : null,
        version: typeof snapshot.version === 'number' ? snapshot.version : revision.revision_number,
        created_at: typeof snapshot.created_at === 'string' ? snapshot.created_at : revision.created_at,
        updated_at: typeof snapshot.updated_at === 'string' ? snapshot.updated_at : null,
    });
}

function trackFromApi(track: SequenceAnalysisTrack): AnalysisTrack {
    return {
        id: track.id,
        name: track.name,
        kind: track.kind,
        description: track.description ?? undefined,
        color: track.color ?? undefined,
        sourceFormat: track.source_format ?? undefined,
        sourceName: track.source_name ?? undefined,
        sourceUrl: track.source_url ?? undefined,
        normalization: track.normalization ?? undefined,
        values: track.values || [],
        minValue: track.min_value ?? undefined,
        maxValue: track.max_value ?? undefined,
        createdAt: track.created_at ?? undefined,
    };
}

function trackToApi(track: AnalysisTrack): SequenceAnalysisTrack {
    return {
        id: track.id,
        name: track.name,
        kind: track.kind,
        description: track.description,
        color: track.color,
        source_format: track.sourceFormat,
        source_name: track.sourceName,
        source_url: track.sourceUrl,
        normalization: track.normalization,
        values: track.values,
        min_value: track.minValue,
        max_value: track.maxValue,
        created_at: track.createdAt,
    };
}

function sequencePayloadFromData(sequenceData: SequenceData): NucleotideSequenceCreate {
    const normalizedType = sequenceData.sequenceType === 'protein' ? 'dna' : sequenceData.sequenceType;
    return {
        name: sequenceData.name.trim() || 'Untitled sequence',
        description: sequenceData.description?.trim() || undefined,
        sequence: sequenceData.sequence,
        is_circular: sequenceData.circular,
        sequence_type: normalizedType,
        molecule_strandedness: sequenceData.moleculeStrandedness,
        molecule_orientation: sequenceData.moleculeOrientation,
        features: normalizeFeatureList(sequenceData.features).map((feature) => ({
            ...feature,
            qualifiers: feature.qualifiers,
            provenance: feature.provenance,
            segments: feature.segments,
            notes: feature.notes || feature.qualifiers,
        })),
        primers: sequenceData.primers?.map((primer) => ({
            ...primer,
            sequence_type: primer.sequenceType || inferSequenceTypeFromSequence(primer.sequence),
            notes: primer.notes,
            provenance: primer.provenance,
            sites: primer.sites,
        })),
        analysis_tracks: (sequenceData.analysisTracks || []).map(trackToApi),
        organism: sequenceData.organism,
        accession: sequenceData.accession,
        source_file: sequenceData.sourceFile,
    };
}

interface QuickAddMenuState {
    x: number;
    y: number;
    snapshot: SelectionSnapshot | null;
}

interface SelectionActionState {
    action: SelectionActionKind;
    snapshot: SelectionSnapshot;
}

interface WorkspaceTab {
    id: string;
    title: string;
    sequenceId: string | null;
    dirty: boolean;
    historyState: HistoryState;
    sequenceType: SequenceData['sequenceType'];
    exactMolecularRevision: MolecularRevision | null;
}

interface PendingWorkspaceTransition {
    description: string;
    workspaceId: string;
    execute: () => void;
    discard?: () => void;
}

function nextWorkspaceId(): string {
    return `workspace_${Math.random().toString(36).slice(2, 10)}`;
}

function WorkspaceTabs({
    tabs,
    activeId,
    onActivate,
    onClose,
}: {
    tabs: WorkspaceTab[];
    activeId: string;
    onActivate: (id: string) => void;
    onClose: (id: string) => void;
}) {
    return (
        <div className="border-b border-slate-700 bg-slate-900/80 px-2 py-1">
            <div className="flex gap-2 overflow-x-auto pb-1">
                {tabs.map((tab) => (
                    <div
                        key={tab.id}
                        className={`group flex min-w-[12rem] items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors ${
                            tab.id === activeId
                                ? 'border-cyan-500/50 bg-slate-800 text-slate-100'
                                : 'border-slate-700 bg-slate-900/70 text-slate-400 hover:border-slate-500 hover:text-slate-200'
                        }`}
                    >
                        <button
                            type="button"
                            onClick={() => onActivate(tab.id)}
                            className="min-w-0 flex-1 text-left"
                        >
                            <div className="truncate font-medium">
                                {tab.title || 'Untitled'}
                                {tab.dirty ? ' *' : ''}
                            </div>
                            <div className="mt-0.5 text-[10px] uppercase tracking-[0.1em] text-slate-500">
                                {tab.sequenceType}
                            </div>
                        </button>
                        <button
                            type="button"
                            onClick={() => onClose(tab.id)}
                            className="rounded p-1 text-slate-500 transition-colors hover:bg-slate-700 hover:text-slate-200"
                            title="Close workspace"
                        >
                            ×
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}

function sourceDisplayStrandForSequenceData(sequenceData: SequenceData): NucleotideDisplayStrand {
    if (sequenceData.sequenceType === 'protein') {
        return 'plus';
    }
    return displayStrandForMoleculeOrientation(sequenceData.moleculeOrientation);
}

function assertExactMolecularRevisionIdentity(
    revision: MolecularRevision,
    sequenceId: string,
    revisionId: string,
): void {
    if (
        revision.sequence_id !== sequenceId
        || revision.document_id !== sequenceId
        || revision.revision_id !== revisionId
        || revision.reopen_destination.params.sequence_id !== sequenceId
        || revision.reopen_destination.params.revision_id !== revisionId
    ) {
        throw new Error('Molecular revision response identity does not match the exact requested sequence/revision pair.');
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════

export function MolBioToolkitV2() {
    const location = useLocation();
    const { workspaceId, globalExperimentId, domainExperimentId, stateRevisionId, updateQueryParams, contextHref } = useGlobalExperimentContext();
    const queryParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
    const requestedCanonicalMolecularSequenceId = queryParams.get('molbio_sequence_id')?.trim() || null;
    const requestedCanonicalMolecularRevisionId = queryParams.get('molbio_revision_id')?.trim() || null;
    const requestedLegacyMolecularSequenceId = queryParams.get('sequence_id')?.trim() || null;
    const requestedLegacyMolecularRevisionId = queryParams.get('revision_id')?.trim() || null;
    const molecularOpenRequest = useMemo(() => resolveMolecularOpenRequest(queryParams), [queryParams]);
    const molecularOpenRequestKey = useMemo(() => JSON.stringify(molecularOpenRequest), [molecularOpenRequest]);
    const [approvedMolecularOpenRequestKey, setApprovedMolecularOpenRequestKey] = useState<string | null>(null);
    const molecularOpenRequestApproved = approvedMolecularOpenRequestKey === molecularOpenRequestKey;
    const requestedMolecularSequenceId = molecularOpenRequest.kind === 'current' || molecularOpenRequest.kind === 'exact'
        ? molecularOpenRequest.sequenceId
        : null;
    const requestedMolecularRevisionId = molecularOpenRequest.kind === 'exact'
        ? molecularOpenRequest.revisionId
        : null;
    const requestedPcrExperimentId = queryParams.get('pcr_experiment_id')?.trim() || null;
    const requestedPcrRevisionId = queryParams.get('pcr_revision_id')?.trim() || null;
    const hasPcrRevisionQuery = requestedPcrExperimentId !== null || requestedPcrRevisionId !== null;
    const hasExactMolecularPair = molecularOpenRequest.kind === 'exact';
    const hasIncompleteMolecularPair = molecularOpenRequest.kind === 'invalid';
    const [exactMolecularRevision, setExactMolecularRevision] = useState<MolecularRevision | null>(null);
    const [exactMolecularLoading, setExactMolecularLoading] = useState(false);
    const [exactMolecularError, setExactMolecularError] = useState<string | null>(null);
    const deepLinkSequenceId = requestedLegacyMolecularSequenceId;
    const deepLinkRevisionId = requestedLegacyMolecularRevisionId;
    const deepLinkOperationId = queryParams.get('operation_id')?.trim() || null;
    const deepLinkReceiptId = queryParams.get('receipt_id')?.trim() || null;
    const [deepLinkOperationState, setDeepLinkOperationState] = useState<'loading' | 'loaded' | 'unavailable' | null>(null);
    const openedDeepLinkRef = useRef<string | null>(null);
    // State
    const [sequences, setSequences] = useState<NucleotideSequenceListItem[]>([]);
    const [showAllConstructs, setShowAllConstructs] = useState(false);
    const [selectedSequenceId, setSelectedSequenceId] = useState<string | null>(null);
    const [showInputModal, setShowInputModal] = useState(false);
    const [visibility, setVisibility] = useState<VisibilityState>(DEFAULT_VISIBILITY);
    const [activePanel, setActivePanel] = useState<ActivePanel>('view');
    const [selection, setSelection] = useState<SelectionInfo | null>(null);
    const [quickAddMenu, setQuickAddMenu] = useState<QuickAddMenuState | null>(null);
    const quickAddMenuRef = useRef<HTMLDivElement | null>(null);
    const quickAddInvokerRef = useRef<HTMLDivElement | null>(null);
    const toolkitRootRef = useRef<HTMLDivElement | null>(null);
    const [selectionAction, setSelectionAction] = useState<SelectionActionState | null>(null);
    const [quickAddBusy, setQuickAddBusy] = useState<SelectionActionKind | null>(null);
    const [selectionActionError, setSelectionActionError] = useState<string | null>(null);
    const [highlightedRegions, setHighlightedRegions] = useState<HighlightedRegion[]>([]);
    const [isDirty, setIsDirty] = useState(false);
    const [pendingWorkspaceTransition, setPendingWorkspaceTransition] = useState<PendingWorkspaceTransition | null>(null);
    const [workspaceTransitionError, setWorkspaceTransitionError] = useState<string | null>(null);
    const [workspaceRestoreNotice, setWorkspaceRestoreNotice] = useState<string | null>(null);
    const [workspaceRestoreComplete, setWorkspaceRestoreComplete] = useState(false);
    const browserLeaveApprovedRef = useRef(false);
    const [colorPalette, setColorPalette] = useState<ColorPaletteName>('classic');
    const [visibleFrames, setVisibleFrames] = useState<Set<1 | 2 | 3 | -1 | -2 | -3>>(new Set([1]));
    const [derivedTranslations, setDerivedTranslations] = useState<SequenceData['translations']>([]);
    const [rnaStructureResult, setRnaStructureResult] = useState<RnaStructureResult | null>(null);
    const [rnaDisplayMode, setRnaDisplayMode] = useState<RnaStructureDisplayMode>('probability');
    const [selectedRnaTrackId, setSelectedRnaTrackId] = useState<string | null>(null);
    const [demoPlasmids, setDemoPlasmids] = useState<SequenceData[]>([]);
    const [demoLoading, setDemoLoading] = useState(true);
    const [ngsWorkups, setNgsWorkups] = useState<Array<{ job_id: string; scientific_status: 'PASS' | 'FAIL' | 'REVIEW'; revision_relation: 'current' | 'historical'; manifest_available: boolean }>>([]);
    const [ngsWorkupStatus, setNgsWorkupStatus] = useState<MobileMolBioWorkupStatus>('idle');

    useEffect(() => {
        if (!selectedSequenceId) {
            setNgsWorkups([]);
            setNgsWorkupStatus('idle');
            return;
        }
        let cancelled = false;
        setNgsWorkups([]);
        setNgsWorkupStatus('loading');
        fetch(`/api/molbio/sequences/${encodeURIComponent(selectedSequenceId)}/ngs-workup`)
            .then((response) => {
                if (!response.ok) throw new Error(`QC workup request failed with HTTP ${response.status}.`);
                return response.json();
            })
            .then((payload: unknown) => {
                const workups = parseMobileMolBioWorkups(payload);
                if (!workups) throw new Error('QC workup response is malformed.');
                if (!cancelled) {
                    setNgsWorkups(workups);
                    setNgsWorkupStatus('ready');
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setNgsWorkups([]);
                    setNgsWorkupStatus('unavailable');
                }
            });
        return () => { cancelled = true; };
    }, [selectedSequenceId]);

    // Enzymes currently displayed on the viewer - controlled by DigestPanel
    const [selectedEnzymes, setSelectedEnzymes] = useState<string[]>([
        // Default: Common 6-cutters for cloning
        'EcoRI', 'BamHI', 'HindIII', 'XbaI', 'SalI', 'PstI', 'SmaI', 'KpnI', 'SacI', 'XhoI',
        'NotI', 'NdeI', 'NcoI', 'BglII', 'SpeI', 'MluI', 'ApaI', 'ClaI', 'EcoRV', 'NheI',
        // Golden Gate / MoClo enzymes
        'BsaI', 'BbsI', 'SapI',
        // Other frequently used
        'AgeI', 'AscI', 'PacI', 'SfiI', 'FseI', 'PmeI'
    ]);

    // History hook for undo/redo
    const {
        sequenceData,
        set: setSequenceData,
        undo,
        redo,
        hydrate,
        historyState,
        historyJournal,
        canUndo,
        canRedo
    } = useSequenceHistory(EMPTY_SEQUENCE);

    const [workspaceTabs, setWorkspaceTabs] = useState<WorkspaceTab[]>([
        {
            id: 'workspace_initial',
            title: EMPTY_SEQUENCE.name,
            sequenceId: null,
            dirty: false,
            historyState: createHistoryState(EMPTY_SEQUENCE, 'Initialize workspace'),
            sequenceType: EMPTY_SEQUENCE.sequenceType,
            exactMolecularRevision: null,
        },
    ]);
    const [activeWorkspaceId, setActiveWorkspaceId] = useState('workspace_initial');
    const activeWorkspace = useMemo(
        () => workspaceTabs.find((tab) => tab.id === activeWorkspaceId) ?? null,
        [activeWorkspaceId, workspaceTabs],
    );
    const activeExactMolecularRevision = activeWorkspace?.exactMolecularRevision ?? null;
    const activeExactMatchesRequest = activeExactMolecularRevision !== null
        && activeExactMolecularRevision.sequence_id === requestedMolecularSequenceId
        && activeExactMolecularRevision.revision_id === requestedMolecularRevisionId;
    const selectedExactMolecularRevision = exactMolecularRevision
        ?? (hasExactMolecularPair
            ? (activeExactMatchesRequest ? activeExactMolecularRevision : null)
            : activeExactMolecularRevision);
    const isExactMolecularAuthority = resolveExactMolecularAuthority(
        molecularOpenRequestApproved,
        hasExactMolecularPair,
        activeExactMolecularRevision !== null,
    );
    const exactMolecularAuthorityRef = useRef(isExactMolecularAuthority);
    exactMolecularAuthorityRef.current = isExactMolecularAuthority;
    const sequenceLoadControllerRef = useRef(createLatestAsyncResourceController());
    const mobileSequenceIntentRef = useRef<MolBioMobileSequenceIntent | null>(null);
    useEffect(() => () => sequenceLoadControllerRef.current.dispose(), []);
    const [activeDisplayStrand, setActiveDisplayStrand] = useState<NucleotideDisplayStrand>(() => sourceDisplayStrandForSequenceData(EMPTY_SEQUENCE));
    const sourceDisplayStrand = useMemo(
        () => sourceDisplayStrandForSequenceData(sequenceData),
        [sequenceData.moleculeOrientation, sequenceData.sequenceType],
    );

    const [restrictionCatalog, setRestrictionCatalog] = useState<RestrictionCatalogReceipt | null>(null);
    const [restrictionCatalogRecords, setRestrictionCatalogRecords] = useState<RestrictionRecord[]>([]);
    const [restrictionAnalysis, setRestrictionAnalysis] = useState<RestrictionAnalysisResponse | null>(null);
    const [restrictionAuthorityLoading, setRestrictionAuthorityLoading] = useState(false);
    const [restrictionAuthorityError, setRestrictionAuthorityError] = useState<string | null>(null);
    const [restrictionDigest, setRestrictionDigest] = useState<RestrictionDigestSimulation | null>(null);
    const [restrictionDigestLoading, setRestrictionDigestLoading] = useState(false);
    const [restrictionDigestError, setRestrictionDigestError] = useState<string | null>(null);

    const restrictionAuthorityControllerRef = useRef(createLatestAsyncResourceController());
    const restrictionDigestControllerRef = useRef(createLatestAsyncResourceController());
    const restrictionSource = useMemo<RestrictionSource | null>(() => {
        if (!sequenceData.sequence || sequenceData.sequenceType !== 'dna') return null;
        if (selectedExactMolecularRevision) {
            return {
                kind: 'molecular_revision',
                sequence_id: selectedExactMolecularRevision.sequence_id,
                revision_id: selectedExactMolecularRevision.revision_id,
                expected_content_sha256: selectedExactMolecularRevision.content_sha256,
                topology: sequenceData.circular ? 'circular' : 'linear',
            };
        }
        return {
            kind: 'inline_dna',
            name: sequenceData.name,
            dna: sequenceData.sequence,
            topology: sequenceData.circular ? 'circular' : 'linear',
        };
    }, [selectedExactMolecularRevision, sequenceData.circular, sequenceData.name, sequenceData.sequence, sequenceData.sequenceType]);
    const restrictionSelectionKey = useMemo(() => JSON.stringify([...selectedEnzymes].sort()), [selectedEnzymes]);

    useEffect(() => {
        const authorityController = restrictionAuthorityControllerRef.current;
        const digestController = restrictionDigestControllerRef.current;
        const token = authorityController.begin();
        digestController.begin();
        setRestrictionCatalog(null);
        setRestrictionCatalogRecords([]);
        setRestrictionAnalysis(null);
        setRestrictionDigest(null);

        setRestrictionDigestError(null);
        setRestrictionAuthorityError(null);
        if (!restrictionSource) {
            setRestrictionAuthorityLoading(false);
            return;
        }
        const abort = new AbortController();
        setRestrictionAuthorityLoading(true);
        void (async () => {
            try {
                const catalogResult = await fetchRestrictionCatalog({ signal: abort.signal });
                if (!authorityController.isCurrent(token)) return;
                const analysisResult = await fetchRestrictionAnalysis({
                    source: restrictionSource,
                    catalog: {
                        catalog_id: catalogResult.catalog.catalog_id,
                        expected_catalog_sha256: catalogResult.catalog.catalog_sha256,
                    },
                    signal: abort.signal,
                });
                if (!authorityController.isCurrent(token)) return;
                setRestrictionCatalog(catalogResult.catalog);
                setRestrictionCatalogRecords(catalogResult.items);
                setRestrictionAnalysis(analysisResult);
                setRestrictionAuthorityError(null);
            } catch (error) {
                if (!authorityController.isCurrent(token)) return;
                setRestrictionCatalog(null);
                setRestrictionCatalogRecords([]);
                setRestrictionAnalysis(null);
                setRestrictionAuthorityError(error instanceof Error ? error.message : 'Restriction analysis is unavailable.');
            } finally {
                if (authorityController.isCurrent(token)) setRestrictionAuthorityLoading(false);
            }
        })();
        return () => abort.abort();
    }, [restrictionSelectionKey, restrictionSource]);

    useEffect(() => () => {
        restrictionAuthorityControllerRef.current.dispose();
        restrictionDigestControllerRef.current.dispose();
    }, []);

    const runRestrictionDigest = useCallback((enzymeIds: string[]) => {
        const controller = restrictionDigestControllerRef.current;
        const token = controller.begin();
        setRestrictionDigest(null);
        setRestrictionDigestError(null);
        if (!restrictionSource || !restrictionCatalog || enzymeIds.length === 0) {
            setRestrictionDigestLoading(false);
            return;
        }
        setRestrictionDigestLoading(true);
        const expectedSource = restrictionSource;
        const expectedCatalog = restrictionCatalog;
        void simulateRestrictionDigest({
            source: expectedSource,
            catalog: { catalog_id: expectedCatalog.catalog_id, expected_catalog_sha256: expectedCatalog.catalog_sha256 },
            enzymeIds,
        }).then((result) => {
            if (!controller.isCurrent(token)) return;
            setRestrictionDigest(result);
        }).catch((error) => {
            if (!controller.isCurrent(token)) return;
            setRestrictionDigest(null);
            setRestrictionDigestError(error instanceof Error ? error.message : 'Restriction digest is unavailable.');
        }).finally(() => {
            if (controller.isCurrent(token)) setRestrictionDigestLoading(false);
        });
    }, [restrictionCatalog, restrictionSource]);

    const handleRestrictionDigestSelection = useCallback((enzymeIds: string[]) => {
        void enzymeIds;
        restrictionDigestControllerRef.current.begin();

        setRestrictionDigest(null);
        setRestrictionDigestError(null);
        setRestrictionDigestLoading(false);
    }, []);

    const handleDisplayStrandChange = useCallback((strand: NucleotideDisplayStrand) => {
        setActiveDisplayStrand(strand);
    }, []);

    // API hooks
    const {
        loading,
        error,
        listSequences,
        getSequence,
        invalidateGetSequence,
        createSequence,
        updateSequence
    } = useSequenceOperations();

    // Load sequence library on mount
    const loadLibrary = useCallback(async () => {
        if (!showAllConstructs && workspaceId && globalExperimentId && domainExperimentId && stateRevisionId) {
            const model = await fetchProjectHub(workspaceId, globalExperimentId, domainExperimentId, stateRevisionId);
            setSequences(projectHubDNASequencesToConstructShelf(model));
            return;
        }
        const seqs = await listSequences({
            limit: 24,
            sort_by: 'updated_at',
            sort_desc: true,
        });
        setSequences(seqs);
    }, [domainExperimentId, globalExperimentId, listSequences, showAllConstructs, stateRevisionId, workspaceId]);

    useEffect(() => {
        loadLibrary();
    }, [loadLibrary]);

    useEffect(() => {
        let cancelled = false;
        const loadDemos = async () => {
            try {
                const demos = await loadDemoPlasmids();
                if (!cancelled) {
                    setDemoPlasmids(demos);
                }
            } catch (error) {
                console.error('Failed to load demo plasmids:', error);
            } finally {
                if (!cancelled) {
                    setDemoLoading(false);
                }
            }
        };
        void loadDemos();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        setWorkspaceTabs((current) => current.map((tab) => (
            tab.id === activeWorkspaceId
                ? {
                    ...tab,
                    title: sequenceData.name || tab.title,
                    sequenceId: selectedSequenceId,
                    dirty: isDirty,
                    historyState,
                    sequenceType: sequenceData.sequenceType,
                }
                : tab
        )));
    }, [activeWorkspaceId, historyState, isDirty, selectedSequenceId, sequenceData.name, sequenceData.sequenceType]);

    const approveMolecularOpenRequest = useCallback((request: MolecularOpenRequest) => {
        setApprovedMolecularOpenRequestKey(JSON.stringify(request));
    }, []);

    const setMolecularQueryForWorkspace = useCallback((workspace: WorkspaceTab | null) => {
        if (workspace?.exactMolecularRevision) {
            approveMolecularOpenRequest({
                kind: 'exact',
                sequenceId: workspace.exactMolecularRevision.sequence_id,
                revisionId: workspace.exactMolecularRevision.revision_id,
            });
            updateQueryParams({
                molbio_sequence_id: workspace.exactMolecularRevision.sequence_id,
                molbio_revision_id: workspace.exactMolecularRevision.revision_id,
            });
            return;
        }
        if (workspace?.sequenceId) {
            approveMolecularOpenRequest({ kind: 'current', sequenceId: workspace.sequenceId });
            updateQueryParams({
                molbio_sequence_id: workspace.sequenceId,
                molbio_revision_id: null,
            });
            return;
        }
        approveMolecularOpenRequest({ kind: 'none' });
        updateQueryParams({ molbio_sequence_id: null, molbio_revision_id: null });
    }, [approveMolecularOpenRequest, updateQueryParams]);

    const activateWorkspaceImmediately = useCallback((workspaceId: string) => {
        const workspace = workspaceTabs.find((tab) => tab.id === workspaceId);
        if (!workspace) {
            return;
        }
        setMolecularQueryForWorkspace(workspace);
        setActiveWorkspaceId(workspaceId);
        setActiveDisplayStrand(sourceDisplayStrandForSequenceData(workspace.historyState.present));
        hydrate(workspace.historyState);
        setSelectedSequenceId(workspace.sequenceId);
        setIsDirty(workspace.exactMolecularRevision ? false : workspace.dirty);
        setSelection(null);
        setHighlightedRegions([]);
        setRnaStructureResult(null);
        setSelectedRnaTrackId(workspace.historyState.present.analysisTracks?.[0]?.id || null);
        if (workspace.exactMolecularRevision && !EXACT_REVISION_ALLOWED_PANELS.has(activePanel)) {
            setActivePanel('view');
        }
    }, [activePanel, hydrate, setMolecularQueryForWorkspace, workspaceTabs]);

    const openWorkspace = useCallback((nextSequence: SequenceData, options?: {
        sequenceId?: string | null;
        dirty?: boolean;
        label?: string;
    }) => {
        const sequenceId = options?.sequenceId || null;
        const tabId = sequenceId ? molecularWorkspaceId(sequenceId) : nextWorkspaceId();
        const nextHistory = createHistoryState(nextSequence, options?.label || 'Open workspace');
        const nextTab: WorkspaceTab = {
            id: tabId,
            title: nextSequence.name || 'Untitled',
            sequenceId,
            dirty: options?.dirty ?? false,
            historyState: nextHistory,
            sequenceType: nextSequence.sequenceType,
            exactMolecularRevision: null,
        };
        setWorkspaceTabs((current) => sequenceId
            ? upsertStableMolecularWorkspace(current, nextTab)
            : [...current, nextTab]);
        setActiveWorkspaceId(tabId);
        setActiveDisplayStrand(sourceDisplayStrandForSequenceData(nextSequence));
        hydrate(nextHistory);
        setSelectedSequenceId(sequenceId);
        setIsDirty(options?.dirty ?? false);
        setSelection(null);
        setHighlightedRegions([]);
        setRnaStructureResult(null);
        setSelectedRnaTrackId(nextSequence.analysisTracks?.[0]?.id || null);
    }, [hydrate]);

    const openExactMolecularWorkspace = useCallback((revision: MolecularRevision, nextSequence: SequenceData) => {
        const tabId = molecularWorkspaceId(revision.sequence_id);
        const nextHistory = createHistoryState(
            nextSequence,
            `Open immutable revision ${revision.revision_number}`,
        );
        setWorkspaceTabs((current) => upsertStableMolecularWorkspace(current, {
            id: tabId,
            title: `${nextSequence.name} · r${revision.revision_number} (read-only)`,
            sequenceId: revision.sequence_id,
            dirty: false,
            historyState: nextHistory,
            sequenceType: nextSequence.sequenceType,
            exactMolecularRevision: revision,
        }));
        setActiveWorkspaceId(tabId);
        setActiveDisplayStrand(sourceDisplayStrandForSequenceData(nextSequence));
        hydrate(nextHistory);
        setSelectedSequenceId(revision.sequence_id);
        setIsDirty(false);
        setSelection(null);
        setHighlightedRegions([]);
        setRnaStructureResult(null);
        setSelectedRnaTrackId(nextSequence.analysisTracks?.[0]?.id || null);
        setActivePanel((current) => EXACT_REVISION_ALLOWED_PANELS.has(current) ? current : 'view');
    }, [hydrate]);

    useEffect(() => {
        if (!workspaceRestoreComplete || !molecularOpenRequestApproved) return;
        if (!hasExactMolecularPair || !requestedMolecularSequenceId || !requestedMolecularRevisionId) {
            setExactMolecularRevision(null);
            setExactMolecularLoading(false);
            setExactMolecularError(null);
            return;
        }

        const mobileIntentResolution = resolveMolBioMobileSequenceIntent(
            mobileSequenceIntentRef.current,
            requestedMolecularSequenceId,
            requestedMolecularRevisionId,
        );
        if (!mobileIntentResolution.allow) return;
        if (mobileIntentResolution.clearIntent) {
            mobileSequenceIntentRef.current = null;
            sequenceLoadControllerRef.current.begin();
            invalidateGetSequence();
        }

        let cancelled = false;
        setExactMolecularRevision(null);
        setExactMolecularLoading(true);
        setExactMolecularError(null);
        void fetchMolecularRevision(requestedMolecularSequenceId, requestedMolecularRevisionId)
            .then((revision) => {
                assertExactMolecularRevisionIdentity(
                    revision,
                    requestedMolecularSequenceId,
                    requestedMolecularRevisionId,
                );
                const nextSequence = sequenceDataFromMolecularRevision(revision);
                if (cancelled) return;
                setExactMolecularRevision(revision);
                openExactMolecularWorkspace(revision, nextSequence);
            })
            .catch((revisionError) => {
                if (!cancelled) {
                    setExactMolecularError(revisionError instanceof Error ? revisionError.message : String(revisionError));
                }
            })
            .finally(() => {
                if (!cancelled) setExactMolecularLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [
        hasExactMolecularPair,
        molecularOpenRequestApproved,
        invalidateGetSequence,
        openExactMolecularWorkspace,
        requestedMolecularRevisionId,
        requestedMolecularSequenceId,
        workspaceRestoreComplete,
    ]);

    const closeWorkspaceImmediately = useCallback((workspaceId: string) => {
        if (workspaceTabs.length === 1) {
            const emptyHistory = createHistoryState(EMPTY_SEQUENCE, 'Reset workspace');
            const emptyWorkspaceId = nextWorkspaceId();
            setWorkspaceTabs([{
                id: emptyWorkspaceId,
                title: EMPTY_SEQUENCE.name,
                sequenceId: null,
                dirty: false,
                historyState: emptyHistory,
                sequenceType: EMPTY_SEQUENCE.sequenceType,
                exactMolecularRevision: null,
            }]);
            setActiveWorkspaceId(emptyWorkspaceId);
            setActiveDisplayStrand(sourceDisplayStrandForSequenceData(EMPTY_SEQUENCE));
            hydrate(emptyHistory);
            setSelectedSequenceId(null);
            setIsDirty(false);
            setSelection(null);
            setHighlightedRegions([]);
            setRnaStructureResult(null);
            setSelectedRnaTrackId(null);
            setMolecularQueryForWorkspace(null);
            return;
        }

        const currentIndex = workspaceTabs.findIndex((tab) => tab.id === workspaceId);
        const remaining = workspaceTabs.filter((tab) => tab.id !== workspaceId);
        setWorkspaceTabs(remaining);

        if (workspaceId === activeWorkspaceId) {
            const nextWorkspace = remaining[Math.max(0, currentIndex - 1)] || remaining[0];
            if (nextWorkspace) {
                setActiveWorkspaceId(nextWorkspace.id);
                setActiveDisplayStrand(sourceDisplayStrandForSequenceData(nextWorkspace.historyState.present));
                hydrate(nextWorkspace.historyState);
                setSelectedSequenceId(nextWorkspace.sequenceId);
                setIsDirty(nextWorkspace.exactMolecularRevision ? false : nextWorkspace.dirty);
                setSelection(null);
                setHighlightedRegions([]);
                setRnaStructureResult(null);
                setSelectedRnaTrackId(nextWorkspace.historyState.present.analysisTracks?.[0]?.id || null);
                setMolecularQueryForWorkspace(nextWorkspace);
            }
        }
    }, [activeWorkspaceId, hydrate, setMolecularQueryForWorkspace, workspaceTabs]);

    // Auto-compute ORFs for display only. Keep them out of persisted undo history.
    useEffect(() => {
        if (sequenceData.sequence && sequenceData.sequence.length > 100) {
            setDerivedTranslations(findOpenReadingFrames(
                sequenceData.sequence,
                100,
                sequenceData.circular,
            ));
        } else {
            setDerivedTranslations([]);
        }
    }, [sequenceData.circular, sequenceData.sequence]);

    const viewerSequenceData = useMemo(() => ({
        ...sequenceData,
        translations: derivedTranslations,
    }), [sequenceData, derivedTranslations]);

    useEffect(() => {
        if (sequenceData.sequenceType !== 'rna') {
            if (activePanel === 'rna') {
                setActivePanel('view');
            }
            setRnaStructureResult(null);
            setSelectedRnaTrackId(null);
            return;
        }

        if (
            rnaStructureResult &&
            (rnaStructureResult.sequence !== sequenceData.sequence || rnaStructureResult.circular !== sequenceData.circular)
        ) {
            setRnaStructureResult(null);
        }
    }, [activePanel, rnaStructureResult, sequenceData.circular, sequenceData.sequence, sequenceData.sequenceType]);

    // Load selected sequence
    const loadSequence = useCallback(async (id: string, options?: { forceReload?: boolean }): Promise<boolean> => {
        const loadToken = sequenceLoadControllerRef.current.begin();
        invalidateGetSequence();
        const shouldUpdateRequest = requestedMolecularRevisionId || requestedMolecularSequenceId !== id;
        if (shouldUpdateRequest) {
            approveMolecularOpenRequest({ kind: 'current', sequenceId: id });
            updateQueryParams({
                molbio_sequence_id: id,
                molbio_revision_id: null,
            });
        }
        const existing = workspaceTabs.find((tab) => tab.sequenceId === id && !tab.exactMolecularRevision);
        if (existing && !options?.forceReload) {
            if (!sequenceLoadControllerRef.current.isCurrent(loadToken)) return false;
            activateWorkspaceImmediately(existing.id);
            return true;
        }
        const seq = await getSequence(id);
        if (!sequenceLoadControllerRef.current.isCurrent(loadToken)) return false;
        if (!seq) {
            return false;
        }
        const converted = sequenceDataFromApiRecord(seq);
        openWorkspace(converted, {
            sequenceId: id,
            dirty: false,
            label: `Open ${seq.name}`,
        });
        return true;
    }, [
        activateWorkspaceImmediately,
        approveMolecularOpenRequest,
        getSequence,
        invalidateGetSequence,
        openWorkspace,
        requestedMolecularRevisionId,
        requestedMolecularSequenceId,
        updateQueryParams,
        workspaceTabs,
    ]);

    useEffect(() => {
        if (!workspaceRestoreComplete || !molecularOpenRequestApproved) return;
        if (!requestedMolecularSequenceId || requestedMolecularRevisionId) return;
        const mobileIntentResolution = resolveMolBioMobileSequenceIntent(
            mobileSequenceIntentRef.current,
            requestedMolecularSequenceId,
            requestedMolecularRevisionId,
        );
        if (!mobileIntentResolution.allow) return;
        if (mobileIntentResolution.clearIntent) mobileSequenceIntentRef.current = null;
        const activeWorkspace = workspaceTabs.find((tab) => tab.id === activeWorkspaceId);
        if (
            selectedSequenceId === requestedMolecularSequenceId
            && activeWorkspace
            && !activeWorkspace.exactMolecularRevision
        ) {
            return;
        }
        void loadSequence(requestedMolecularSequenceId);
    }, [
        activeWorkspaceId,
        loadSequence,
        molecularOpenRequestApproved,
        requestedMolecularRevisionId,
        requestedMolecularSequenceId,
        selectedSequenceId,
        workspaceRestoreComplete,
        workspaceTabs,
    ]);

    const openCurrentEditableProjection = useCallback(() => {
        const sequenceId = requestedMolecularSequenceId ?? activeExactMolecularRevision?.sequence_id ?? null;
        if (!sequenceId) return;
        void loadSequence(sequenceId);
    }, [activeExactMolecularRevision?.sequence_id, loadSequence, requestedMolecularSequenceId]);

    useEffect(() => {
        if (!workspaceRestoreComplete) return;
        if (!deepLinkSequenceId) return;
        const identity = `${deepLinkSequenceId}:${deepLinkRevisionId ?? 'current'}`;
        if (openedDeepLinkRef.current === identity) return;
        openedDeepLinkRef.current = identity;
        let cancelled = false;
        const openDeepLink = async () => {
            try {
                if (!deepLinkRevisionId) {
                    await loadSequence(deepLinkSequenceId);
                    return;
                }
                const [current, response] = await Promise.all([
                    getSequence(deepLinkSequenceId),
                    fetch(
                        `/api/molbio/sequences/${encodeURIComponent(deepLinkSequenceId)}/revisions/${encodeURIComponent(deepLinkRevisionId)}`,
                    ),
                ]);
                if (!current || !response.ok) throw new Error('Exact molecular revision is unavailable.');
                const detail = await response.json() as { revision_id?: string; sequence_id?: string; snapshot?: Record<string, unknown> };
                if (
                    detail.revision_id !== deepLinkRevisionId
                    || detail.sequence_id !== deepLinkSequenceId
                    || !detail.snapshot
                ) {
                    throw new Error('Molecular revision identity does not match the requested record.');
                }
                if (cancelled) return;
                const converted = sequenceDataFromApiRecord({
                    ...current,
                    ...detail.snapshot,
                    id: deepLinkSequenceId,
                });
                openWorkspace(converted, {
                    sequenceId: deepLinkSequenceId,
                    dirty: false,
                    label: `Open revision ${deepLinkRevisionId}`,
                });
            } catch (error) {
                console.error('Failed to reopen exact molecular record:', error);
            }
        };
        void openDeepLink();
        return () => { cancelled = true; };
    }, [deepLinkRevisionId, deepLinkSequenceId, getSequence, loadSequence, openWorkspace, workspaceRestoreComplete]);

    useEffect(() => {
        if (!deepLinkOperationId) {
            setDeepLinkOperationState(null);
            return;
        }
        let cancelled = false;
        setDeepLinkOperationState('loading');
        void fetch(`/api/molbio/operations/${encodeURIComponent(deepLinkOperationId)}`)
            .then(async (response) => {
                if (!response.ok) throw new Error('operation unavailable');
                const detail = await response.json() as {
                    operation_id?: string;
                    inputs?: Array<{ revision_id?: string }>;
                    outputs?: Array<{ revision_id?: string }>;
                };
                const linkedRevisionIds = [...(detail.inputs ?? []), ...(detail.outputs ?? [])]
                    .map((item) => item.revision_id)
                    .filter(Boolean);
                if (
                    detail.operation_id !== deepLinkOperationId
                    || (deepLinkRevisionId && !linkedRevisionIds.includes(deepLinkRevisionId))
                ) {
                    throw new Error('operation identity mismatch');
                }
                if (!cancelled) setDeepLinkOperationState('loaded');
            })
            .catch(() => {
                if (!cancelled) setDeepLinkOperationState('unavailable');
            });
        return () => {
            cancelled = true;
        };
    }, [deepLinkOperationId, deepLinkRevisionId]);

    // Load demo plasmid (no API, direct)
    const loadDemo = useCallback((demo: SequenceData) => {
        openWorkspace({
            ...demo,
            features: normalizeFeatureList(demo.features || []),
            analysisTracks: demo.analysisTracks || [],
        }, {
            sequenceId: null,
            dirty: false,
            label: `Open demo ${demo.name}`,
        });
    }, [openWorkspace]);

    // Create a new in-memory sequence from pasted text (can be saved afterward)
    const handlePasteSequence = useCallback((data: {
        name: string;
        sequence: string;
        sequenceType: 'dna' | 'rna';
        circular: boolean;
        description?: string;
    }) => {
        if (exactMolecularAuthorityRef.current) return;
        const moleculeStrandedness = data.sequenceType === 'rna' ? 'single' : 'double';
        const moleculeOrientation = moleculeStrandedness === 'double' ? 'not_applicable' : 'unknown';
        const newSequence: SequenceData = {
            name: data.name,
            description: data.description,
            sequence: data.sequence,
            circular: data.circular,
            sequenceType: data.sequenceType,
            moleculeStrandedness,
            moleculeOrientation,
            moleculeLabel: moleculeLabelForNucleotide(data.sequenceType, moleculeStrandedness, moleculeOrientation),
            features: [],
            primers: [],
            translations: [],
            analysisTracks: [],
        };

        openWorkspace(newSequence, {
            sequenceId: null,
            dirty: true,
            label: `Create ${data.name}`,
        });
    }, [openWorkspace]);

    const handleOpenPrimerAsConstruct = useCallback((data: {
        name: string;
        sequence: string;
        description?: string;
    }) => {
        handlePasteSequence({
            name: data.name,
            description: data.description,
            sequence: data.sequence.toUpperCase(),
            sequenceType: inferSequenceTypeFromSequence(data.sequence),
            circular: false,
        });
    }, [handlePasteSequence]);

    // Import file using Teselagen bio-parsers
    const handleImport = useCallback(async (file: File, topology: ImportTopology = 'preserve') => {
        if (exactMolecularAuthorityRef.current) return;
        try {
            const result = await anyToJson(file, {
                fileName: file.name,
                parseOptions: { inclusive1BasedStart: false, jsonType: 'json' }
            });
            const results = Array.isArray(result) ? result : [result];

            if (results.length === 0 || !results[0]?.parsedSequence) {
                alert('Failed to parse file. Supported formats: GenBank, FASTA, SnapGene, etc.');
                return;
            }

            const parsed = results[0].parsedSequence;
            const moleculeMetadata = inferNucleotideMoleculeMetadataFromParsedRecord(parsed);
            const inferredType = parsed.isProtein ? 'protein' : moleculeMetadata.sequenceType;

            if (inferredType === 'protein') {
                alert('Protein records are not supported in the molecular toolkit yet. Import a DNA or RNA construct instead.');
                return;
            }

            const normalizedSequence = normalizeSequenceForType(parsed.sequence || '', inferredType);
            const importedCircular = applyImportedTopology(Boolean(parsed.circular), topology);
            const sequenceData: SequenceData = {
                name: parsed.name || file.name.replace(/\.[^.]+$/, ''),
                description: parsed.description || undefined,
                sequence: normalizedSequence,
                circular: importedCircular,
                sequenceType: inferredType,
                moleculeStrandedness: moleculeMetadata.moleculeStrandedness,
                moleculeOrientation: moleculeMetadata.moleculeOrientation,
                moleculeLabel: moleculeMetadata.moleculeLabel,
                features: normalizeFeatureList((parsed.features || []).map((f: UntypedApiValue, i: number) => normalizeFeatureRecord(f, `f_${i}`))),
                primers: (parsed.primers || []).map((p: UntypedApiValue, i: number) => normalizePrimerRecord(
                    p,
                    `p_${i}`,
                    normalizedSequence.length,
                    importedCircular,
                )),
                translations: [],
                analysisTracks: [],
                sourceFile: file.name,
            };

            if (exactMolecularAuthorityRef.current) return;
            const savedImport = await createSequence(sequencePayloadFromData(sequenceData));
            if (savedImport && !exactMolecularAuthorityRef.current) {
                openWorkspace(sequenceDataFromApiRecord(savedImport), {
                    sequenceId: savedImport.id,
                    dirty: false,
                    label: `Import and save ${savedImport.name}`,
                });
                await loadLibrary();
                return;
            }

            openWorkspace(sequenceData, {
                sequenceId: null,
                dirty: true,
                label: `Import ${sequenceData.name}`,
            });
            alert('Imported into the editor, but saving to the construct library failed. Use Save after checking the API status.');
        } catch (error) {
            console.error('Import error:', error);
            alert(`Failed to parse file: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    }, [openWorkspace, createSequence, loadLibrary]);

    // Save one explicit workspace so transition order cannot retarget the write.
    const saveWorkspace = useCallback(async (workspaceId: string): Promise<boolean> => {
        const target = workspaceTabs.find((tab) => tab.id === workspaceId);
        if (!target || target.exactMolecularRevision) return false;

        const targetIsActive = workspaceId === activeWorkspaceId;
        const targetHistory = targetIsActive ? historyState : target.historyState;
        const targetData = targetIsActive ? sequenceData : targetHistory.present;
        if (!targetData.sequence.trim()) return false;

        const payload = sequencePayloadFromData(targetData);
        const savedRecord = target.sequenceId
            ? await updateSequence(target.sequenceId, payload)
            : await createSequence(payload);
        if (!savedRecord) return false;

        const savedData = sequenceDataFromApiRecord(savedRecord);
        const savedHistory: HistoryState = {
            ...targetHistory,
            present: savedData,
        };
        const stableWorkspaceId = molecularWorkspaceId(savedRecord.id);
        setWorkspaceTabs((current) => current.map((tab) => tab.id === workspaceId
            ? {
                ...tab,
                id: stableWorkspaceId,
                title: savedData.name || tab.title,
                sequenceId: savedRecord.id,
                dirty: false,
                historyState: savedHistory,
                sequenceType: savedData.sequenceType,
                exactMolecularRevision: null,
            }
            : tab));

        if (targetIsActive) {
            setActiveWorkspaceId(stableWorkspaceId);
            setSelectedSequenceId(savedRecord.id);
            hydrate(savedHistory);
            setIsDirty(false);
        }
        void loadLibrary();
        return true;
    }, [activeWorkspaceId, createSequence, historyState, hydrate, loadLibrary, sequenceData, updateSequence, workspaceTabs]);

    const saveSequence = useCallback(
        (): Promise<boolean> => saveWorkspace(activeWorkspaceId),
        [activeWorkspaceId, saveWorkspace],
    );

    const discardActiveWorkspace = useCallback(() => {
        setWorkspaceTabs((current) => current.filter((tab) => tab.id !== activeWorkspaceId));
    }, [activeWorkspaceId]);

    const requestWorkspaceTransition = useCallback((
        description: string,
        execute: () => void,
        options?: {
            dirty?: boolean;
            workspaceId?: string;
            discard?: () => void;
        },
    ) => {
        const dirty = options?.dirty ?? isDirty;
        if (!dirty) {
            execute();
            return;
        }
        setWorkspaceTransitionError(null);
        setPendingWorkspaceTransition({
            description,
            workspaceId: options?.workspaceId ?? activeWorkspaceId,
            execute,
            discard: options?.discard,
        });
    }, [activeWorkspaceId, isDirty]);

    const completeWorkspaceTransition = useCallback(async (choice: DirtyWorkspaceChoice) => {
        if (!pendingWorkspaceTransition) return;
        if (choice === 'stay') {
            setPendingWorkspaceTransition(null);
            setWorkspaceTransitionError(null);
            return;
        }
        const shouldContinue = await runDirtyWorkspaceTransition(
            true,
            choice,
            () => saveWorkspace(pendingWorkspaceTransition.workspaceId),
        );
        if (!shouldContinue) {
            if (choice === 'save') setWorkspaceTransitionError('Save failed. The workspace remains open with unsaved changes.');
            return;
        }
        const transition = pendingWorkspaceTransition;
        setPendingWorkspaceTransition(null);
        setWorkspaceTransitionError(null);
        if (choice === 'discard') transition.discard?.();
        transition.execute();
    }, [pendingWorkspaceTransition, saveWorkspace]);

    const activateWorkspace = useCallback((workspaceId: string) => {
        const target = workspaceTabs.find((tab) => tab.id === workspaceId);
        if (!target || workspaceId === activeWorkspaceId) return;
        const current = workspaceTabs.find((tab) => tab.id === activeWorkspaceId);
        requestWorkspaceTransition(
            `switch to ${target.title}`,
            () => activateWorkspaceImmediately(workspaceId),
            {
                dirty: Boolean(current?.dirty || isDirty),
                workspaceId: activeWorkspaceId,
                discard: discardActiveWorkspace,
            },
        );
    }, [activeWorkspaceId, activateWorkspaceImmediately, discardActiveWorkspace, isDirty, requestWorkspaceTransition, workspaceTabs]);

    const closeWorkspace = useCallback((workspaceId: string) => {
        const target = workspaceTabs.find((tab) => tab.id === workspaceId);
        if (!target) return;
        requestWorkspaceTransition(
            `close ${target.title}`,
            () => closeWorkspaceImmediately(workspaceId),
            {
                dirty: target.dirty || (workspaceId === activeWorkspaceId && isDirty),
                workspaceId,
            },
        );
    }, [activeWorkspaceId, closeWorkspaceImmediately, isDirty, requestWorkspaceTransition, workspaceTabs]);

    const guardedLoadSequence = useCallback((id: string) => {
        if (!isDirty) {
            void loadSequence(id);
            return;
        }
        requestWorkspaceTransition(
            'open another construct',
            () => { void loadSequence(id, { forceReload: true }); },
            { dirty: true, workspaceId: activeWorkspaceId, discard: discardActiveWorkspace },
        );
    }, [activeWorkspaceId, discardActiveWorkspace, isDirty, loadSequence, requestWorkspaceTransition]);

    const guardedLoadDemo = useCallback((demo: SequenceData) => {
        requestWorkspaceTransition(
            'open another construct',
            () => loadDemo(demo),
            { dirty: isDirty, workspaceId: activeWorkspaceId, discard: discardActiveWorkspace },
        );
    }, [activeWorkspaceId, discardActiveWorkspace, isDirty, loadDemo, requestWorkspaceTransition]);

    useEffect(() => {
        if (!workspaceRestoreComplete || molecularOpenRequestApproved) return;
        if (molecularOpenRequest.kind === 'none' || molecularOpenRequest.kind === 'invalid') {
            setApprovedMolecularOpenRequestKey(molecularOpenRequestKey);
            return;
        }
        const activeWorkspace = workspaceTabs.find((tab) => tab.id === activeWorkspaceId);
        const activeWorkspaceIsDirty = Boolean(
            activeWorkspace && (activeWorkspace.dirty || isDirty),
        );
        requestWorkspaceTransition(
            molecularOpenRequest.kind === 'exact'
                ? 'open the requested immutable molecular revision'
                : 'open the requested current molecular workspace',
            () => setApprovedMolecularOpenRequestKey(molecularOpenRequestKey),
            {
                dirty: activeWorkspaceIsDirty,
                workspaceId: activeWorkspaceId,
                discard: discardActiveWorkspace,
            },
        );
    }, [
        activeWorkspaceId,
        discardActiveWorkspace,
        isDirty,
        molecularOpenRequest,
        molecularOpenRequestApproved,
        molecularOpenRequestKey,
        requestWorkspaceTransition,
        workspaceRestoreComplete,
        workspaceTabs,
    ]);

    useEffect(() => {
        const hasDirtyWorkspace = isDirty || workspaceTabs.some((tab) => tab.dirty);
        if (!hasDirtyWorkspace) return;
        const handleBeforeUnload = (event: BeforeUnloadEvent) => {
            if (browserLeaveApprovedRef.current) return;
            event.preventDefault();
            event.returnValue = '';
        };
        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [isDirty, workspaceTabs]);

    const handleGuardedRouteClick = useCallback((event: MouseEvent) => {
        if (!isDirty || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = (event.target as HTMLElement | null)?.closest<HTMLAnchorElement>('a[href]');
        if (!anchor || anchor.target === '_blank' || anchor.download) return;
        const destination = new URL(anchor.href, window.location.href);
        if (destination.href === window.location.href) return;
        event.preventDefault();
        event.stopPropagation();
        requestWorkspaceTransition('leave this molecular workspace', () => {
            browserLeaveApprovedRef.current = true;
            window.location.assign(destination.href);
        });
    }, [isDirty, requestWorkspaceTransition]);

    useEffect(() => {
        document.addEventListener('click', handleGuardedRouteClick, true);
        return () => document.removeEventListener('click', handleGuardedRouteClick, true);
    }, [handleGuardedRouteClick]);

    // Visibility toggle handler
    const handleVisibilityChange = useCallback((key: keyof VisibilityState) => {
        setVisibility(prev => ({ ...prev, [key]: !prev[key] }));
    }, []);

    const ensureCutSitesVisible = useCallback(() => {
        setVisibility((previous) => (
            previous.cutsites ? previous : { ...previous, cutsites: true }
        ));
    }, []);

    // SequenceViewer emits one finalized value per pointer gesture.
    const handleSelection = useCallback((sel: SelectionInfo) => {
        setSelection(sel);
    }, []);

    const closeQuickAddMenu = useCallback(() => {
        setQuickAddMenu(null);
    }, []);

    const closeQuickAddMenuAndRestoreFocus = useCallback(() => {
        setQuickAddMenu(null);
        window.requestAnimationFrame(() => {
            quickAddInvokerRef.current?.focus({ preventScroll: true });
        });
    }, []);

    useEffect(() => {
        if (!quickAddMenu) {
            return;
        }
        const frame = window.requestAnimationFrame(() => {
            quickAddMenuRef.current
                ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')
                ?.focus();
        });
        return () => window.cancelAnimationFrame(frame);
    }, [quickAddMenu]);

    const handleQuickAddMenuKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeQuickAddMenuAndRestoreFocus();
            return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Tab'].includes(event.key)) {
            return;
        }
        const items = Array.from(
            quickAddMenuRef.current
                ?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [],
        );
        if (!items.length) {
            return;
        }
        if (moveMenuFocus(
            items,
            document.activeElement as HTMLButtonElement,
            event.key as MenuNavigationKey,
            event.shiftKey,
        )) {
            event.preventDefault();
        }
    }, [closeQuickAddMenuAndRestoreFocus]);

    const closeSelectionAction = useCallback(() => {
        if (quickAddBusy) {
            return;
        }
        setSelectionAction(null);
        setSelectionActionError(null);
    }, [quickAddBusy]);

    const handleViewerContextMenu = useCallback((
        event: ReactMouseEvent<HTMLDivElement> | ReactKeyboardEvent<HTMLDivElement>,
    ) => {
        if (exactMolecularAuthorityRef.current) return;
        event.preventDefault();
        event.stopPropagation();
        const bounds = event.currentTarget.getBoundingClientRect();
        const isPointerPosition = 'clientX' in event && event.clientX > 0 && event.clientY > 0;
        quickAddInvokerRef.current = event.currentTarget;
        setQuickAddMenu({
            x: isPointerPosition ? event.clientX : bounds.left + Math.min(bounds.width / 2, 320),
            y: isPointerPosition ? event.clientY : bounds.top + Math.min(bounds.height / 2, 240),
            snapshot: createSelectionSnapshot(
                selection,
                sequenceData.sequence,
                sequenceData.circular,
            ),
        });
    }, [selection, sequenceData.circular, sequenceData.sequence]);

    const openSelectionAction = useCallback((action: SelectionActionKind) => {
        if (exactMolecularAuthorityRef.current) return;
        if (!quickAddMenu?.snapshot) {
            return;
        }
        setSelectionAction({
            action,
            snapshot: quickAddMenu.snapshot,
        });
        setSelectionActionError(null);
        setQuickAddMenu(null);
    }, [quickAddMenu]);

    // Add feature handler
    const handleAddFeature = useCallback((feature: Feature) => {
        if (exactMolecularAuthorityRef.current) return;
        setSequenceData({
            ...sequenceData,
            features: normalizeFeatureList([...sequenceData.features, normalizeFeatureRecord(feature, feature.id)])
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    // Remove feature handler
    const handleRemoveFeature = useCallback((featureId: string) => {
        if (exactMolecularAuthorityRef.current) return;
        setSequenceData({
            ...sequenceData,
            features: sequenceData.features.filter(f => f.id !== featureId)
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    // Update feature handler (for inline edit)
    const handleUpdateFeature = useCallback((updatedFeature: Feature) => {
        if (exactMolecularAuthorityRef.current) return;
        setSequenceData({
            ...sequenceData,
            features: normalizeFeatureList(sequenceData.features.map(f =>
                f.id === updatedFeature.id ? normalizeFeatureRecord(updatedFeature, updatedFeature.id) : f
            ))
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    const handleAddFeatures = useCallback((newFeatures: Feature[]) => {
        if (exactMolecularAuthorityRef.current || newFeatures.length === 0) return;
        setSequenceData({
            ...sequenceData,
            features: normalizeFeatureList([...sequenceData.features, ...newFeatures]),
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    // Add primer handler
    const handleAddPrimer = useCallback((primer: Primer) => {
        if (exactMolecularAuthorityRef.current) return;
        setSequenceData({
            ...sequenceData,
            primers: [...(sequenceData.primers || []), primer]
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    // Remove primer handler
    const handleRemovePrimer = useCallback((primerId: string) => {
        if (exactMolecularAuthorityRef.current) return;
        setSequenceData({
            ...sequenceData,
            primers: (sequenceData.primers || []).filter(p => p.id !== primerId)
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    const handleAnalysisTracksChange = useCallback((tracks: AnalysisTrack[]) => {
        if (exactMolecularAuthorityRef.current) return;
        setSequenceData({
            ...sequenceData,
            analysisTracks: tracks,
        });
        setIsDirty(true);
    }, [isExactMolecularAuthority, sequenceData, setSequenceData]);

    // Auto-annotation state
    const [isAnnotating, setIsAnnotating] = useState(false);
    const [showAnnotatePanel, setShowAnnotatePanel] = useState(false);
    const [annotationSourceStatus, setAnnotationSourceStatus] = useState<AnnotationSourceStatus | null>(null);

    useEffect(() => {
        if (!isExactMolecularAuthority) return;
        setActivePanel((current) => EXACT_REVISION_ALLOWED_PANELS.has(current) ? current : 'view');
        setShowAnnotatePanel(false);
        setShowInputModal(false);
        setQuickAddMenu(null);
        setSelectionAction(null);
        setSelectionActionError(null);
        setIsDirty(false);
    }, [isExactMolecularAuthority]);

    useEffect(() => {
        if (hasPcrRevisionQuery && !isExactMolecularAuthority) {
            setActivePanel('pcr');
        }
    }, [hasPcrRevisionQuery, isExactMolecularAuthority]);

    useEffect(() => {
        if (!showAnnotatePanel) return;
        let cancelled = false;
        setAnnotationSourceStatus(null);
        void fetchAnnotationSourceStatus()
            .then((status) => {
                if (!cancelled) setAnnotationSourceStatus(status);
            })
            .catch(() => {
                if (!cancelled) {
                    setAnnotationSourceStatus({ ncbi: { available: true }, addgene: { available: false } });
                }
            });
        return () => {
            cancelled = true;
        };
    }, [showAnnotatePanel]);

    // Display projection is workspace-local; every newly opened sequence starts in Both.
    type ViewMode = 'linear' | 'circular' | 'both';
    type ResizeHandleSide = 'left' | 'right';
    const initialViewportWidth = typeof window === 'undefined' ? 1440 : window.innerWidth;
    const initialViewportHeight = typeof window === 'undefined' ? 900 : window.innerHeight;
    const [workspaceViewModes, setWorkspaceViewModes] = useState<Record<string, ViewMode>>({});
    const viewMode = workspaceViewModes[activeWorkspaceId] ?? 'both';
    const setViewMode = useCallback((mode: ViewMode) => {
        setWorkspaceViewModes((current) => ({ ...current, [activeWorkspaceId]: mode }));
    }, [activeWorkspaceId]);
    const effectiveViewMode: ViewMode = viewMode;

    type MolecularWorkspaceRestorePayload = {
        restored: ReturnType<typeof deserializeMolecularWorkspaceIdentity>;
        loaded: Array<WorkspaceTab | null>;
    };
    const loadMolecularWorkspaceRestore = useCallback(async (): Promise<MolecularWorkspaceRestorePayload> => {
        const restored = deserializeMolecularWorkspaceIdentity(
            window.localStorage.getItem(MOLECULAR_WORKSPACE_STORAGE_KEY),
        );
        const loaded = await Promise.all(restored.tabs.map(async (persisted): Promise<WorkspaceTab | null> => {
            try {
                if (persisted.lens === 'historical' && persisted.exactRevisionId) {
                    const revision = await fetchMolecularRevision(persisted.sequenceId, persisted.exactRevisionId);
                    assertExactMolecularRevisionIdentity(
                        revision,
                        persisted.sequenceId,
                        persisted.exactRevisionId,
                    );
                    const data = sequenceDataFromMolecularRevision(revision);
                    return {
                        id: persisted.id,
                        title: `${data.name} · r${revision.revision_number} (read-only)`,
                        sequenceId: persisted.sequenceId,
                        dirty: false,
                        historyState: createHistoryState(data, `Restore immutable revision ${revision.revision_number}`),
                        sequenceType: data.sequenceType,
                        exactMolecularRevision: revision,
                    };
                }
                const current = await loadMolecularWorkspaceCurrentSequence(
                    persisted.sequenceId,
                    async (sequenceId) => {
                        const response = await fetchNucleotideSequence(sequenceId);
                        return response.data as NucleotideSequenceResponse;
                    },
                );
                if (!current) return null;
                const data = sequenceDataFromApiRecord(current);
                return {
                    id: persisted.id,
                    title: data.name || 'Untitled',
                    sequenceId: persisted.sequenceId,
                    dirty: false,
                    historyState: createHistoryState(data, `Restore latest ${data.name}`),
                    sequenceType: data.sequenceType,
                    exactMolecularRevision: null,
                };
            } catch {
                return null;
            }
        }));
        return { restored, loaded };
    }, []);
    const publishMolecularWorkspaceRestore = useCallback(({ restored, loaded }: MolecularWorkspaceRestorePayload) => {
        const tabs = loaded.filter((tab): tab is WorkspaceTab => tab !== null);
        const failedCount = restored.tabs.length - tabs.length;
        if (restored.notice || failedCount > 0) {
            setWorkspaceRestoreNotice('Some saved molecular workspaces could not be restored and were skipped.');
        }
        if (tabs.length > 0) {
            const nextActiveId = restored.activeWorkspaceId && tabs.some((tab) => tab.id === restored.activeWorkspaceId)
                ? restored.activeWorkspaceId
                : tabs[0].id;
            const active = tabs.find((tab) => tab.id === nextActiveId) ?? tabs[0];
            setWorkspaceTabs(tabs);
            setActiveWorkspaceId(active.id);
            hydrate(active.historyState);
            setSelectedSequenceId(active.sequenceId);
            setIsDirty(false);
            setActiveDisplayStrand(sourceDisplayStrandForSequenceData(active.historyState.present));
            const restoredViewModes: Record<string, ViewMode> = {};
            restored.tabs.forEach((persisted) => {
                const mode = persisted.viewContext.viewMode;
                if (mode === 'linear' || mode === 'circular' || mode === 'both') restoredViewModes[persisted.id] = mode;
            });
            setWorkspaceViewModes(restoredViewModes);
            const activeIdentity = restored.tabs.find((persisted) => persisted.id === active.id);
            const panel = activeIdentity?.viewContext.activePanel;
            if (panel) setActivePanel(panel as ActivePanel);
            const strand = activeIdentity?.viewContext.displayStrand;
            if (strand === 'plus' || strand === 'minus') setActiveDisplayStrand(strand);
        }
        setWorkspaceRestoreComplete(true);
    }, [hydrate]);
    useMolecularWorkspaceRestoreEffect(
        !workspaceRestoreComplete,
        loadMolecularWorkspaceRestore,
        publishMolecularWorkspaceRestore,
    );

    useEffect(() => {
        if (!workspaceRestoreComplete) return;
        const persisted: PersistedMolecularWorkspace[] = workspaceTabs
            .filter((tab) => Boolean(tab.sequenceId))
            .map((tab) => ({
                id: molecularWorkspaceId(tab.sequenceId as string),
                sequenceId: tab.sequenceId as string,
                lens: tab.exactMolecularRevision ? 'historical' : 'current',
                ...(tab.exactMolecularRevision ? { exactRevisionId: tab.exactMolecularRevision.revision_id } : {}),
                viewContext: {
                    activePanel: tab.id === activeWorkspaceId ? activePanel ?? 'view' : 'view',
                    viewMode: workspaceViewModes[tab.id] ?? 'both',
                    displayStrand: tab.id === activeWorkspaceId ? activeDisplayStrand : undefined,
                },
            }));
        if (persisted.length === 0) {
            window.localStorage.removeItem(MOLECULAR_WORKSPACE_STORAGE_KEY);
            return;
        }
        const persistedActiveId = persisted.some((tab) => tab.id === activeWorkspaceId)
            ? activeWorkspaceId
            : persisted[0].id;
        window.localStorage.setItem(
            MOLECULAR_WORKSPACE_STORAGE_KEY,
            serializeMolecularWorkspaceIdentity(persisted, persistedActiveId),
        );
    }, [activeDisplayStrand, activePanel, activeWorkspaceId, workspaceRestoreComplete, workspaceTabs, workspaceViewModes]);

    const [isViewerFullscreen, setIsViewerFullscreen] = useState(false);
    const [isLibraryPanelCollapsed, setIsLibraryPanelCollapsed] = useState(() => shouldCollapseMolBioPanelsForViewport(initialViewportWidth));
    const [isToolPanelCollapsed, setIsToolPanelCollapsed] = useState(() => shouldCollapseMolBioPanelsForViewport(initialViewportWidth));
    const [viewportWidth, setViewportWidth] = useState(initialViewportWidth);
    const [viewportHeight, setViewportHeight] = useState(initialViewportHeight);
    const [mobileSurface, setMobileSurface] = useState<MolBioMobileSurface>('map');
    const [mobileConstructPickerOpen, setMobileConstructPickerOpen] = useState(false);
    const pendingMobileDemoRef = useRef<SequenceData | null>(null);
    const [pendingMobileDemoVersion, setPendingMobileDemoVersion] = useState(0);
    const isCordovaMolBioShell = useMemo(
        () => detectMolBioCordovaShell(typeof window === 'undefined' ? null : window),
        [],
    );
    const coarsePointer = detectMolBioPrimaryCoarsePointer(
        typeof window === 'undefined' ? null : window,
    );
    const isMobileMolBio = shouldUseMolBioMobileLayout({
        cordovaShell: isCordovaMolBioShell,
        coarsePointer,
        viewportWidth,
        viewportHeight,
    });
    useMolBioBodyScrollLock(isViewerFullscreen, isMobileMolBio);
    const [leftPanelWidth, setLeftPanelWidth] = useState(MOLBIO_LIBRARY_PANEL_DEFAULT_WIDTH);
    const [rightPanelWidth, setRightPanelWidth] = useState(() => getDefaultMolBioToolPanelWidth('view'));
    const resizeStateRef = useRef<{
        side: ResizeHandleSide;
        pointerX: number;
        startWidth: number;
    } | null>(null);

    // GC track visibility state
    const [showGCTrack, setShowGCTrack] = useState(false);
    const [primerTmOptions, setPrimerTmOptions] = useState<PrimerTmOptionsResponse | null>(null);
    const [primerTmSettings, setPrimerTmSettings] = useState<PrimerTmSettings>(DEFAULT_DNA_TM_SETTINGS);
    const viewerLayout = useMemo(() => resolveMolBioViewerLayout({
        activePanel,
        viewportWidth,
        leftPanelWidth,
        rightPanelWidth,
        isViewerFullscreen,
        isLibraryPanelCollapsed,
        isToolPanelCollapsed,
    }), [
        activePanel,
        viewportWidth,
        leftPanelWidth,
        rightPanelWidth,
        isViewerFullscreen,
        isLibraryPanelCollapsed,
        isToolPanelCollapsed,
    ]);

    useEffect(() => {
        let cancelled = false;

        const loadPrimerTmOptions = async () => {
            try {
                const response = await fetchPrimerTmOptions();
                if (cancelled) {
                    return;
                }
                setPrimerTmOptions(response.data);
                const preferredSequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
                const supported = response.data.algorithms.some(
                    (option) =>
                        option.id === primerTmSettings.algorithm &&
                        option.sequence_types.includes(preferredSequenceType),
                );
                if (!supported) {
                    setPrimerTmSettings(response.data.defaults[preferredSequenceType]);
                }
            } catch (tmError) {
                console.error('Failed to load primer Tm options:', tmError);
            }
        };

        loadPrimerTmOptions();
        return () => {
            cancelled = true;
        };
    }, [primerTmSettings.algorithm, sequenceData.sequenceType]);

    useEffect(() => {
        if (!primerTmOptions) {
            return;
        }
        const preferredSequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
        const supported = primerTmOptions.algorithms.some(
            (option) =>
                option.id === primerTmSettings.algorithm &&
                option.sequence_types.includes(preferredSequenceType),
        );
        if (!supported) {
            setPrimerTmSettings(primerTmOptions.defaults[preferredSequenceType]);
        }
    }, [primerTmOptions, primerTmSettings.algorithm, sequenceData.sequenceType]);

    useEffect(() => {
        const handleWindowResize = () => {
            setViewportWidth(window.innerWidth);
            setViewportHeight(window.innerHeight);
        };
        handleWindowResize();
        window.addEventListener('resize', handleWindowResize);
        return () => window.removeEventListener('resize', handleWindowResize);
    }, []);

    useEffect(() => {
        setRightPanelWidth(getDefaultMolBioToolPanelWidth(activePanel));
    }, [activePanel]);

    useEffect(() => {
        if (!isViewerFullscreen) {
            return undefined;
        }

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setIsViewerFullscreen(false);
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isViewerFullscreen]);

    const handleMobileBack = useCallback(() => {
        if (isViewerFullscreen) {
            setIsViewerFullscreen(false);
            return;
        }
        const action = resolveMolBioMobileBackAction({
            constructPickerOpen: mobileConstructPickerOpen,
            hasSequence: Boolean(sequenceData.sequence),
            surface: mobileSurface,
        });
        if (action === 'close-constructs') {
            setMobileConstructPickerOpen(false);
            return;
        }
        if (action === 'show-map') {
            setMobileSurface('map');
            return;
        }
        window.history.back();
    }, [isViewerFullscreen, mobileConstructPickerOpen, mobileSurface, sequenceData.sequence]);

    useEffect(() => {
        if (!isMobileMolBio && !isViewerFullscreen) {
            return undefined;
        }
        const handleAndroidBack = (event: Event) => {
            event.preventDefault();
            handleMobileBack();
        };
        document.addEventListener('backbutton', handleAndroidBack);
        return () => document.removeEventListener('backbutton', handleAndroidBack);
    }, [handleMobileBack, isMobileMolBio, isViewerFullscreen]);

    useEffect(() => {
        if (isMobileMolBio) {
            setIsViewerFullscreen(false);
        }
    }, [isMobileMolBio]);

    useEffect(() => {
        const handlePointerMove = (event: PointerEvent) => {
            const resizeState = resizeStateRef.current;
            if (!resizeState) {
                return;
            }

            if (resizeState.side === 'left') {
                setLeftPanelWidth(clampMolBioPanelWidth(
                    resizeState.startWidth + (event.clientX - resizeState.pointerX),
                    viewerLayout.leftPanelBounds,
                ));
                return;
            }

            setRightPanelWidth(clampMolBioPanelWidth(
                resizeState.startWidth - (event.clientX - resizeState.pointerX),
                viewerLayout.rightPanelBounds,
            ));
        };

        const stopResize = () => {
            resizeStateRef.current = null;
        };

        window.addEventListener('pointermove', handlePointerMove);
        window.addEventListener('pointerup', stopResize);
        window.addEventListener('pointercancel', stopResize);
        return () => {
            window.removeEventListener('pointermove', handlePointerMove);
            window.removeEventListener('pointerup', stopResize);
            window.removeEventListener('pointercancel', stopResize);
        };
    }, [viewerLayout.leftPanelBounds, viewerLayout.rightPanelBounds]);

    const startPanelResize = useCallback((side: ResizeHandleSide) => (event: ReactPointerEvent<HTMLButtonElement>) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        resizeStateRef.current = {
            side,
            pointerX: event.clientX,
            startWidth: side === 'left' ? viewerLayout.leftPanelWidth : viewerLayout.rightPanelWidth,
        };
    }, [viewerLayout.leftPanelWidth, viewerLayout.rightPanelWidth]);

    const toggleViewerFullscreen = useCallback(() => {
        setIsViewerFullscreen((current) => !current);
    }, []);

    const toggleLibraryPanel = useCallback(() => {
        setIsLibraryPanelCollapsed((current) => !current);
    }, []);

    const toggleToolPanel = useCallback(() => {
        setIsToolPanelCollapsed((current) => !current);
    }, []);

    // Open auto-annotate settings panel
    const handleAutoAnnotate = useCallback(() => {
        if (exactMolecularAuthorityRef.current) return;
        setShowAnnotatePanel(true);
    }, []);

    const clearAnnotations = useCallback(() => {
        if (exactMolecularAuthorityRef.current) return;
        const featureCount = sequenceData.features.length;
        if (featureCount === 0) return;
        setSequenceData(
            clearFeatureAnnotations(sequenceData),
            `Clear ${featureCount} feature annotations`,
        );
        setIsDirty(true);
    }, [sequenceData, setSequenceData]);

    const importAnnotationsFromFile = useCallback(async (
        file: File,
        publishedSource?: AnnotationSourceProvenance,
    ): Promise<string> => {
        if (exactMolecularAuthorityRef.current) {
            throw new Error('Exact immutable molecular revisions are read-only. Open the current editable projection before importing annotations.');
        }
        if (!sequenceData.sequence) {
            throw new Error('Open a construct before importing annotations.');
        }
        if (sequenceData.features.length > 0) {
            throw new Error('This construct already has existing feature annotations. Use Clear all feature annotations before importing a published annotation set; source sets are never silently merged.');
        }

        const fileBytes = await file.arrayBuffer();
        const sha256Hex = async (content: BufferSource): Promise<string> => {
            const digest = await crypto.subtle.digest('SHA-256', content);
            return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
        };
        const sourceFileSha256 = await sha256Hex(fileBytes);
        if (publishedSource) {
            assertAnnotationArtifactChecksum(publishedSource, sourceFileSha256);
        }

        const result = await anyToJson(file, {
            fileName: file.name,
            inclusive1BasedStart: false,
            jsonType: 'json',
        });
        const results = Array.isArray(result) ? result : [result];
        const parserMessages = results.flatMap((entry) => Array.isArray(entry?.messages)
            ? entry.messages.map((message: unknown) => String(message))
            : []);
        if (results.some((entry) => entry?.success === false)) {
            const detail = parserMessages.length > 0 ? `: ${parserMessages.join('; ')}` : '';
            throw new Error(`The annotated file parser reported a failure${detail}`);
        }
        const parsedRecords = results
            .map((entry) => entry?.parsedSequence)
            .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
        if (parsedRecords.length === 0) {
            throw new Error('The annotated file could not be parsed.');
        }
        if (parsedRecords.length !== 1) {
            throw new Error('Annotation transfer requires exactly one sequence record per file.');
        }
        const parsed = parsedRecords[0];

        const moleculeMetadata = inferNucleotideMoleculeMetadataFromParsedRecord(parsed);
        const importedType = parsed.isProtein ? 'protein' : moleculeMetadata.sequenceType;
        if (importedType === 'protein' || importedType !== sequenceData.sequenceType) {
            throw new Error(`Annotated-file molecule type (${importedType}) does not match the open ${sequenceData.sequenceType.toUpperCase()} construct.`);
        }

        const sourceStrandednessExplicit = hasExplicitNucleotideStrandednessMetadata(parsed);
        const alignmentPolicy = resolveAnnotationAlignmentPolicy(
            {
                sequenceType: moleculeMetadata.sequenceType,
                moleculeStrandedness: moleculeMetadata.moleculeStrandedness ?? 'unknown',
                strandednessExplicit: sourceStrandednessExplicit,
            },
            {
                sequenceType: sequenceData.sequenceType,
                moleculeStrandedness: sequenceData.moleculeStrandedness ?? 'unknown',
                strandednessExplicit: sequenceData.moleculeStrandedness !== undefined,
            },
        );
        assertAnnotationTopology(Boolean(parsed.circular), sequenceData.circular);
        const importedSequence = String(parsed.sequence || '');
        const alignment = resolveAnnotationSequenceAlignment(
            importedSequence,
            sequenceData.sequence,
            sequenceData.circular,
            alignmentPolicy,
        );
        const parsedFeatures = parsed.features || [];
        if (parsedFeatures.length === 0) {
            throw new Error('The annotated file contains no feature annotations to transfer.');
        }

        const importedAt = new Date().toISOString();
        const sourceSequenceSha256 = await sha256Hex(new TextEncoder().encode(importedSequence));
        const targetSequenceSha256 = await sha256Hex(new TextEncoder().encode(sequenceData.sequence));
        const importedFeatures = parsedFeatures.map((rawFeature: UntypedApiValue, index: number) => {
            const fallbackId = `annotation_import_${Date.now()}_${index}`;
            const normalized = normalizeFeatureRecord(rawFeature, fallbackId);
            const transformed = transformFeatureForAlignment(normalized, alignment);
            return {
                ...transformed,
                id: fallbackId,
                provenance: {
                    ...(transformed.provenance || {}),
                    annotation_import: {
                        provider: publishedSource?.provider ?? 'user_file',
                        source_origin: publishedSource?.source_url ?? 'user_provided_origin_unknown',
                        source_id: publishedSource?.source_id,
                        published_source: publishedSource,
                        source_file: file.name,
                        source_media_type: file.type || 'application/octet-stream',
                        source_byte_count: file.size,
                        source_file_sha256: sourceFileSha256,
                        source_sequence_sha256: sourceSequenceSha256,
                        target_sequence_sha256: targetSequenceSha256,
                        source_topology: parsed.circular ? 'circular' : 'linear',
                        source_sequence_type: moleculeMetadata.sequenceType,
                        source_strandedness: moleculeMetadata.moleculeStrandedness,
                        source_strandedness_explicit: sourceStrandednessExplicit,
                        parser: '@teselagen/bio-parsers:anyToJson',
                        parser_version: '0.4.32',
                        parser_messages: parserMessages,
                        match_mode: alignment.mode,
                        rotation_offset: alignment.rotation,
                        reverse_complement: alignment.reverseComplement,
                        imported_at: importedAt,
                        source_feature_record: rawFeature,
                    },
                },
            };
        });

        const transferredFeatures = normalizeFeatureList(importedFeatures)
            .sort((left, right) => left.start - right.start || left.end - right.end || left.name.localeCompare(right.name));
        if (exactMolecularAuthorityRef.current) {
            throw new Error('Exact immutable molecular revision authority opened before annotation import completed; no local annotations were changed.');
        }
        setSequenceData({
            ...sequenceData,
            features: transferredFeatures,
        }, `Import ${transferredFeatures.length} annotations from ${file.name}`);
        setIsDirty(true);

        const parserMessage = parserMessages.length > 0 ? ` Parser messages retained: ${parserMessages.length}.` : '';
        return `Imported ${transferredFeatures.length} feature annotations from ${file.name} using ${alignment.mode.replaceAll('_', ' ')} sequence alignment.${parserMessage}`;
    }, [sequenceData, setSequenceData]);

    const retrieveNcbiAnnotations = useCallback(async (accession: string): Promise<string> => {
        const retrieved = await retrieveNcbiAnnotationSource(accession);
        return importAnnotationsFromFile(retrieved.file, retrieved.source);
    }, [importAnnotationsFromFile]);

    const retrieveAddgeneAnnotations = useCallback(async (plasmidId: string): Promise<string> => {
        const retrieved = await retrieveAddgeneAnnotationSource(plasmidId);
        return importAnnotationsFromFile(retrieved.file, retrieved.source);
    }, [importAnnotationsFromFile]);

    // Run auto-annotation with user settings
    const runAutoAnnotate = useCallback(async (settings: AutoAnnotateSettings) => {
        if (exactMolecularAuthorityRef.current) return;
        if (!sequenceData.sequence) return;
        if (sequenceData.sequenceType === 'rna') {
            alert('Auto-annotation currently targets plasmid-centric DNA constructs. RNA feature annotation needs a separate database pass.');
            return;
        }

        setShowAnnotatePanel(false);
        setIsAnnotating(true);

        try {
            const response = await fetch('/api/molbio/auto-annotate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sequence: sequenceData.sequence,
                    is_linear: !sequenceData.circular,
                    detailed: settings.detailed,
                    min_identity: settings.minIdentity
                })
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
                throw new Error(error.detail || `HTTP ${response.status}`);
            }

            const { features: detectedFeatures, message } = await response.json();

            // Apply fragment filter if enabled
            let filteredFeatures = detectedFeatures;
            if (settings.filterFragments) {
                filteredFeatures = detectedFeatures.filter((f: UntypedApiValue) => !f.is_fragment);
            }

            if (filteredFeatures.length === 0) {
                alert('No features detected matching your criteria.');
                return;
            }

            // Color palette for different feature types
            const typeColors: Record<string, string> = {
                'CDS': '#22c55e',
                'gene': '#16a34a',
                'promoter': '#8b5cf6',
                'terminator': '#ef4444',
                'rep_origin': '#ec4899',
                'misc_feature': '#3b82f6',
                'primer_bind': '#f59e0b'
            };

            // Convert detected features to our Feature format
            const newFeatures: Feature[] = filteredFeatures.map((f: UntypedApiValue, i: number) => ({
                id: `auto_${Date.now()}_${i}`,
                name: f.name,
                type: f.type,
                start: f.start,
                end: f.end,
                strand: f.strand,
                color: typeColors[f.type] || '#6b7280',
                description: f.description || undefined,
                notes: {
                    source: 'pLannotate',
                    identity_pct: Number(f.identity_pct.toFixed(1)),
                    match_length_pct: Number(f.match_length_pct.toFixed(1)),
                    database: f.database,
                    is_fragment: Boolean(f.is_fragment),
                },
                qualifiers: {
                    source: 'pLannotate',
                    identity_pct: Number(f.identity_pct.toFixed(1)),
                    match_length_pct: Number(f.match_length_pct.toFixed(1)),
                    database: f.database,
                    is_fragment: Boolean(f.is_fragment),
                },
                provenance: {
                    workflow: 'auto_annotate',
                    engine: 'pLannotate',
                    detailed: settings.detailed,
                    min_identity: settings.minIdentity,
                },
            }));

            // Deduplicate: filter out features that already exist
            // A feature is considered duplicate if it has same name and overlapping position (>80% overlap)
            const existingFeatures = sequenceData.features;
            const uniqueNewFeatures = newFeatures.filter(newF => {
                return !existingFeatures.some(existingF => {
                    // Same name check (case-insensitive)
                    const sameName = existingF.name.toLowerCase() === newF.name.toLowerCase();
                    const sameType = existingF.type === newF.type;
                    if (!sameName || !sameType) return false;

                    // Calculate overlap over authoritative segments, not aggregate gaps.
                    const overlapLength = featureOverlapLength(existingF, newF);
                    const newLength = featureLength(newF);
                    const existingLength = featureLength(existingF);
                    const minLength = Math.min(newLength, existingLength);

                    // If >80% overlap, consider it duplicate
                    return minLength > 0 && (overlapLength / minLength) > 0.8;
                });
            });

            const skippedCount = newFeatures.length - uniqueNewFeatures.length;

            // Merge with existing features
            const mergedFeatures = normalizeFeatureList([...sequenceData.features, ...uniqueNewFeatures]).sort((a, b) =>
                a.start - b.start || a.end - b.end || a.name.localeCompare(b.name)
            );
            if (exactMolecularAuthorityRef.current) {
                throw new Error('Exact immutable molecular revision authority opened before auto-annotation completed; no local features were changed.');
            }
            setSequenceData({
                ...sequenceData,
                features: mergedFeatures
            });
            setIsDirty(true);

            const skippedMsg = skippedCount > 0 ? ` (${skippedCount} duplicates skipped)` : '';
            alert(`Added ${uniqueNewFeatures.length} new features!${skippedMsg} ${message}`);
        } catch (error) {
            console.error('Auto-annotation failed:', error);
            alert(`Auto-annotation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        } finally {
            setIsAnnotating(false);
        }
    }, [sequenceData, setSequenceData]);

    // Track dirty state
    useEffect(() => {
        if (!isExactMolecularAuthority && canUndo) setIsDirty(true);
    }, [canUndo, isExactMolecularAuthority]);

    // Keyboard shortcuts
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (isExactMolecularAuthority) {
                if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 's')) {
                    e.preventDefault();
                }
                return;
            }
            if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
                if (e.shiftKey) {
                    e.preventDefault();
                    redo();
                } else {
                    e.preventDefault();
                    undo();
                }
            }
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                saveSequence();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isExactMolecularAuthority, undo, redo, saveSequence]);

    useEffect(() => {
        if (!quickAddMenu) {
            return;
        }

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                closeQuickAddMenu();
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [closeQuickAddMenu, quickAddMenu]);

    const selectedRnaEvidenceTrack = useMemo(
        () => viewerSequenceData.analysisTracks?.find((track) => track.id === selectedRnaTrackId) || null,
        [selectedRnaTrackId, viewerSequenceData.analysisTracks],
    );

    const alignmentTargets = useMemo(
        () => workspaceTabs
            .filter((tab) => tab.id !== activeWorkspaceId)
            .map((tab) => ({
                id: tab.id,
                label: tab.title,
                sequence: tab.historyState.present.sequence,
                circular: tab.historyState.present.circular,
                sequenceType: tab.historyState.present.sequenceType,
            }))
            .filter((target) => Boolean(target.sequence)),
        [activeWorkspaceId, workspaceTabs],
    );
    const workspaceSummaries = useMemo(
        () => workspaceTabs.map((tab) => ({
            id: tab.id,
            title: tab.title,
            sequenceId: tab.sequenceId,
            dirty: tab.dirty,
            sequenceType: tab.sequenceType,
            sequenceLength: tab.historyState.present.sequence.length,
        })),
        [workspaceTabs],
    );

    const showRnaStructureViewer = Boolean(
        sequenceData.sequenceType === 'rna' &&
        activePanel === 'rna' &&
        rnaStructureResult,
    );

    const selectionSnapshot = useMemo(
        () => createSelectionSnapshot(selection, sequenceData.sequence, sequenceData.circular),
        [selection, sequenceData.circular, sequenceData.sequence],
    );
    const selectionLength = selectionSnapshot?.length ?? 0;
    const hasRangeSelection = Boolean(selectionSnapshot);
    const selectionCoordinateLabel = selectionSnapshot?.coordinateLabel ?? 'No selection';

    const handleLoadAssemblyProduct = useCallback((product: SequenceData, savedSequenceId?: string | null) => {
        if (exactMolecularAuthorityRef.current) return;
        openWorkspace(product, {
            sequenceId: savedSequenceId || null,
            dirty: !savedSequenceId,
            label: `Open ${product.name}`,
        });
        if (savedSequenceId) {
            void loadLibrary();
        }
    }, [loadLibrary, openWorkspace]);

    const handleQuickAddPrimer = useCallback(async (input: SelectionPrimerInput) => {
        if (exactMolecularAuthorityRef.current) return;
        if (!selectionAction || selectionAction.action === 'feature') {
            return;
        }

        const operation = selectionAction.action;
        const snapshot = selectionAction.snapshot;
        setQuickAddBusy(operation);
        setSelectionActionError(null);

        try {
            const preparedPrimer = prepareSelectionPrimer(
                snapshot,
                operation === 'reverse_primer' ? 'reverse' : 'forward',
                sequenceData.sequenceType,
            );
            const tmResponse = await calculatePrimerTm(
                buildPrimerTmRequest(preparedPrimer, primerTmSettings),
            );
            const tmResult = tmResponse.data[0] ?? null;

            const primer = buildSelectionPrimer({
                id: `primer_${Date.now().toString(36)}_${preparedPrimer.strand === 1 ? 'f' : 'r'}`,
                name: input.name,
                notes: input.notes,
                snapshot,
                prepared: preparedPrimer,
                tm: tmResult?.tm ?? undefined,
                gcPercent: tmResult?.gc_percent ?? calculateGcPercent(preparedPrimer.sequence),
                tmAlgorithm: tmResult?.algorithm,
                tmSaltCorrection: tmResult?.salt_correction,
                tmSettings: primerTmSettings,
            });

            if (exactMolecularAuthorityRef.current) {
                setQuickAddBusy(null);
                return;
            }
            handleAddPrimer(primer);
            setHighlightedRegions(getPrimerHighlightRegions(
                primer,
                preparedPrimer.strand === 1 ? '#22c55e' : '#ef4444',
                primer.name,
                sequenceData.sequence.length,
                sequenceData.circular,
            ));
            setSelectionAction(null);
            setQuickAddBusy(null);
        } catch (error) {
            console.error('Failed to add primer from selection:', error);
            setSelectionActionError(
                `Failed to add primer: ${error instanceof Error ? error.message : 'Unknown error'}`,
            );
            setQuickAddBusy(null);
        }
    }, [
        handleAddPrimer,
        primerTmSettings,
        selectionAction,
        sequenceData.sequenceType,
    ]);

    const handleQuickAddFeature = useCallback((input: SelectionFeatureInput) => {
        if (exactMolecularAuthorityRef.current) return;
        if (!selectionAction || selectionAction.action !== 'feature') {
            return;
        }

        setQuickAddBusy('feature');
        setSelectionActionError(null);
        const snapshot = selectionAction.snapshot;
        const segments = snapshot.ranges.map((range) => ({
            start: range.start,
            end: range.end,
        }));
        const feature: Feature = {
            id: `feature_${Date.now().toString(36)}`,
            name: input.name,
            type: input.type,
            start: snapshot.placement.start,
            end: snapshot.placement.end,
            strand: input.strand,
            color: input.color || getFeatureColor(input.type),
            description: input.description || undefined,
            notes: {
                source: 'selection_dialog',
                ...(input.description ? { note: input.description } : {}),
            },
            qualifiers: {
                source: 'selection_dialog',
            },
            provenance: {
                workflow: 'selection_dialog',
                wraps_origin: snapshot.placement.wrapsOrigin,
                selected_ranges: snapshot.ranges,
            },
            segments: segments.length > 1 ? segments : undefined,
        };

        handleAddFeature(feature);
        setHighlightedRegions((feature.segments && feature.segments.length > 0 ? feature.segments : [{
            start: feature.start,
            end: feature.end,
        }]).map((segment) => ({
            start: segment.start,
            end: segment.end,
            color: feature.color || '#6b7280',
            label: feature.name,
        })));
        setSelectionAction(null);
        setQuickAddBusy(null);
    }, [handleAddFeature, selectionAction]);

    const handleMobileSelectSequence = useCallback((sequenceId: string) => {
        mobileSequenceIntentRef.current = {
            sequenceId,
            supersededSequenceId: requestedMolecularSequenceId,
            supersededRevisionId: requestedMolecularRevisionId,
        };
        void activateMobileMolBioSequence({
            sequenceId,
            loadSequence,
            onActivated: () => {
                setMobileConstructPickerOpen(false);
                setMobileSurface('map');
            },
        }).then((activated) => {
            if (!activated && mobileSequenceIntentRef.current?.sequenceId === sequenceId) {
                mobileSequenceIntentRef.current = null;
            }
        });
    }, [loadSequence, requestedMolecularRevisionId, requestedMolecularSequenceId]);

    const handleMobileLoadDemo = useCallback((demo: SequenceData) => {
        mobileSequenceIntentRef.current = null;
        sequenceLoadControllerRef.current.begin();
        invalidateGetSequence();
        pendingMobileDemoRef.current = demo;
        updateQueryParams({
            molbio_sequence_id: null,
            molbio_revision_id: null,
            sequence_id: null,
            revision_id: null,
        });
        setPendingMobileDemoVersion((current) => current + 1);
    }, [invalidateGetSequence, updateQueryParams]);

    useEffect(() => {
        const pendingDemo = pendingMobileDemoRef.current;
        if (!pendingDemo) return;
        if (
            requestedCanonicalMolecularSequenceId
            || requestedCanonicalMolecularRevisionId
            || requestedLegacyMolecularSequenceId
            || requestedLegacyMolecularRevisionId
        ) return;
        pendingMobileDemoRef.current = null;
        sequenceLoadControllerRef.current.begin();
        invalidateGetSequence();
        loadDemo(pendingDemo);
        setMobileConstructPickerOpen(false);
        setMobileSurface(pendingDemo.circular ? 'map' : 'sequence');
    }, [
        invalidateGetSequence,
        loadDemo,
        pendingMobileDemoVersion,
        requestedCanonicalMolecularRevisionId,
        requestedCanonicalMolecularSequenceId,
        requestedLegacyMolecularRevisionId,
        requestedLegacyMolecularSequenceId,
    ]);

    if (isMobileMolBio) {
        const mobileMapViewMode: ViewMode = sequenceData.circular ? 'circular' : 'linear';
        const mobileViewer = (mode: ViewMode) => (
            <div className="h-full min-h-0 overflow-hidden">
                <SequenceViewer
                    sequenceData={viewerSequenceData}
                    visibility={visibility}
                    selectedEnzymes={selectedEnzymes}
                    restrictionAnalysis={restrictionAnalysis}
                    selection={selection}
                    onSelection={handleSelection}
                    highlightedRegions={highlightedRegions}
                    viewMode={mode}
                    colorPalette={colorPalette}
                    visibleFrames={visibleFrames}
                    activeDisplayStrand={activeDisplayStrand}
                />
            </div>
        );

        return (
            <MobileMolBioWorkspace
                constructName={sequenceData.name}
                digestIdentity={`${activeWorkspaceId}:${selectedSequenceId ?? sequenceData.name}:${sequenceData.sequence.length}`}
                digestAvailable={!isExactMolecularAuthority}
                qcAvailable={!isExactMolecularAuthority}
                error={error}
                hasSequence={Boolean(sequenceData.sequence)}
                constructPickerOpen={mobileConstructPickerOpen}
                surface={mobileSurface}
                onBack={handleMobileBack}
                onOpenConstructs={() => setMobileConstructPickerOpen(true)}
                onSurfaceChange={setMobileSurface}
                constructs={(
                    <div className="flex h-full min-h-0 overflow-hidden">
                        <SequenceLibrary
                            mobile
                            sequences={sequences}
                            demos={demoPlasmids}
                            demoLoading={demoLoading}
                            selectedId={selectedSequenceId}
                            onSelect={(sequence) => handleMobileSelectSequence(sequence.id)}
                            onRefresh={loadLibrary}
                            onLoadDemo={handleMobileLoadDemo}
                            loading={loading}
                            width={viewportWidth}
                            projectScoped={Boolean(workspaceId && globalExperimentId && domainExperimentId && stateRevisionId)}
                            showAllConstructs={showAllConstructs}
                            onToggleAllConstructs={() => setShowAllConstructs((value) => !value)}
                        />
                    </div>
                )}
                map={mobileViewer(mobileMapViewMode)}
                sequence={mobileViewer('linear')}
                details={(
                    <MobileMolBioReadPanel
                        mode="details"
                        sequenceData={sequenceData}
                        workups={ngsWorkups}
                        workupsStatus={ngsWorkupStatus}
                    />
                )}
                digest={(
                    <DigestPanel
                        mobile
                        compactLandscape={viewportWidth > viewportHeight && viewportHeight <= 500}
                        sequenceData={sequenceData}
                        sequenceId={selectedSequenceId}
                        selection={selection}
                        onHighlight={setHighlightedRegions}
                        selectedEnzymes={selectedEnzymes}
                        onEnzymesChange={setSelectedEnzymes}
                        onMapVisibilityRequest={ensureCutSitesVisible}
                        catalog={restrictionCatalog}
                        catalogRecords={restrictionCatalogRecords}
                        analysis={restrictionAnalysis}
                        authorityLoading={restrictionAuthorityLoading}
                        authorityError={restrictionAuthorityError}
                        digestSimulation={restrictionDigest}
                        digestLoading={restrictionDigestLoading}
                        digestError={restrictionDigestError}
                        onDigestSelectionChange={handleRestrictionDigestSelection}
                        onSimulateDigest={runRestrictionDigest}
                    />
                )}
                qc={(
                    <MobileMolBioReadPanel
                        mode="qc"
                        sequenceData={sequenceData}
                        workups={ngsWorkups}
                        workupsStatus={ngsWorkupStatus}
                    />
                )}
            />
        );
    }

    return (
        <>
            {(deepLinkOperationId || deepLinkReceiptId) && (
                <aside className="border-b border-slate-700 bg-slate-950 px-4 py-2 text-xs text-slate-200" aria-label="Exact MolBio source context">
                    {deepLinkOperationId && (
                        <span className="mr-3">Operation <code>{deepLinkOperationId}</code>{' '}
                            <strong>{deepLinkOperationState ?? 'loading'}</strong>
                        </span>
                    )}
                    {deepLinkReceiptId && (
                        <span>Receipt <code>{deepLinkReceiptId}</code>{' '}
                            <strong>{
                                deepLinkReceiptId === deepLinkRevisionId || deepLinkReceiptId === deepLinkOperationId
                                    ? 'loaded'
                                    : 'unavailable'
                            }</strong>
                        </span>
                    )}
                </aside>
            )}
            <div
                ref={toolkitRootRef}
                tabIndex={-1}
                className={`molbio-toolkit w-full flex bg-slate-900 text-slate-100 overflow-hidden ${isViewerFullscreen ? 'fixed inset-0 z-[70] h-full' : ''}`}
                style={isViewerFullscreen
                    ? undefined
                    : { height: 'clamp(36rem, calc(100vh - 8rem), 96rem)' }}
                data-molbio-viewer-fullscreen={isViewerFullscreen ? 'true' : 'false'}
                data-molbio-viewer-desktop={!isMobileMolBio ? 'true' : 'false'}
            >
                {/* Left: Sequence Library */}
                {viewerLayout.showLibraryPanel && (
                    <>
                        <SequenceLibrary
                            sequences={sequences}
                            demos={demoPlasmids}
                            demoLoading={demoLoading}
                            selectedId={selectedSequenceId}
                            onSelect={(sequence) => {
                                if (sequence.revision_id) {
                                    requestWorkspaceTransition('open an immutable revision', () => {
                                        approveMolecularOpenRequest({
                                            kind: 'exact',
                                            sequenceId: sequence.id,
                                            revisionId: sequence.revision_id!,
                                        });
                                        updateQueryParams({
                                            molbio_sequence_id: sequence.id,
                                            molbio_revision_id: sequence.revision_id,
                                        });
                                    }, {
                                        dirty: isDirty,
                                        workspaceId: activeWorkspaceId,
                                        discard: discardActiveWorkspace,
                                    });
                                    return;
                                }
                                guardedLoadSequence(sequence.id);
                            }}
                            onRefresh={loadLibrary}
                            onLoadDemo={guardedLoadDemo}
                            loading={loading}
                            width={viewerLayout.leftPanelWidth}
                            projectScoped={Boolean(workspaceId && globalExperimentId && domainExperimentId && stateRevisionId)}
                            showAllConstructs={showAllConstructs}
                            onToggleAllConstructs={() => setShowAllConstructs((value) => !value)}
                        />
                        {viewerLayout.showLibraryResizeHandle && (
                            <button
                                type="button"
                                data-molbio-panel-resize-handle="left"
                                aria-label="Resize construct shelf"
                                title="Resize construct shelf"
                                onPointerDown={startPanelResize('left')}
                                className="touch-none w-4 md:w-1.5 flex-shrink-0 cursor-col-resize bg-slate-950/80 transition-colors hover:bg-blue-500/60"
                            />
                        )}
                    </>
                )}

                {/* Center: Viewer */}
                <div className="relative flex-1 flex flex-col min-w-0 overflow-hidden">
                    {!isViewerFullscreen && (
                        <SequenceHeader
                            sequenceData={sequenceData}
                            onSave={isExactMolecularAuthority ? undefined : saveSequence}
                            onUndo={isExactMolecularAuthority ? undefined : undo}
                            onRedo={isExactMolecularAuthority ? undefined : redo}
                            onAutoAnnotate={isExactMolecularAuthority ? undefined : handleAutoAnnotate}
                            canUndo={!isExactMolecularAuthority && canUndo}
                            canRedo={!isExactMolecularAuthority && canRedo}
                            isDirty={!isExactMolecularAuthority && isDirty}
                            loading={loading}
                            isAnnotating={isAnnotating}
                            viewMode={effectiveViewMode}
                            onViewModeChange={setViewMode}
                            activeDisplayStrand={activeDisplayStrand}
                            sourceDisplayStrand={sourceDisplayStrand}
                            onDisplayStrandChange={handleDisplayStrandChange}
                            showGCTrack={showGCTrack}
                            onGCTrackToggle={() => setShowGCTrack(prev => !prev)}
                            onOpenLibrary={isExactMolecularAuthority ? undefined : () => setShowInputModal(true)}
                            isViewerFullscreen={isViewerFullscreen}
                            onToggleFullscreen={toggleViewerFullscreen}
                            isLibraryPanelCollapsed={isLibraryPanelCollapsed}
                            isToolPanelCollapsed={isToolPanelCollapsed}
                            onToggleLibraryPanel={toggleLibraryPanel}
                            onToggleToolPanel={toggleToolPanel}
                            historyJournal={historyJournal}
                        />
                    )}

                    {workspaceRestoreNotice && !isViewerFullscreen && (
                        <div role="status" className="mx-3 mt-2 flex items-center justify-between gap-3 rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
                            <span>{workspaceRestoreNotice}</span>
                            <button type="button" onClick={() => setWorkspaceRestoreNotice(null)} className="text-amber-100 hover:text-white">Dismiss</button>
                        </div>
                    )}

                    {!isViewerFullscreen && (hasIncompleteMolecularPair || exactMolecularLoading || exactMolecularError || selectedExactMolecularRevision) && (
                        <section
                            className={`mx-3 mt-2 rounded-lg border px-3 py-3 text-xs ${selectedExactMolecularRevision
                                ? 'border-amber-500/60 bg-amber-950/30'
                                : exactMolecularError || hasIncompleteMolecularPair
                                    ? 'border-red-700 bg-red-950/30'
                                    : 'border-cyan-700 bg-cyan-950/30'
                            }`}
                            aria-label="Exact immutable molecular revision authority"
                        >
                            {hasIncompleteMolecularPair && (
                                <div className="text-red-300">
                                    A molecular revision ID cannot be reopened without molbio_sequence_id. No sequence ID was inferred.
                                </div>
                            )}
                            {exactMolecularLoading && (
                                <div className="text-cyan-200">Loading the requested immutable molecular revision…</div>
                            )}
                            {exactMolecularError && (
                                <div className="text-red-300">Unable to open exact molecular revision: {exactMolecularError}</div>
                            )}
                            {selectedExactMolecularRevision && (
                                <div className="space-y-3">
                                    <div className="flex flex-wrap items-center justify-between gap-3">
                                        <div>
                                            <div className="font-semibold text-amber-200">
                                                Viewing saved revision #{selectedExactMolecularRevision.revision_number} · Read-only
                                            </div>
                                            <div className="mt-1 text-slate-300">Changes are disabled until you open the latest editable version.</div>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={openCurrentEditableProjection}
                                            className="rounded-md border border-cyan-600 bg-cyan-950/60 px-3 py-1.5 font-medium text-cyan-200 transition-colors hover:bg-cyan-900/60"
                                        >
                                            Open latest editable version
                                        </button>
                                    </div>
                                    <details className="rounded border border-amber-800/60 bg-slate-950/50 px-3 py-2">
                                        <summary className="cursor-pointer font-medium text-amber-200">Revision details</summary>
                                        <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-2 xl:grid-cols-4">
                                            <div><dt className="text-slate-500">Sequence ID</dt><dd className="break-all font-mono text-slate-200">{selectedExactMolecularRevision.sequence_id}</dd></div>
                                            <div><dt className="text-slate-500">Revision ID</dt><dd className="break-all font-mono text-slate-200">{selectedExactMolecularRevision.revision_id}</dd></div>
                                            <div><dt className="text-slate-500">Revision</dt><dd className="text-slate-200">#{selectedExactMolecularRevision.revision_number} · {selectedExactMolecularRevision.relation}</dd></div>
                                            <div><dt className="text-slate-500">Created</dt><dd className="text-slate-200">{new Date(selectedExactMolecularRevision.created_at).toLocaleString()}</dd></div>
                                            <div className="sm:col-span-2 xl:col-span-4"><dt className="text-slate-500">Content SHA-256</dt><dd className="break-all font-mono text-slate-200">{selectedExactMolecularRevision.content_sha256}</dd></div>
                                            <div><dt className="text-slate-500">Change kind</dt><dd className="text-slate-200">{selectedExactMolecularRevision.change_kind.replace(/_/g, ' ')}</dd></div>
                                            <div><dt className="text-slate-500">Parent revision</dt><dd className="break-all font-mono text-slate-200">{selectedExactMolecularRevision.parent_revision_id ?? 'root revision'}</dd></div>
                                            <div><dt className="text-slate-500">Operation ID</dt><dd className="break-all font-mono text-slate-200">{selectedExactMolecularRevision.operation_id ?? 'none'}</dd></div>
                                            <div><dt className="text-slate-500">Created by</dt><dd className="break-all text-slate-200">{selectedExactMolecularRevision.created_by ?? 'not recorded'}</dd></div>
                                        </dl>
                                        <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-all border-t border-amber-800/60 pt-3 text-[11px] text-slate-300">{JSON.stringify(selectedExactMolecularRevision.provenance, null, 2)}</pre>
                                    </details>
                                </div>
                            )}
                        </section>
                    )}

                    {!isViewerFullscreen && selectedSequenceId && (
                        <section className="mx-3 mt-2 rounded border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs" aria-label="Sequencing verification workup">
                            <div className="flex items-center justify-between gap-3">
                                <span className="font-medium text-slate-200">Sequencing verification (read-only)</span>
                                <a
                                    className="text-blue-300 hover:text-blue-200"
                                    href={contextHref('/ngs', {
                                        molbio_sequence_id: selectedSequenceId,
                                        molbio_revision_id: selectedExactMolecularRevision?.revision_id ?? null,
                                    })}
                                >
                                    Hand off {selectedExactMolecularRevision ? 'this exact immutable revision' : 'current construct'} to Nanopore
                                </a>
                            </div>
                            {ngsWorkups.length === 0 ? (
                                <p className="mt-1 text-slate-400">No revision-bound NGS evidence. Job completion is not a scientific PASS.</p>
                            ) : ngsWorkups.map((workup) => (
                                <div key={workup.job_id} className="mt-1 flex items-center justify-between text-slate-300">
                                    <span>{workup.scientific_status} · {workup.revision_relation === 'current' ? 'current revision' : 'historical revision'} · {workup.manifest_available ? 'validated manifest' : 'evidence unavailable/review'}</span>
                                    <a className="text-blue-300 hover:text-blue-200" href={ngsResultHref(workup.job_id, location.search)}>NGS Run Inspector</a>
                                </div>
                            ))}
                        </section>
                    )}

                    {!isViewerFullscreen && (
                        <WorkspaceTabs
                            tabs={workspaceTabs}
                            activeId={activeWorkspaceId}
                            onActivate={activateWorkspace}
                            onClose={closeWorkspace}
                        />
                    )}


                    <div className="relative flex-1 overflow-hidden flex flex-col">
                        {sequenceData.circular && (
                            <div
                                style={isViewerFullscreen ? {
                                    left: 'calc(env(safe-area-inset-left) + 0.75rem)',
                                    top: 'calc(env(safe-area-inset-top) + 0.75rem)',
                                } : undefined}
                                className={`pointer-events-none absolute z-20 flex items-center gap-2 ${
                                    isViewerFullscreen
                                        ? 'left-[max(env(safe-area-inset-left),0.75rem)] top-[max(env(safe-area-inset-top),0.75rem)]'
                                        : `right-4 ${showGCTrack ? 'top-[212px]' : 'top-4'}`
                                }`}
                            >
                                {isViewerFullscreen ? (
                                    <button
                                        type="button"
                                        data-molbio-focus-exit="true"
                                        onClick={() => setIsViewerFullscreen(false)}
                                        className="pointer-events-auto min-h-12 rounded-lg border border-cyan-500/70 bg-slate-950/95 px-4 text-sm font-semibold text-slate-100 shadow-xl transition-colors hover:bg-slate-800"
                                        title="Exit focused plasmid view"
                                    >
                                        Exit Focus
                                    </button>
                                ) : (
                                    <button
                                        type="button"
                                        onClick={toggleViewerFullscreen}
                                        className="pointer-events-auto rounded-full border border-slate-600 bg-slate-900/90 px-3 py-1.5 text-sm font-medium text-slate-100 shadow-lg transition-colors hover:bg-slate-800"
                                        title="Focus Viewer"
                                    >
                                        Focus Viewer
                                    </button>
                                )}
                            </div>
                        )}
                        {sequenceData.sequence ? (
                            <>
                                {/* GC Content Track */}
                                {!isViewerFullscreen && showGCTrack && (
                                    <GCContentTrack
                                        sequence={sequenceData.sequence}
                                        sequenceType={sequenceData.sequenceType === 'rna' ? 'rna' : 'dna'}
                                        reverseCoordinates={sourceDisplayStrand !== activeDisplayStrand}
                                        circular={sequenceData.circular}
                                        selectedEnzymes={selectedEnzymes}
                                        restrictionOccurrences={restrictionAnalysis?.analysis.occurrences ?? []}
                                        selection={selection}
                                        onSelectionChange={handleSelection}
                                        onClearSelection={() => setSelection(null)}
                                        windowSize={Math.max(20, Math.min(100, Math.floor(sequenceData.sequence.length / 50)))}
                                        height={108}
                                    />
                                )}

                                {/* Sequence Viewer */}
                                {showRnaStructureViewer ? (
                                    <div className="flex-1 overflow-hidden grid grid-rows-[minmax(220px,42%)_minmax(280px,58%)]">
                                        <div className="min-h-0 overflow-hidden">
                                            <SequenceViewer
                                                sequenceData={viewerSequenceData}
                                                visibility={visibility}
                                                selectedEnzymes={selectedEnzymes}
                    restrictionAnalysis={restrictionAnalysis}
                                                selection={selection}
                                                onSelection={handleSelection}
                                                onContextMenu={isExactMolecularAuthority ? undefined : handleViewerContextMenu}
                                                highlightedRegions={highlightedRegions}
                                                viewMode={effectiveViewMode}
                                                colorPalette={colorPalette}
                                                visibleFrames={visibleFrames}
                                                activeDisplayStrand={activeDisplayStrand}
                                            />
                                        </div>
                                        {rnaStructureResult && (
                                            <div className="min-h-0 overflow-hidden border-t border-slate-700">
                                                <RnaStructureViewer
                                                    result={rnaStructureResult}
                                                    displayMode={rnaDisplayMode}
                                                    evidenceTrack={selectedRnaEvidenceTrack}
                                                />
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    <div className="flex-1 min-h-0 overflow-hidden">
                                        <SequenceViewer
                                            sequenceData={viewerSequenceData}
                                            visibility={visibility}
                                            selectedEnzymes={selectedEnzymes}
                    restrictionAnalysis={restrictionAnalysis}
                                            selection={selection}
                                            onSelection={handleSelection}
                                            onContextMenu={isExactMolecularAuthority ? undefined : handleViewerContextMenu}
                                            highlightedRegions={highlightedRegions}
                                            viewMode={effectiveViewMode}
                                            colorPalette={colorPalette}
                                            visibleFrames={visibleFrames}
                                            activeDisplayStrand={activeDisplayStrand}
                                        />
                                    </div>
                                )}
                            </>
                        ) : (
                            <div className="flex items-center justify-center h-full text-slate-500">
                                <div className="text-center">
                                    <svg className="w-16 h-16 mx-auto mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                    </svg>
                                    <p className="text-lg">Select a sequence from the library</p>
                                    <p className="text-sm mt-1">or expand "Demo Plasmids" to try one</p>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Selection info bar */}
                    {!isViewerFullscreen && selection && (
                        <div className="border-t border-slate-700 bg-slate-800 px-4 py-2 text-sm text-slate-300 flex-shrink-0">
                            <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                    {selectionLength === 0
                                        ? `Cursor: ${selection.start + 1}`
                                        : `Selected: ${selectionCoordinateLabel} (${selectionLength} ${sequenceUnitLabel(sequenceData.sequenceType === 'rna' ? 'rna' : 'dna')})`}
                                </div>
                                <div className="flex items-center gap-2">
                                    {hasRangeSelection && !isExactMolecularAuthority && (
                                        <>
                                            <button
                                                onClick={() => setActivePanel('primers')}
                                                className="rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-emerald-500"
                                                title="Open primer tools for the selected span"
                                            >
                                                Primer
                                            </button>
                                            <button
                                                onClick={() => setActivePanel('features')}
                                                className="rounded-md bg-violet-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-violet-500"
                                                title="Open feature tools for the selected span"
                                            >
                                                Marker
                                            </button>
                                            <button
                                                onClick={() => setActivePanel('edit')}
                                                className="rounded-md bg-amber-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-amber-500"
                                                title="Edit the selected span"
                                            >
                                                Edit
                                            </button>
                                            <button
                                                onClick={() => setActivePanel('align')}
                                                className="rounded-md bg-sky-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-sky-500"
                                                title="Compare the selected span in alignment tools"
                                            >
                                                Align
                                            </button>
                                            <button
                                                onClick={() => setActivePanel('pcr')}
                                                className="rounded-md bg-cyan-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-cyan-500"
                                                title="Use the selected span in PCR tools"
                                            >
                                                PCR
                                            </button>
                                            <button
                                                onClick={() => setActivePanel('assembly')}
                                                className="rounded-md bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-indigo-500"
                                                title="Send the selected span into assembly workflows"
                                            >
                                                Assembly
                                            </button>
                                        </>
                                    )}
                                    <button
                                        onClick={() => setSelection(null)}
                                        className="rounded-md border border-slate-600 px-2.5 py-1 text-xs text-slate-300 transition-colors hover:bg-slate-700"
                                        title="Clear current selection"
                                    >
                                        Clear
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Right: Tool Panels */}
                {viewerLayout.showToolPanel && (
                    <>
                        {viewerLayout.showToolResizeHandle && (
                            <button
                                type="button"
                                data-molbio-panel-resize-handle="right"
                                aria-label="Resize toolkit panels"
                                title="Resize toolkit panels"
                                onPointerDown={startPanelResize('right')}
                                className="touch-none w-4 md:w-1.5 flex-shrink-0 cursor-col-resize bg-slate-950/80 transition-colors hover:bg-blue-500/60"
                            />
                        )}
                        <div
                            className="flex-shrink-0 border-l border-slate-700 bg-slate-800 flex flex-col overflow-hidden transition-[width] duration-200"
                            style={{ width: `${viewerLayout.rightPanelWidth}px` }}
                        >
                            <PanelTabs
                                active={activePanel}
                                onChange={setActivePanel}
                                sequenceType={sequenceData.sequenceType}
                                readOnlyAuthority={isExactMolecularAuthority}
                            />

                            <div className="flex-1 overflow-y-auto">
                        {(activePanel === 'view' || activePanel === null) && (
                            <VisibilityPanel
                                visibility={visibility}
                                onChange={handleVisibilityChange}
                                colorPalette={colorPalette}
                                onColorPaletteChange={setColorPalette}
                                visibleFrames={visibleFrames}
                                onVisibleFramesChange={setVisibleFrames}
                            />
                        )}
                        {activePanel === 'search' && (
                            <SearchPanel
                                sequenceData={sequenceData}
                                onHighlight={setHighlightedRegions}
                                onOrfsFound={setDerivedTranslations}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'align' && (
                            <AlignmentPanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                onAddFeatures={handleAddFeatures}
                                comparisonTargets={alignmentTargets}
                            />
                        )}
                        {activePanel === 'history' && (
                            <HistoryPanel
                                sequenceData={sequenceData}
                                selectedSequenceId={selectedSequenceId}
                                historyJournal={historyJournal}
                                workspaces={workspaceSummaries}
                                activeWorkspaceId={activeWorkspaceId}
                                onActivateWorkspace={activateWorkspace}
                                revisionHref={(sequenceId, revisionId) => contextHref(location.pathname, {
                                    molbio_sequence_id: sequenceId,
                                    molbio_revision_id: revisionId,
                                })}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'assembly' && (
                            <AssemblyPanel
                                sequenceData={sequenceData}
                                selection={selection}
                                selectedSequenceId={selectedSequenceId}
                                onLoadProduct={handleLoadAssemblyProduct}
                                onLoadSavedWorkup={guardedLoadSequence}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'edit' && (
                            <EditPanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onSequenceChange={(newData, actionLabel) => {
                                    if (exactMolecularAuthorityRef.current) return;
                                    setSequenceData({
                                        ...newData,
                                        features: normalizeFeatureList(newData.features || []),
                                    }, actionLabel);
                                    setIsDirty(true);
                                }}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'digest' && (
                            <DigestPanel
                                sequenceData={sequenceData}
                                sequenceId={selectedSequenceId}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                selectedEnzymes={selectedEnzymes}
                                onEnzymesChange={setSelectedEnzymes}
                                onMapVisibilityRequest={ensureCutSitesVisible}
                        catalog={restrictionCatalog}
                        catalogRecords={restrictionCatalogRecords}
                        analysis={restrictionAnalysis}
                        authorityLoading={restrictionAuthorityLoading}
                        authorityError={restrictionAuthorityError}
                        digestSimulation={restrictionDigest}
                        digestLoading={restrictionDigestLoading}
                        digestError={restrictionDigestError}
                        onDigestSelectionChange={handleRestrictionDigestSelection}
                        onSimulateDigest={runRestrictionDigest}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'pcr' && (
                            <PCRPanel
                                sequenceData={sequenceData}
                                sequenceId={selectedSequenceId}
                                onHighlight={setHighlightedRegions}
                                tmOptions={primerTmOptions}
                                tmSettings={primerTmSettings}
                                onTmSettingsChange={setPrimerTmSettings}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'primers' && (
                            <PrimerPanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                onAddPrimer={handleAddPrimer}
                                onRemovePrimer={handleRemovePrimer}
                                tmOptions={primerTmOptions}
                                tmSettings={primerTmSettings}
                                onTmSettingsChange={setPrimerTmSettings}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'rna' && (
                            <RnaStructurePanel
                                sequenceData={sequenceData}
                                structureResult={rnaStructureResult}
                                displayMode={rnaDisplayMode}
                                onDisplayModeChange={setRnaDisplayMode}
                                onStructureResultChange={setRnaStructureResult}
                                selectedTrackId={selectedRnaTrackId}
                                onSelectedTrackChange={setSelectedRnaTrackId}
                                onAnalysisTracksChange={handleAnalysisTracksChange}
                            />
                        )}
                        {!isExactMolecularAuthority && activePanel === 'features' && (
                            <FeaturePanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                onAddFeature={handleAddFeature}
                                onRemoveFeature={handleRemoveFeature}
                                onUpdateFeature={handleUpdateFeature}
                            />
                        )}
                            </div>

                            {/* Error display */}
                            {error && (
                                <div role="alert" className="p-3 bg-red-900/50 border-t border-red-800 text-red-200 text-sm flex-shrink-0">
                                    <strong className="block">MolBio request unavailable</strong>
                                    <span className="mt-1 block">Retry the action. {String(error).replace(/\s*\(?HTTP\s+\d+\)?/gi, '').trim()}</span>
                                </div>
                            )}
                        </div>
                    </>
                )}
            </div>

            {quickAddMenu && (
                <>
                    <button
                        type="button"
                        aria-label="Close quick add menu"
                        className="fixed inset-0 z-40 cursor-default bg-transparent"
                        onClick={closeQuickAddMenuAndRestoreFocus}
                    />
                    <div
                        ref={quickAddMenuRef}
                        role="menu"
                        aria-label="Selection actions"
                        onKeyDown={handleQuickAddMenuKeyDown}
                        onBlur={(event) => {
                            if (didFocusLeaveContainer(event.currentTarget, event.relatedTarget)) {
                                closeQuickAddMenuAndRestoreFocus();
                            }
                        }}
                        className="fixed z-50 w-64 rounded-xl border border-slate-700 bg-slate-900/95 p-2 shadow-2xl backdrop-blur"
                        style={{
                            left: Math.max(12, Math.min(quickAddMenu.x, window.innerWidth - 280)),
                            top: Math.max(12, Math.min(quickAddMenu.y, window.innerHeight - 280)),
                        }}
                    >
                        <div className="border-b border-slate-700 px-2 pb-2">
                            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">
                                Quick Add
                            </div>
                            <div className="mt-1 text-sm text-slate-200">
                                {quickAddMenu.snapshot?.coordinateLabel ?? 'No range selected'}
                            </div>
                            <div className="mt-1 text-[11px] text-slate-500">
                                {quickAddMenu.snapshot
                                    ? `${quickAddMenu.snapshot.length} ${sequenceUnitLabel(sequenceData.sequenceType === 'rna' ? 'rna' : 'dna')} selected and locked`
                                    : 'Drag a span in the viewer, then right-click to add primers or features.'}
                            </div>
                        </div>

                        <div className="space-y-1 px-1 py-2">
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => openSelectionAction('forward_primer')}
                                disabled={!quickAddMenu.snapshot}
                                className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                <span>Configure Forward Primer…</span>
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => openSelectionAction('reverse_primer')}
                                disabled={!quickAddMenu.snapshot}
                                className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                <span>Configure Reverse Primer…</span>
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => openSelectionAction('feature')}
                                disabled={!quickAddMenu.snapshot}
                                className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                <span>Configure Marker / Feature…</span>
                            </button>
                        </div>

                        <div className="border-t border-slate-700 px-1 pt-2">
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    setActivePanel('primers');
                                    closeQuickAddMenuAndRestoreFocus();
                                }}
                                className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-300 transition-colors hover:bg-slate-800"
                            >
                                Open Primer Workspace
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    setActivePanel('features');
                                    closeQuickAddMenuAndRestoreFocus();
                                }}
                                className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-300 transition-colors hover:bg-slate-800"
                            >
                                Open Feature Workspace
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    setActivePanel('assembly');
                                    closeQuickAddMenuAndRestoreFocus();
                                }}
                                className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-300 transition-colors hover:bg-slate-800"
                            >
                                Open Assembly Workspace
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    setSelection(null);
                                    closeQuickAddMenuAndRestoreFocus();
                                }}
                                className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-400 transition-colors hover:bg-slate-800"
                            >
                                Clear Selection
                            </button>
                        </div>
                    </div>
                </>
            )}

            {!isExactMolecularAuthority && selectionAction && (
                <SelectionActionDialog
                    action={selectionAction.action}
                    snapshot={selectionAction.snapshot}
                    sequenceType={sequenceData.sequenceType}
                    busy={quickAddBusy !== null}
                    error={selectionActionError}
                    returnFocusTarget={quickAddInvokerRef.current}
                    fallbackFocusTarget={toolkitRootRef.current}
                    onClose={closeSelectionAction}
                    onConfirmFeature={handleQuickAddFeature}
                    onConfirmPrimer={(input) => void handleQuickAddPrimer(input)}
                />
            )}

            {/* Auto-Annotate Settings Panel */}
            <AutoAnnotatePanel
                isOpen={!isExactMolecularAuthority && showAnnotatePanel}
                onClose={() => setShowAnnotatePanel(false)}
                onAnnotate={runAutoAnnotate}
                onClearAnnotations={clearAnnotations}
                onImportAnnotations={importAnnotationsFromFile}
                onRetrieveNcbi={retrieveNcbiAnnotations}
                onRetrieveAddgene={retrieveAddgeneAnnotations}
                annotationSourceStatus={annotationSourceStatus}
                isAnnotating={isAnnotating}
                hasSequence={!!sequenceData.sequence}
                featureCount={sequenceData.features.length}
                sequenceLength={sequenceData.sequence.length}
                isCircular={sequenceData.circular}
            />

            {pendingWorkspaceTransition && (
                <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Unsaved molecular workspace">
                    <div className="w-full max-w-md rounded-xl border border-amber-700 bg-slate-900 p-5 shadow-2xl">
                        <h3 className="text-base font-semibold text-amber-200">Unsaved molecular changes</h3>
                        <p className="mt-2 text-sm text-slate-300">Save or discard changes before you {pendingWorkspaceTransition.description}.</p>
                        {workspaceTransitionError && <p role="alert" className="mt-3 text-sm text-red-300">{workspaceTransitionError}</p>}
                        <div className="mt-5 flex flex-wrap justify-end gap-2">
                            <button type="button" onClick={() => void completeWorkspaceTransition('stay')} className="rounded border border-slate-600 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800">Stay</button>
                            <button type="button" onClick={() => void completeWorkspaceTransition('discard')} className="rounded border border-red-700 px-3 py-2 text-sm text-red-200 hover:bg-red-950">Discard and continue</button>
                            <button type="button" onClick={() => void completeWorkspaceTransition('save')} className="rounded bg-cyan-700 px-3 py-2 text-sm font-medium text-white hover:bg-cyan-600">Save and continue</button>
                        </div>
                    </div>
                </div>
            )}

            <MolecularInputModal
                isOpen={!isExactMolecularAuthority && showInputModal}
                onClose={() => setShowInputModal(false)}
                onSelectSequence={guardedLoadSequence}
                onImportFile={handleImport}
                onCreateSequence={handlePasteSequence}
                onLoadDemo={guardedLoadDemo}
                onAddPrimerToCurrentSequence={handleAddPrimer}
                onOpenPrimerAsConstruct={handleOpenPrimerAsConstruct}
                hasOpenSequence={Boolean(sequenceData.sequence)}
                currentSequenceData={sequenceData.sequence ? sequenceData : null}
                demos={demoPlasmids}
            />
        </>
    );
}

// Default export for backwards compatibility
export default MolBioToolkitV2;
