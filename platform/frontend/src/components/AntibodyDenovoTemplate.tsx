import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob, uploadFile, extractChain, annotateFrameworkCdrs, downloadSabdabFramework, launchAntibodyIteration, launchManualMutagenesis, previewBoltzGenDesignSpec, type BoltzGenPreviewResponse, type CDRAnnotationResponse, type RfScreeningScope } from '../lib/api';
import { useNavigate, useLocation } from 'react-router-dom';
import { getModelByNumber, parsePDBFile, type Chain, type ParsedPDB } from '../utils/pdbUtils';
import { EpitopeSelector } from './EpitopeSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { DesignModeSelector } from './DesignModeSelector';
import {
    QualitySettingsPanel,
    type QualitySettings,
    type PPIFlowStageMode,
    type PPIFlowRegionMode,
    type PPIFlowObjectiveMode,
} from './QualitySettingsPanel';
import { PRESETS, applyPpiFlowStageMode, normalizePpiFlowTuningProfile } from './qualitySettingsLogic';
import { TemplateManagerModal } from './TemplateManagerModal';
import { FrameworkBrowser, type SelectedFramework } from './FrameworkBrowser';
import { FrameworkEditor, type FrameworkEditorState } from './FrameworkEditor';
import {
    deriveBoltzgenScaffoldSelectionUpdate,
    resolveBoltzgenReferencePreviewEnabled,
} from './antibodyDenovoBoltzgenScaffold';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings } from './PhysicsRefinementPanel';
import { DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './physicsRefinementSettings';
import { CDRRangeSelector, type CDRDefinition } from './CDRRangeSelector';
import {
    clearAntibodyRefinementLaunchState,
    loadAntibodyRefinementLaunchState,
    saveAntibodyRefinementLaunchState,
    type AntibodyRefinementLaunchState,
} from '../lib/refinementLaunchState';
import {
    ANTIBODY_DENOVO_PIPELINE_MODE,
    ANTIBODY_REFINEMENT_PIPELINE_MODE,
} from '../lib/antibodyModes';
import { useLiveGpuCatalog } from './useLiveGpuCatalog';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { createLatestAsyncResourceController } from '../lib/latestAsyncResource';

interface AntibodyDenovoTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, UntypedApiValue>;
}

type DesignMode = 'cdr_only' | 'cdr_selective' | 'framework_allowed' | 'full_design';
type LoopLengthMode = 'defaults' | 'custom_ranges';
type LoopLengthRange = { min: number; max: number };
type InteractiveGateStage = 'post_rfantibody' | 'post_boltzgen' | 'post_ppiflow_generator' | 'post_fampnn' | 'post_caliby' | 'post_structure_validation';
type SeqDesigner = 'none' | 'fampnn' | 'caliby' | 'antifold' | 'proteinmpnn';
type RefinementPreset = 'full_loop' | 'fampnn_only' | 'validation_only' | 'ppiflow_only' | 'manual_mutagenesis' | 'custom';
type MutagenesisMethod = 'explicit_substitutions' | 'cdr_indels';
type MutagenesisLaunchMode = 'seeded_refinement' | 'exact_evaluation';
type DeNovoGenerator = 'rfantibody' | 'boltzgen' | 'ppiflow';
type DeNovoOrchestrationStage = 'sequence_design' | 'ppiflow' | 'validation' | 'qc';
type BoltzgenScaffoldSource = 'default_ensemble' | 'selected_scaffold' | 'sequence_template';
type BoltzgenCheckpointMode = 'both' | 'diverse' | 'adherence';

const normalizeRfScreeningScope = (value: unknown): RfScreeningScope =>
    value === 'whole_antibody' ? 'whole_antibody' : 'cdr_loops';

const DEFAULT_RFA_LOOP_LENGTH_RANGES: Record<string, LoopLengthRange> = {
    H1: { min: 7, max: 10 },
    H2: { min: 6, max: 8 },
    H3: { min: 5, max: 15 },
    L1: { min: 8, max: 13 },
    L2: { min: 7, max: 7 },
    L3: { min: 9, max: 11 },
};

const DEFAULT_BOLTZGEN_VHH_FRAMEWORK = `QVQLVESGGGLVQPGGSLRLSCAASGGSEYSYSTFSLGWFRQAPGQGLEAVAAIASMGGLTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCAAVRGYFMRLPSSHNFRYWGQGTLVTVS`;
const DEFAULT_BOLTZGEN_ENSEMBLE = ['3DWT', '5U64', '7EOW', '8Z8M'];

const buildGeneratorOnlyStageSelection = (): Record<DeNovoOrchestrationStage, boolean> => ({
    sequence_design: false,
    ppiflow: false,
    validation: false,
    qc: false,
});

const cloneDefaultLoopRanges = (): Record<string, LoopLengthRange> =>
    Object.fromEntries(
        Object.entries(DEFAULT_RFA_LOOP_LENGTH_RANGES).map(([loopId, range]) => [
            loopId,
            { ...range },
        ])
    );

const parseLoopLengthRanges = (raw: unknown): Record<string, LoopLengthRange> => {
    const parsed = cloneDefaultLoopRanges();

    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
        Object.entries(raw as Record<string, UntypedApiValue>).forEach(([loopId, value]) => {
            if (!parsed[loopId] || !value || typeof value !== 'object') return;
            const min = Number((value as UntypedApiValue).min);
            const max = Number((value as UntypedApiValue).max);
            if (Number.isFinite(min) && Number.isFinite(max) && min >= 1 && max >= min) {
                parsed[loopId] = { min, max };
            }
        });
        return parsed;
    }

    if (typeof raw === 'string') {
        const body = raw.trim().replace(/^\[/, '').replace(/\]$/, '');
        body.split(',').map((token) => token.trim()).filter(Boolean).forEach((token) => {
            const match = token.match(/^([HL][123]):(\d+)(?:-(\d+))?$/i);
            if (!match) return;
            const loopId = match[1].toUpperCase();
            const min = Number(match[2]);
            const max = Number(match[3] || match[2]);
            if (parsed[loopId] && Number.isFinite(min) && Number.isFinite(max) && min >= 1 && max >= min) {
                parsed[loopId] = { min, max };
            }
        });
    }

    return parsed;
};

const normalizeLoopScopeInput = (raw: string | null | undefined): string | undefined => {
    if (!raw) return undefined;
    const loops = raw
        .split('[').join('').split(']').join('')
        .split(/[\s,;|]+/)
        .map((value) => value.trim().toUpperCase())
        .filter(Boolean);
    return loops.length > 0 ? Array.from(new Set(loops)).join(',') : undefined;
};

const normalizePpiFlowRegionMode = (raw: unknown): PPIFlowRegionMode => {
    const normalized = String(raw || '').trim().toLowerCase();
    if (normalized === 'all_cdrs') return 'all_cdrs';
    if (normalized === 'framework_only' || normalized === 'framework') return 'framework_only';
    if (normalized === 'all_antibody' || normalized === 'whole_antibody' || normalized === 'full_antibody') return 'all_antibody';
    return 'selected_cdrs';
};

const normalizePpiFlowObjectiveMode = (raw: unknown): PPIFlowObjectiveMode => {
    const normalized = String(raw || '').trim().toLowerCase();
    if (normalized === 'selected_interface') return 'selected_interface';
    if (normalized === 'loop_target') return 'loop_target';
    if (normalized === 'loop_epitope') return 'loop_epitope';
    return 'balanced';
};

const LEGACY_PPIFLOW_TUNING_KEYS: Array<keyof QualitySettings> = [
    'ppiflow_start_t',
    'ppiflow_samples_per_target',
    'ppiflow_retry_limit',
    'ppiflow_require_anchors',
    'ppiflow_objective_mode',
    'ppiflow_objective_threshold',
    'maturation_anchor_threshold',
    'maturation_anchor_distance_cutoff',
];

const buildManualCdrPositionsByLoop = (definitions: CDRDefinition[]): Record<string, number[]> | undefined => {
    const byLoop: Record<string, number[]> = {};
    definitions.forEach((definition) => {
        const loopId = String(definition.id || '').trim().toUpperCase();
        if (!loopId) return;
        const residues = Array.from(definition.residues || [])
            .map((token) => {
                const match = String(token).trim().toUpperCase();
                const parsed = match.slice(1);
                return /^\d+$/.test(parsed) ? Number(parsed) : null;
            })
            .filter((value): value is number => Number.isFinite(value));
        if (!residues.length) return;
        byLoop[loopId] = Array.from(new Set(residues)).sort((a, b) => a - b);
    });
    return Object.keys(byLoop).length > 0 ? byLoop : undefined;
};

const themedPanelStyle: React.CSSProperties = {
    backgroundColor: 'var(--bg-secondary)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
};

const themedInsetStyle: React.CSSProperties = {
    backgroundColor: 'color-mix(in srgb, var(--bg-tertiary) 58%, transparent)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
};

const themedMutedInsetStyle: React.CSSProperties = {
    backgroundColor: 'color-mix(in srgb, var(--bg-tertiary) 42%, transparent)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-secondary)',
};

const themedSelectedStyle = (accent: string): React.CSSProperties => ({
    backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`,
    borderColor: `color-mix(in srgb, ${accent} 72%, var(--border-primary))`,
    color: 'var(--text-primary)',
});

const themedTagStyle = (accent: string): React.CSSProperties => ({
    backgroundColor: `color-mix(in srgb, ${accent} 12%, transparent)`,
    borderColor: `color-mix(in srgb, ${accent} 56%, var(--border-primary))`,
    color: 'var(--text-primary)',
});

const residueKeyForChain = (chain: Chain, residue: Chain['residues'][number]) =>
    `${chain.id}${residue.resNum}${residue.iCode || ''}`;

const buildAvailableResidueKeySet = (chains: Chain[]) =>
    new Set(
        chains.flatMap((chain) => chain.residues.map((residue) => residueKeyForChain(chain, residue)))
    );

export const AntibodyDenovoTemplate: React.FC<AntibodyDenovoTemplateProps> = ({ onBack, initialValues }) => {
    const location = useLocation();
    const { gpuOptions } = useLiveGpuCatalog();
    const refinementQueryEnabled = useMemo(
        () => new URLSearchParams(location.search).get('refinement') === '1',
        [location.search]
    );
    const routeRefinementState = (location.state as AntibodyRefinementLaunchState | null) ?? null;
    const storedRefinementState = useMemo(
        () => (refinementQueryEnabled ? loadAntibodyRefinementLaunchState() : null),
        [refinementQueryEnabled]
    );
    const refinementState = useMemo<AntibodyRefinementLaunchState | null>(() => {
        if (!refinementQueryEnabled && !routeRefinementState?.refinementMode) {
            return null;
        }
        const merged = {
            ...(storedRefinementState || {}),
            ...(routeRefinementState || {}),
            refinementMode: true,
        };
        const hasLaunchSource = Boolean(merged.sourceJobId) && (
            Boolean(merged.selectedDesignIds?.length)
            || Boolean(merged.reviewFilterSetId)
            || Boolean(merged.sourceSavedFilterSetId)
        );
        return hasLaunchSource ? merged : null;
    }, [refinementQueryEnabled, routeRefinementState, storedRefinementState]);
    const isRefinementMode = !!refinementState?.refinementMode;
    const refinementParentJobId = refinementState?.sourceJobId;
    const refinementDesignIds = refinementState?.selectedDesignIds;
    const refinementSavedFilterSetId = refinementState?.reviewFilterSetId || refinementState?.sourceSavedFilterSetId || undefined;
    const refinementSavedFilterSetName = refinementState?.reviewFilterSetName || refinementState?.sourceSavedFilterSetName || undefined;
    const refinementSavedFilterSetCreatedAt = refinementState?.reviewFilterSetCreatedAt || refinementState?.sourceSavedFilterSetCreatedAt || undefined;
    const refinementSavedFilterSetDesignCount = refinementState?.reviewFilterSetDesignCount ?? refinementState?.sourceSavedFilterSetDesignCount ?? undefined;
    const refinementSourceArtifactGroup = refinementState?.sourceArtifactGroup;
    const refinementSourceOutputSourceFilter = refinementState?.sourceOutputSourceFilter;
    const refinementSourceSortField = refinementState?.sourceSortField;
    const refinementSourceSortDir = refinementState?.sourceSortDir;
    const refinementSourceVisibleCount = refinementState?.sourceVisibleCount;
    const refinementSourceTotalCount = refinementState?.sourceTotalCount;
    const refinementInputCount = refinementDesignIds?.length ?? refinementSavedFilterSetDesignCount ?? 0;
    const refinementReviewFilterSetId = refinementDesignIds?.length ? undefined : refinementSavedFilterSetId;
    const refinementHasLaunchSource = refinementInputCount > 0 && (!!refinementDesignIds?.length || !!refinementReviewFilterSetId);
    const refinementSourceLabel = refinementSourceArtifactGroup === 'raw'
        ? 'Raw RF review set'
        : refinementSourceArtifactGroup === 'filtered'
            ? 'Screened RF review set'
            : refinementSavedFilterSetName
                ? `saved dataset '${refinementSavedFilterSetName}'`
            : refinementSourceOutputSourceFilter && refinementSourceOutputSourceFilter !== 'all'
                ? `${refinementSourceOutputSourceFilter} output set`
                : 'selected output set';
    const refinementSavedFilterSetCreatedLabel = useMemo(() => {
        if (!refinementSavedFilterSetCreatedAt) return null;
        const parsed = new Date(refinementSavedFilterSetCreatedAt);
        if (Number.isNaN(parsed.getTime())) return refinementSavedFilterSetCreatedAt;
        return parsed.toLocaleString();
    }, [refinementSavedFilterSetCreatedAt]);
    const [deNovoGenerator, setDeNovoGenerator] = useState<DeNovoGenerator>(() => {
        const explicit = String(initialValues?.denovo_generator || initialValues?.generator || '').trim().toLowerCase();
        if (explicit === 'ppiflow') return 'ppiflow';
        if (explicit === 'boltzgen') return 'boltzgen';
        const ppiflowMode = String(initialValues?.stage_family === 'ppiflow' ? initialValues?.stage_mode : initialValues?.mode || '').trim().toLowerCase();
        if (ppiflowMode === 'generator_backbone_refine') return 'ppiflow';
        const boltzMode = String(initialValues?.boltzgen_mode || initialValues?.mode || '').trim().toLowerCase();
        if (boltzMode === 'nanobody_binder') return 'boltzgen';
        return 'rfantibody';
    });
    const [deNovoStageSelection, setDeNovoStageSelection] = useState<Record<DeNovoOrchestrationStage, boolean>>(() => ({
        ...buildGeneratorOnlyStageSelection(),
        sequence_design: initialValues?.initial_orchestration_sequence_design === true,
        ppiflow: initialValues?.initial_orchestration_ppiflow === true,
        validation: initialValues?.initial_orchestration_validation === true,
        qc: initialValues?.initial_orchestration_qc === true,
    }));

    useEffect(() => {
        if (refinementState?.refinementMode) {
            saveAntibodyRefinementLaunchState(refinementState);
            return;
        }
        if (refinementQueryEnabled) {
            clearAntibodyRefinementLaunchState();
        }
    }, [refinementQueryEnabled, refinementState]);

    const restoringSelectionRef = useRef<{ chain: string | null; residues: string[]; modelNumber: number | null } | null>(null);

    const normalizeProtenixModel = useCallback((_model?: string) => 'protenix-v2', []);
    const mergeQualitySettingsFromParams = useCallback((params?: Record<string, UntypedApiValue>): QualitySettings => {
        const legacyPresetKey = typeof params?.quality_preset === 'string' && params.quality_preset in PRESETS
            ? (params.quality_preset as keyof typeof PRESETS)
            : 'balanced';
        const merged = {
            ...PRESETS[legacyPresetKey],
            ...(params?.quality_settings || params?.qualitySettings || {}),
        } as QualitySettings;

        if (!params) {
            return merged;
        }

        (Object.keys(PRESETS.balanced) as Array<keyof QualitySettings>).forEach((key) => {
            if (params[key] !== undefined) {
                if (
                    (key === 'fampnn_checkpoint' || key === 'ppiflow_checkpoint') &&
                    typeof params[key] === 'string' &&
                    !params[key].trim()
                ) {
                    return;
                }
                (merged as UntypedApiValue)[key] = key === 'protenix_model_weights'
                    ? normalizeProtenixModel(params[key])
                    : params[key];
            }
        });

        merged.ppiflow_objective_mode = normalizePpiFlowObjectiveMode(merged.ppiflow_objective_mode);
        if (!Number.isFinite(Number(merged.ppiflow_objective_threshold))) {
            merged.ppiflow_objective_threshold = 0;
        } else {
            merged.ppiflow_objective_threshold = Number(merged.ppiflow_objective_threshold);
        }
        const rawPpiFlowTuningProfile =
            params?.quality_settings?.ppiflow_tuning_profile
            ?? params?.qualitySettings?.ppiflow_tuning_profile
            ?? params?.ppiflow_tuning_profile;
        const hasLegacyExplicitPpiFlowControls = LEGACY_PPIFLOW_TUNING_KEYS.some((key) =>
            params?.[key] !== undefined
            || params?.quality_settings?.[key] !== undefined
            || params?.qualitySettings?.[key] !== undefined,
        );
        merged.ppiflow_tuning_profile = normalizePpiFlowTuningProfile(rawPpiFlowTuningProfile);
        if (rawPpiFlowTuningProfile === undefined && hasLegacyExplicitPpiFlowControls) {
            merged.ppiflow_tuning_profile = 'manual';
        }
        if (merged.ppiflow_tuning_profile === 'stage_optimized' && merged.ppiflow_stage_mode === 'both') {
            merged.ppiflow_tuning_profile = 'manual';
        }

        return merged;
    }, [normalizeProtenixModel]);

    const [jobName, setJobName] = useState('antibody_design');
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(initialValues?.pinned_gpus ?? []);
    const [lockGpus, setLockGpus] = useState(false);
    const [targetPdb, setTargetPdb] = useState<File | null>(null);
    const [targetSource, setTargetSource] = useState<{ type: string; url?: string; path?: string; designId?: string; pdbId?: string; name?: string } | null>(null);
    const [numDesigns, setNumDesigns] = useState(10);
    const [seqDesigner, setSeqDesigner] = useState<SeqDesigner>('fampnn');
    const [fampnnConstraintMode, setFampnnConstraintMode] = useState<'generic' | 'antibody'>('antibody');
    const [useAntiberty, setUseAntiberty] = useState(false);  // Disabled by default, planned for removal
    const [, setUseThermoMPNN] = useState(true);  // Legacy setter retained for template/state hydration
    const [runFrustrampnn, setRunFrustrampnn] = useState(false);
    const [runStructureValidation, setRunStructureValidation] = useState(initialValues?.run_structure_validation !== false);
    const [runAnarciiPost, setRunAnarciiPost] = useState(false);
    const [anarciiIncludeChildren, setAnarciiIncludeChildren] = useState(true);
    const [interactiveWorkflow, setInteractiveWorkflow] = useState(
        initialValues?.interactive_swa ?? initialValues?.interactive_gating ?? true
    );
    const [interactiveGateStage, setInteractiveGateStage] = useState<InteractiveGateStage>(
        initialValues?.interactive_gate_stage === 'post_structure_validation'
            ? 'post_structure_validation'
            : initialValues?.interactive_gate_stage === 'post_boltzgen'
                ? 'post_boltzgen'
            : initialValues?.interactive_gate_stage === 'post_ppiflow_generator'
                ? 'post_ppiflow_generator'
            : initialValues?.interactive_gate_stage === 'post_caliby'
                ? 'post_caliby'
            : initialValues?.interactive_gate_stage === 'post_fampnn'
                ? 'post_fampnn'
                : 'post_rfantibody'
    );
    const [structureValidator, setStructureValidator] = useState<'boltz2' | 'protenix' | 'esmfold2'>(
        initialValues?.structure_validator === 'protenix'
            ? 'protenix'
            : initialValues?.structure_validator === 'esmfold2'
                ? 'esmfold2'
                : 'boltz2'
    );
    // explorationMode is now always true - parallelism controlled via parallelMode
    const [seqsPerDesign, setSeqsPerDesign] = useState(8); // Number of sequence variants per backbone

    // Orchestrator parallelism settings
    const [parallelMode, setParallelMode] = useState<'standard' | 'full_orchestrator'>('standard');
    const [designsPerJob, setDesignsPerJob] = useState(5); // Backbones per child job
    const [pdBsPerJob, setPdBsPerJob] = useState(5); // FAMPNN PDBs per child job
    const [seqsPerBoltzJob, setSeqsPerBoltzJob] = useState(10); // Sequences per Boltz validation job

    // Template manager
    const [showTemplateManager, setShowTemplateManager] = useState(false);
    const interactiveWorkflowTouchedRef = useRef(false);
    const interactiveGateStageTouchedRef = useRef(false);

    // Design mode settings
    const [designMode, setDesignMode] = useState<DesignMode>('cdr_only');
    const [selectedCDRLoops, setSelectedCDRLoops] = useState<Set<string>>(new Set(['H1', 'H2', 'H3', 'L1', 'L2', 'L3']));
    const [protectTetrad, setProtectTetrad] = useState(true);
    const [rfantibodyLoopLengthMode, setRfantibodyLoopLengthMode] = useState<LoopLengthMode>(
        initialValues?.rfantibody_loop_length_mode === 'custom_ranges' ? 'custom_ranges' : 'defaults'
    );
    const [rfantibodyLoopLengthRanges, setRfantibodyLoopLengthRanges] = useState<Record<string, LoopLengthRange>>(
        () => parseLoopLengthRanges(initialValues?.rfantibody_loop_length_ranges_config || initialValues?.rfantibody_loop_length_ranges)
    );
    const [enableRfantibodyFilter, setEnableRfantibodyFilter] = useState<boolean>(
        isRefinementMode ? false : initialValues?.enable_rfantibody_filter === true
    );
    const [rfantibodyScreenReferenceScope, setRfantibodyScreenReferenceScope] = useState<RfScreeningScope>(
        normalizeRfScreeningScope(initialValues?.rfantibody_screen_reference_scope)
    );
    const [rfantibodyMinEpitopeContacts, setRfantibodyMinEpitopeContacts] = useState<number>(
        Number.isFinite(Number(initialValues?.rfantibody_min_epitope_contacts))
            ? Math.max(0, Number(initialValues?.rfantibody_min_epitope_contacts))
            : 1
    );
    const [rfantibodyMaxEpitopeDistance, setRfantibodyMaxEpitopeDistance] = useState<number>(
        Number.isFinite(Number(initialValues?.rfantibody_max_epitope_distance))
            ? Math.max(0, Number(initialValues?.rfantibody_max_epitope_distance))
            : 20
    );
    const [rfantibodyMinTargetContacts, setRfantibodyMinTargetContacts] = useState<number>(
        Number.isFinite(Number(initialValues?.rfantibody_min_target_contacts))
            ? Math.max(0, Number(initialValues?.rfantibody_min_target_contacts))
            : 3
    );
    const [rfantibodyMaxTargetDistance, setRfantibodyMaxTargetDistance] = useState<number>(
        Number.isFinite(Number((initialValues as UntypedApiValue)?.rfantibody_max_target_distance))
            ? Math.max(0, Number((initialValues as UntypedApiValue)?.rfantibody_max_target_distance))
            : 0
    );
    const [rfantibodyMaxEpitopeCentroidDistance, setRfantibodyMaxEpitopeCentroidDistance] = useState<number>(
        Number.isFinite(Number(initialValues?.rfantibody_max_epitope_centroid_distance))
            ? Math.max(0, Number(initialValues?.rfantibody_max_epitope_centroid_distance))
            : 40
    );
    const [rfantibodyContactDistanceThreshold, setRfantibodyContactDistanceThreshold] = useState<number>(
        Number.isFinite(Number(initialValues?.rfantibody_contact_distance_threshold))
            ? Math.max(0, Number(initialValues?.rfantibody_contact_distance_threshold))
            : 8
    );
    const [rfantibodyTargetContactDistanceThreshold, setRfantibodyTargetContactDistanceThreshold] = useState<number>(
        Number.isFinite(Number(initialValues?.rfantibody_target_contact_distance_threshold))
            ? Math.max(0, Number(initialValues?.rfantibody_target_contact_distance_threshold))
            : 12
    );
    const rfantibodyScopeLabel = rfantibodyScreenReferenceScope === 'whole_antibody' ? 'whole-antibody' : 'CDR-loop';
    // Manual CDR definitions (for custom loop positions)
    const [manualCDRDefinitions, setManualCDRDefinitions] = useState<CDRDefinition[]>([]);
    const [showCDREditor, setShowCDREditor] = useState(false);

    // Quality settings
    const [qualitySettings, setQualitySettings] = useState<QualitySettings>(() => mergeQualitySettingsFromParams(initialValues));
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);
    const resolvedFampnnCheckpoint = qualitySettings.fampnn_checkpoint.trim() || PRESETS.balanced.fampnn_checkpoint;
    const resolvedPpiFlowCheckpoint = qualitySettings.ppiflow_checkpoint.trim() || PRESETS.balanced.ppiflow_checkpoint;
    const selectedLoopList = Array.from(selectedCDRLoops).sort();
    const ppiflowStageMode = (qualitySettings.ppiflow_stage_mode || (qualitySettings.run_maturation ? 'post_fampnn' : 'off')) as PPIFlowStageMode;
    const hasEnabledDeNovoDownstreamStages = Object.values(deNovoStageSelection).some(Boolean);
    const showOnlyCoreGeneratorStep = !isRefinementMode && !hasEnabledDeNovoDownstreamStages;
    const boltzgenGeneratorSelected = !isRefinementMode && deNovoGenerator === 'boltzgen';
    const ppiflowGeneratorSelected = !isRefinementMode && deNovoGenerator === 'ppiflow';
    const deNovoDownstreamLocked = boltzgenGeneratorSelected || ppiflowGeneratorSelected;
    const effectiveSeqDesigner = !isRefinementMode && !deNovoStageSelection.sequence_design ? 'none' : seqDesigner;
    const effectivePpiFlowStageMode = (!isRefinementMode && (!deNovoStageSelection.ppiflow || deNovoDownstreamLocked))
        ? 'off'
        : ppiflowStageMode;
    const runPpiFlowBackboneRefine = effectivePpiFlowStageMode === 'post_rfantibody' || effectivePpiFlowStageMode === 'post_ppiflow' || effectivePpiFlowStageMode === 'both';
    const runPpiFlowMaturation = effectivePpiFlowStageMode === 'post_fampnn' || effectivePpiFlowStageMode === 'both';
    const anyPpiFlowStageEnabled = runPpiFlowBackboneRefine || runPpiFlowMaturation;
    const effectiveRunStructureValidation = (!isRefinementMode && (!deNovoStageSelection.validation || deNovoDownstreamLocked))
        ? false
        : runStructureValidation;
    const effectiveUseAntiberty = (!isRefinementMode && (!deNovoStageSelection.qc || deNovoDownstreamLocked))
        ? false
        : useAntiberty;
    const effectiveUseThermoMPNN = (!isRefinementMode && (!deNovoStageSelection.qc || deNovoDownstreamLocked))
        ? false
        : Boolean(qualitySettings.run_thermompnn);
    const effectiveRunFrustrampnn = (!isRefinementMode && (!deNovoStageSelection.qc || deNovoDownstreamLocked))
        ? false
        : runFrustrampnn;
    const effectiveRunAnarciiPost = (!isRefinementMode && (!deNovoStageSelection.qc || deNovoDownstreamLocked))
        ? false
        : runAnarciiPost;
    const effectivePhysicsEnabled = (!isRefinementMode && (!deNovoStageSelection.qc || deNovoDownstreamLocked))
        ? false
        : physicsSettings.enabled;
    const showExecutionModePanel = true;
    const showStructureValidatorPanel = isRefinementMode || (!deNovoDownstreamLocked && deNovoStageSelection.validation);
    const showQualitySettingsPanel = isRefinementMode || (!deNovoDownstreamLocked && hasEnabledDeNovoDownstreamStages);
    const showSequenceDesignerPanel = isRefinementMode || (!deNovoDownstreamLocked && deNovoStageSelection.sequence_design);
    const showQcPanels = isRefinementMode || (!deNovoDownstreamLocked && deNovoStageSelection.qc);
    const showRfQualitySettings = !isRefinementMode && deNovoGenerator === 'rfantibody';
    const showStructureValidationQualitySettings = effectiveRunStructureValidation && structureValidator !== 'esmfold2';
    const showFampnnQualitySettings = effectiveSeqDesigner === 'fampnn' || (anyPpiFlowStageEnabled && qualitySettings.maturation_redesign_enabled !== false);
    const showCalibyQualitySettings = effectiveSeqDesigner === 'caliby';
    const showOrchestratorPanel = true;
    const showDebugPanel = true;
    const refinementSourceIsPpiFlow = isRefinementMode && refinementSourceOutputSourceFilter === 'ppiflow';
    const refinementBlocksImmediatePpiFlowBackbone = isRefinementMode && (
        refinementSourceOutputSourceFilter === 'boltzgen'
        || refinementSourceOutputSourceFilter === 'caliby'
        || refinementSourceOutputSourceFilter === 'fampnn'
        || refinementSourceOutputSourceFilter === 'validation'
    );
    const ppiflowBackboneRegionMode = normalizePpiFlowRegionMode(qualitySettings.ppiflow_backbone_region_mode);
    const ppiflowMaturationRegionMode = normalizePpiFlowRegionMode(qualitySettings.ppiflow_maturation_region_mode);
    const effectivePpiFlowBackboneLoopScope = ppiflowBackboneRegionMode === 'selected_cdrs'
        ? (normalizeLoopScopeInput(qualitySettings.ppiflow_backbone_loop_scope) || selectedLoopList.join(','))
        : undefined;
    const effectivePpiFlowMaturationLoopScope = ppiflowMaturationRegionMode === 'selected_cdrs'
        ? (normalizeLoopScopeInput(qualitySettings.ppiflow_maturation_loop_scope) || selectedLoopList.join(','))
        : undefined;

    useEffect(() => {
        if (refinementSourceIsPpiFlow) {
            if (ppiflowStageMode !== 'post_rfantibody' && ppiflowStageMode !== 'both') return;

            setQualitySettings((current) => {
                const currentMode = (current.ppiflow_stage_mode || (current.run_maturation ? 'post_fampnn' : 'off')) as PPIFlowStageMode;
                if (currentMode !== 'post_rfantibody' && currentMode !== 'both') {
                    return current;
                }
                return applyPpiFlowStageMode(current, 'post_ppiflow');
            });
            return;
        }

        if (!refinementBlocksImmediatePpiFlowBackbone) return;
        if (ppiflowStageMode !== 'post_rfantibody' && ppiflowStageMode !== 'both') return;

        setQualitySettings((current) => {
            const currentMode = (current.ppiflow_stage_mode || (current.run_maturation ? 'post_fampnn' : 'off')) as PPIFlowStageMode;
            if (currentMode !== 'post_rfantibody' && currentMode !== 'both') {
                return current;
            }
            const nextMode: PPIFlowStageMode = currentMode === 'both' ? 'post_fampnn' : 'off';
            return applyPpiFlowStageMode(current, nextMode);
        });
    }, [ppiflowStageMode, refinementBlocksImmediatePpiFlowBackbone, refinementSourceIsPpiFlow]);

    // Framework selection - preset, custom, or SAbDab
    type FrameworkType = 'standard-fv' | 'nanobody' | 'custom' | 'sabdab';
    const [frameworkType, setFrameworkType] = useState<FrameworkType>('standard-fv');
    const [customFrameworkFile, setCustomFrameworkFile] = useState<File | null>(null);
    const [customFrameworkPath, setCustomFrameworkPath] = useState<string | null>(null);
    const [sabdabFramework, setSabdabFramework] = useState<SelectedFramework | null>(null);

    // ANARCII CDR detection state
    const [detectedCDRs, setDetectedCDRs] = useState<CDRAnnotationResponse | null>(null);
    const [isDetectingCDRs, setIsDetectingCDRs] = useState(false);

    // Framework protection settings
    const [frameworkProtection, setFrameworkProtection] = useState<FrameworkEditorState>({
        protectedPositions: [],
        protectTetrad: true,
        protectDisulfides: true,
        protectFrContacts: false
    });

    const [isUploading, setIsUploading] = useState(false);
    const [uploadedPath, setUploadedPath] = useState<string | null>(null);

    const [parsedTargetStructure, setParsedTargetStructure] = useState<ParsedPDB | null>(null);
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [parsedFrameworkChains, setParsedFrameworkChains] = useState<Chain[]>([]);
    const [selectedTargetModel, setSelectedTargetModel] = useState<number | null>(
        Number.isFinite(Number(initialValues?.target_model_number))
            ? Number(initialValues?.target_model_number)
            : 1
    );
    const [selectedChain, setSelectedChain] = useState<string | null>(null);
    const [selectedResidues, setSelectedResidues] = useState<Set<string>>(new Set());
    const [isParsing, setIsParsing] = useState(false);
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);
    // Blob URLs are renderer resources. These refs make replacement and unmount
    // ownership explicit rather than relying on asynchronous state snapshots.
    const pdbBlobUrlRef = useRef<string | null>(null);
    const frameworkPdbObjectUrlRef = useRef<string | null>(null);
    const frameworkLoadControllerRef = useRef(createLatestAsyncResourceController());
    const targetLoadControllerRef = useRef(createLatestAsyncResourceController());
    const replacePdbBlobUrl = useCallback((nextUrl: string | null) => {
        if (pdbBlobUrlRef.current) URL.revokeObjectURL(pdbBlobUrlRef.current);
        pdbBlobUrlRef.current = nextUrl;
        setPdbBlobUrl(nextUrl);
    }, []);
    const replaceFrameworkPdbUrl = useCallback((nextUrl: string | null) => {
        if (frameworkPdbObjectUrlRef.current) URL.revokeObjectURL(frameworkPdbObjectUrlRef.current);
        frameworkPdbObjectUrlRef.current = nextUrl?.startsWith('blob:') ? nextUrl : null;
        setFrameworkPdbUrl(nextUrl);
    }, []);
    useEffect(() => () => {
        if (pdbBlobUrlRef.current) URL.revokeObjectURL(pdbBlobUrlRef.current);
        if (frameworkPdbObjectUrlRef.current) URL.revokeObjectURL(frameworkPdbObjectUrlRef.current);
        pdbBlobUrlRef.current = null;
        frameworkPdbObjectUrlRef.current = null;
        frameworkLoadControllerRef.current.dispose();
        targetLoadControllerRef.current.dispose();
    }, []);
    const [show3DViewer, setShow3DViewer] = useState(false);  // 3D viewer toggle, off by default
    const [boltzgenUseFrameworkTemplate, setBoltzgenUseFrameworkTemplate] = useState(
        initialValues?.boltzgen_use_framework_template !== false
    );
    const [boltzgenScaffoldSource, setBoltzgenScaffoldSource] = useState<BoltzgenScaffoldSource>(
        (initialValues?.boltzgen_scaffold_source as BoltzgenScaffoldSource) || 'default_ensemble'
    );
    const [boltzgenNanobodyFramework, setBoltzgenNanobodyFramework] = useState(
        initialValues?.boltzgen_nanobody_framework || DEFAULT_BOLTZGEN_VHH_FRAMEWORK
    );
    const [boltzgenScaffoldLength, setBoltzgenScaffoldLength] = useState(
        initialValues?.boltzgen_scaffold_length || '100-135'
    );
    const [boltzgenBatchSize, setBoltzgenBatchSize] = useState(initialValues?.batch_size || initialValues?.boltzgen_batch_size || 1);
    const [boltzgenParallelMode, setBoltzgenParallelMode] = useState(Boolean(initialValues?.boltzgen_parallel_mode));
    const [boltzgenDesignsPerJob, setBoltzgenDesignsPerJob] = useState(initialValues?.boltzgen_designs_per_job || 100);
    const [boltzgenReuseExisting, setBoltzgenReuseExisting] = useState(Boolean(initialValues?.boltzgen_reuse));
    const [boltzgenCdrH1Length, setBoltzgenCdrH1Length] = useState(initialValues?.boltzgen_cdr_h1_length || '5-8');
    const [boltzgenCdrH2Length, setBoltzgenCdrH2Length] = useState(initialValues?.boltzgen_cdr_h2_length || '6-10');
    const [boltzgenCdrH3Length, setBoltzgenCdrH3Length] = useState(initialValues?.boltzgen_cdr_h3_length || '12-18');
    const [showBoltzgenFrameworkBrowser, setShowBoltzgenFrameworkBrowser] = useState(false);
    const [boltzgenViewReferenceStructure, setBoltzgenViewReferenceStructure] = useState(
        () => resolveBoltzgenReferencePreviewEnabled(initialValues)
    );
    const [boltzgenCheckpointMode, setBoltzgenCheckpointMode] = useState<BoltzgenCheckpointMode>(
        (initialValues?.boltzgen_checkpoint_mode as BoltzgenCheckpointMode) || 'both'
    );
    const [boltzgenSkipInverseFolding, setBoltzgenSkipInverseFolding] = useState(Boolean(initialValues?.boltzgen_skip_inverse_folding));
    const [boltzgenInverseFoldAvoid, setBoltzgenInverseFoldAvoid] = useState<string>(initialValues?.boltzgen_inverse_fold_avoid || '');
    const [boltzgenInverseFoldNumSequences, setBoltzgenInverseFoldNumSequences] = useState(initialValues?.boltzgen_inverse_fold_num_sequences || 1);
    const [boltzgenAvoidCysteine, setBoltzgenAvoidCysteine] = useState(
        initialValues?.boltzgen_avoid_cysteine ?? true
    );
    const [boltzgenStepScale, setBoltzgenStepScale] = useState<number | ''>(initialValues?.boltzgen_step_scale || 1.8);
    const [boltzgenNoiseScale, setBoltzgenNoiseScale] = useState<number | ''>(initialValues?.boltzgen_noise_scale || 0.98);
    const [boltzgenBudget, setBoltzgenBudget] = useState<number | ''>(initialValues?.boltzgen_budget || 50);
    const [boltzgenAlpha, setBoltzgenAlpha] = useState(initialValues?.boltzgen_alpha || 0.01);
    const [boltzgenMaxRmsd, setBoltzgenMaxRmsd] = useState<number | ''>(initialValues?.boltzgen_max_rmsd || 2.0);
    const [boltzgenMinPlddt, setBoltzgenMinPlddt] = useState<number | ''>(initialValues?.boltzgen_min_plddt || 70);
    const [boltzgenMinConfScore, setBoltzgenMinConfScore] = useState<number | ''>(initialValues?.boltzgen_min_conf_score || '');
    const [boltzgenFilterBiased, setBoltzgenFilterBiased] = useState(initialValues?.boltzgen_filter_biased !== false);
    const [boltzgenMetricsOverride, setBoltzgenMetricsOverride] = useState(initialValues?.boltzgen_metrics_override || '');
    const [boltzgenAdditionalFilters, setBoltzgenAdditionalFilters] = useState(initialValues?.boltzgen_additional_filters || '');
    const [boltzgenSizeBuckets, setBoltzgenSizeBuckets] = useState(initialValues?.boltzgen_size_buckets || '');
    const [showBoltzgenPreview, setShowBoltzgenPreview] = useState(false);
    const [boltzgenPreview, setBoltzgenPreview] = useState<BoltzGenPreviewResponse | null>(null);
    const [ppiflowSeedComplexFile, setPpiflowSeedComplexFile] = useState<File | null>(null);
    const [ppiflowSeedComplexPath, setPpiflowSeedComplexPath] = useState<string | null>(
        typeof initialValues?.ppiflow_seed_complex_path === 'string' ? initialValues.ppiflow_seed_complex_path : null
    );
    const [ppiflowSeedInputDir, setPpiflowSeedInputDir] = useState<string>(
        typeof initialValues?.ppiflow_seed_input_dir === 'string'
            ? initialValues.ppiflow_seed_input_dir
            : (typeof initialValues?.selected_input_dir === 'string' && String(initialValues?.stage_family || '').trim().toLowerCase() === 'ppiflow'
                ? initialValues.selected_input_dir
                : '')
    );
    const [ppiflowSeedAntibodyChains, setPpiflowSeedAntibodyChains] = useState<string>(
        typeof initialValues?.antibody_chains === 'string' && initialValues.antibody_chains.trim()
            ? initialValues.antibody_chains
            : 'H'
    );
    const [ppiflowSeedAntigenChains, setPpiflowSeedAntigenChains] = useState<string>(
        typeof initialValues?.antigen_chains === 'string' && initialValues.antigen_chains.trim()
            ? initialValues.antigen_chains
            : (typeof initialValues?.selected_chain === 'string' ? initialValues.selected_chain : '')
    );
    const hasPpiFlowSeedLaunchInput = Boolean(ppiflowSeedInputDir.trim() || ppiflowSeedComplexFile || ppiflowSeedComplexPath?.trim());

    // Viewer mode - toggle between target and framework preview
    type ViewerMode = 'target' | 'framework';
    const [viewerMode, setViewerMode] = useState<ViewerMode>('target');
    const [frameworkPdbUrl, setFrameworkPdbUrl] = useState<string | null>(null);

    // Optional DNA/RNA sequence for complex prediction (when protein binds nucleic acid)
    const [targetDnaSeq, setTargetDnaSeq] = useState<string>('');
    const [showDnaInput, setShowDnaInput] = useState(false);

    // Debug mode settings - hidden by default
    const [showDebugSettings, setShowDebugSettings] = useState(false);
    const [skipRFantibody, setSkipRFantibody] = useState(false);
    const [rfantibodyInputPdbs, setRfantibodyInputPdbs] = useState<string>('');
    const [skipFampnn, setSkipFampnn] = useState(false);
    const [fampnnCollectedPdbs, setFampnnCollectedPdbs] = useState<string>('');
    const [customOutputDir, setCustomOutputDir] = useState<string>(initialValues?.out_dir || '');
    const [refinementPreset, setRefinementPreset] = useState<RefinementPreset>(isRefinementMode ? 'full_loop' : 'custom');
    const [useManualMutagenesis, setUseManualMutagenesis] = useState(false);
    const [mutagenesisMethod, setMutagenesisMethod] = useState<MutagenesisMethod>('explicit_substitutions');
    const [mutagenesisLaunchMode, setMutagenesisLaunchMode] = useState<MutagenesisLaunchMode>('seeded_refinement');
    const [manualMutagenesisConfig, setManualMutagenesisConfig] = useState({
        chain_id: '',
        mutation_sets_text: '',
        predictor: 'protenix' as 'protenix' | 'boltz2',
        msa_provider: 'local' as 'local' | 'colabfold_api',
    });
    const [cdrIndelConfig, setCdrIndelConfig] = useState({
        loop_ids: ['H1', 'H2', 'H3'],
        variants_per_design: 5,
        allow_insertions: true,
        allow_deletions: true,
        indel_sizes: [1, 2],
        indel_probability: 0.1,
        allowed_aas: [] as string[],
        blocked_aas: [] as string[],
        predictor: 'protenix' as 'protenix' | 'boltz2',
        msa_provider: 'local' as 'local' | 'colabfold_api',
    });
    const detectedAntibodyType = String(detectedCDRs?.antibody_type || '').trim().toLowerCase();
    const isSingleDomainFramework = frameworkType === 'nanobody'
        || detectedAntibodyType.includes('vhh')
        || detectedAntibodyType.includes('nanobody');
    const availableDesignLoops = useMemo(() => (isSingleDomainFramework
        ? ['H1', 'H2', 'H3']
        : ['H1', 'H2', 'H3', 'L1', 'L2', 'L3']), [isSingleDomainFramework]);
    const availableDesignLoopKey = availableDesignLoops.join(',');

    // If starting in refinement mode, we are bypassing RFantibody by default
    useEffect(() => {
        if (isRefinementMode) {
            setSkipRFantibody(true);
        }
    }, [isRefinementMode]);

    useEffect(() => {
        const allowedLoops = new Set(availableDesignLoops);

        setSelectedCDRLoops((current) => {
            const filtered = Array.from(current).filter((loopId) => allowedLoops.has(loopId));
            if (filtered.length === 0) {
                return new Set(availableDesignLoops);
            }
            if (filtered.length === current.size && filtered.every((loopId) => current.has(loopId))) {
                return current;
            }
            return new Set(filtered);
        });

        setCdrIndelConfig((current) => {
            const filteredLoops = current.loop_ids.filter((loopId) => allowedLoops.has(loopId));
            const nextLoops = filteredLoops.length > 0 ? filteredLoops : availableDesignLoops;
            if (
                nextLoops.length === current.loop_ids.length
                && nextLoops.every((loopId, index) => current.loop_ids[index] === loopId)
            ) {
                return current;
            }
            return {
                ...current,
                loop_ids: nextLoops,
            };
        });
    }, [availableDesignLoopKey, availableDesignLoops]);

    const buildFilesApiUrl = useCallback((mode: 'download' | 'pdb', path: string) =>
        `/api/files/${mode}/${encodeURIComponent(path)}`, []);

    const loadPdbFileFromUrl = useCallback(async (url: string, fallbackName: string) => {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const fileName = fallbackName.toLowerCase().endsWith('.pdb') ? fallbackName : `${fallbackName}.pdb`;
        return new File([blob], fileName, { type: 'chemical/x-pdb' });
    }, []);

    const loadSabdabFrameworkFile = useCallback(async (pdbCode: string, fallbackName: string) => {
        const response = await downloadSabdabFramework(pdbCode, {
            scheme: 'imgt',
            convert_hlt: true,
            include_content: true,
        });
        const data = response.data as UntypedApiValue;
        if (!data?.pdb_content) {
            throw new Error(`No PDB content returned for SAbDab framework ${pdbCode}`);
        }
        const blob = new Blob([data.pdb_content], { type: 'text/plain' });
        const fileName = fallbackName.toLowerCase().endsWith('.pdb') ? fallbackName : `${fallbackName}.pdb`;
        return {
            file: new File([blob], fileName, { type: 'chemical/x-pdb' }),
            url: URL.createObjectURL(blob),
            filePath: data.file_path as string | undefined,
        };
    }, []);

    const restoreFrameworkPreview = useCallback(async (saved: Record<string, UntypedApiValue>) => {
        const loadToken = frameworkLoadControllerRef.current.begin();
        const savedFramework = saved.sabdab_framework as SelectedFramework | undefined;
        const savedFrameworkPath = (saved.custom_framework_path || saved.framework_pdb || savedFramework?.filePath || '').trim();

        if (saved.framework_type === 'sabdab' && savedFramework) {
            const preferredSabdabPath = savedFramework.filePath || savedFrameworkPath || null;
            setSabdabFramework({ ...savedFramework, filePath: preferredSabdabPath || savedFramework.filePath });
            setCustomFrameworkPath(preferredSabdabPath);
            setViewerMode('framework');
            setShow3DViewer(true);

            if (savedFramework.pdbContent) {
                const blob = new Blob([savedFramework.pdbContent], { type: 'text/plain' });
                const url = URL.createObjectURL(blob);
                replaceFrameworkPdbUrl(url);
                const fwFile = new File([blob], `${savedFramework.pdbCode || savedFramework.name || 'framework'}.pdb`);
                const parsed = await parsePDBFile(fwFile);
                if (frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                    setParsedFrameworkChains(parsed.chains);
                }
                return;
            }

            if (!savedFramework.pdbCode) return;

            const hydrated = await loadSabdabFrameworkFile(
                savedFramework.pdbCode,
                `${savedFramework.pdbCode || savedFramework.name || 'framework'}.pdb`
            );
            if (!frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                URL.revokeObjectURL(hydrated.url);
                return;
            }
            setSabdabFramework((prev) => prev ? { ...prev, filePath: hydrated.filePath || prev.filePath } : prev);
            setCustomFrameworkPath(hydrated.filePath || preferredSabdabPath);
            replaceFrameworkPdbUrl(hydrated.url);
            const fwFile = hydrated.file;
            const parsed = await parsePDBFile(fwFile);
            if (frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                setParsedFrameworkChains(parsed.chains);
            }
            return;
        }

        if (saved.framework_type === 'custom' && savedFrameworkPath) {
            setSabdabFramework(null);
            setCustomFrameworkPath(savedFrameworkPath);
            setViewerMode('framework');
            setShow3DViewer(true);
            const fwUrl = buildFilesApiUrl('download', savedFrameworkPath);
            replaceFrameworkPdbUrl(fwUrl);
            const fwFile = await loadPdbFileFromUrl(fwUrl, savedFrameworkPath.split('/').pop() || 'framework.pdb');
            if (!frameworkLoadControllerRef.current.isCurrent(loadToken)) return;
            setCustomFrameworkFile(fwFile);
            const parsed = await parsePDBFile(fwFile);
            if (frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                setParsedFrameworkChains(parsed.chains);
            }
            return;
        }

        setSabdabFramework(null);
    }, [buildFilesApiUrl, loadPdbFileFromUrl, loadSabdabFrameworkFile, replaceFrameworkPdbUrl]);

    const getSavedResidueSelection = useCallback((saved: Record<string, UntypedApiValue>): string[] => {
        if (Array.isArray(saved.selected_residues)) {
            return saved.selected_residues.map((res) => String(res).trim()).filter(Boolean);
        }
        if (typeof saved.epitope_residues === 'string') {
            return saved.epitope_residues
                .split(',')
                .map((res) => res.trim())
                .filter(Boolean);
        }
        return [];
    }, []);

    const getSavedTargetModelNumber = useCallback((saved: Record<string, UntypedApiValue>): number | null => {
        const raw = saved.target_model_number ?? saved.selected_target_model ?? saved.target_model;
        const parsed = Number(raw);
        return Number.isFinite(parsed) && parsed >= 1 ? parsed : null;
    }, []);

    const queueRestoredSelection = useCallback((saved: Record<string, UntypedApiValue>) => {
        const residues = getSavedResidueSelection(saved);
        const rawChain = saved.selected_chain || saved.antigen_chains || null;
        const chain = typeof rawChain === 'string'
            ? rawChain.split(',').map((token) => token.trim()).find(Boolean) || null
            : null;
        const savedModel = getSavedTargetModelNumber(saved);
        restoringSelectionRef.current = { chain, residues, modelNumber: savedModel };
        if (savedModel != null) {
            setSelectedTargetModel(savedModel);
        }
        if (chain) {
            setSelectedChain(chain);
        }
        setSelectedResidues(new Set(residues));
    }, [getSavedResidueSelection, getSavedTargetModelNumber]);

    const restoreTargetFromSaved = useCallback(async (saved: Record<string, UntypedApiValue>) => {
        const savedSource = saved.target_source as { type?: string; url?: string; path?: string; designId?: string; pdbId?: string; name?: string } | undefined;
        const savedUploadedPath = typeof saved.uploaded_path === 'string' ? saved.uploaded_path : '';
        const rawPath = (savedSource?.path || savedUploadedPath || saved.target_pdb || '').trim();
        const rcsbMatch = rawPath.match(/(?:^|\/)([a-z0-9]{4})\.pdb$/i);

        let fetchUrl = savedSource?.url || '';
        let sourceType = savedSource?.type || '';
        if (fetchUrl.includes('/api/files/download?path=') && rawPath) {
            fetchUrl = buildFilesApiUrl('download', rawPath);
        }
        if (!fetchUrl && rawPath) {
            if (sourceType === 'rcsb' || rcsbMatch) {
                const pdbId = (savedSource?.pdbId || rcsbMatch?.[1] || '').toUpperCase();
                if (pdbId) {
                    fetchUrl = `/api/rcsb/${pdbId}/file`;
                    sourceType = 'rcsb';
                }
            } else {
                fetchUrl = buildFilesApiUrl('download', rawPath);
                sourceType = sourceType || 'preset';
            }
        }

        if (!rawPath && !fetchUrl) {
            return;
        }

        const sourceName = savedSource?.name || rawPath.split('/').pop() || 'target.pdb';
        setUploadedPath(savedUploadedPath || (sourceType === 'upload' ? rawPath : null));
        setTargetSource({
            type: sourceType || 'preset',
            url: fetchUrl || undefined,
            path: rawPath || undefined,
            designId: savedSource?.designId,
            pdbId: savedSource?.pdbId || rcsbMatch?.[1]?.toUpperCase(),
            name: sourceName,
        });

        if (!fetchUrl) return;

        const file = await loadPdbFileFromUrl(fetchUrl, sourceName);
        setTargetPdb(file);
    }, [buildFilesApiUrl, loadPdbFileFromUrl]);

    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const submitMutation = useMutation({
        mutationFn: async (data: UntypedApiValue) => submitJob(data),
        onSuccess: () => {
            clearAntibodyRefinementLaunchState();
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        }
    });

    const boltzgenPreviewMutation = useMutation({
        mutationFn: async (payload: { params: Record<string, UntypedApiValue>; validate?: boolean }) => previewBoltzGenDesignSpec(payload),
        onSuccess: (response) => {
            setBoltzgenPreview(response.data);
            setShowBoltzgenPreview(true);
        },
        onError: (error: UntypedApiValue) => {
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object' ? JSON.stringify(detail, null, 2) : (detail || error.message);
            window.alert('BoltzGen preview failed:\n' + message);
        },
    });

    useEffect(() => {
        if (!selectedChain || ppiflowSeedAntigenChains.trim()) return;
        setPpiflowSeedAntigenChains(selectedChain);
    }, [selectedChain, ppiflowSeedAntigenChains]);

    const launchMutagenesisMutation = useMutation({
        mutationFn: async (data: UntypedApiValue) => launchManualMutagenesis(data),
        onSuccess: () => {
            clearAntibodyRefinementLaunchState();
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
    });

    const serializeSabdabFramework = () => (
        sabdabFramework ? {
            type: sabdabFramework.type,
            id: sabdabFramework.id,
            name: sabdabFramework.name,
            pdbCode: sabdabFramework.pdbCode,
            sequence: sabdabFramework.sequence,
            filePath: sabdabFramework.filePath,
            cdrH3Length: sabdabFramework.cdrH3Length,
            hChain: sabdabFramework.hChain,
            lChain: sabdabFramework.lChain,
            antigenChain: sabdabFramework.antigenChain,
        } : null
    );

    const resolveTargetPdbPathForLaunch = async (allowSkipFallback: boolean = false) => {
        let pdbPath = targetSource?.path || uploadedPath;
        if (!pdbPath && targetPdb) {
            pdbPath = await handleFileUpload(targetPdb);
        }
        if (!pdbPath && allowSkipFallback) {
            pdbPath = isRefinementMode ? 'refinement_mode' : skipRFantibody ? rfantibodyInputPdbs : fampnnCollectedPdbs;
        }
        if (!pdbPath) {
            throw new Error('Failed to determine PDB file path');
        }

        if (selectedChain && parsedChains.length > 1) {
            const extractResult = await extractChain(
                pdbPath,
                selectedChain,
                undefined,
                selectedTargetModel ?? undefined
            );
            pdbPath = extractResult.data.output_path;
        }

        return pdbPath;
    };

    const buildBoltzgenInverseFoldAvoid = () => {
        if (boltzgenAvoidCysteine) {
            const avoidSet = new Set(
                boltzgenInverseFoldAvoid
                    .split('')
                    .map((value) => value.trim().toUpperCase())
                    .filter((value) => value.match(/[A-Z]/))
            );
            avoidSet.add('C');
            return Array.from(avoidSet).join('');
        }
        return boltzgenInverseFoldAvoid.trim() || undefined;
    };

    const buildStandaloneBoltzgenParams = (pdbPath: string, epitopeString: string) => {
        const boltzgenParams: Record<string, UntypedApiValue> = {
            diffusion_method: 'boltzgen',
            run_boltzgen_only: true,
            run_docking: false,
            run_diffdock: false,
            run_unidock: false,
            boltzgen_mode: 'nanobody_binder',
            boltzgen_protocol: 'nanobody-anything',
            boltzgen_num_designs: numDesigns,
            boltzgen_batch_size: Math.max(1, Number(boltzgenBatchSize) || 1),
            boltzgen_scaffold_length: boltzgenScaffoldLength,
            boltzgen_target_pdb_path: pdbPath,
            boltzgen_cdr_h1_length: boltzgenCdrH1Length,
            boltzgen_cdr_h2_length: boltzgenCdrH2Length,
            boltzgen_cdr_h3_length: boltzgenCdrH3Length,
            boltzgen_use_framework_template: boltzgenUseFrameworkTemplate,
            boltzgen_scaffold_source: boltzgenUseFrameworkTemplate ? boltzgenScaffoldSource : undefined,
            framework_type: 'nanobody',
            antibody_format: 'vhh',
            antibody_chains: 'H',
            binder_chains: 'H',
            antigen_chains: selectedChain || undefined,
            target_chains: selectedChain || undefined,
            boltzgen_binding_site_residues: epitopeString || undefined,
            epitope_residues: epitopeString || undefined,
            selected_residues: epitopeString || undefined,
            pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
            lock_gpus: lockGpus && pinnedGpus.length > 0,
            interactive_swa: interactiveWorkflow,
            interactive_gating: interactiveWorkflow,
            interactive_gate_stage: interactiveWorkflow ? 'post_boltzgen' : undefined,
            stage_family: 'boltzgen',
            stage_mode: 'nanobody_binder',
            out_dir: customOutputDir.trim() || undefined,
            sabdab_framework: serializeSabdabFramework(),
            custom_framework_path: customFrameworkPath || undefined,
            boltzgen_checkpoint_mode: boltzgenCheckpointMode !== 'both' ? boltzgenCheckpointMode : undefined,
            boltzgen_skip_inverse_folding: boltzgenSkipInverseFolding || undefined,
            boltzgen_inverse_fold_num_sequences: boltzgenInverseFoldNumSequences > 1 ? boltzgenInverseFoldNumSequences : undefined,
            boltzgen_inverse_fold_avoid: buildBoltzgenInverseFoldAvoid(),
            boltzgen_avoid_cysteine: boltzgenAvoidCysteine,
            boltzgen_step_scale: boltzgenStepScale || undefined,
            boltzgen_noise_scale: boltzgenNoiseScale || undefined,
            boltzgen_budget: boltzgenBudget || undefined,
            boltzgen_alpha: boltzgenAlpha,
            boltzgen_max_rmsd: boltzgenMaxRmsd || undefined,
            boltzgen_min_plddt: boltzgenMinPlddt || undefined,
            boltzgen_min_conf_score: boltzgenMinConfScore || undefined,
            boltzgen_filter_biased: boltzgenFilterBiased,
            boltzgen_metrics_override: boltzgenMetricsOverride.trim() || undefined,
            boltzgen_additional_filters: boltzgenAdditionalFilters.trim() || undefined,
            boltzgen_size_buckets: boltzgenSizeBuckets.trim() || undefined,
        };

        if (boltzgenUseFrameworkTemplate && boltzgenScaffoldSource === 'sequence_template' && boltzgenNanobodyFramework.trim()) {
            boltzgenParams.boltzgen_nanobody_framework = boltzgenNanobodyFramework.trim();
        }
        if (boltzgenParallelMode) {
            boltzgenParams.boltzgen_parallel_mode = true;
            boltzgenParams.boltzgen_designs_per_job = Math.max(1, Number(boltzgenDesignsPerJob) || 1);
        }
        if (boltzgenReuseExisting) {
            boltzgenParams.boltzgen_reuse = true;
        }

        return boltzgenParams;
    };

    const resolvePpiFlowSeedPathForLaunch = async (): Promise<{ seedComplexPath?: string; seedInputDir?: string }> => {
        const seedInputDir = ppiflowSeedInputDir.trim();
        if (seedInputDir) {
            return { seedInputDir };
        }

        let seedComplexPath = ppiflowSeedComplexPath?.trim() || '';
        if (!seedComplexPath && ppiflowSeedComplexFile) {
            setIsUploading(true);
            try {
                const response = await uploadFile('inputs/antibody', ppiflowSeedComplexFile);
                seedComplexPath = response.data?.path || `inputs/antibody/${ppiflowSeedComplexFile.name}`;
            } finally {
                setIsUploading(false);
            }
            setPpiflowSeedComplexPath(seedComplexPath);
        }

        if (!seedComplexPath) {
            throw new Error('Choose a seed complex PDB or provide a seed input directory before launching PPIFlow.');
        }

        return { seedComplexPath };
    };

    const buildStandalonePpiFlowGeneratorParams = (
        pdbPath: string | undefined,
        epitopeString: string,
        seedLaunch: { seedComplexPath?: string; seedInputDir?: string },
    ) => {
        const antibodyChains = ppiflowSeedAntibodyChains.trim() || 'H';
        const antigenChains = ppiflowSeedAntigenChains.trim() || selectedChain || undefined;
        const antibodyChainList = antibodyChains
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean);
        const heavyChain = antibodyChainList[0] || 'H';
        const lightChain = antibodyChainList[1] || undefined;
        const loopScope = effectivePpiFlowBackboneLoopScope || selectedLoopList.join(',');

        return {
            framework_type: 'nanobody',
            antibody_format: 'vhh',
            antibody_chains: antibodyChains,
            binder_chains: antibodyChains,
            antigen_chains: antigenChains,
            target_chains: antigenChains,
            target_pdb: pdbPath,
            target_model_number: selectedTargetModel || undefined,
            selected_residues: epitopeString || undefined,
            epitope_residues: epitopeString || undefined,
            stage_family: 'ppiflow',
            stage_mode: 'generator_backbone_refine',
            ppiflow_stage_mode: 'generator_backbone_refine',
            ppiflow_mode: 'backbone_refine',
            ppiflow_seed_complex_path: seedLaunch.seedComplexPath,
            ppiflow_seed_input_dir: seedLaunch.seedInputDir,
            selected_input_dir: seedLaunch.seedInputDir,
            ppiflow_samples_per_target: qualitySettings.ppiflow_samples_per_target,
            ppiflow_start_t: qualitySettings.ppiflow_start_t,
            ppiflow_retry_limit: qualitySettings.ppiflow_retry_limit,
            ppiflow_config: qualitySettings.ppiflow_config.trim() || undefined,
            ppiflow_weights_dir: qualitySettings.ppiflow_weights_dir.trim() || undefined,
            ppiflow_checkpoint_path: qualitySettings.ppiflow_checkpoint_path.trim() || undefined,
            ppiflow_checkpoint: resolvedPpiFlowCheckpoint || undefined,
            ppiflow_require_anchors: qualitySettings.ppiflow_require_anchors,
            ppiflow_rotamer_enrichment_enabled: qualitySettings.ppiflow_rotamer_enrichment_enabled,
            ppiflow_rotamer_shell_distance: qualitySettings.ppiflow_rotamer_shell_cutoff,
            ppiflow_rotamer_shell_cutoff: qualitySettings.ppiflow_rotamer_shell_cutoff,
            ppiflow_region_mode: ppiflowBackboneRegionMode,
            ppiflow_selected_loops: loopScope || undefined,
            ppiflow_objective_mode: qualitySettings.ppiflow_objective_mode,
            ppiflow_objective_threshold: qualitySettings.ppiflow_objective_threshold,
            ppiflow_backbone_region_mode: ppiflowBackboneRegionMode,
            ppiflow_backbone_loop_scope: loopScope || undefined,
            ppiflow_antigen_chain: antigenChains,
            ppiflow_heavy_chain: heavyChain,
            ppiflow_light_chain: lightChain,
            cdr_positions_by_loop: {},
            manual_cdr_definitions: [],
            maturation_redesign_enabled: false,
            pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
            lock_gpus: lockGpus && pinnedGpus.length > 0,
            interactive_swa: interactiveWorkflow,
            interactive_gating: interactiveWorkflow,
            interactive_gate_stage: interactiveWorkflow ? 'post_ppiflow_generator' : undefined,
            structure_validator: structureValidator,
            sabdab_framework: serializeSabdabFramework(),
            custom_framework_path: customFrameworkPath || undefined,
            out_dir: customOutputDir.trim() || undefined,
        };
    };

    const handleBoltzgenPreview = async () => {
        if (!targetPdb && !targetSource?.path && !uploadedPath) {
            window.alert('Choose a target structure before previewing the BoltzGen design spec.');
            return;
        }
        if (boltzgenUseFrameworkTemplate && boltzgenScaffoldSource === 'selected_scaffold' && !sabdabFramework?.pdbCode && !customFrameworkPath) {
            window.alert('Select a SAbDab framework or switch the scaffold source before previewing the BoltzGen design spec.');
            return;
        }

        try {
            const pdbPath = await resolveTargetPdbPathForLaunch(false);
            const epitopeString = Array.from(selectedResidues).sort().join(',');
            const params = buildStandaloneBoltzgenParams(pdbPath, epitopeString);
            await boltzgenPreviewMutation.mutateAsync({ params, validate: true });
        } catch (error: UntypedApiValue) {
            window.alert(`BoltzGen preview failed:\n${error?.message || error}`);
        }
    };

    const applyRefinementPreset = (preset: RefinementPreset) => {
        setRefinementPreset(preset);
        setUseManualMutagenesis(preset === 'manual_mutagenesis');
        if (preset === 'manual_mutagenesis') {
            setManualMutagenesisConfig((current) => ({
                ...current,
                predictor: structureValidator === 'protenix' ? 'protenix' : 'boltz2',
            }));
            setMutagenesisLaunchMode('seeded_refinement');
            return;
        }
        if (preset === 'full_loop') {
            setSeqDesigner((current) => (current === 'none' ? 'fampnn' : current));
            setRunStructureValidation(true);
            return;
        }
        if (preset === 'fampnn_only') {
            setSeqDesigner('fampnn');
            setRunStructureValidation(false);
            setRunFrustrampnn(false);
            setQualitySettings((current) => applyPpiFlowStageMode(current, 'off'));
            setInteractiveWorkflow(true);
            setInteractiveGateStage('post_fampnn');
            return;
        }
        if (preset === 'validation_only') {
            setSeqDesigner('none');
            setRunStructureValidation(true);
            setRunFrustrampnn(false);
            setQualitySettings((current) => applyPpiFlowStageMode(current, 'off'));
            setInteractiveWorkflow(true);
            setInteractiveGateStage('post_structure_validation');
            return;
        }
        if (preset === 'ppiflow_only') {
            setSeqDesigner('none');
            setRunStructureValidation(false);
            setRunFrustrampnn(false);
            setQualitySettings((current) => {
                const nextStageMode = current.ppiflow_stage_mode !== 'off'
                    ? current.ppiflow_stage_mode
                    : (refinementSourceIsPpiFlow ? 'post_ppiflow' : 'post_rfantibody');
                return applyPpiFlowStageMode(current, nextStageMode);
            });
            setInteractiveWorkflow(false);
        }
    };

    // Initialize from initialValues (Clone Job)
    useEffect(() => {
        if (initialValues) {
            console.log('[ANTIBODY_DENOVO] Initializing from values:', initialValues);
            setQualitySettings(mergeQualitySettingsFromParams(initialValues));

            // Basic params
            if (initialValues.job_name) setJobName(initialValues.job_name);
            else if (initialValues.name) setJobName(initialValues.name); // Job name usually comes from wrapper but might be passed
            if (initialValues.rfantibody_num_designs) setNumDesigns(initialValues.rfantibody_num_designs);
            if (initialValues.seqs_per_design) setSeqsPerDesign(initialValues.seqs_per_design);
            if (initialValues.seqs_per_validation_job) setSeqsPerBoltzJob(initialValues.seqs_per_validation_job);
            else if (initialValues.seqs_per_boltz_job) setSeqsPerBoltzJob(initialValues.seqs_per_boltz_job);
            // exploration_mode is now always true - controlled via parallel_mode instead

            // Booleans
            if (initialValues.run_immunogenicity_scoring !== undefined) setUseAntiberty(initialValues.run_immunogenicity_scoring);
            if (initialValues.run_thermompnn !== undefined) setUseThermoMPNN(initialValues.run_thermompnn);
            else if (initialValues.run_stability_scoring !== undefined) setUseThermoMPNN(initialValues.run_stability_scoring);
            if (initialValues.run_frustrampnn !== undefined) setRunFrustrampnn(initialValues.run_frustrampnn);
            if (initialValues.run_structure_validation !== undefined) setRunStructureValidation(initialValues.run_structure_validation !== false);
            if (initialValues.run_anarcii_post !== undefined) setRunAnarciiPost(initialValues.run_anarcii_post);
            if (initialValues.anarcii_include_children !== undefined) setAnarciiIncludeChildren(initialValues.anarcii_include_children);
            if (!interactiveWorkflowTouchedRef.current) {
                if (initialValues.interactive_swa !== undefined) setInteractiveWorkflow(initialValues.interactive_swa);
                else if (initialValues.interactive_gating !== undefined) setInteractiveWorkflow(initialValues.interactive_gating);
            }
            if (
                !interactiveGateStageTouchedRef.current &&
                (
                    initialValues.interactive_gate_stage === 'post_rfantibody' ||
                    initialValues.interactive_gate_stage === 'post_boltzgen' ||
                    initialValues.interactive_gate_stage === 'post_ppiflow_generator' ||
                    initialValues.interactive_gate_stage === 'post_caliby' ||
                    initialValues.interactive_gate_stage === 'post_structure_validation' ||
                    initialValues.interactive_gate_stage === 'post_fampnn'
                )
            ) {
                setInteractiveGateStage(initialValues.interactive_gate_stage);
            }
            // Handling renamed/mapped boolean params if unknown
            if (initialValues.use_antiberty !== undefined) setUseAntiberty(initialValues.use_antiberty);
            if (initialValues.use_thermompnn !== undefined) setUseThermoMPNN(initialValues.use_thermompnn);
            if (Array.isArray(initialValues.pinned_gpus)) setPinnedGpus(initialValues.pinned_gpus);
            if (typeof initialValues.lock_gpus === 'boolean') setLockGpus(initialValues.lock_gpus);
            if (initialValues.parallel_mode) setParallelMode(initialValues.parallel_mode);
            if (initialValues.designs_per_job) setDesignsPerJob(initialValues.designs_per_job);
            if (initialValues.pdbs_per_job) setPdBsPerJob(initialValues.pdbs_per_job);
            else if (initialValues.seqs_per_job) setPdBsPerJob(initialValues.seqs_per_job);
            if (initialValues.boltzgen_batch_size) setBoltzgenBatchSize(initialValues.boltzgen_batch_size);
            else if (initialValues.batch_size) setBoltzgenBatchSize(initialValues.batch_size);
            if (typeof initialValues.boltzgen_parallel_mode === 'boolean') setBoltzgenParallelMode(initialValues.boltzgen_parallel_mode);
            if (initialValues.boltzgen_designs_per_job) setBoltzgenDesignsPerJob(initialValues.boltzgen_designs_per_job);
            if (typeof initialValues.boltzgen_reuse === 'boolean') setBoltzgenReuseExisting(initialValues.boltzgen_reuse);
            if (initialValues.boltzgen_scaffold_source === 'default_ensemble' || initialValues.boltzgen_scaffold_source === 'selected_scaffold' || initialValues.boltzgen_scaffold_source === 'sequence_template') {
                setBoltzgenScaffoldSource(initialValues.boltzgen_scaffold_source);
            }
            if (initialValues.boltzgen_checkpoint_mode === 'both' || initialValues.boltzgen_checkpoint_mode === 'diverse' || initialValues.boltzgen_checkpoint_mode === 'adherence') {
                setBoltzgenCheckpointMode(initialValues.boltzgen_checkpoint_mode);
            }
            if (typeof initialValues.boltzgen_skip_inverse_folding === 'boolean') setBoltzgenSkipInverseFolding(initialValues.boltzgen_skip_inverse_folding);
            if (typeof initialValues.boltzgen_inverse_fold_avoid === 'string') setBoltzgenInverseFoldAvoid(initialValues.boltzgen_inverse_fold_avoid);
            if (typeof initialValues.boltzgen_inverse_fold_num_sequences === 'number') setBoltzgenInverseFoldNumSequences(initialValues.boltzgen_inverse_fold_num_sequences);
            if (typeof initialValues.boltzgen_avoid_cysteine === 'boolean') setBoltzgenAvoidCysteine(initialValues.boltzgen_avoid_cysteine);
            if (typeof initialValues.boltzgen_step_scale === 'number') setBoltzgenStepScale(initialValues.boltzgen_step_scale);
            if (typeof initialValues.boltzgen_noise_scale === 'number') setBoltzgenNoiseScale(initialValues.boltzgen_noise_scale);
            if (typeof initialValues.boltzgen_budget === 'number') setBoltzgenBudget(initialValues.boltzgen_budget);
            if (typeof initialValues.boltzgen_view_reference_structure === 'boolean') {
                setBoltzgenViewReferenceStructure(initialValues.boltzgen_view_reference_structure);
            }
            if (typeof initialValues.boltzgen_alpha === 'number') setBoltzgenAlpha(initialValues.boltzgen_alpha);
            if (typeof initialValues.boltzgen_max_rmsd === 'number') setBoltzgenMaxRmsd(initialValues.boltzgen_max_rmsd);
            if (typeof initialValues.boltzgen_min_plddt === 'number') setBoltzgenMinPlddt(initialValues.boltzgen_min_plddt);
            if (typeof initialValues.boltzgen_min_conf_score === 'number') setBoltzgenMinConfScore(initialValues.boltzgen_min_conf_score);
            if (typeof initialValues.boltzgen_filter_biased === 'boolean') setBoltzgenFilterBiased(initialValues.boltzgen_filter_biased);
            if (typeof initialValues.boltzgen_metrics_override === 'string') setBoltzgenMetricsOverride(initialValues.boltzgen_metrics_override);
            if (typeof initialValues.boltzgen_additional_filters === 'string') setBoltzgenAdditionalFilters(initialValues.boltzgen_additional_filters);
            if (typeof initialValues.boltzgen_size_buckets === 'string') setBoltzgenSizeBuckets(initialValues.boltzgen_size_buckets);
            if (typeof initialValues.ppiflow_seed_input_dir === 'string') setPpiflowSeedInputDir(initialValues.ppiflow_seed_input_dir);
            else if (typeof initialValues.selected_input_dir === 'string' && String(initialValues.stage_family || '').trim().toLowerCase() === 'ppiflow') {
                setPpiflowSeedInputDir(initialValues.selected_input_dir);
            }
            if (typeof initialValues.ppiflow_seed_complex_path === 'string') setPpiflowSeedComplexPath(initialValues.ppiflow_seed_complex_path);
            if (typeof initialValues.antibody_chains === 'string' && initialValues.antibody_chains.trim()) {
                setPpiflowSeedAntibodyChains(initialValues.antibody_chains);
            }
            if (typeof initialValues.antigen_chains === 'string' && initialValues.antigen_chains.trim()) {
                setPpiflowSeedAntigenChains(initialValues.antigen_chains);
            }
            if (typeof initialValues.out_dir === 'string') setCustomOutputDir(initialValues.out_dir);
            if (initialValues.target_dna_seq) {
                setTargetDnaSeq(initialValues.target_dna_seq);
                setShowDnaInput(true);
            }

            // Sequence Designer
            if (initialValues.seq_design_fampnn) setSeqDesigner('fampnn');
            else if (initialValues.seq_design_caliby) setSeqDesigner('caliby');
            else if (initialValues.seq_design_antifold) setSeqDesigner('antifold');
            else if (initialValues.seq_design_proteinmpnn) setSeqDesigner('proteinmpnn');
            else if (
                initialValues.seq_design_fampnn === false &&
                initialValues.seq_design_caliby === false &&
                initialValues.seq_design_antifold === false &&
                initialValues.seq_design_proteinmpnn === false
            ) setSeqDesigner('none');
            else if (initialValues.seq_designer) setSeqDesigner(initialValues.seq_designer); // Direct name
            if (initialValues.fampnn_constraint_mode) {
                setFampnnConstraintMode(initialValues.fampnn_constraint_mode);
            }

            // Framework
            if (initialValues.framework_type) setFrameworkType(initialValues.framework_type);
            if (initialValues.design_mode || initialValues.antibody_design_mode) {
                setDesignMode(initialValues.design_mode || initialValues.antibody_design_mode);
            }
            if (Array.isArray(initialValues.selected_cdr_loops)) {
                setSelectedCDRLoops(new Set(initialValues.selected_cdr_loops));
            } else if (initialValues.antibody_design_loops) {
                setSelectedCDRLoops(new Set(String(initialValues.antibody_design_loops).split(',').map((v: string) => v.trim()).filter(Boolean)));
            }
            if (initialValues.rfantibody_loop_length_mode === 'custom_ranges' || initialValues.rfantibody_loop_length_mode === 'defaults') {
                setRfantibodyLoopLengthMode(initialValues.rfantibody_loop_length_mode);
            }
            if (initialValues.rfantibody_loop_length_ranges_config || initialValues.rfantibody_loop_length_ranges) {
                setRfantibodyLoopLengthRanges(
                    parseLoopLengthRanges(initialValues.rfantibody_loop_length_ranges_config || initialValues.rfantibody_loop_length_ranges)
                );
            }
            if (!isRefinementMode && typeof initialValues.enable_rfantibody_filter === 'boolean') {
                setEnableRfantibodyFilter(initialValues.enable_rfantibody_filter);
            }
            if (!isRefinementMode && initialValues.rfantibody_min_epitope_contacts !== undefined) {
                setRfantibodyMinEpitopeContacts(Math.max(0, Number(initialValues.rfantibody_min_epitope_contacts) || 0));
            }
            if (!isRefinementMode && initialValues.rfantibody_max_epitope_distance !== undefined) {
                setRfantibodyMaxEpitopeDistance(Math.max(0, Number(initialValues.rfantibody_max_epitope_distance) || 0));
            }
            if (!isRefinementMode && initialValues.rfantibody_min_target_contacts !== undefined) {
                setRfantibodyMinTargetContacts(Math.max(0, Number(initialValues.rfantibody_min_target_contacts) || 0));
            }
            if (!isRefinementMode && (initialValues as UntypedApiValue).rfantibody_max_target_distance !== undefined) {
                setRfantibodyMaxTargetDistance(Math.max(0, Number((initialValues as UntypedApiValue).rfantibody_max_target_distance) || 0));
            }
            if (!isRefinementMode && initialValues.rfantibody_max_epitope_centroid_distance !== undefined) {
                setRfantibodyMaxEpitopeCentroidDistance(Math.max(0, Number(initialValues.rfantibody_max_epitope_centroid_distance) || 0));
            }
            if (!isRefinementMode && initialValues.rfantibody_contact_distance_threshold !== undefined) {
                setRfantibodyContactDistanceThreshold(Math.max(0, Number(initialValues.rfantibody_contact_distance_threshold) || 0));
            }
            if (!isRefinementMode && initialValues.rfantibody_target_contact_distance_threshold !== undefined) {
                setRfantibodyTargetContactDistanceThreshold(Math.max(0, Number(initialValues.rfantibody_target_contact_distance_threshold) || 0));
            }
            if (typeof initialValues.protect_tetrad === 'boolean') setProtectTetrad(initialValues.protect_tetrad);
            else if (typeof initialValues.protect_vhh_tetrad === 'boolean') setProtectTetrad(initialValues.protect_vhh_tetrad);
            if (Array.isArray(initialValues.manual_cdr_definitions)) {
                const defs = initialValues.manual_cdr_definitions.map((d: UntypedApiValue) => ({
                    ...d,
                    residues: new Set(d.residues || [])
                }));
                setManualCDRDefinitions(defs);
                setShowCDREditor(defs.length > 0);
            }

            queueRestoredSelection(initialValues);

            restoreTargetFromSaved(initialValues)
                .catch((e) => console.error('[ANTIBODY_DENOVO] Failed to restore saved target state', e));
            restoreFrameworkPreview(initialValues)
                .catch((e) => console.error('[ANTIBODY_DENOVO] Failed to restore saved framework state', e));
        }
    }, [initialValues, isRefinementMode, mergeQualitySettingsFromParams, queueRestoredSelection, restoreFrameworkPreview, restoreTargetFromSaved]);

    // Parse the uploaded/selected target structure, preserving all available models.
    useEffect(() => {
        if (!targetPdb) {
            setParsedTargetStructure(null);
            setParsedChains([]);
            setSelectedChain(null);
            setSelectedTargetModel(1);
            if (!restoringSelectionRef.current) {
                setSelectedResidues(new Set());
            }
            return;
        }

        setIsParsing(true);

        parsePDBFile(targetPdb)
            .then((result) => {
                setParsedTargetStructure(result);
                const queuedModel = restoringSelectionRef.current?.modelNumber ?? null;
                const preferredModel =
                    queuedModel
                    ?? getSavedTargetModelNumber(initialValues || {})
                    ?? selectedTargetModel;
                const resolvedModel = getModelByNumber(result, preferredModel) ?? result.models[0] ?? null;
                setSelectedTargetModel(resolvedModel?.modelNumber ?? 1);
                if (!uploadedPath && !initialValues) setUploadedPath(null);
                console.log(
                    '[ANTIBODY_DENOVO] Parsed target PDB models:',
                    result.models.map((model) => `${model.label}:${model.chains.map((c) => `${c.id}:${c.length}aa`).join('|')}`)
                );
            })
            .catch((err) => {
                console.error('[ANTIBODY_DENOVO] Failed to parse PDB:', err);
                setParsedTargetStructure(null);
                setParsedChains([]);
            })
            .finally(() => setIsParsing(false));
    }, [getSavedTargetModelNumber, initialValues, selectedTargetModel, targetPdb, uploadedPath]);

    // Keep the active target chains/viewer content aligned to the currently selected model.
    // This effect depends only on its input model; functional updates prevent a
    // state-mutation dependency cycle from continually recreating the blob URL.
    useEffect(() => {
        if (!parsedTargetStructure) {
            replacePdbBlobUrl(null);
            return;
        }

        const activeModel = getModelByNumber(parsedTargetStructure, selectedTargetModel);
        if (!activeModel) {
            setParsedChains([]);
            setSelectedChain(null);
            replacePdbBlobUrl(null);
            return;
        }

        const chainIds = activeModel.chains.map((chain) => chain.id);
        const availableResidues = buildAvailableResidueKeySet(activeModel.chains);
        const queuedRestore = restoringSelectionRef.current;

        setParsedChains(activeModel.chains);
        replacePdbBlobUrl(URL.createObjectURL(new Blob([activeModel.content], { type: 'text/plain' })));

        setSelectedChain((current) => {
            if (activeModel.chains.length === 0) return null;
            if (queuedRestore?.chain && chainIds.includes(queuedRestore.chain)) return queuedRestore.chain;
            if (!current || !chainIds.includes(current)) {
                return activeModel.chains.reduce((a, b) => (a.length > b.length ? a : b)).id;
            }
            return current;
        });
        setSelectedResidues((current) => new Set(
            (queuedRestore?.residues || Array.from(current)).filter((key) => availableResidues.has(key)),
        ));
        restoringSelectionRef.current = null;
    }, [parsedTargetStructure, replacePdbBlobUrl, selectedTargetModel]);

    // Parse custom framework PDB for accurate CDR mapping when uploaded.
    useEffect(() => {
        const loadToken = frameworkLoadControllerRef.current.begin();
        if (frameworkType !== 'custom') return;
        if (!customFrameworkFile) {
            setParsedFrameworkChains([]);
            return;
        }

        parsePDBFile(customFrameworkFile)
            .then((result) => {
                if (frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                    setParsedFrameworkChains(result.chains);
                }
            })
            .catch((err) => {
                if (!frameworkLoadControllerRef.current.isCurrent(loadToken)) return;
                console.error('[ANTIBODY_DENOVO] Failed to parse custom framework PDB:', err);
                setParsedFrameworkChains([]);
            });
    }, [frameworkType, customFrameworkFile]);

    const normalizeChainId = (chainId?: string | null) => (chainId || '').trim().toUpperCase();

    const resolveFrameworkChains = (): { heavyChain?: Chain; lightChain?: Chain } => {
        if (parsedFrameworkChains.length === 0) {
            return {};
        }

        const heavyChainId = normalizeChainId(sabdabFramework?.hChain);
        const lightChainId = normalizeChainId(sabdabFramework?.lChain || null);
        const antigenChains = new Set(
            (sabdabFramework?.antigenChain || '')
                .split(',')
                .map((c) => normalizeChainId(c))
                .filter(Boolean)
        );
        const findById = (id: string) =>
            parsedFrameworkChains.find((chain) => normalizeChainId(chain.id) === id);

        let heavyChain = heavyChainId ? findById(heavyChainId) : undefined;
        if (!heavyChain) {
            const nonAntigenChains = parsedFrameworkChains.filter(
                (chain) => !antigenChains.has(normalizeChainId(chain.id))
            );
            const pool = nonAntigenChains.length > 0 ? nonAntigenChains : parsedFrameworkChains;
            heavyChain = [...pool].sort((a, b) => b.length - a.length)[0];
        }

        let lightChain = lightChainId ? findById(lightChainId) : undefined;
        if (!lightChain) {
            lightChain = parsedFrameworkChains.find(
                (chain) =>
                    (!heavyChain || normalizeChainId(chain.id) !== normalizeChainId(heavyChain.id)) &&
                    !antigenChains.has(normalizeChainId(chain.id))
            );
        }

        return { heavyChain, lightChain };
    };

    const collectResiduesFromDetectedRange = (
        chain: Chain | undefined,
        seqRange: [number, number] | null | undefined,
        imgtRange: [number, number] | null | undefined,
        chainIdFallback?: string
    ): Set<string> => {
        const residues = new Set<string>();

        if (chain) {
            if (seqRange) {
                for (let i = seqRange[0]; i <= seqRange[1]; i++) {
                    if (i < 0 || i >= chain.residues.length) continue;
                    const res = chain.residues[i];
                    residues.add(`${res.chainId}${res.resNum}${res.iCode || ''}`);
                }
                if (residues.size > 0) {
                    return residues;
                }
            }

            if (imgtRange) {
                const [start, end] = imgtRange;
                for (const res of chain.residues) {
                    if (res.resNum >= start && res.resNum <= end) {
                        residues.add(`${res.chainId}${res.resNum}${res.iCode || ''}`);
                    }
                }
            }
        }

        // Last-resort fallback: synthesize residues from IMGT ranges so
        // "Use These CDRs" still applies detected loops even if parsing/mapping fails.
        if (residues.size === 0 && imgtRange && chainIdFallback) {
            const [start, end] = imgtRange;
            const chainId = normalizeChainId(chainIdFallback) || 'H';
            for (let pos = start; pos <= end; pos++) {
                residues.add(`${chainId}${pos}`);
            }
        }

        return residues;
    };

    const handleFileUpload = async (file: File) => {
        setIsUploading(true);
        try {
            const response = await uploadFile('inputs/antibody', file);
            const path = response.data?.path || `inputs/antibody/${file.name}`;
            setUploadedPath(path);
            console.log('[ANTIBODY_DENOVO] File uploaded:', path, response);
            return path;
        } catch (error) {
            console.error('[ANTIBODY_DENOVO] Upload failed:', error);
            alert('Failed to upload PDB file. Please try again.');
            throw error;
        } finally {
            setIsUploading(false);
        }
    };

    const handleSubmit = async () => {
        // When skipping early steps, target PDB and epitope are not required
        const skippingEarlySteps = deNovoGenerator === 'rfantibody' && (skipRFantibody || skipFampnn);
        const runSequenceDesign = effectiveSeqDesigner !== 'none';
        const requiresTargetAndEpitope =
            !isRefinementMode &&
            deNovoGenerator !== 'ppiflow' &&
            !skippingEarlySteps;
        const hasResolvedTarget = Boolean(targetPdb || targetSource?.path || uploadedPath);

        if (requiresTargetAndEpitope && !hasResolvedTarget) {
            alert('Please upload a target PDB file');
            return;
        }
        if (requiresTargetAndEpitope && selectedResidues.size === 0) {
            alert('Please select at least one epitope residue');
            return;
        }

        // When skipping, use a placeholder or the input dir path
        if (isRefinementMode) {
            // In refinement mode, the backend determines the input PDB paths via selection_dir
            // We just let it proceed
        } else {
            // Validate skip inputs have paths
            if (skipRFantibody && !rfantibodyInputPdbs.trim()) {
                alert('Please provide a path to backbone PDBs for Skip RFantibody');
                return;
            }
            if (skipFampnn && !fampnnCollectedPdbs.trim()) {
                alert('Please provide a path to sequenced PDBs for Skip FAMPNN');
                return;
            }
        }
        const fampnnCheckpointSpecified = Boolean(
            qualitySettings.fampnn_checkpoint_path.trim() || resolvedFampnnCheckpoint.trim()
        );
        const needsFampnnCheckpoint =
            effectiveSeqDesigner === 'fampnn' ||
            (runPpiFlowMaturation && qualitySettings.maturation_redesign_enabled !== false);
        if (needsFampnnCheckpoint && !fampnnCheckpointSpecified) {
            alert('Please choose FAMPNN weights or provide a checkpoint path before submitting.');
            return;
        }

        if (isRefinementMode && !useManualMutagenesis && !runSequenceDesign && !anyPpiFlowStageEnabled && !effectiveRunStructureValidation && !effectiveRunFrustrampnn) {
            alert('Enable at least one refinement stage before launching.');
            return;
        }

        // Validate that a SAbDab framework was actually selected
        if (deNovoGenerator === 'rfantibody' && frameworkType === 'sabdab' && !sabdabFramework?.pdbCode) {
            alert('Please select a specific framework from the SAbDab database before submitting, or select a different framework preset.');
            return;
        }

        try {
            // Format selected residues for backend
            const epitopeString = Array.from(selectedResidues).sort().join(',');
            const allowSkipFallback = skippingEarlySteps || isRefinementMode;
            const pdbPath = hasResolvedTarget || allowSkipFallback
                ? await resolveTargetPdbPathForLaunch(allowSkipFallback)
                : undefined;

            if (!isRefinementMode && deNovoGenerator === 'boltzgen') {
                if (boltzgenUseFrameworkTemplate && boltzgenScaffoldSource === 'selected_scaffold' && !sabdabFramework?.pdbCode && !customFrameworkPath) {
                    alert('Select a SAbDab framework or switch the scaffold source before launching BoltzGen.');
                    return;
                }
                if (!pdbPath) {
                    throw new Error('Failed to determine target PDB path for BoltzGen launch.');
                }

                const boltzgenParams = buildStandaloneBoltzgenParams(pdbPath, epitopeString);

                await submitMutation.mutateAsync({
                    name: jobName,
                    model_id: 'boltzgen',
                    mode: 'nanobody_binder',
                    params: boltzgenParams,
                    pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null,
                });
                return;
            }

            if (!isRefinementMode && deNovoGenerator === 'ppiflow') {
                const seedLaunch = await resolvePpiFlowSeedPathForLaunch();
                const ppiflowParams = buildStandalonePpiFlowGeneratorParams(pdbPath, epitopeString, seedLaunch);

                await submitMutation.mutateAsync({
                    name: jobName,
                    model_id: 'ppiflow',
                    mode: 'generator_backbone_refine',
                    params: ppiflowParams,
                    pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null,
                });
                return;
            }

            // Determine pipeline steps
            const pipelineSteps = [isRefinementMode ? 'selected_inputs' : 'rfantibody'];
            if (runSequenceDesign) pipelineSteps.push(effectiveSeqDesigner);
            if (runPpiFlowBackboneRefine) pipelineSteps.push('ppiflow_backbone_refine');
            if (runPpiFlowMaturation) pipelineSteps.push('ppiflow_maturation');
            if (effectiveUseAntiberty) pipelineSteps.push('antiberty');
            if (effectiveUseThermoMPNN) pipelineSteps.push('thermompnn');
            if (effectiveRunStructureValidation) pipelineSteps.push(structureValidator);
            if (effectiveRunFrustrampnn) pipelineSteps.push('frustrampnn');

            // Step 2: Upload custom framework if provided
            let frameworkPath = frameworkType === 'sabdab'
                ? (sabdabFramework?.filePath || customFrameworkPath)
                : customFrameworkPath;
            let effectiveFrameworkType = frameworkType;
            let effectiveAntibodyType = frameworkType === 'nanobody' ? 'vhh' : 'scfv';

            if (frameworkType === 'custom' && customFrameworkFile && !frameworkPath) {
                const response = await uploadFile('inputs/antibody', customFrameworkFile);
                frameworkPath = response.data?.path || `inputs/antibody/${customFrameworkFile.name}`;
                setCustomFrameworkPath(frameworkPath);
                console.log('[ANTIBODY_DENOVO] Custom framework uploaded:', frameworkPath, response);
                effectiveAntibodyType = 'custom';
            } else if (frameworkType === 'sabdab' && sabdabFramework?.pdbCode) {
                // Use the converted H/L/T SAbDab artifact from our own backend, not a raw RCSB fetch.
                try {
                    effectiveFrameworkType = 'custom';
                    effectiveAntibodyType = !sabdabFramework.lChain ? 'vhh' : 'fab';
                    frameworkPath = frameworkPath || sabdabFramework.filePath || null;

                    if (!frameworkPath) {
                        const hydrated = await loadSabdabFrameworkFile(
                            sabdabFramework.pdbCode,
                            `${sabdabFramework.pdbCode}_framework.pdb`
                        );
                        frameworkPath = hydrated.filePath || await handleFileUpload(hydrated.file);
                        setCustomFrameworkPath(frameworkPath);
                        setSabdabFramework((prev) => prev ? { ...prev, filePath: frameworkPath || prev.filePath } : prev);
                    }
                } catch (err) {
                    console.error('[ANTIBODY_DENOVO] Failed to process SAbDab framework:', err);
                    alert(`Failed to prepare SAbDab framework ${sabdabFramework.pdbCode}. Please try a different one or use the Nanobody preset.`);
                    return;
                }
            }

            // Step 3: Submit job with uploaded file path
            const selectedLoops = Array.from(selectedCDRLoops).sort();
            const applicableLoops = selectedLoops.filter((loopId) => {
                if (frameworkType === 'nanobody') return loopId.startsWith('H');
                return true;
            });
            const manualCdrPositionsByLoop = buildManualCdrPositionsByLoop(manualCDRDefinitions);
            const rfantibodyLoopLengthSpec = rfantibodyLoopLengthMode === 'custom_ranges' && applicableLoops.length > 0
                ? `[${applicableLoops.map((loopId) => {
                    const range = rfantibodyLoopLengthRanges[loopId] || DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId];
                    const min = Math.max(1, Number(range?.min) || DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.min || 1);
                    const max = Math.max(min, Number(range?.max) || DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.max || min);
                    return `${loopId}:${min}${max !== min ? `-${max}` : ''}`;
                }).join(',')}]`
                : undefined;
            // Serialize manualCDRDefinitions strictly, dropping the generic 'H1' logic
            // RFA/FAMPNN needs format: ['H27-H38', 'L56-L65']
            let customRfalLoopsSpec: string | undefined = undefined;
            if (manualCDRDefinitions && manualCDRDefinitions.length > 0) {
                const parts: string[] = [];
                manualCDRDefinitions.forEach(def => {
                    if (def.residues.size > 0) {
                        const resArray = Array.from(def.residues).sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)));
                        // Get the first and last residue ID (e.g., 'H27' and 'H38')
                        // We rely on the raw PDB order preserving numeric suffix order nicely.
                        const start = resArray[0];
                        const end = resArray[resArray.length - 1];
                        parts.push(`${start.charAt(0)}${start.substring(1)}-${end.substring(1)}`);
                    }
                });
                if (parts.length > 0) {
                    customRfalLoopsSpec = `[${parts.join(',')}]`;
                }
            }

            const antibodyPipelineMode = isRefinementMode
                ? ANTIBODY_REFINEMENT_PIPELINE_MODE
                : ANTIBODY_DENOVO_PIPELINE_MODE;

            const jobData = {
                name: jobName,
                model_id: 'template_antibody_denovo',
                mode: antibodyPipelineMode,
                pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null,
                params: {
                    target_pdb: isRefinementMode ? undefined : pdbPath,
                    target_model_number: isRefinementMode ? undefined : selectedTargetModel || undefined,
                    pdb_source: isRefinementMode ? undefined : 'upload',
                    epitope_residues: isRefinementMode ? undefined : epitopeString,
                    antigen_chains: isRefinementMode ? undefined : selectedChain || undefined, // Send selected chain
                    pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                    lock_gpus: lockGpus && pinnedGpus.length > 0, // GPU locking
                    // Framework configuration
                    framework_type: effectiveFrameworkType,
                    framework_pdb: frameworkPath || undefined, // Only if custom or sabdab
                    // Pipeline configuration
                    rfd_mode: antibodyPipelineMode,
                    antibody_pipeline_steps: pipelineSteps,
                    rfantibody_num_designs: numDesigns,
                    seq_design_fampnn: effectiveSeqDesigner === 'fampnn',
                    seq_design_caliby: effectiveSeqDesigner === 'caliby',
                    seq_design_antifold: effectiveSeqDesigner === 'antifold',
                    seq_design_proteinmpnn: effectiveSeqDesigner === 'proteinmpnn',
                    seq_designer: effectiveSeqDesigner,
                    run_immunogenicity_scoring: effectiveUseAntiberty,
                    run_stability_scoring: effectiveUseThermoMPNN,
                    run_structure_validation: effectiveRunStructureValidation,
                    structure_validator: structureValidator,
                    run_frustrampnn: effectiveRunFrustrampnn,
                    run_anarcii_post: effectiveRunAnarciiPost,
                    anarcii_include_children: anarciiIncludeChildren,
                    interactive_swa: interactiveWorkflow,
                    interactive_gating: interactiveWorkflow,
                    interactive_gate_stage: interactiveGateStage,
                    exploration_mode: true, // Always parallel - granularity controlled via parallel_mode
                    seqs_per_design: seqsPerDesign, // Number of sequence variants per backbone
                    // Optional DNA sequence for complex prediction
                    target_dna_seq: targetDnaSeq.trim() || undefined,
                    // Design mode settings
                    antibody_design_mode: designMode,
                    antibody_design_loops: selectedLoops.join(','),
                    // Use explicit ranges from manualCDRDefinitions built off the true PDB index
                    rfantibody_design_loops_custom: customRfalLoopsSpec,
                    rfantibody_loop_length_mode: rfantibodyLoopLengthMode,
                    rfantibody_loop_length_ranges: rfantibodyLoopLengthSpec,
                    enable_rfantibody_filter: enableRfantibodyFilter,
                    rfantibody_screen_reference_scope: rfantibodyScreenReferenceScope,
                    rfantibody_min_epitope_contacts: enableRfantibodyFilter ? rfantibodyMinEpitopeContacts : undefined,
                    rfantibody_max_epitope_distance: enableRfantibodyFilter ? rfantibodyMaxEpitopeDistance : undefined,
                    rfantibody_min_target_contacts: enableRfantibodyFilter ? rfantibodyMinTargetContacts : undefined,
                    rfantibody_max_target_distance: enableRfantibodyFilter && rfantibodyMaxTargetDistance > 0 ? rfantibodyMaxTargetDistance : undefined,
                    rfantibody_max_epitope_centroid_distance: enableRfantibodyFilter ? rfantibodyMaxEpitopeCentroidDistance : undefined,
                    rfantibody_contact_distance_threshold: enableRfantibodyFilter ? rfantibodyContactDistanceThreshold : undefined,
                    rfantibody_target_contact_distance_threshold: enableRfantibodyFilter ? rfantibodyTargetContactDistanceThreshold : undefined,
                    protect_vhh_tetrad: protectTetrad,
                    antibody_chains: effectiveAntibodyType === 'vhh' ? 'H' : 'H,L',
                    // Quality settings - RFantibody (backbone diffusion)
                    rfantibody_diffusion_steps: qualitySettings.rfantibody_diffusion_steps,
                    rfantibody_noise_scale_ca: qualitySettings.rfantibody_noise_scale_ca,
                    rfantibody_noise_scale_frame: qualitySettings.rfantibody_noise_scale_frame,
                    rfantibody_guide_scale: qualitySettings.rfantibody_guide_scale,
                    rfantibody_ckpt_override: qualitySettings.rfantibody_ckpt_override.trim() || undefined,
                    rfantibody_debug_repo_overlay: qualitySettings.rfantibody_debug_repo_overlay,
                    // Structure validation settings
                    msa_preset: qualitySettings.msa_preset,
                    boltz_sampling_steps: qualitySettings.boltz_sampling_steps,
                    boltz_recycling_steps: qualitySettings.boltz_recycling_steps,
                    boltz_num_samples: qualitySettings.boltz_num_samples,
                    boltz_use_potentials: qualitySettings.boltz_use_potentials,
                    boltz_use_msa: qualitySettings.boltz_use_msa,
                    boltz_anchor_target: qualitySettings.boltz_anchor_target,
                    boltz_anchor_strict: qualitySettings.boltz_anchor_strict,
                    // Boltz-2 affinity prediction
                    boltz_predict_affinity: qualitySettings.boltz_predict_affinity,
                    boltz_diffusion_samples_affinity: qualitySettings.boltz_diffusion_samples_affinity,
                    protenix_model_weights: qualitySettings.protenix_model_weights,
                    protenix_seeds: qualitySettings.protenix_seeds,
                    protenix_n_sample: qualitySettings.protenix_n_sample,
                    protenix_n_step: qualitySettings.protenix_n_step,
                    protenix_n_cycle: qualitySettings.protenix_n_cycle,
                    protenix_use_msa: qualitySettings.protenix_use_msa,
                    protenix_msa_backend: qualitySettings.protenix_msa_backend,
                    protenix_use_template: qualitySettings.protenix_use_template,
                    protenix_anchor_target: qualitySettings.protenix_anchor_target,
                    protenix_anchor_strict: qualitySettings.protenix_anchor_strict,
                    protenix_enable_cache: qualitySettings.protenix_enable_cache,
                    protenix_enable_fusion: qualitySettings.protenix_enable_fusion,
                    protenix_auto_oom_retry: qualitySettings.protenix_auto_oom_retry,
                    protenix_oom_retry_attempts: qualitySettings.protenix_oom_retry_attempts,
                    colabfold_api_host: qualitySettings.colabfold_api_host.trim() || undefined,
                    msa_use_gpu: qualitySettings.msa_use_gpu,
                    msa_local_db: qualitySettings.msa_local_db.trim() || undefined,
                    msa_cache_dir: qualitySettings.msa_cache_dir.trim() || undefined,
                    msa_threads: qualitySettings.msa_threads ?? undefined,
                    msa_gpu_mode: qualitySettings.msa_gpu_mode,
                    msa_gpu_threshold: qualitySettings.msa_gpu_threshold,
                    msa_preferred_gpus: qualitySettings.msa_preferred_gpus.trim() || undefined,
                    msa_excluded_gpus: qualitySettings.msa_excluded_gpus.trim() || undefined,
                    msa_gpu_server_mode: qualitySettings.msa_gpu_server_mode,
                    msa_gpu_server_wait_timeout: qualitySettings.msa_gpu_server_wait_timeout,
                    msa_gpu_server_db_load_mode: qualitySettings.msa_gpu_server_db_load_mode,
                    msa_gpu_server_startup_wait: qualitySettings.msa_gpu_server_startup_wait,
                    // Quality settings - FAMPNN (sequence design)
                    fampnn_checkpoint: resolvedFampnnCheckpoint || undefined,
                    fampnn_checkpoint_path: qualitySettings.fampnn_checkpoint_path.trim() || undefined,
                    fampnn_temperature: qualitySettings.fampnn_temperature,
                    fampnn_num_steps: qualitySettings.fampnn_num_steps,
                    fampnn_psce_threshold: qualitySettings.fampnn_psce_threshold,
                    lock_target_chains: qualitySettings.lock_target_chains,
                    lock_antibody_framework: qualitySettings.lock_antibody_framework,
                    fampnn_constraint_mode: effectiveSeqDesigner === 'fampnn' ? fampnnConstraintMode : undefined,
                    caliby_model_name: qualitySettings.caliby_model_name,
                    caliby_temperature: qualitySettings.caliby_temperature,
                    caliby_batch_size: qualitySettings.caliby_batch_size,
                    caliby_num_workers: qualitySettings.caliby_num_workers,
                    caliby_clean_num_workers: qualitySettings.caliby_clean_num_workers,
                    caliby_omit_aas: qualitySettings.caliby_omit_aas.trim() || undefined,
                    caliby_run_self_consistency_eval: qualitySettings.caliby_run_self_consistency_eval,
                    caliby_self_consistency_num_models: qualitySettings.caliby_self_consistency_num_models,
                    caliby_self_consistency_num_recycles: qualitySettings.caliby_self_consistency_num_recycles,
                    caliby_self_consistency_use_multimer: qualitySettings.caliby_self_consistency_use_multimer,
                    enable_caliby_filter: qualitySettings.enable_caliby_filter,
                    caliby_max_potts_energy: qualitySettings.caliby_max_potts_energy ?? undefined,
                    caliby_min_sc_plddt: qualitySettings.caliby_min_sc_plddt ?? undefined,
                    caliby_max_sc_rmsd: qualitySettings.caliby_max_sc_rmsd ?? undefined,
                    caliby_fixed_pos_override_seq: qualitySettings.caliby_fixed_pos_override_seq.trim() || undefined,
                    caliby_pos_restrict_aatype: qualitySettings.caliby_pos_restrict_aatype.trim() || undefined,
                    caliby_symmetry_pos: qualitySettings.caliby_symmetry_pos.trim() || undefined,
                    caliby_sampling_overrides_json: qualitySettings.caliby_sampling_overrides_json.trim() || undefined,
                    // PPIFlow settings
                    run_ppiflow_backbone_refine: runPpiFlowBackboneRefine,
                    run_ppiflow_maturation: runPpiFlowMaturation,
                    run_maturation: runPpiFlowMaturation,
                    ppiflow_stage_mode: ppiflowStageMode,
                    ppiflow_tuning_profile: qualitySettings.ppiflow_tuning_profile,
                    ppiflow_backbone_region_mode: ppiflowBackboneRegionMode,
                    ppiflow_maturation_region_mode: ppiflowMaturationRegionMode,
                    ppiflow_backbone_loop_scope: effectivePpiFlowBackboneLoopScope || undefined,
                    ppiflow_maturation_loop_scope: effectivePpiFlowMaturationLoopScope || undefined,
                    ppiflow_selected_loops: runPpiFlowBackboneRefine
                        ? (effectivePpiFlowBackboneLoopScope || undefined)
                        : runPpiFlowMaturation
                            ? (effectivePpiFlowMaturationLoopScope || undefined)
                            : undefined,
                    cdr_positions_by_loop: manualCdrPositionsByLoop,
                    run_post_validation_maturation: false,
                    run_post_boltz_maturation: false,
                    ppiflow_start_t: qualitySettings.ppiflow_start_t,
                    ppiflow_samples_per_target: qualitySettings.ppiflow_samples_per_target,
                    ppiflow_retry_limit: qualitySettings.ppiflow_retry_limit,
                    ppiflow_config: qualitySettings.ppiflow_config,
                    ppiflow_weights_dir: qualitySettings.ppiflow_weights_dir,
                    ppiflow_checkpoint_path: qualitySettings.ppiflow_checkpoint_path,
                    ppiflow_rotamer_enrichment_enabled: qualitySettings.ppiflow_rotamer_enrichment_enabled,
                    ppiflow_require_anchors: qualitySettings.ppiflow_require_anchors,
                    ppiflow_rotamer_shell_cutoff: qualitySettings.ppiflow_rotamer_shell_cutoff,
                    ppiflow_objective_mode: qualitySettings.ppiflow_objective_mode,
                    ppiflow_objective_threshold: qualitySettings.ppiflow_objective_threshold,
                    maturation_anchor_threshold: qualitySettings.maturation_anchor_threshold,
                    maturation_anchor_distance_cutoff: qualitySettings.maturation_anchor_distance_cutoff,
                    maturation_min_improvement: qualitySettings.maturation_min_improvement,
                    maturation_redesign_temp: qualitySettings.maturation_redesign_temp,
                    maturation_redesign_steps: qualitySettings.maturation_redesign_steps,
                    maturation_design_mode: qualitySettings.maturation_design_mode,
                    maturation_designs_per_job: qualitySettings.maturation_designs_per_job,
                    maturation_filter_percentile: qualitySettings.maturation_filter_percentile,
                    maturation_redesign_enabled: qualitySettings.maturation_redesign_enabled,
                    maturation_redesign_top_n: qualitySettings.maturation_redesign_top_n,
                    ppiflow_checkpoint: resolvedPpiFlowCheckpoint,
                    ppiflow_antigen_chain: qualitySettings.ppiflow_antigen_chain,
                    ppiflow_heavy_chain: qualitySettings.ppiflow_heavy_chain,
                    ppiflow_light_chain: qualitySettings.ppiflow_light_chain,
                    // Pre-Boltz filtering (saves compute)
                    fampnn_max_psce: qualitySettings.fampnn_max_psce,
                    fampnn_max_residue_psce: qualitySettings.fampnn_max_residue_psce,
                    // ThermoMPNN stability scoring (before Boltz when enabled)
                    run_thermompnn: effectiveUseThermoMPNN,
                    thermompnn_max_ddg: qualitySettings.thermompnn_max_ddg,
                    // AF2 Backprop CDR refinement (after ThermoMPNN, before Boltz)
                    run_af2_backprop: qualitySettings.run_af2_backprop,
                    af2_backprop_soft_iters: qualitySettings.af2_backprop_soft_iters,
                    af2_backprop_temp_iters: qualitySettings.af2_backprop_temp_iters,
                    af2_backprop_hard_iters: qualitySettings.af2_backprop_hard_iters,
                    af2_backprop_num_recycles: qualitySettings.af2_backprop_num_recycles,
                    af2_backprop_learning_rate: qualitySettings.af2_backprop_learning_rate,
                    af2_backprop_use_multimer: qualitySettings.af2_backprop_use_multimer,
                    af2_backprop_num_models: qualitySettings.af2_backprop_num_models,
                    af2_backprop_loss_plddt: qualitySettings.af2_backprop_loss_plddt,
                    af2_backprop_loss_pae: qualitySettings.af2_backprop_loss_pae,
                    af2_backprop_loss_contact: qualitySettings.af2_backprop_loss_contact,
                    // Post-validation filtering
                    boltz_max_binder_rmsd: qualitySettings.boltz_max_binder_rmsd,
                    boltz_min_ptm_interface: qualitySettings.boltz_min_ptm_interface,
                    // Orchestrator parallelism mode
                    parallel_mode: parallelMode,
                    designs_per_job: designsPerJob,
                    pdbs_per_job: pdBsPerJob,
                    seqs_per_job: pdBsPerJob,
                    seqs_per_boltz_job: seqsPerBoltzJob,
                    seqs_per_validation_job: seqsPerBoltzJob,
                    // Debug: Skip step settings
                    skip_rfantibody: skipRFantibody || undefined,
                    rfantibody_input_pdbs: rfantibodyInputPdbs.trim() || undefined,
                    fampnn_collected_pdbs: fampnnCollectedPdbs.trim() || undefined,
                    // Debug: Custom output directory
                    out_dir: customOutputDir.trim() || undefined,
                    // Physics refinement (OpenMM)
                    openmm_enabled: effectivePhysicsEnabled,
                    openmm_compute_tier: physicsSettings.computeTier,
                    openmm_cdr_only: physicsSettings.cdrOnly,
                    openmm_restraint_mode: physicsSettings.restraintMode,
                    openmm_mmgbsa_mode: physicsSettings.mmgbsaMode,
                    openmm_force_field: physicsSettings.forceField,
                    openmm_top_n_percentage: physicsSettings.topNPercentage,
                    openmm_max_iterations: physicsSettings.maxIterations,
                    openmm_tolerance: physicsSettings.tolerance,
                    openmm_restraint_strength: physicsSettings.restraintStrength,
                    openmm_implicit_solvent: physicsSettings.implicitSolvent,
                    openmm_platform: physicsSettings.platform,
                }
            };

            if (isRefinementMode && refinementParentJobId && (refinementDesignIds?.length || refinementReviewFilterSetId)) {
                // Determine action based on UI settings
                // Nextflow determines the correct start based on skip flags which jobs.py injects
                const refinementOverrides = { ...jobData.params } as Record<string, UntypedApiValue>;
                for (const key of [
                    'enable_rfantibody_filter',
                    'rfantibody_screen_reference_scope',
                    'rfantibody_min_epitope_contacts',
                    'rfantibody_max_epitope_distance',
                    'rfantibody_min_target_contacts',
                    'rfantibody_max_target_distance',
                    'rfantibody_max_epitope_centroid_distance',
                    'rfantibody_contact_distance_threshold',
                    'rfantibody_target_contact_distance_threshold',
                ]) {
                    delete refinementOverrides[key];
                }
                refinementOverrides.rfantibody_screen_reference_scope = rfantibodyScreenReferenceScope;
                if (enableRfantibodyFilter) {
                    refinementOverrides.enable_rfantibody_filter = true;
                    refinementOverrides.rfantibody_min_epitope_contacts = rfantibodyMinEpitopeContacts;
                    refinementOverrides.rfantibody_max_epitope_distance = rfantibodyMaxEpitopeDistance;
                    refinementOverrides.rfantibody_min_target_contacts = rfantibodyMinTargetContacts;
                    if (rfantibodyMaxTargetDistance > 0) refinementOverrides.rfantibody_max_target_distance = rfantibodyMaxTargetDistance;
                    refinementOverrides.rfantibody_max_epitope_centroid_distance = rfantibodyMaxEpitopeCentroidDistance;
                    refinementOverrides.rfantibody_contact_distance_threshold = rfantibodyContactDistanceThreshold;
                    refinementOverrides.rfantibody_target_contact_distance_threshold = rfantibodyTargetContactDistanceThreshold;
                }

                if (useManualMutagenesis) {
                    if (mutagenesisMethod === 'cdr_indels') {
                        if (cdrIndelConfig.loop_ids.length === 0) {
                            alert('Select at least one CDR loop before launching a CDR indel round.');
                            return;
                        }
                        if (!cdrIndelConfig.allow_insertions && !cdrIndelConfig.allow_deletions) {
                            alert('Enable insertions, deletions, or both before launching a CDR indel round.');
                            return;
                        }

                        await launchAntibodyIteration({
                            source_job_id: refinementParentJobId,
                            action: mutagenesisLaunchMode === 'seeded_refinement' ? 'mutation_seeded_refinement' : 'cdr_indel_round',
                            design_ids: refinementDesignIds ?? [],
                            review_filter_set_id: refinementReviewFilterSetId,
                            cdr_indel_config: cdrIndelConfig,
                            param_overrides: refinementOverrides,
                        });
                        clearAntibodyRefinementLaunchState();
                        queryClient.invalidateQueries({ queryKey: ['jobs'] });
                        navigate('/');
                        return;
                    }

                    const mutationSets = manualMutagenesisConfig.mutation_sets_text
                        .split('\n')
                        .map((entry) => entry.trim())
                        .filter(Boolean);
                    if (mutationSets.length === 0) {
                        alert('Add at least one manual mutation set, one per line, before launching.');
                        return;
                    }

                    if (mutagenesisLaunchMode === 'seeded_refinement') {
                        await launchAntibodyIteration({
                            source_job_id: refinementParentJobId,
                            action: 'mutation_seeded_refinement',
                            design_ids: refinementDesignIds ?? [],
                            review_filter_set_id: refinementReviewFilterSetId,
                            manual_mutagenesis_config: {
                                chain_id: manualMutagenesisConfig.chain_id.trim() || undefined,
                                mutation_sets: mutationSets,
                                predictor: manualMutagenesisConfig.predictor,
                                msa_provider: manualMutagenesisConfig.msa_provider,
                            },
                            param_overrides: refinementOverrides,
                        });
                        clearAntibodyRefinementLaunchState();
                        queryClient.invalidateQueries({ queryKey: ['jobs'] });
                        navigate('/');
                        return;
                    }

                    await launchMutagenesisMutation.mutateAsync({
                        source_job_id: refinementParentJobId,
                        design_ids: refinementDesignIds ?? [],
                        review_filter_set_id: refinementReviewFilterSetId,
                        config: {
                            chain_id: manualMutagenesisConfig.chain_id.trim() || undefined,
                            mutation_sets: mutationSets,
                            predictor: manualMutagenesisConfig.predictor,
                            msa_provider: manualMutagenesisConfig.msa_provider,
                        },
                        param_overrides: refinementOverrides,
                    });
                    return;
                }

                await launchAntibodyIteration({
                    source_job_id: refinementParentJobId,
                    action: 'ui_refinement',
                    design_ids: refinementDesignIds ?? [],
                    review_filter_set_id: refinementReviewFilterSetId,
                    param_overrides: refinementOverrides,
                });
                clearAntibodyRefinementLaunchState();
                queryClient.invalidateQueries({ queryKey: ['jobs'] });
                navigate('/');
                return;
            }

            await submitMutation.mutateAsync(jobData);
        } catch (error) {
            console.error('[ANTIBODY_DENOVO] Submission failed', error);
        }
    };

    const hasFrameworkChainsForCDR = parsedFrameworkChains.length > 0;
    const cdrEditorChains = hasFrameworkChainsForCDR ? parsedFrameworkChains : [];
    const { heavyChain: cdrEditorHeavyChain } = resolveFrameworkChains();
    const cdrEditorActiveChain = hasFrameworkChainsForCDR
        ? (
            normalizeChainId(sabdabFramework?.hChain) ||
            normalizeChainId(cdrEditorHeavyChain?.id) ||
            normalizeChainId(parsedFrameworkChains[0]?.id) ||
            undefined
        )
        : undefined;
    const availableMutagenesisLoops = availableDesignLoops;
    const availableTargetModels = parsedTargetStructure?.models ?? [];
    const activeTargetModel = parsedTargetStructure ? getModelByNumber(parsedTargetStructure, selectedTargetModel) : null;
    const activeTargetResidues = buildAvailableResidueKeySet(parsedChains);
    const deNovoGeneratorSelector = !isRefinementMode ? (
        <div className="mb-6 space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
            <div>
                <div className="text-sm font-medium text-[var(--text-primary)]">Generation Engine</div>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                    Keep the existing workflow shell, but swap the core de novo generator between RFantibody, BoltzGen nanobody mode, and seeded PPIFlow.
                </p>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
                <button
                    type="button"
                    onClick={() => {
                        setDeNovoGenerator('rfantibody');
                        setShowBoltzgenFrameworkBrowser(false);
                        setDeNovoStageSelection(buildGeneratorOnlyStageSelection());
                        setInteractiveGateStage('post_rfantibody');
                    }}
                    className="rounded-xl border p-4 text-left transition-colors"
                    style={deNovoGenerator === 'rfantibody' ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                >
                    <div className="text-sm font-medium text-[var(--text-primary)]">RFantibody Stack</div>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        Diffusion backbones, screening, and review-first nanobody generation in the original workflow structure.
                    </p>
                </button>
                <button
                    type="button"
                    onClick={() => {
                        setDeNovoGenerator('boltzgen');
                        setShowBoltzgenFrameworkBrowser(false);
                        setDeNovoStageSelection(buildGeneratorOnlyStageSelection());
                        setInteractiveGateStage('post_boltzgen');
                    }}
                    className="rounded-xl border p-4 text-left transition-colors"
                    style={deNovoGenerator === 'boltzgen' ? themedSelectedStyle('#f59e0b') : themedInsetStyle}
                >
                    <div className="text-sm font-medium text-[var(--text-primary)]">BoltzGen Nanobody</div>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        All-atom nanobody generation with the same target-selection workflow, then Antibody Refinement for downstream redesign.
                    </p>
                </button>
                <button
                    type="button"
                    onClick={() => {
                        setDeNovoGenerator('ppiflow');
                        setShowBoltzgenFrameworkBrowser(false);
                        setDeNovoStageSelection(buildGeneratorOnlyStageSelection());
                        setInteractiveGateStage('post_ppiflow_generator');
                    }}
                    className="rounded-xl border p-4 text-left transition-colors"
                    style={deNovoGenerator === 'ppiflow' ? themedSelectedStyle('var(--accent-secondary)') : themedInsetStyle}
                >
                    <div className="text-sm font-medium text-[var(--text-primary)]">PPIFlow Seeded</div>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        Seeded partial-flow backbone generation from antibody-target complexes, then Antibody Refinement for downstream redesign.
                    </p>
                </button>
            </div>

            <div className="rounded-xl border p-4" style={themedInsetStyle}>
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <div className="text-sm font-medium text-[var(--text-primary)]">Initial Orchestration Menu</div>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                            Default keeps only the core generator active. Expose downstream modules only when you want them in the initial batch run.
                        </p>
                    </div>
                    {showOnlyCoreGeneratorStep && (
                        <span className="rounded-full border px-2.5 py-1 text-[11px]" style={themedTagStyle('var(--success)')}>
                            Generator Only
                        </span>
                    )}
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-4">
                    {([
                        ['sequence_design', 'Sequence Design', 'FAMPNN / AntiFold / ProteinMPNN'],
                        ['ppiflow', 'PPIFlow', 'Backbone refine or maturation'],
                        ['validation', 'Validation', 'Boltz2 / Protenix / ESMFold2'],
                        ['qc', 'QC + Physics', 'ThermoMPNN / OpenMM / Frustra'],
                    ] as Array<[DeNovoOrchestrationStage, string, string]>).map(([stageKey, label, detail]) => {
                        const disabled = deNovoDownstreamLocked;
                        const enabled = deNovoStageSelection[stageKey];
                        return (
                            <button
                                key={stageKey}
                                type="button"
                                disabled={disabled}
                                onClick={() => {
                                    if (disabled) return;
                                    setDeNovoStageSelection((current) => ({
                                        ...current,
                                        [stageKey]: !current[stageKey],
                                    }));
                                }}
                                className="rounded-lg border p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                                style={enabled ? themedSelectedStyle('var(--accent-secondary)') : themedMutedInsetStyle}
                            >
                                <div className="text-sm font-medium text-[var(--text-primary)]">{label}</div>
                                <div className="mt-1 text-[11px] text-[var(--text-secondary)]">{detail}</div>
                            </button>
                        );
                    })}
                </div>
                {deNovoDownstreamLocked ? (
                    <p className="mt-3 text-[11px] text-[var(--text-secondary)]">
                        Generator-only first pass. Shortlist outputs, then open <span className="font-medium text-[var(--text-primary)]">Antibody Refinement</span> for heavier stages.
                    </p>
                ) : (
                    <p className="mt-3 text-[11px] text-[var(--text-secondary)]">
                        Batch-run-filter first; enable downstream stages only when needed.
                    </p>
                )}
            </div>
        </div>
    ) : null;

    return (
        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <button
                        onClick={onBack}
                        className="p-2 hover:bg-slate-700 rounded-lg transition-colors text-slate-400 hover:text-white"
                    >
                        ← Back
                    </button>
                    <div>
                        <div className="flex items-center gap-2">
                            <h2 className="text-lg font-semibold text-[var(--text-primary)]">
                                {isRefinementMode ? 'Antibody Refinement' : 'De Novo Nanobody Toolkit'}
                            </h2>
                            <span
                                className="rounded-full border px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-[0.12em]"
                                style={isRefinementMode ? themedTagStyle('var(--accent-primary)') : themedMutedInsetStyle}
                            >
                                {isRefinementMode ? 'Refinement Mode' : 'De Novo Mode'}
                            </span>
                        </div>
                        <p className="text-sm text-[var(--text-secondary)]">
                            {isRefinementMode ? `Configuring a modular downstream run for ${refinementInputCount} locked outputs.` : 'Generate de novo nanobody binders and carry selected outputs into modular downstream refinement.'}
                        </p>
                    </div>
                </div>
            </div>

            <ModelDocumentationLinks
                topics={['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2']}
                summary="Generator and validator background is linked out; this launcher keeps controls and review gates up front."
                compact
                className="mb-6"
            />

            {deNovoGeneratorSelector}

            {isRefinementMode && (
                <div
                    className="mb-6 rounded-lg border p-4 animate-in fade-in slide-in-from-top-4"
                    style={themedTagStyle('var(--accent-primary)')}
                >
                    <div className="flex items-center gap-2 mb-2">
                        <svg className="w-5 h-5 text-[var(--accent-primary)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Pipeline source set locked</h3>
                    </div>
                    <p className="max-w-3xl text-xs leading-relaxed text-[var(--text-secondary)]">
                        You arrived here from an active interactive job (<code>{refinementParentJobId}</code>). The pipeline input set is locked to {refinementSavedFilterSetName ? `saved dataset '${refinementSavedFilterSetName}'` : 'the selected structures'}. Configure exactly how you want these {refinementInputCount} outputs to move through antibody refinement below.
                    </p>
                    <p className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">
                        Source set: <span className="font-medium text-[var(--text-primary)]">{refinementSourceLabel}</span>
                        {refinementSavedFilterSetName ? ` • frozen dataset membership` : ''}
                        {refinementSavedFilterSetCreatedLabel ? ` • saved ${refinementSavedFilterSetCreatedLabel}` : ''}
                        {typeof refinementSourceVisibleCount === 'number' ? ` • ${refinementSourceVisibleCount} visible when launched` : ''}
                        {typeof refinementSourceTotalCount === 'number' ? ` • ${refinementSourceTotalCount} total after filters` : ''}
                        {refinementSourceSortField ? ` • sorted by ${refinementSourceSortField} ${refinementSourceSortDir || 'asc'}` : ''}
                    </p>
                </div>
            )}

            {/* Pipeline Visualization */}
            <div className="mb-6 rounded-lg border p-4" style={themedPanelStyle}>
                <div className="flex items-start justify-between gap-4 mb-3">
                    <div>
                        <h3 className="text-sm font-medium text-[var(--text-primary)]">Workflow Pipeline</h3>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                            {isRefinementMode
                                ? 'Selected outputs are re-queued through antibody refinement. Choose which stages to rerun below.'
                                : deNovoGenerator === 'ppiflow'
                                    ? 'Initial PPIFlow pass; refinement opens from shortlisted outputs.'
                                : deNovoGenerator === 'boltzgen'
                                    ? 'Initial BoltzGen pass; refinement opens from shortlisted outputs.'
                                    : 'Initial de novo pass starts generator-only; enable downstream stages above.'}
                        </p>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2 text-[11px]">
                        <span className="rounded-full border px-2.5 py-1" style={themedMutedInsetStyle}>
                            Validator: <span className="font-medium text-[var(--accent-primary)]">{structureValidator === 'protenix' ? 'Protenix' : 'Boltz2'}</span>
                        </span>
                        {interactiveWorkflow && (
                            <span className="rounded-full border px-2.5 py-1" style={themedTagStyle('var(--warning)')}>
                                Review Gate: {deNovoGenerator === 'boltzgen' && !isRefinementMode
                                    ? 'After BoltzGen'
                                    : deNovoGenerator === 'ppiflow' && !isRefinementMode
                                        ? 'After PPIFlow'
                                    : interactiveGateStage === 'post_structure_validation'
                                    ? 'After validation'
                                    : interactiveGateStage === 'post_fampnn'
                                        ? 'After FAMPNN'
                                        : 'After RFantibody'}
                            </span>
                        )}
                    </div>
                </div>
                <div className="flex flex-wrap items-stretch gap-2">
                    {(() => {
                        const steps: Array<{ title: string; detail: string; accent?: string; muted?: boolean; optional?: boolean }> = [
                            {
                                title: isRefinementMode ? 'Selected Inputs' : (deNovoGenerator === 'boltzgen' ? 'BoltzGen' : deNovoGenerator === 'ppiflow' ? 'PPIFlow Seeded' : 'RFantibody'),
                                detail: isRefinementMode
                                    ? 'Reuse selected backbones or re-screen inputs'
                                    : deNovoGenerator === 'boltzgen'
                                        ? 'All-atom nanobody generation'
                                        : deNovoGenerator === 'ppiflow'
                                            ? 'Seeded backbone generation'
                                        : 'Generate backbone ensemble',
                                accent: 'var(--success)',
                            },
                            {
                                title: effectiveSeqDesigner === 'none' ? 'Sequence Design' : effectiveSeqDesigner.toUpperCase(),
                                detail: effectiveSeqDesigner === 'none' ? 'Hidden for the initial generator pass' : 'Sequence redesign + filter',
                                accent: effectiveSeqDesigner === 'none' ? undefined : 'var(--link)',
                                muted: effectiveSeqDesigner === 'none',
                                optional: true,
                            },
                            {
                                title: 'PPIFlow',
                                detail: !anyPpiFlowStageEnabled
                                    ? 'Hidden until enabled'
                                    : runPpiFlowBackboneRefine && runPpiFlowMaturation
                                        ? 'Backbone refinement + post-FA-MPNN maturation'
                                        : runPpiFlowBackboneRefine
                                            ? (effectivePpiFlowStageMode === 'post_ppiflow' ? 'Backbone reattempt from PPIFlow outputs' : 'Backbone refinement after RFantibody')
                                            : 'Maturation after FA-MPNN',
                                accent: anyPpiFlowStageEnabled ? 'var(--accent-secondary)' : undefined,
                                muted: !anyPpiFlowStageEnabled,
                                optional: true,
                            },
                            {
                                title: structureValidator === 'protenix' ? 'Protenix' : 'Boltz2',
                                detail: effectiveRunStructureValidation ? 'Structure validation' : 'Hidden until enabled',
                                accent: effectiveRunStructureValidation ? 'var(--accent-primary)' : undefined,
                                muted: !effectiveRunStructureValidation,
                                optional: true,
                            },
                        ];

                        return steps.map((step, idx) => (
                            <React.Fragment key={step.title}>
                                {idx > 0 && <span className="self-center text-[var(--text-muted)]">-&gt;</span>}
                                <div
                                    className="min-w-[150px] rounded-xl border px-3 py-2"
                                    style={step.muted ? themedMutedInsetStyle : themedSelectedStyle(step.accent || 'var(--accent-primary)')}
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-sm font-semibold text-[var(--text-primary)]">{idx + 1}. {step.title}</span>
                                        {step.optional && (
                                            <span
                                                className="rounded-full border px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
                                                style={{ borderColor: 'color-mix(in srgb, var(--border-primary) 75%, transparent)', color: 'var(--text-secondary)' }}
                                            >
                                                Optional
                                            </span>
                                        )}
                                    </div>
                                    <div className="mt-1 text-[11px] text-[var(--text-secondary)]">{step.detail}</div>
                                </div>
                            </React.Fragment>
                        ));
                    })()}
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                    {interactiveWorkflow && (
                        <span className="rounded-full border px-2.5 py-1" style={themedTagStyle('var(--warning)')}>
                            Interactive review enabled
                        </span>
                    )}
                    {effectiveRunFrustrampnn && (
                        <span className="rounded-full border px-2.5 py-1" style={themedTagStyle('var(--error)')}>
                            FrustraMPNN QC
                        </span>
                    )}
                    {useManualMutagenesis && (
                        <span className="rounded-full border px-2.5 py-1" style={themedTagStyle('var(--success)')}>
                            Manual mutation sets
                        </span>
                    )}
                    {effectiveUseAntiberty && (
                        <span className="rounded-full border px-2.5 py-1" style={themedTagStyle('var(--warning)')}>
                            AntiBERTy scoring
                        </span>
                    )}
                    {effectiveUseThermoMPNN && (
                        <span className="rounded-full border px-2.5 py-1" style={themedTagStyle('var(--accent-primary)')}>
                            ThermoMPNN stability
                        </span>
                    )}
                    {!effectiveRunFrustrampnn && !effectiveUseAntiberty && !effectiveUseThermoMPNN && (
                        <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-1 text-slate-400">
                            No optional QC stages enabled
                        </span>
                    )}
                </div>
            </div>

            {isRefinementMode && (
                <div className="mb-6 rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div>
                            <h3 className="text-sm font-medium text-indigo-200">Refinement Orchestration</h3>
                            <p className="mt-1 text-xs text-slate-400">
                                Use this form as the single approved relaunch path for selected designs. Stage presets below map onto the same workflow orchestrator used for full antibody runs.
                            </p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {([
                                ['full_loop', 'Full Loop'],
                                ['fampnn_only', 'FAMPNN Only'],
                                ['validation_only', structureValidator === 'protenix' ? 'Protenix Only' : 'Boltz2 Only'],
                                ['ppiflow_only', 'PPIFlow Only'],
                                ['manual_mutagenesis', 'Manual Mutagenesis'],
                            ] as Array<[RefinementPreset, string]>).map(([preset, label]) => (
                                <button
                                    key={preset}
                                    type="button"
                                    onClick={() => applyRefinementPreset(preset)}
                                    className={`rounded-lg border px-3 py-2 text-xs transition-colors ${refinementPreset === preset
                                        ? 'border-indigo-400 bg-indigo-500/20 text-indigo-100'
                                        : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                        }`}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-4">
                        <label className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
                            <div className="flex items-center justify-between gap-3">
                                <span>Sequence redesign</span>
                                <input
                                    type="checkbox"
                                    checked={seqDesigner !== 'none'}
                                    onChange={(e) => {
                                        setRefinementPreset('custom');
                                        setUseManualMutagenesis(false);
                                        setSeqDesigner(e.target.checked ? 'fampnn' : 'none');
                                    }}
                                    className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-indigo-500 focus:ring-indigo-500"
                                />
                            </div>
                            <select
                                value={seqDesigner}
                                onChange={(e) => {
                                    const next = e.target.value as SeqDesigner;
                                    setRefinementPreset('custom');
                                    setUseManualMutagenesis(false);
                                    setSeqDesigner(next);
                                }}
                                className="mt-2 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 disabled:opacity-50"
                                disabled={seqDesigner === 'none'}
                            >
                                <option value="fampnn">FAMPNN</option>
                                <option value="caliby">Caliby</option>
                                <option value="antifold">AntiFold</option>
                                <option value="proteinmpnn">ProteinMPNN</option>
                            </select>
                        </label>

                        <label className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
                            <div className="flex items-center justify-between gap-3">
                                <span>PPIFlow stage</span>
                            </div>
                            <select
                                value={ppiflowStageMode}
                                onChange={(e) => {
                                    const next = e.target.value as PPIFlowStageMode;
                                    setRefinementPreset('custom');
                                    setUseManualMutagenesis(false);
                                    setQualitySettings((current) => applyPpiFlowStageMode(current, next));
                                }}
                                className="mt-2 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200"
                            >
                                <option value="off">Off</option>
                                <option value="post_rfantibody" disabled={refinementBlocksImmediatePpiFlowBackbone}>
                                    Backbone refine after RFantibody
                                </option>
                                {refinementSourceIsPpiFlow && (
                                    <option value="post_ppiflow">
                                        Reattempt from PPIFlow output
                                    </option>
                                )}
                                <option value="post_fampnn">Maturation after FA-MPNN</option>
                                <option value="both" disabled={refinementBlocksImmediatePpiFlowBackbone}>
                                    Run both stages
                                </option>
                            </select>
                            <div className="mt-2 text-[11px] text-slate-500">
                                Sequence-free backbone refinement runs before FA-MPNN. Maturation runs after FA-MPNN on sequenced candidates.
                            </div>
                            {refinementSourceIsPpiFlow && (
                                <div className="mt-2 text-[11px] text-amber-300">
                                    This relaunch starts from prior PPIFlow outputs. Use the post-PPIFlow reattempt mode for another sequence-free pass.
                                    Strict anchor requirement defaults off there to avoid the zero-anchor failure seen on recursive backbone-refine launches.
                                </div>
                            )}
                            {refinementBlocksImmediatePpiFlowBackbone && (
                                <div className="mt-2 text-[11px] text-amber-300">
                                    This relaunch starts from downstream outputs, so immediate post-RFantibody PPIFlow backbone refinement is disabled here.
                                    Use sequence design first, then optionally run post-FA-MPNN PPIFlow maturation.
                                </div>
                            )}
                            {anyPpiFlowStageEnabled && (
                                <div className="mt-2 text-[11px] text-teal-300">
                                    Orchestrated child jobs • {qualitySettings.maturation_designs_per_job} PDB{qualitySettings.maturation_designs_per_job === 1 ? '' : 's'} per PPIFlow child
                                </div>
                            )}
                        </label>

                        <label className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
                            <div className="flex items-center justify-between gap-3">
                                <span>Structure validation</span>
                                <input
                                    type="checkbox"
                                    checked={runStructureValidation}
                                    onChange={(e) => {
                                        setRefinementPreset('custom');
                                        setUseManualMutagenesis(false);
                                        setRunStructureValidation(e.target.checked);
                                    }}
                                    className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-indigo-500 focus:ring-indigo-500"
                                />
                            </div>
                            <select
                                value={structureValidator}
                                onChange={(e) => {
                                    setRefinementPreset('custom');
                                    setStructureValidator(e.target.value as 'boltz2' | 'protenix');
                                }}
                                className="mt-2 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 disabled:opacity-50"
                                disabled={!runStructureValidation}
                            >
                                <option value="boltz2">Boltz-2</option>
                                <option value="protenix">Protenix</option>
                            </select>
                        </label>

                        <label className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
                            <div className="flex items-center justify-between gap-3">
                                <span>FrustraMPNN QC</span>
                                <input
                                    type="checkbox"
                                    checked={runFrustrampnn}
                                    onChange={(e) => {
                                        setRefinementPreset('custom');
                                        setUseManualMutagenesis(false);
                                        setRunFrustrampnn(e.target.checked);
                                    }}
                                    className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-indigo-500 focus:ring-indigo-500"
                                />
                            </div>
                            <div className="mt-2 text-[11px] text-slate-500">
                                Optional QC pass after structure generation.
                            </div>
                        </label>
                    </div>

                    <div className="mt-4 rounded-lg border border-emerald-500/20 bg-slate-950/70 p-3">
                        <label className="flex items-center justify-between gap-3 text-xs text-emerald-200">
                            <span>Mutation methodology</span>
                            <input
                                type="checkbox"
                                checked={useManualMutagenesis}
                                onChange={(e) => {
                                    const enabled = e.target.checked;
                                    setUseManualMutagenesis(enabled);
                                    setRefinementPreset(enabled ? 'manual_mutagenesis' : 'custom');
                                    if (enabled) {
                                        setMutagenesisLaunchMode('seeded_refinement');
                                    }
                                }}
                                className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-emerald-500"
                            />
                        </label>
                        <p className="mt-1 text-[11px] text-slate-500">
                            Launch manual sequence variants from this workflow UI. Substitutions keep sequence length fixed. CDR indels change loop length and rely on the predictor to rebuild the resulting backbone.
                        </p>
                        {useManualMutagenesis && (
                            <div className="mt-3 space-y-4">
                                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                                    <button
                                        type="button"
                                        onClick={() => setMutagenesisLaunchMode('seeded_refinement')}
                                        className={`rounded-lg border px-3 py-2 text-left transition-colors ${mutagenesisLaunchMode === 'seeded_refinement'
                                            ? 'border-cyan-400 bg-cyan-400/10 text-cyan-200'
                                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="text-sm font-medium">Mutation-Seeded Refinement</div>
                                        <div className="mt-1 text-[11px] text-slate-400">
                                            Use the manual variants as new workflow seeds, then continue through the selected refinement stages like FAMPNN, PPIFlow, and validation.
                                        </div>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setMutagenesisLaunchMode('exact_evaluation')}
                                        className={`rounded-lg border px-3 py-2 text-left transition-colors ${mutagenesisLaunchMode === 'exact_evaluation'
                                            ? 'border-amber-400 bg-amber-400/10 text-amber-200'
                                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="text-sm font-medium">Exact Mutant Evaluation</div>
                                        <div className="mt-1 text-[11px] text-slate-400">
                                            Evaluate the exact manual variants directly with Protenix or Boltz-2. This bypasses the antibody refinement orchestrator.
                                        </div>
                                    </button>
                                </div>

                                <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 text-[11px] text-slate-400">
                                    {mutagenesisLaunchMode === 'seeded_refinement'
                                        ? 'Seeded refinement locks requested residues, rebuilds indel seeds, then relaunches refinement.'
                                        : 'Exact evaluation checks the requested mutant directly; it is not a redesign round.'}
                                </div>

                                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                                    <button
                                        type="button"
                                        onClick={() => setMutagenesisMethod('explicit_substitutions')}
                                        className={`rounded-lg border px-3 py-2 text-left transition-colors ${mutagenesisMethod === 'explicit_substitutions'
                                            ? 'border-emerald-400 bg-emerald-400/10 text-emerald-200'
                                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="text-sm font-medium">Explicit substitutions</div>
                                        <div className="mt-1 text-[11px] text-slate-400">
                                            Apply manual residue substitutions like <span className="font-mono">A27Y</span>. This preserves sequence length and does not insert or delete residues.
                                        </div>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setMutagenesisMethod('cdr_indels')}
                                        className={`rounded-lg border px-3 py-2 text-left transition-colors ${mutagenesisMethod === 'cdr_indels'
                                            ? 'border-fuchsia-400 bg-fuchsia-400/10 text-fuchsia-200'
                                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="text-sm font-medium">CDR indels</div>
                                        <div className="mt-1 text-[11px] text-slate-400">
                                            Insert and delete residues within selected CDR loops, then regenerate structure. This is the backbone-changing mutagenesis path.
                                        </div>
                                    </button>
                                </div>

                                {mutagenesisMethod === 'explicit_substitutions' ? (
                                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                                        <label className="text-xs text-slate-400">
                                            Binder chain ID (optional)
                                            <input
                                                type="text"
                                                value={manualMutagenesisConfig.chain_id}
                                                onChange={(e) => setManualMutagenesisConfig((current) => ({ ...current, chain_id: e.target.value }))}
                                                placeholder={isSingleDomainFramework ? 'H' : 'H or L'}
                                                className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                            />
                                        </label>
                                        <label className="text-xs text-slate-400">
                                            Predictor
                                            <select
                                                value={manualMutagenesisConfig.predictor}
                                                onChange={(e) => setManualMutagenesisConfig((current) => ({ ...current, predictor: e.target.value as 'protenix' | 'boltz2' }))}
                                                className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                            >
                                                <option value="protenix">Protenix</option>
                                                <option value="boltz2">Boltz-2</option>
                                            </select>
                                        </label>
                                        <label className="text-xs text-slate-400">
                                            MSA Provider
                                            <select
                                                value={manualMutagenesisConfig.msa_provider}
                                                onChange={(e) => setManualMutagenesisConfig((current) => ({ ...current, msa_provider: e.target.value as 'local' | 'colabfold_api' }))}
                                                className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                            >
                                                <option value="local">Local</option>
                                                <option value="colabfold_api">ColabFold Server</option>
                                            </select>
                                        </label>
                                        <label className="lg:col-span-3 text-xs text-slate-400">
                                            Mutation sets
                                            <textarea
                                                value={manualMutagenesisConfig.mutation_sets_text}
                                                onChange={(e) => setManualMutagenesisConfig((current) => ({ ...current, mutation_sets_text: e.target.value }))}
                                                rows={5}
                                                placeholder={"A27Y,H31W\nS52R"}
                                                className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none font-mono"
                                            />
                                            <div className="mt-1 text-[11px] text-slate-500">
                                                One variant per line. This path supports substitutions only. It does not add residues to the existing output PDB; it edits sequence and sends the new sequence back through the predictor.
                                            </div>
                                        </label>
                                    </div>
                                ) : (
                                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                        <div className="space-y-4">
                                            <div>
                                                <div className="text-xs text-slate-400 mb-2">Target loops</div>
                                                <div className="flex flex-wrap gap-2">
                                                    {availableMutagenesisLoops.map((loopId) => {
                                                        const selected = cdrIndelConfig.loop_ids.includes(loopId);
                                                        return (
                                                            <button
                                                                key={loopId}
                                                                type="button"
                                                                onClick={() => {
                                                                    setCdrIndelConfig((current) => {
                                                                        const next = new Set(current.loop_ids);
                                                                        if (next.has(loopId)) next.delete(loopId);
                                                                        else next.add(loopId);
                                                                        return { ...current, loop_ids: Array.from(next).sort() };
                                                                    });
                                                                }}
                                                                className={`rounded-lg border px-3 py-2 text-xs transition-colors ${selected
                                                                    ? 'border-fuchsia-400 bg-fuchsia-400/10 text-fuchsia-200'
                                                                    : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                                                    }`}
                                                            >
                                                                {loopId}
                                                            </button>
                                                        );
                                                    })}
                                                </div>
                                                <p className="mt-2 text-[11px] text-slate-500">
                                                    {isSingleDomainFramework
                                                        ? 'Single-domain refinement limits indels to H1/H2/H3.'
                                                        : 'Keep loop edits within one chain family per round when possible so variant generation stays interpretable.'}
                                                </p>
                                            </div>

                                            <div className="grid grid-cols-2 gap-3">
                                                <label className="text-xs text-slate-400">
                                                    Variants / design
                                                    <input
                                                        type="number"
                                                        min={1}
                                                        max={200}
                                                        value={cdrIndelConfig.variants_per_design}
                                                        onChange={(e) => setCdrIndelConfig((current) => ({
                                                            ...current,
                                                            variants_per_design: Math.max(1, Math.min(200, Number(e.target.value) || 1)),
                                                        }))}
                                                        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                    />
                                                </label>
                                                <label className="text-xs text-slate-400">
                                                    Indel sizes
                                                    <input
                                                        type="text"
                                                        value={cdrIndelConfig.indel_sizes.join(',')}
                                                        onChange={(e) => {
                                                            const sizes = e.target.value
                                                                .split(',')
                                                                .map((token) => Number(token.trim()))
                                                                .filter((value) => Number.isFinite(value) && value > 0)
                                                                .map((value) => Math.floor(value));
                                                            setCdrIndelConfig((current) => ({
                                                                ...current,
                                                                indel_sizes: sizes.length > 0 ? Array.from(new Set(sizes)).sort((a, b) => a - b) : [1],
                                                            }));
                                                        }}
                                                        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                        placeholder="1,2"
                                                    />
                                                </label>
                                            </div>

                                            <div className="grid grid-cols-2 gap-3">
                                                <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300">
                                                    <input
                                                        type="checkbox"
                                                        checked={cdrIndelConfig.allow_insertions}
                                                        onChange={(e) => setCdrIndelConfig((current) => ({ ...current, allow_insertions: e.target.checked }))}
                                                        className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-fuchsia-500"
                                                    />
                                                    Allow insertions
                                                </label>
                                                <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300">
                                                    <input
                                                        type="checkbox"
                                                        checked={cdrIndelConfig.allow_deletions}
                                                        onChange={(e) => setCdrIndelConfig((current) => ({ ...current, allow_deletions: e.target.checked }))}
                                                        className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-fuchsia-500"
                                                    />
                                                    Allow deletions
                                                </label>
                                            </div>

                                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                                <label className="text-xs text-slate-400">
                                                    Allowed insertion amino acids
                                                    <input
                                                        type="text"
                                                        value={(cdrIndelConfig.allowed_aas || []).join('')}
                                                        onChange={(e) => {
                                                            const aas = Array.from(new Set(
                                                                e.target.value.toUpperCase().replace(/[^A-Z]/g, '').split('')
                                                            )).filter((aa) => 'ACDEFGHIKLMNPQRSTVWY'.includes(aa));
                                                            setCdrIndelConfig((current) => ({ ...current, allowed_aas: aas }));
                                                        }}
                                                        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                        placeholder="Leave blank for full set"
                                                    />
                                                </label>
                                                <label className="text-xs text-slate-400">
                                                    Excluded insertion amino acids
                                                    <input
                                                        type="text"
                                                        value={(cdrIndelConfig.blocked_aas || []).join('')}
                                                        onChange={(e) => {
                                                            const aas = Array.from(new Set(
                                                                e.target.value.toUpperCase().replace(/[^A-Z]/g, '').split('')
                                                            )).filter((aa) => 'ACDEFGHIKLMNPQRSTVWY'.includes(aa));
                                                            setCdrIndelConfig((current) => ({ ...current, blocked_aas: aas }));
                                                        }}
                                                        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                        placeholder="Optional"
                                                    />
                                                </label>
                                            </div>
                                        </div>

                                        <div className="space-y-4">
                                            <div className="grid grid-cols-2 gap-3">
                                                <label className="text-xs text-slate-400">
                                                    Predictor
                                                    <select
                                                        value={cdrIndelConfig.predictor}
                                                        onChange={(e) => setCdrIndelConfig((current) => ({
                                                            ...current,
                                                            predictor: e.target.value === 'boltz2' ? 'boltz2' : 'protenix',
                                                        }))}
                                                        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                    >
                                                        <option value="protenix">Protenix</option>
                                                        <option value="boltz2">Boltz-2</option>
                                                    </select>
                                                </label>
                                                <label className="text-xs text-slate-400">
                                                    MSA provider
                                                    <select
                                                        value={cdrIndelConfig.msa_provider}
                                                        onChange={(e) => setCdrIndelConfig((current) => ({
                                                            ...current,
                                                            msa_provider: e.target.value === 'colabfold_api' ? 'colabfold_api' : 'local',
                                                        }))}
                                                        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                    >
                                                        <option value="local">Local</option>
                                                        <option value="colabfold_api">ColabFold API</option>
                                                    </select>
                                                </label>
                                            </div>

                                            <label className="block text-xs text-slate-400">
                                                Indel probability
                                                <input
                                                    type="number"
                                                    min={0}
                                                    max={1}
                                                    step={0.05}
                                                    value={cdrIndelConfig.indel_probability}
                                                    onChange={(e) => setCdrIndelConfig((current) => ({
                                                        ...current,
                                                        indel_probability: Math.max(0, Math.min(1, Number(e.target.value) || 0)),
                                                    }))}
                                                    className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                />
                                            </label>

                                            <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 text-xs text-slate-400">
                                                <div className="text-slate-200 font-medium mb-1">Launch summary</div>
                                                <div>{refinementInputCount} input design{refinementInputCount === 1 ? '' : 's'}</div>
                                                <div>{cdrIndelConfig.variants_per_design} variant{cdrIndelConfig.variants_per_design === 1 ? '' : 's'} per design</div>
                                                <div>{cdrIndelConfig.loop_ids.join(', ') || 'No loops selected'}</div>
                                                <div className="mt-1 text-fuchsia-200">
                                                    {refinementInputCount * cdrIndelConfig.variants_per_design} total variant predictions
                                                </div>
                                                {cdrIndelConfig.msa_provider === 'colabfold_api' && refinementInputCount * cdrIndelConfig.variants_per_design > 1 && (
                                                    <div className="mt-2 text-amber-300">
                                                        Multi-variant indel rounds are automatically downgraded to local MSA.
                                                    </div>
                                                )}
                                            </div>

                                            <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 text-[11px] text-slate-500">
                                                The workflow does not splice residues directly into the existing output PDB. It edits the binder sequence, preserves the other chains, then asks the selected predictor to rebuild the complex for that new sequence.
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Form - 2 Column Layout */}
            <div className="grid grid-cols-2 gap-8">
                {/* LEFT COLUMN: Target & Epitope Selection */}
                <div className="space-y-5">
                    {/* Job Name & GPU Pinning */}
                    <div className="flex gap-6">
                        <div className="flex-1">
                            <label className="mb-2 block text-sm font-medium text-[var(--text-secondary)]">Job Name</label>
                            <input
                                type="text"
                                value={jobName}
                                onChange={(e) => setJobName(e.target.value)}
                                className="w-full rounded-lg border px-4 py-2.5 text-[var(--text-primary)] outline-none focus:ring-2"
                                style={{ backgroundColor: 'var(--bg-tertiary)', borderColor: 'var(--border-primary)', caretColor: 'var(--accent-primary)' }}
                                placeholder="antibody_design"
                            />
                        </div>
                        <div>
                            <label className="mb-2 block text-sm font-medium text-[var(--text-secondary)]">
                                GPU Pinning {pinnedGpus.length > 0 && <span className="text-[var(--accent-primary)]">({pinnedGpus.length} selected)</span>}
                            </label>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setPinnedGpus([])}
                                    className="rounded-lg border px-3 py-2 text-sm font-medium transition-all"
                                    style={pinnedGpus.length === 0 ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                                >
                                    Auto
                                </button>
                                {gpuOptions.map(gpu => (
                                    <button
                                        key={gpu.index}
                                        onClick={() => {
                                            setPinnedGpus(prev =>
                                                prev.includes(gpu.index)
                                                    ? prev.filter(g => g !== gpu.index)
                                                    : [...prev, gpu.index].sort((a, b) => a - b)
                                            );
                                        }}
                                        className="rounded-lg border px-3 py-2 text-sm font-medium transition-all"
                                        style={pinnedGpus.includes(gpu.index) ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                                    >
                                        {gpu.label}
                                    </button>
                                ))}
                            </div>
                            {pinnedGpus.length > 0 && (
                                <label className="flex items-center gap-2 mt-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={lockGpus}
                                        onChange={e => setLockGpus(e.target.checked)}
                                        className="h-4 w-4 rounded"
                                        style={{ borderColor: 'var(--border-primary)', backgroundColor: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}
                                    />
                                    <span className="text-sm text-[var(--text-secondary)]">Lock selected GPU(s) exclusively during workflow</span>
                                </label>
                            )}
                        </div>
                    </div>

                    {/* Target PDB Selection - Now with multiple sources */}
                    {!isRefinementMode && (
                        <TargetAntigenSelector
                            onSelect={async (target) => {
                                const loadToken = targetLoadControllerRef.current.begin();
                                if (target) {
                                    if (target.type === 'upload' && target.file) {
                                        if (!targetLoadControllerRef.current.isCurrent(loadToken)) return;
                                        setTargetPdb(target.file);
                                        setTargetSource({ type: 'upload' });
                                    } else if (target.url) {
                                        setTargetSource({
                                            type: target.type,
                                            url: target.url,
                                            path: target.path,
                                            designId: target.designId,
                                            pdbId: target.pdbId
                                        });
                                        try {
                                            const res = await fetch(target.url);
                                            const blob = await res.blob();
                                            if (!targetLoadControllerRef.current.isCurrent(loadToken)) return;
                                            const file = new File([blob], target.name + '.pdb', { type: 'chemical/x-pdb' });
                                            setTargetPdb(file);
                                        } catch (err) {
                                            if (!targetLoadControllerRef.current.isCurrent(loadToken)) return;
                                            console.error('[ANTIBODY_DENOVO] Failed to fetch PDB:', err);
                                            alert('Failed to load PDB from source');
                                        }
                                    }
                                } else if (targetLoadControllerRef.current.isCurrent(loadToken)) {
                                    setTargetPdb(null);
                                    setTargetSource(null);
                                }
                            }}
                            selectedTarget={targetPdb ? { type: (targetSource?.type || 'upload') as 'upload' | 'run' | 'preset' | 'rcsb', name: targetPdb.name } : undefined}
                        />
                    )}

                    {!isRefinementMode && deNovoGenerator === 'boltzgen' ? (
                        <div className="space-y-5">
                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                <div className="flex items-center justify-between mb-3">
                                    <div>
                                        <h3 className="text-sm font-semibold text-slate-200">Antibody Framework</h3>
                                        <p className="text-xs text-slate-500 mt-1">
                                            Keep the same antibody shell as RFantibody, but drive the generator with native BoltzGen nanobody scaffold or sequence-template inputs.
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <button
                                            type="button"
                                            onClick={handleBoltzgenPreview}
                                            disabled={boltzgenPreviewMutation.isPending}
                                            className="rounded-lg border px-2.5 py-1 text-[11px] transition-colors disabled:opacity-50"
                                            style={themedInsetStyle}
                                        >
                                            {boltzgenPreviewMutation.isPending ? 'Previewing...' : 'Preview Spec'}
                                        </button>
                                        <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] text-amber-200">
                                            Core Step
                                        </span>
                                    </div>
                                </div>

                                <div className="grid grid-cols-2 gap-3 mb-4">
                                    <button
                                        type="button"
                                        onClick={() => setBoltzgenUseFrameworkTemplate(true)}
                                        className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                        style={boltzgenUseFrameworkTemplate ? themedSelectedStyle('var(--warning)') : themedInsetStyle}
                                    >
                                        Scaffold / Template
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setBoltzgenUseFrameworkTemplate(false)}
                                        className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                        style={!boltzgenUseFrameworkTemplate ? themedSelectedStyle('var(--accent-secondary)') : themedInsetStyle}
                                    >
                                        Full De Novo
                                    </button>
                                </div>

                                {boltzgenUseFrameworkTemplate && (
                                    <div className="mb-4 space-y-3">
                                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                            {[
                                                ['default_ensemble', 'Default Ensemble', `${DEFAULT_BOLTZGEN_ENSEMBLE.join(', ')}`],
                                                ['selected_scaffold', 'Selected Scaffold', 'One SAbDab/custom framework'],
                                                ['sequence_template', 'Sequence Template', 'Legacy editable VHH sequence'],
                                            ].map(([id, label, detail]) => (
                                                <button
                                                    key={id}
                                                    type="button"
                                                    onClick={() => setBoltzgenScaffoldSource(id as BoltzgenScaffoldSource)}
                                                    className="rounded-lg border px-3 py-2 text-left text-sm transition-colors"
                                                    style={boltzgenScaffoldSource === id ? themedSelectedStyle('var(--warning)') : themedInsetStyle}
                                                >
                                                    <div className="font-medium text-slate-200">{label}</div>
                                                    <div className="text-[11px] text-slate-500">{detail}</div>
                                                </button>
                                            ))}
                                        </div>

                                        {boltzgenScaffoldSource === 'default_ensemble' && (
                                            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-[11px] text-slate-400">
                                                Curated nanobody ensemble. The backend hydrates scaffold-backed specs from {DEFAULT_BOLTZGEN_ENSEMBLE.join(', ')} and preserves their framework geometry while replacing the CDR loops.
                                            </div>
                                        )}

                                        {boltzgenScaffoldSource === 'selected_scaffold' && (
                                            <div>
                                                <div className="mb-2 flex items-center justify-between gap-3">
                                                    <div className="text-xs text-slate-500">SAbDab scaffold source</div>
                                                    <button
                                                        type="button"
                                                        onClick={() => setShowBoltzgenFrameworkBrowser((current) => !current)}
                                                        className="rounded-lg border px-2.5 py-1 text-[11px] transition-colors"
                                                        style={showBoltzgenFrameworkBrowser ? themedSelectedStyle('var(--warning)') : themedInsetStyle}
                                                    >
                                                        {showBoltzgenFrameworkBrowser ? 'Hide SAbDab Browser' : 'Browse SAbDab'}
                                                    </button>
                                                </div>

                                                <label className="mb-3 flex items-center gap-2 text-xs text-slate-400">
                                                    <input
                                                        type="checkbox"
                                                        checked={boltzgenViewReferenceStructure}
                                                        onChange={(e) => setBoltzgenViewReferenceStructure(e.target.checked)}
                                                        className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-amber-500"
                                                    />
                                                    Open the selected reference structure in the framework viewer
                                                </label>

                                                {sabdabFramework?.pdbCode && (
                                                    <div className="mb-3 flex items-center gap-2 text-xs">
                                                        <span className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                                                            {sabdabFramework.pdbCode}
                                                        </span>
                                                        <button
                                                            type="button"
                                                            onClick={() => {
                                                                setSabdabFramework(null);
                                                                setBoltzgenNanobodyFramework(DEFAULT_BOLTZGEN_VHH_FRAMEWORK);
                                                                replaceFrameworkPdbUrl(null);
                                                                setParsedFrameworkChains([]);
                                                                if (viewerMode === 'framework') {
                                                                    setShow3DViewer(false);
                                                                }
                                                            }}
                                                            className="text-slate-400 hover:text-red-400"
                                                        >
                                                            Clear
                                                        </button>
                                                    </div>
                                                )}

                                                {showBoltzgenFrameworkBrowser && (
                                                    <div className="mb-3 rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                                                        <FrameworkBrowser
                                                            onSelect={(framework) => {
                                                                const scaffoldSelection = deriveBoltzgenScaffoldSelectionUpdate({
                                                                    framework,
                                                                    viewReferenceStructure: boltzgenViewReferenceStructure,
                                                                });
                                                                setSabdabFramework(framework);
                                                                if (scaffoldSelection.nextFrameworkSequence) {
                                                                    setBoltzgenNanobodyFramework(scaffoldSelection.nextFrameworkSequence);
                                                                }
                                                                if (scaffoldSelection.nextCdrH3Length) {
                                                                    setBoltzgenCdrH3Length(scaffoldSelection.nextCdrH3Length);
                                                                }
                                                                if (scaffoldSelection.shouldOpenReferencePreview && scaffoldSelection.referencePdbUrl) {
                                                                    replaceFrameworkPdbUrl(scaffoldSelection.referencePdbUrl);
                                                                    setViewerMode('framework');
                                                                    setShow3DViewer(true);
                                                                } else {
                                                                    replaceFrameworkPdbUrl(null);
                                                                    setParsedFrameworkChains([]);
                                                                    if (viewerMode === 'framework') {
                                                                        setShow3DViewer(false);
                                                                    }
                                                                }
                                                                setShowBoltzgenFrameworkBrowser(false);
                                                            }}
                                                            selectedFramework={sabdabFramework}
                                                            showCustomUpload={false}
                                                        />
                                                    </div>
                                                )}
                                            </div>
                                        )}

                                        {boltzgenScaffoldSource === 'sequence_template' && (
                                            <label className="block text-xs text-slate-500">
                                                VHH framework sequence
                                                <textarea
                                                    value={boltzgenNanobodyFramework}
                                                    onChange={(e) => setBoltzgenNanobodyFramework(e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                                                    rows={3}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-amber-500 outline-none"
                                                />
                                            </label>
                                        )}
                                    </div>
                                )}
                                <p className="mt-2 text-[11px] text-slate-500">
                                    The target chain and selected epitope residues above feed the same batch-run-filter loop, but BoltzGen now owns the initial generator spec, filtering, and checkpoint settings in this section.
                                </p>
                            </div>

                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                <div className="mb-3">
                                    <h3 className="text-sm font-semibold text-slate-200">CDR Loop Length Variability</h3>
                                    <p className="text-xs text-slate-500 mt-1">
                                        Configure the BoltzGen nanobody loop search space for the initial batch generation.
                                    </p>
                                </div>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                                    {([
                                        ['CDR-H1', boltzgenCdrH1Length, setBoltzgenCdrH1Length],
                                        ['CDR-H2', boltzgenCdrH2Length, setBoltzgenCdrH2Length],
                                        ['CDR-H3', boltzgenCdrH3Length, setBoltzgenCdrH3Length],
                                    ] as Array<[string, string, React.Dispatch<React.SetStateAction<string>>]>).map(([label, value, setter]) => (
                                        <label key={label} className="text-xs text-slate-500">
                                            {label}
                                            <input
                                                type="text"
                                                value={value}
                                                onChange={(e) => setter(e.target.value)}
                                                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-amber-500 outline-none"
                                                placeholder="e.g. 5-8"
                                            />
                                        </label>
                                    ))}
                                </div>
                                <div className="mt-4">
                                    <label className="text-xs text-slate-500">
                                        Nanobody size envelope
                                        <input
                                            type="text"
                                            value={boltzgenScaffoldLength}
                                            onChange={(e) => setBoltzgenScaffoldLength(e.target.value)}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-amber-500 outline-none"
                                            placeholder="e.g. 100-135"
                                        />
                                    </label>
                                </div>
                                <p className="mt-2 text-[11px] text-slate-500">
                                    Keep the initial batch broad enough to seed a useful refinement set. Most campaigns should leave downstream modules off here, rank the batch outputs, then continue with the selected subset.
                                </p>
                            </div>

                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                <div className="mb-3">
                                    <h3 className="text-sm font-semibold text-slate-200">BoltzGen Controls</h3>
                                    <p className="text-xs text-slate-500 mt-1">
                                        Late-alpha runtime controls wired directly into the BoltzGen launcher and filter pass.
                                    </p>
                                </div>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                    <label className="text-xs text-slate-500">
                                        Checkpoint mode
                                        <select
                                            value={boltzgenCheckpointMode}
                                            onChange={(e) => setBoltzgenCheckpointMode(e.target.value as BoltzgenCheckpointMode)}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        >
                                            <option value="both">Both checkpoints</option>
                                            <option value="diverse">Diverse only</option>
                                            <option value="adherence">Adherence only</option>
                                        </select>
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Inverse-fold sequences
                                        <input
                                            type="number"
                                            min={1}
                                            value={boltzgenInverseFoldNumSequences}
                                            onChange={(e) => setBoltzgenInverseFoldNumSequences(Math.max(1, Number(e.target.value) || 1))}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Inverse-fold avoid AA set
                                        <input
                                            type="text"
                                            value={boltzgenInverseFoldAvoid}
                                            onChange={(e) => setBoltzgenInverseFoldAvoid(e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                                            placeholder="e.g. CM"
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Diversity budget
                                        <input
                                            type="number"
                                            min={1}
                                            value={boltzgenBudget}
                                            onChange={(e) => setBoltzgenBudget(e.target.value ? Math.max(1, Number(e.target.value) || 1) : '')}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Step scale
                                        <input
                                            type="number"
                                            min={0}
                                            step={0.01}
                                            value={boltzgenStepScale}
                                            onChange={(e) => setBoltzgenStepScale(e.target.value ? Number(e.target.value) : '')}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Noise scale
                                        <input
                                            type="number"
                                            min={0}
                                            step={0.01}
                                            value={boltzgenNoiseScale}
                                            onChange={(e) => setBoltzgenNoiseScale(e.target.value ? Number(e.target.value) : '')}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Min pLDDT
                                        <input
                                            type="number"
                                            min={0}
                                            max={100}
                                            step={1}
                                            value={boltzgenMinPlddt}
                                            onChange={(e) => setBoltzgenMinPlddt(e.target.value ? Number(e.target.value) : '')}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Max refold RMSD
                                        <input
                                            type="number"
                                            min={0}
                                            step={0.1}
                                            value={boltzgenMaxRmsd}
                                            onChange={(e) => setBoltzgenMaxRmsd(e.target.value ? Number(e.target.value) : '')}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Min confidence score
                                        <input
                                            type="number"
                                            min={0}
                                            max={1}
                                            step={0.01}
                                            value={boltzgenMinConfScore}
                                            onChange={(e) => setBoltzgenMinConfScore(e.target.value ? Number(e.target.value) : '')}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Metrics override
                                        <input
                                            type="text"
                                            value={boltzgenMetricsOverride}
                                            onChange={(e) => setBoltzgenMetricsOverride(e.target.value)}
                                            placeholder="plddt=none filter_rmsd=none"
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500 sm:col-span-2">
                                        Additional filters
                                        <input
                                            type="text"
                                            value={boltzgenAdditionalFilters}
                                            onChange={(e) => setBoltzgenAdditionalFilters(e.target.value)}
                                            placeholder="affinity_probability>0.8 design_ptm>=0.75"
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500 sm:col-span-2">
                                        Size buckets
                                        <input
                                            type="text"
                                            value={boltzgenSizeBuckets}
                                            onChange={(e) => setBoltzgenSizeBuckets(e.target.value)}
                                            placeholder="1-5:4 6-10:8 11-18:8"
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-amber-500"
                                        />
                                    </label>
                                </div>
                                <div className="mt-4 flex flex-wrap gap-4">
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={boltzgenSkipInverseFolding}
                                            onChange={(e) => setBoltzgenSkipInverseFolding(e.target.checked)}
                                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-amber-500"
                                        />
                                        Skip inverse folding
                                    </label>
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={boltzgenAvoidCysteine}
                                            onChange={(e) => setBoltzgenAvoidCysteine(e.target.checked)}
                                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-amber-500"
                                        />
                                        Avoid cysteine
                                    </label>
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={boltzgenFilterBiased}
                                            onChange={(e) => setBoltzgenFilterBiased(e.target.checked)}
                                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-amber-500"
                                        />
                                        Filter biased compositions
                                    </label>
                                </div>
                            </div>

                            {showBoltzgenPreview && boltzgenPreview && (
                                <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                    <div className="mb-3 flex items-center justify-between gap-3">
                                        <div>
                                            <h3 className="text-sm font-semibold text-slate-200">BoltzGen Preflight</h3>
                                            <p className="text-xs text-slate-500 mt-1">
                                                Generated design spec plus `boltzgen check` status from the current nanobody settings.
                                            </p>
                                        </div>
                                        <span className={`rounded-full px-2.5 py-1 text-[11px] ${boltzgenPreview.check_ok ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border border-amber-500/30 bg-amber-500/10 text-amber-200'}`}>
                                            {boltzgenPreview.check_ok ? 'Check Passed' : 'Check Pending / Warn'}
                                        </span>
                                    </div>
                                    {boltzgenPreview.notes?.length > 0 && (
                                        <div className="mb-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-[11px] text-slate-400">
                                            {boltzgenPreview.notes.join(' | ')}
                                        </div>
                                    )}
                                    {boltzgenPreview.scaffold_specs?.length > 0 && (
                                        <div className="mb-3 flex flex-wrap gap-2 text-[11px]">
                                            {boltzgenPreview.scaffold_specs.map((spec, index) => (
                                                <span key={`${spec.name || spec.path || index}`} className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                                                    {spec.name || spec.path || `scaffold_${index + 1}`}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    <pre className="max-h-80 overflow-auto rounded-lg border border-slate-700 bg-slate-950/80 p-3 text-[11px] text-slate-200">{boltzgenPreview.yaml_text}</pre>
                                    {(boltzgenPreview.check_stdout || boltzgenPreview.check_stderr) && (
                                        <pre className="mt-3 max-h-48 overflow-auto rounded-lg border border-slate-700 bg-slate-950/80 p-3 text-[11px] text-slate-400">{[boltzgenPreview.check_stdout, boltzgenPreview.check_stderr].filter(Boolean).join('\n\n')}</pre>
                                    )}
                                </div>
                            )}
                        </div>
                    ) : !isRefinementMode && deNovoGenerator === 'ppiflow' ? (
                        <div className="space-y-5">
                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                <div className="mb-3 flex items-center justify-between gap-3">
                                    <div>
                                        <h3 className="text-sm font-semibold text-slate-200">Seeded PPIFlow Generator</h3>
                                        <p className="text-xs text-slate-500 mt-1">
                                            Start from existing antibody-target complexes, run seeded partial-flow backbone generation, then reopen shortlisted outputs in Antibody Refinement.
                                        </p>
                                    </div>
                                    <span className="rounded-full border border-teal-500/30 bg-teal-500/10 px-2.5 py-1 text-[11px] text-teal-200">
                                        Core Step
                                    </span>
                                </div>

                                <div className="space-y-3">
                                    <label className="block text-xs text-slate-500">
                                        Seed complex directory
                                        <input
                                            type="text"
                                            value={ppiflowSeedInputDir}
                                            onChange={(e) => setPpiflowSeedInputDir(e.target.value)}
                                            placeholder="/path/to/seed_complexes"
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500 font-mono"
                                        />
                                    </label>

                                    <div className="rounded-lg border border-slate-700/60 bg-slate-950/40 p-3">
                                        <div className="text-xs text-slate-500">Or upload one seed complex PDB</div>
                                        <input
                                            type="file"
                                            accept=".pdb,.cif,.mmcif"
                                            onChange={(e) => {
                                                const file = e.target.files?.[0] || null;
                                                setPpiflowSeedComplexFile(file);
                                                if (file) {
                                                    setPpiflowSeedComplexPath(null);
                                                }
                                            }}
                                            className="mt-2 block w-full text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-teal-500/20 file:px-3 file:py-2 file:text-xs file:font-medium file:text-teal-100 hover:file:bg-teal-500/30"
                                        />
                                        {(ppiflowSeedComplexFile || ppiflowSeedComplexPath) && (
                                            <div className="mt-2 text-[11px] text-slate-400">
                                                {ppiflowSeedComplexFile
                                                    ? `Queued upload: ${ppiflowSeedComplexFile.name}`
                                                    : `Resolved seed path: ${ppiflowSeedComplexPath}`}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                <p className="mt-3 text-[11px] text-slate-500">
                                    Seed complexes should already contain both antibody and antigen chains. The target structure and hotspots above remain useful context for campaign setup, but the generator itself runs from the seeded complex set.
                                </p>
                            </div>

                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                <div className="mb-3">
                                    <h3 className="text-sm font-semibold text-slate-200">Chain Roles</h3>
                                    <p className="text-xs text-slate-500 mt-1">
                                        Tell PPIFlow which chains belong to the binder versus the antigen inside the seed complexes.
                                    </p>
                                </div>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                    <label className="text-xs text-slate-500">
                                        Antibody chain(s)
                                        <input
                                            type="text"
                                            value={ppiflowSeedAntibodyChains}
                                            onChange={(e) => setPpiflowSeedAntibodyChains(e.target.value.toUpperCase())}
                                            placeholder="H"
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Antigen chain(s)
                                        <input
                                            type="text"
                                            value={ppiflowSeedAntigenChains}
                                            onChange={(e) => setPpiflowSeedAntigenChains(e.target.value.toUpperCase())}
                                            placeholder={selectedChain || 'A'}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500"
                                        />
                                    </label>
                                </div>
                            </div>

                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4">
                                <div className="mb-3">
                                    <h3 className="text-sm font-semibold text-slate-200">PPIFlow Generator Controls</h3>
                                    <p className="text-xs text-slate-500 mt-1">
                                        Tune the seeded backbone-generation pass only. Sequence design, maturation, and validation stay in the shared refinement loop.
                                    </p>
                                </div>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                    <label className="text-xs text-slate-500">
                                        Samples per seed
                                        <input
                                            type="number"
                                            min={1}
                                            max={64}
                                            value={qualitySettings.ppiflow_samples_per_target}
                                            onChange={(e) => setQualitySettings((current) => ({
                                                ...current,
                                                ppiflow_samples_per_target: Math.max(1, Number(e.target.value) || 1),
                                            }))}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Partial-flow start t
                                        <input
                                            type="number"
                                            min={0}
                                            max={1}
                                            step={0.05}
                                            value={qualitySettings.ppiflow_start_t}
                                            onChange={(e) => setQualitySettings((current) => ({
                                                ...current,
                                                ppiflow_start_t: Math.max(0, Math.min(1, Number(e.target.value) || 0)),
                                            }))}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Objective mode
                                        <select
                                            value={qualitySettings.ppiflow_objective_mode}
                                            onChange={(e) => setQualitySettings((current) => ({
                                                ...current,
                                                ppiflow_objective_mode: e.target.value as PPIFlowObjectiveMode,
                                            }))}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500"
                                        >
                                            <option value="balanced">Balanced</option>
                                            <option value="loop_epitope">Loop epitope</option>
                                            <option value="loop_target">Loop target</option>
                                            <option value="selected_interface">Selected interface</option>
                                        </select>
                                    </label>
                                    <label className="text-xs text-slate-500">
                                        Objective threshold
                                        <input
                                            type="number"
                                            step={0.1}
                                            value={qualitySettings.ppiflow_objective_threshold}
                                            onChange={(e) => setQualitySettings((current) => ({
                                                ...current,
                                                ppiflow_objective_threshold: Number(e.target.value) || 0,
                                            }))}
                                            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:ring-2 focus:ring-teal-500"
                                        />
                                    </label>
                                </div>
                                <div className="mt-4 flex flex-wrap gap-4">
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={qualitySettings.ppiflow_require_anchors}
                                            onChange={(e) => setQualitySettings((current) => ({
                                                ...current,
                                                ppiflow_require_anchors: e.target.checked,
                                            }))}
                                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-teal-500 focus:ring-teal-500"
                                        />
                                        Require anchors
                                    </label>
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={qualitySettings.ppiflow_rotamer_enrichment_enabled}
                                            onChange={(e) => setQualitySettings((current) => ({
                                                ...current,
                                                ppiflow_rotamer_enrichment_enabled: e.target.checked,
                                            }))}
                                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-teal-500 focus:ring-teal-500"
                                        />
                                        Rotamer enrichment
                                    </label>
                                </div>
                                <p className="mt-3 text-[11px] text-slate-500">
                                    The seeded batch stays generator-only here. Use the review gate to shortlist top candidates, then reopen them in Antibody Refinement for sequence design, maturation, and validation.
                                </p>
                            </div>
                        </div>
                    ) : (
                        <>
                    {/* Framework Selection */}
                    {!isRefinementMode && (
                        <div>
                            <label className="mb-2 block text-sm font-medium text-[var(--text-secondary)]">Antibody Framework</label>
                            <div className="grid grid-cols-2 gap-3 mb-3">
                                {[
                                    { id: 'standard-fv', name: 'Standard Fv', desc: 'hu-4D5-8 (Herceptin)', color: 'blue' },
                                    { id: 'nanobody', name: 'Nanobody', desc: 'VHH single-domain', color: 'purple' },
                                    { id: 'sabdab', name: 'SAbDab', desc: 'Browse database', color: 'emerald' },
                                    { id: 'custom', name: 'Custom', desc: 'Upload HLT PDB', color: 'amber' },
                                ].map((fw) => (
                                    <button
                                        key={fw.id}
                                        onClick={() => {
                                            frameworkLoadControllerRef.current.begin();
                                            setFrameworkType(fw.id as FrameworkType);
                                        }}
                                        className="rounded-lg border p-3 text-left transition-all"
                                        style={
                                            frameworkType === fw.id
                                                ? themedSelectedStyle(
                                                    fw.id === 'standard-fv'
                                                        ? 'var(--link)'
                                                        : fw.id === 'nanobody'
                                                            ? 'var(--accent-primary)'
                                                            : fw.id === 'sabdab'
                                                                ? 'var(--success)'
                                                                : 'var(--warning)'
                                                )
                                                : themedInsetStyle
                                        }
                                    >
                                        <div className="text-sm font-medium text-[var(--text-primary)]">{fw.name}</div>
                                        <div className="text-xs text-[var(--text-secondary)]">{fw.desc}</div>
                                    </button>
                                ))}
                            </div>

                            {/* SAbDab Framework Browser */}
                            {frameworkType === 'sabdab' && (
                                <div className="mt-3 p-3 bg-slate-900/50 rounded-lg border border-slate-700">
                                    <FrameworkBrowser
                                        onSelect={(fw) => {
                                            const loadToken = frameworkLoadControllerRef.current.begin();
                                            setSabdabFramework(fw);
                                            setDetectedCDRs(null); // Clear previous detection
                                            // Set framework PDB URL for 3D preview if pdbCode available
                                            if (fw?.pdbContent) {
                                                const blob = new Blob([fw.pdbContent], { type: 'text/plain' });
                                                const url = URL.createObjectURL(blob);
                                                replaceFrameworkPdbUrl(url);
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                                setParsedFrameworkChains([]);

                                                const fwFile = new File([blob], `${fw.pdbCode || 'framework'}.pdb`);
                                                parsePDBFile(fwFile)
                                                    .then((parsed) => {
                                                        if (frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                                                            setParsedFrameworkChains(parsed.chains);
                                                        }
                                                    })
                                                    .catch((err) => {
                                                        if (!frameworkLoadControllerRef.current.isCurrent(loadToken)) return;
                                                        console.error('Failed to parse selected framework PDB:', err);
                                                        setParsedFrameworkChains([]);
                                                    });
                                            } else if (fw?.filePath || fw?.pdbCode) {
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                                setParsedFrameworkChains([]);

                                                loadSabdabFrameworkFile(fw.pdbCode || fw.id, `${fw.pdbCode || 'framework'}.pdb`)
                                                    .then(async ({ file, url, filePath }) => {
                                                        if (!frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                                                            URL.revokeObjectURL(url);
                                                            return null;
                                                        }
                                                        if (filePath) {
                                                            setSabdabFramework((prev) => prev ? { ...prev, filePath } : prev);
                                                        }
                                                        replaceFrameworkPdbUrl(url);
                                                        const parsed = await parsePDBFile(file);
                                                        return frameworkLoadControllerRef.current.isCurrent(loadToken) ? parsed : null;
                                                    })
                                                    .then((parsed) => {
                                                        if (parsed && frameworkLoadControllerRef.current.isCurrent(loadToken)) {
                                                            setParsedFrameworkChains(parsed.chains);
                                                        }
                                                    })
                                                    .catch((err) => {
                                                        if (!frameworkLoadControllerRef.current.isCurrent(loadToken)) return;
                                                        console.error('Failed to parse cached framework PDB:', err);
                                                        setParsedFrameworkChains([]);
                                                    });
                                            } else {
                                                replaceFrameworkPdbUrl(null);
                                                setParsedFrameworkChains([]);
                                            }
                                        }}
                                        selectedFramework={sabdabFramework}
                                        showCustomUpload={false}
                                    />

                                    {/* ANARCII CDR Detection */}
                                    {sabdabFramework?.pdbCode && (
                                        <div className="mt-3 pt-3 border-t border-slate-700">
                                            <div className="flex items-center justify-between mb-2">
                                                <span className="text-sm font-medium text-slate-400">CDR Detection (ANARCII)</span>
                                                <button
                                                    type="button"
                                                    onClick={async () => {
                                                        if (!sabdabFramework?.pdbCode) return;
                                                        setIsDetectingCDRs(true);
                                                        try {
                                                            const result = await annotateFrameworkCdrs(sabdabFramework.pdbCode);
                                                            setDetectedCDRs(result.data);
                                                        } catch (err) {
                                                            console.error('CDR detection failed:', err);
                                                        } finally {
                                                            setIsDetectingCDRs(false);
                                                        }
                                                    }}
                                                    disabled={isDetectingCDRs}
                                                    className="px-3 py-1.5 text-xs bg-cyan-600 hover:bg-cyan-500 disabled:bg-slate-600 text-white rounded-lg transition-all"
                                                >
                                                    {isDetectingCDRs ? 'Detecting...' : detectedCDRs ? 'Re-Detect CDRs' : 'Detect CDRs'}
                                                </button>
                                            </div>

                                            {/* Detection Results */}
                                            {detectedCDRs && (
                                                <div className="bg-slate-800/50 rounded-lg p-3 space-y-2">
                                                    <div className="text-xs text-slate-500 mb-2">
                                                        Detected {detectedCDRs.antibody_type} CDR regions:
                                                    </div>
                                                    <div className="grid grid-cols-3 gap-2 text-xs">
                                                        {detectedCDRs.cdr_h1 && (
                                                            <div className="bg-emerald-900/30 border border-emerald-800/50 rounded p-2">
                                                                <div className="text-emerald-400 font-medium">H1</div>
                                                                <div className="text-slate-300 font-mono text-xs truncate" title={detectedCDRs.cdr_h1}>{detectedCDRs.cdr_h1}</div>
                                                                {detectedCDRs.cdr_h1_range && <div className="text-slate-500">{detectedCDRs.cdr_h1_range[0]}-{detectedCDRs.cdr_h1_range[1]}</div>}
                                                            </div>
                                                        )}
                                                        {detectedCDRs.cdr_h2 && (
                                                            <div className="bg-emerald-900/30 border border-emerald-800/50 rounded p-2">
                                                                <div className="text-emerald-400 font-medium">H2</div>
                                                                <div className="text-slate-300 font-mono text-xs truncate" title={detectedCDRs.cdr_h2}>{detectedCDRs.cdr_h2}</div>
                                                                {detectedCDRs.cdr_h2_range && <div className="text-slate-500">{detectedCDRs.cdr_h2_range[0]}-{detectedCDRs.cdr_h2_range[1]}</div>}
                                                            </div>
                                                        )}
                                                        {detectedCDRs.cdr_h3 && (
                                                            <div className="bg-emerald-900/30 border border-emerald-800/50 rounded p-2">
                                                                <div className="text-emerald-400 font-medium">H3</div>
                                                                <div className="text-slate-300 font-mono text-xs truncate" title={detectedCDRs.cdr_h3}>{detectedCDRs.cdr_h3}</div>
                                                                {detectedCDRs.cdr_h3_range && <div className="text-slate-500">{detectedCDRs.cdr_h3_range[0]}-{detectedCDRs.cdr_h3_range[1]}</div>}
                                                            </div>
                                                        )}
                                                    </div>
                                                    {/* Light chain CDRs if present */}
                                                    {(detectedCDRs.cdr_l1 || detectedCDRs.cdr_l2 || detectedCDRs.cdr_l3) && (
                                                        <div className="grid grid-cols-3 gap-2 text-xs mt-2">
                                                            {detectedCDRs.cdr_l1 && (
                                                                <div className="bg-accent/10 border border-accent/30 rounded p-2">
                                                                    <div className="text-accent font-medium">L1</div>
                                                                    <div className="text-slate-300 font-mono text-xs truncate" title={detectedCDRs.cdr_l1}>{detectedCDRs.cdr_l1}</div>
                                                                    {detectedCDRs.cdr_l1_range && <div className="text-slate-500">{detectedCDRs.cdr_l1_range[0]}-{detectedCDRs.cdr_l1_range[1]}</div>}
                                                                </div>
                                                            )}
                                                            {detectedCDRs.cdr_l2 && (
                                                                <div className="bg-accent/10 border border-accent/30 rounded p-2">
                                                                    <div className="text-accent font-medium">L2</div>
                                                                    <div className="text-slate-300 font-mono text-xs truncate" title={detectedCDRs.cdr_l2}>{detectedCDRs.cdr_l2}</div>
                                                                    {detectedCDRs.cdr_l2_range && <div className="text-slate-500">{detectedCDRs.cdr_l2_range[0]}-{detectedCDRs.cdr_l2_range[1]}</div>}
                                                                </div>
                                                            )}
                                                            {detectedCDRs.cdr_l3 && (
                                                                <div className="bg-accent/10 border border-accent/30 rounded p-2">
                                                                    <div className="text-accent font-medium">L3</div>
                                                                    <div className="text-slate-300 font-mono text-xs truncate" title={detectedCDRs.cdr_l3}>{detectedCDRs.cdr_l3}</div>
                                                                    {detectedCDRs.cdr_l3_range && <div className="text-slate-500">{detectedCDRs.cdr_l3_range[0]}-{detectedCDRs.cdr_l3_range[1]}</div>}
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}
                                                    {/* Confirmation Button */}
                                                    <button
                                                        type="button"
                                                        onClick={() => {
                                                            if (frameworkType === 'sabdab' && parsedFrameworkChains.length === 0) {
                                                                alert('Framework residues are not parsed yet. Re-select the framework and try CDR detection again.');
                                                                return;
                                                            }

                                                            // Toggle standard checkboxes
                                                            const loops = new Set<string>();
                                                            if (detectedCDRs.cdr_h1) loops.add('H1');
                                                            if (detectedCDRs.cdr_h2) loops.add('H2');
                                                            if (detectedCDRs.cdr_h3) loops.add('H3');
                                                            if (detectedCDRs.cdr_l1) loops.add('L1');
                                                            if (detectedCDRs.cdr_l2) loops.add('L2');
                                                            if (detectedCDRs.cdr_l3) loops.add('L3');
                                                            setSelectedCDRLoops(loops);

                                                            // Explicitly define these as manual CDR zones for tracking.
                                                            // Prefer raw sequence-index ranges; fall back to IMGT number ranges if needed.
                                                            const { heavyChain, lightChain } = resolveFrameworkChains();
                                                            const heavyChainLabel =
                                                                normalizeChainId(sabdabFramework?.hChain) ||
                                                                normalizeChainId(heavyChain?.id) ||
                                                                'H';
                                                            const lightChainLabel =
                                                                normalizeChainId(sabdabFramework?.lChain) ||
                                                                normalizeChainId(lightChain?.id) ||
                                                                'L';

                                                            const newDefs: import('./CDRRangeSelector').CDRDefinition[] = [];

                                                            // Helper to build definition
                                                            const buildDef = (
                                                                id: string,
                                                                name: string,
                                                                seqRange: [number, number] | null | undefined,
                                                                imgtRange: [number, number] | null | undefined,
                                                                chain: Chain | undefined,
                                                                chainLabel: string,
                                                                colorBase: string
                                                            ) => {
                                                                const residues = collectResiduesFromDetectedRange(chain, seqRange, imgtRange, chainLabel);
                                                                if (residues.size > 0) {
                                                                    newDefs.push({
                                                                        id, name, residues, color: `bg-${colorBase}-500/30`
                                                                    });
                                                                }
                                                            };

                                                            if (heavyChain) {
                                                                buildDef('H1', 'CDR-H1', detectedCDRs.cdr_h1_seq_range, detectedCDRs.cdr_h1_range, heavyChain, heavyChainLabel, 'blue');
                                                                buildDef('H2', 'CDR-H2', detectedCDRs.cdr_h2_seq_range, detectedCDRs.cdr_h2_range, heavyChain, heavyChainLabel, 'cyan');
                                                                buildDef('H3', 'CDR-H3', detectedCDRs.cdr_h3_seq_range, detectedCDRs.cdr_h3_range, heavyChain, heavyChainLabel, 'indigo');
                                                            } else {
                                                                buildDef('H1', 'CDR-H1', detectedCDRs.cdr_h1_seq_range, detectedCDRs.cdr_h1_range, undefined, heavyChainLabel, 'blue');
                                                                buildDef('H2', 'CDR-H2', detectedCDRs.cdr_h2_seq_range, detectedCDRs.cdr_h2_range, undefined, heavyChainLabel, 'cyan');
                                                                buildDef('H3', 'CDR-H3', detectedCDRs.cdr_h3_seq_range, detectedCDRs.cdr_h3_range, undefined, heavyChainLabel, 'indigo');
                                                            }
                                                            if (lightChain) {
                                                                buildDef('L1', 'CDR-L1', detectedCDRs.cdr_l1_seq_range, detectedCDRs.cdr_l1_range, lightChain, lightChainLabel, 'emerald');
                                                                buildDef('L2', 'CDR-L2', detectedCDRs.cdr_l2_seq_range, detectedCDRs.cdr_l2_range, lightChain, lightChainLabel, 'teal');
                                                                buildDef('L3', 'CDR-L3', detectedCDRs.cdr_l3_seq_range, detectedCDRs.cdr_l3_range, lightChain, lightChainLabel, 'green');
                                                            } else if (detectedCDRs.cdr_l1 || detectedCDRs.cdr_l2 || detectedCDRs.cdr_l3) {
                                                                buildDef('L1', 'CDR-L1', detectedCDRs.cdr_l1_seq_range, detectedCDRs.cdr_l1_range, undefined, lightChainLabel, 'emerald');
                                                                buildDef('L2', 'CDR-L2', detectedCDRs.cdr_l2_seq_range, detectedCDRs.cdr_l2_range, undefined, lightChainLabel, 'teal');
                                                                buildDef('L3', 'CDR-L3', detectedCDRs.cdr_l3_seq_range, detectedCDRs.cdr_l3_range, undefined, lightChainLabel, 'green');
                                                            }

                                                            if (newDefs.length > 0) {
                                                                setManualCDRDefinitions(newDefs);
                                                                // Force the accordion open to show user what happened
                                                                setDesignMode('cdr_only');
                                                                setShowCDREditor(true);
                                                            } else {
                                                                alert('Could not map detected CDRs to framework residues yet. Try re-selecting the framework and re-running CDR detection.');
                                                            }
                                                        }}
                                                        className="w-full mt-2 px-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-all"
                                                    >
                                                        ✓ Use These CDRs
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Custom framework upload */}
                            {frameworkType === 'custom' && (
                                <div className="mt-3">
                                    <input
                                        type="file"
                                        accept=".pdb"
                                        onChange={(e) => {
                                            frameworkLoadControllerRef.current.begin();
                                            const file = e.target.files?.[0] || null;
                                            setCustomFrameworkFile(file);
                                            setCustomFrameworkPath(null);
                                            setDetectedCDRs(null);

                                            if (file) {
                                                const blobUrl = URL.createObjectURL(file);
                                                replaceFrameworkPdbUrl(blobUrl);
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                            } else {
                                                replaceFrameworkPdbUrl(null);
                                                setParsedFrameworkChains([]);
                                            }
                                        }}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-amber-500 outline-none file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-amber-600 file:text-white file:cursor-pointer"
                                    />
                                    <p className="mt-1 text-xs text-slate-500">Upload HLT-formatted framework PDB with chain H (Heavy) and L (Light)</p>
                                </div>
                            )}

                            <p className="mt-1 text-xs text-slate-500">
                                {frameworkType === 'standard-fv' && 'Standard humanized Fv framework - good for most applications'}
                                {frameworkType === 'nanobody' && 'Single-domain VHH antibody - smaller, better tissue penetration'}
                                {frameworkType === 'sabdab' && 'Browse VHH structures from SAbDab database (CC-BY 4.0)'}
                                {frameworkType === 'custom' && 'Use your own HLT-formatted antibody framework'}
                            </p>
                        </div>
                    )}

                    {/* Chain Selector (when PDB is parsed) */}
                    {(availableTargetModels.length > 1 || parsedChains.length > 1) && (
                        <div className="grid gap-4 md:grid-cols-2">
                            {availableTargetModels.length > 1 && (
                                <div>
                                    <label className="mb-2 block text-sm font-medium text-[var(--text-secondary)]">Target Conformation</label>
                                    <div className="flex items-center gap-3">
                                        <select
                                            value={selectedTargetModel ?? availableTargetModels[0]?.modelNumber ?? 1}
                                            onChange={(e) => setSelectedTargetModel(Number(e.target.value))}
                                            className="min-w-[12rem] rounded-lg border px-3 py-2 text-sm text-[var(--text-primary)] outline-none"
                                            style={{ backgroundColor: 'var(--bg-tertiary)', borderColor: 'var(--border-primary)' }}
                                        >
                                            {availableTargetModels.map((model) => (
                                                <option key={model.modelNumber} value={model.modelNumber}>
                                                    {model.label}
                                                </option>
                                            ))}
                                        </select>
                                        <span className="text-xs text-[var(--text-secondary)]">
                                            {activeTargetModel
                                                ? `${activeTargetModel.chains.length} chain${activeTargetModel.chains.length === 1 ? '' : 's'}`
                                                : `${availableTargetModels.length} models`}
                                        </span>
                                    </div>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                        Choose the specific target conformation to visualize, select hotspots on, and launch into the workflow.
                                    </p>
                                </div>
                            )}
                            {parsedChains.length > 1 && (
                                <div>
                                    <label className="mb-2 block text-sm font-medium text-[var(--text-secondary)]">Antigen Chain</label>
                                    <div className="flex flex-wrap gap-2">
                                        {parsedChains.map((chain) => (
                                            <button
                                                key={chain.id}
                                                onClick={() => {
                                                    setSelectedChain(chain.id);
                                                    setSelectedResidues(new Set());
                                                }}
                                                className="rounded-lg border px-4 py-2 font-medium transition-all"
                                                style={selectedChain === chain.id ? themedSelectedStyle('var(--link)') : themedInsetStyle}
                                            >
                                                Chain {chain.id} ({chain.length} aa)
                                            </button>
                                        ))}
                                    </div>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Select the chain representing the antigen/target</p>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Interactive Epitope Selector with 3D Viewer */}
                    {parsedChains.length > 0 && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <label className="block text-sm font-medium text-[var(--text-secondary)]">
                                    Epitope Selection
                                    <span className="ml-2 text-xs font-normal text-[var(--text-secondary)]">
                                        (Select hotspot residues the antibody should target)
                                    </span>
                                </label>

                                {/* Explicit Toggle Buttons for Target and Framework Viewers */}
                                <div className="flex gap-2">
                                    {pdbBlobUrl && (
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setViewerMode('target');
                                                setShow3DViewer(show3DViewer && viewerMode === 'target' ? false : true);
                                            }}
                                            className="flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-all"
                                            style={show3DViewer && viewerMode === 'target' ? themedSelectedStyle('var(--success)') : themedMutedInsetStyle}
                                        >
                                            Target 3D
                                        </button>
                                    )}
                                    {frameworkPdbUrl && (
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setViewerMode('framework');
                                                setShow3DViewer(show3DViewer && viewerMode === 'framework' ? false : true);
                                            }}
                                            className="flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-all"
                                            style={show3DViewer && viewerMode === 'framework' ? themedSelectedStyle('var(--accent-primary)') : themedMutedInsetStyle}
                                        >
                                            Framework 3D
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* 3D Molstar Viewer for visualization - toggled */}
                            {(pdbBlobUrl || frameworkPdbUrl) && show3DViewer && (
                                <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                                    {/* Label showing current view */}
                                    <div className="mb-2 text-xs text-[var(--text-secondary)]">
                                        {viewerMode === 'framework' ? 'Framework Template Preview' : 'Target Antigen Preview'}
                                        {viewerMode === 'framework' && detectedCDRs && (
                                            <span className="ml-2 text-[var(--success)]">(CDRs highlighted)</span>
                                        )}
                                        {viewerMode === 'target' && activeTargetModel && availableTargetModels.length > 1 && (
                                            <span className="ml-2 text-[var(--accent-primary)]">({activeTargetModel.label})</span>
                                        )}
                                    </div>
                                    <EpitopeMolstarViewer
                                        structureUrl={viewerMode === 'framework' && frameworkPdbUrl ? frameworkPdbUrl : pdbBlobUrl || ''}
                                        height={400}
                                        selectedResidues={viewerMode === 'target' ? selectedResidues : (() => {
                                            // When viewing framework, highlight detected CDR residues using raw array mapping
                                            const cdrResidues = new Set<string>();
                                            if (detectedCDRs) {
                                                const { heavyChain, lightChain } = resolveFrameworkChains();

                                                if (heavyChain) {
                                                    collectResiduesFromDetectedRange(heavyChain, detectedCDRs.cdr_h1_seq_range, detectedCDRs.cdr_h1_range).forEach((r) => cdrResidues.add(r));
                                                    collectResiduesFromDetectedRange(heavyChain, detectedCDRs.cdr_h2_seq_range, detectedCDRs.cdr_h2_range).forEach((r) => cdrResidues.add(r));
                                                    collectResiduesFromDetectedRange(heavyChain, detectedCDRs.cdr_h3_seq_range, detectedCDRs.cdr_h3_range).forEach((r) => cdrResidues.add(r));
                                                }
                                                if (lightChain) {
                                                    collectResiduesFromDetectedRange(lightChain, detectedCDRs.cdr_l1_seq_range, detectedCDRs.cdr_l1_range).forEach((r) => cdrResidues.add(r));
                                                    collectResiduesFromDetectedRange(lightChain, detectedCDRs.cdr_l2_seq_range, detectedCDRs.cdr_l2_range).forEach((r) => cdrResidues.add(r));
                                                    collectResiduesFromDetectedRange(lightChain, detectedCDRs.cdr_l3_seq_range, detectedCDRs.cdr_l3_range).forEach((r) => cdrResidues.add(r));
                                                }
                                            }
                                            return cdrResidues;
                                        })()}
                                        onResidueClick={viewerMode === 'target' ? (residueKey) => {
                                            if (!activeTargetResidues.has(residueKey)) {
                                                return;
                                            }
                                            if (selectedChain && !residueKey.startsWith(selectedChain)) {
                                                return;
                                            }
                                            setSelectedResidues((prev) => {
                                                const next = new Set(prev);
                                                if (next.has(residueKey)) {
                                                    next.delete(residueKey);
                                                } else {
                                                    next.add(residueKey);
                                                }
                                                return next;
                                            });
                                        } : undefined}
                                    />
                                </div>
                            )}

                            {/* 2D Sequence Grid */}
                            <div>
                                <div className="mb-1 text-xs text-[var(--text-secondary)]">
                                    2D Sequence View (shift+click for range)
                                    {activeTargetModel && availableTargetModels.length > 1 && (
                                        <span className="ml-2 text-[var(--accent-primary)]">Bound to {activeTargetModel.label}</span>
                                    )}
                                </div>
                                <EpitopeSelector
                                    chains={parsedChains}
                                    selectedResidues={selectedResidues}
                                    onSelectionChange={setSelectedResidues}
                                    activeChain={selectedChain || undefined}
                                />
                            </div>
                        </div>
                    )}

                    {/* Fallback text input if no PDB */}
                    {parsedChains.length === 0 && targetPdb && !isParsing && (
                        <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-lg text-amber-400 text-sm">
                            Warning: Could not parse PDB file. Please ensure it's a valid PDB format.
                        </div>
                    )}

                    {/* Optional DNA/RNA Sequence for Complex Prediction */}
                    {!isRefinementMode && (
                        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                            <div className="flex items-center justify-between mb-3">
                                <div>
                                    <h4 className="text-sm font-medium text-slate-300">DNA/RNA Binding Partner (Optional)</h4>
                                    <p className="text-xs text-slate-500">For proteins that form optimal structures when bound to nucleic acid</p>
                                </div>
                                <button
                                    onClick={() => setShowDnaInput(!showDnaInput)}
                                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${showDnaInput
                                        ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'
                                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                        }`}
                                >
                                    {showDnaInput ? 'Enabled' : '+ Add DNA/RNA'}
                                </button>
                            </div>
                            {showDnaInput && (
                                <div className="mt-3">
                                    <textarea
                                        value={targetDnaSeq}
                                        onChange={(e) => setTargetDnaSeq(e.target.value.toUpperCase().replace(/[^ATGCU\s]/gi, ''))}
                                        placeholder="Enter DNA (ATGC) or RNA (AUGC) sequence..."
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white font-mono text-sm focus:ring-2 focus:ring-cyan-500 outline-none h-24 resize-none"
                                    />
                                    <div className="flex items-center justify-between mt-2">
                                        <p className="text-xs text-slate-500">
                                            {targetDnaSeq.replace(/\s/g, '').length > 0
                                                ? `${targetDnaSeq.replace(/\s/g, '').length} nucleotides`
                                                : 'DNA sequence for protein-DNA complex prediction'
                                            }
                                        </p>
                                        {targetDnaSeq && (
                                            <span className="text-xs text-cyan-400">Complex prediction will precede antibody design</span>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Design Mode Selector */}
                    <div className="space-y-3">
                        {isRefinementMode && (
                            <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-4 py-3 text-xs text-cyan-100">
                                Refinement mode reuses the selected backbone inputs. The design-mode controls here affect downstream redesign steps like FAMPNN and PPIFlow rather than RFantibody backbone generation.
                            </div>
                        )}
                        <DesignModeSelector
                            mode={designMode}
                            onModeChange={setDesignMode}
                            selectedLoops={selectedCDRLoops}
                            onLoopsChange={setSelectedCDRLoops}
                            protectTetrad={protectTetrad}
                            onProtectTetradChange={setProtectTetrad}
                            frameworkType={frameworkType}
                        />
                    </div>

                    {!isRefinementMode ? (
                        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 space-y-4">
                                <div>
                                    <h3 className="text-sm font-semibold text-slate-200">Initial Loop Length Variability</h3>
                                    <p className="text-xs text-slate-500 mt-1">
                                        Control RFantibody’s initial CDR loop-length search space independently from the downstream manual CDR position map used by FAMPNN.
                                    </p>
                                </div>

                                <div className="grid grid-cols-2 gap-3">
                                    <button
                                        type="button"
                                        onClick={() => setRfantibodyLoopLengthMode('defaults')}
                                        className={`rounded-lg border px-3 py-2 text-sm transition-colors ${rfantibodyLoopLengthMode === 'defaults'
                                            ? 'border-emerald-400 bg-emerald-400/10 text-emerald-300'
                                            : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        Default Ranges
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setRfantibodyLoopLengthMode('custom_ranges')}
                                        className={`rounded-lg border px-3 py-2 text-sm transition-colors ${rfantibodyLoopLengthMode === 'custom_ranges'
                                            ? 'border-cyan-400 bg-cyan-400/10 text-cyan-300'
                                            : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        Custom Ranges
                                    </button>
                                </div>

                                <p className="text-xs text-slate-500">
                                    {rfantibodyLoopLengthMode === 'defaults'
                                        ? 'Use RFantibody’s standard loop-length priors for the selected CDRs.'
                                        : 'Expand or tighten the initial de novo backbone search space per selected loop. This affects RFantibody generation, not the later fixed-position FAMPNN constraint map.'}
                                </p>

                                {rfantibodyLoopLengthMode === 'custom_ranges' && (
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                        {Array.from(selectedCDRLoops)
                                            .sort()
                                            .filter((loopId) => availableDesignLoops.includes(loopId))
                                            .map((loopId) => {
                                                const range = rfantibodyLoopLengthRanges[loopId] || DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId];
                                                return (
                                                    <div key={loopId} className="rounded-lg border border-slate-700/60 bg-slate-950/40 p-3">
                                                        <div className="flex items-center justify-between mb-2">
                                                            <div className="text-sm font-medium text-slate-200">{loopId}</div>
                                                            <div className="text-[11px] text-slate-500">
                                                                default {DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.min}
                                                                {DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.max !== DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.min
                                                                    ? `-${DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.max}`
                                                                    : ''}
                                                            </div>
                                                        </div>
                                                        <div className="grid grid-cols-2 gap-3">
                                                            <label className="text-xs text-slate-500">
                                                                Min
                                                                <input
                                                                    type="number"
                                                                    min={1}
                                                                    value={range.min}
                                                                    onChange={(e) => {
                                                                        const min = Math.max(1, Number(e.target.value) || 1);
                                                                        setRfantibodyLoopLengthRanges((current) => ({
                                                                            ...current,
                                                                            [loopId]: {
                                                                                min,
                                                                                max: Math.max(min, current[loopId]?.max ?? DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.max ?? min),
                                                                            },
                                                                        }));
                                                                    }}
                                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-cyan-500 outline-none"
                                                                />
                                                            </label>
                                                            <label className="text-xs text-slate-500">
                                                                Max
                                                                <input
                                                                    type="number"
                                                                    min={range.min}
                                                                    value={range.max}
                                                                    onChange={(e) => {
                                                                        const max = Math.max(range.min, Number(e.target.value) || range.min);
                                                                        setRfantibodyLoopLengthRanges((current) => ({
                                                                            ...current,
                                                                            [loopId]: {
                                                                                min: current[loopId]?.min ?? DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId]?.min ?? 1,
                                                                                max,
                                                                            },
                                                                        }));
                                                                    }}
                                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-cyan-500 outline-none"
                                                                />
                                                            </label>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                    </div>
                                )}
                            </div>
                    ) : (
                        <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-4 text-xs text-slate-400">
                            RFantibody loop-length variability is only used for de novo backbone generation. In refinement mode, use{' '}
                            <span className="text-fuchsia-300">Manual mutagenesis methodology &rarr; CDR indels</span>{' '}
                            when you want loop insertions/deletions and downstream backbone rebuilding.
                        </div>
                    )}

                    {!isRefinementMode && (
                        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 space-y-4">
                            <div>
                                <h3 className="text-sm font-semibold text-slate-200">
                                    {isRefinementMode ? 'Optional Input Re-screening' : 'RFantibody Backbone Screening'}
                                </h3>
                                <p className="text-xs text-slate-500 mt-1">
                                    {isRefinementMode
                                        ? 'Re-screen selected inputs before downstream refinement. Turn off if already curated.'
                                        : 'Optional coarse contact screen before expensive downstream stages.'}
                                </p>
                            </div>

                            <label className="flex items-center justify-between rounded-lg border border-slate-700/50 bg-slate-950/40 px-3 py-2 text-sm text-slate-300">
                                <span>{isRefinementMode ? 'Re-screen Selected Inputs' : 'Enable Automatic Screening'}</span>
                                <input
                                    type="checkbox"
                                    checked={enableRfantibodyFilter}
                                    onChange={(e) => setEnableRfantibodyFilter(e.target.checked)}
                                    className="rounded border-slate-600 bg-slate-900 text-emerald-500 focus:ring-emerald-500"
                                />
                            </label>

                            <label className="text-xs text-slate-500">
                                Screening reference scope
                                <select
                                    value={rfantibodyScreenReferenceScope}
                                    onChange={(e) => setRfantibodyScreenReferenceScope(normalizeRfScreeningScope(e.target.value))}
                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                >
                                    <option value="cdr_loops">CDR loops</option>
                                    <option value="whole_antibody">Whole antibody</option>
                                </select>
                                <span className="mt-1 block text-[11px] text-slate-500">
                                    Headline screening metrics and review defaults will follow the selected reference. Use `CDR loops` as the default screen, then switch to `Whole antibody` when you want to inspect framework-mediated nanobody engagement.
                                </span>
                            </label>

                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                <label className="text-xs text-slate-500">
                                    Min {rfantibodyScopeLabel} epitope contacts
                                    <input
                                        type="number"
                                        min={0}
                                        step={1}
                                        value={rfantibodyMinEpitopeContacts}
                                        onChange={(e) => setRfantibodyMinEpitopeContacts(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                                <label className="text-xs text-slate-500">
                                    Max {rfantibodyScopeLabel} epitope distance (A)
                                    <input
                                        type="number"
                                        min={0}
                                        step={0.5}
                                        value={rfantibodyMaxEpitopeDistance}
                                        onChange={(e) => setRfantibodyMaxEpitopeDistance(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                                <label className="text-xs text-slate-500">
                                    Min {rfantibodyScopeLabel} target contacts
                                    <input
                                        type="number"
                                        min={0}
                                        step={1}
                                        value={rfantibodyMinTargetContacts}
                                        onChange={(e) => setRfantibodyMinTargetContacts(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                                <label className="text-xs text-slate-500">
                                    Max {rfantibodyScopeLabel} target distance (A)
                                    <input
                                        type="number"
                                        min={0}
                                        step={0.5}
                                        value={rfantibodyMaxTargetDistance}
                                        onChange={(e) => setRfantibodyMaxTargetDistance(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                                <label className="text-xs text-slate-500">
                                    Max {rfantibodyScopeLabel} epitope centroid distance (A)
                                    <input
                                        type="number"
                                        min={0}
                                        step={0.5}
                                        value={rfantibodyMaxEpitopeCentroidDistance}
                                        onChange={(e) => setRfantibodyMaxEpitopeCentroidDistance(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                            </div>

                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                <label className="text-xs text-slate-500">
                                    {rfantibodyScopeLabel} epitope contact cutoff (A)
                                    <input
                                        type="number"
                                        min={0}
                                        step={0.5}
                                        value={rfantibodyContactDistanceThreshold}
                                        onChange={(e) => setRfantibodyContactDistanceThreshold(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                                <label className="text-xs text-slate-500">
                                    {rfantibodyScopeLabel} target contact cutoff (A)
                                    <input
                                        type="number"
                                        min={0}
                                        step={0.5}
                                        value={rfantibodyTargetContactDistanceThreshold}
                                        onChange={(e) => setRfantibodyTargetContactDistanceThreshold(Math.max(0, Number(e.target.value) || 0))}
                                        disabled={!enableRfantibodyFilter}
                                        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none disabled:opacity-50"
                                    />
                                </label>
                            </div>

                            <p className="text-xs text-slate-500">
                                Defaults: CDR loops, 1 epitope contact @8 A, 3 target contacts @12 A. Switch scope only when framework engagement should count.
                            </p>
                        </div>
                    )}

                    {/* Manual CDR Definition - Toggle */}
                    {!isRefinementMode && designMode === 'cdr_only' && (
                        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                            <div className="flex items-center justify-between mb-3">
                                <div>
                                    <h3 className="text-sm font-semibold text-slate-200">Custom CDR Positions</h3>
                                    <p className="text-xs text-slate-500 mt-0.5">
                                        Define custom loop positions instead of IMGT defaults
                                    </p>
                                </div>
                                <button
                                    onClick={() => setShowCDREditor(!showCDREditor)}
                                    className={`text-xs px-3 py-1.5 rounded transition-colors ${showCDREditor
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                                        }`}
                                >
                                    {showCDREditor ? 'Use Defaults' : 'Define Manually'}
                                </button>
                            </div>
                            {showCDREditor && cdrEditorChains.length > 0 && (
                                <CDRRangeSelector
                                    chains={cdrEditorChains}
                                    cdrDefinitions={manualCDRDefinitions}
                                    onDefinitionsChange={setManualCDRDefinitions}
                                    activeChain={cdrEditorActiveChain}
                                />
                            )}
                            {showCDREditor && cdrEditorChains.length === 0 && (
                                <p className="text-sm text-amber-400 italic">
                                    {frameworkType === 'sabdab'
                                        ? 'Select and parse a SAbDab framework first to map CDR positions correctly.'
                                        : frameworkType === 'custom'
                                            ? 'Upload and parse a custom framework PDB first to map CDR positions correctly.'
                                            : 'Manual CDR mapping requires a parsed framework (SAbDab or Custom).'}
                                </p>
                            )}
                            {manualCDRDefinitions.length > 0 && !showCDREditor && (
                                <p className="text-xs text-emerald-400">
                                    ✓ {manualCDRDefinitions.length} custom CDR(s) defined
                                </p>
                            )}
                        </div>
                    )}

                    {/* Framework Editor - shown for framework_allowed and full_design modes */}
                    {!isRefinementMode && (designMode === 'framework_allowed' || designMode === 'full_design') && (
                        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                            <FrameworkEditor
                                state={frameworkProtection}
                                onChange={setFrameworkProtection}
                                frameworkType={frameworkType}
                                compact={true}
                            />
                            <p className="mt-2 text-xs text-slate-500">
                                Configure which framework positions should remain fixed during sequence design.
                                Protected positions will not be mutated by FAMPNN/ProteinMPNN.
                            </p>
                        </div>
                    )}
                        </>
                    )}

                </div> {/* End LEFT COLUMN */}

                {/* RIGHT COLUMN: Quality Settings & Debug */}
                <div className="space-y-5">
                    {!isRefinementMode && showOnlyCoreGeneratorStep && (
                        <div className="rounded-lg border p-4" style={themedInsetStyle}>
                            <div className="text-sm font-semibold text-[var(--text-primary)]">Generator-Only Batch</div>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                Controls stay hidden until enabled in orchestration above.
                            </p>
                        </div>
                    )}
                    {showExecutionModePanel && (
                    <div className="space-y-4 rounded-lg border p-4" style={themedPanelStyle}>
                        <div>
                            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Execution Mode</h3>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                {isRefinementMode
                                    ? 'Choose whether the refinement loop pauses for manual review or runs through without intervention.'
                                    : deNovoGenerator === 'ppiflow'
                                        ? 'Straight run or pause for review before refinement.'
                                    : deNovoGenerator === 'boltzgen'
                                        ? 'Straight run or pause for review before refinement.'
                                        : 'Choose whether the workflow pauses for manual review or runs through without intervention.'}
                            </p>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <button
                                type="button"
                                onClick={() => {
                                    interactiveWorkflowTouchedRef.current = true;
                                    setInteractiveWorkflow(false);
                                }}
                                className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                style={!interactiveWorkflow ? themedSelectedStyle('var(--success)') : themedInsetStyle}
                            >
                                Static
                            </button>
                            <button
                                type="button"
                                onClick={() => {
                                    interactiveWorkflowTouchedRef.current = true;
                                    setInteractiveWorkflow(true);
                                }}
                                className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                style={interactiveWorkflow ? themedSelectedStyle('var(--warning)') : themedInsetStyle}
                            >
                                Interactive
                            </button>
                        </div>

                        {interactiveWorkflow && deNovoGenerator === 'boltzgen' && !isRefinementMode ? (
                            <div className="space-y-2 rounded-lg border p-3" style={themedInsetStyle}>
                                <div className="text-xs font-medium text-[var(--text-primary)]">BoltzGen Review Loop</div>
                                <p className="text-xs text-[var(--text-secondary)]">
                                    Pause after generation/filtering, shortlist, then open <span className="font-medium text-[var(--text-primary)]">Antibody Refinement</span>.
                                </p>
                            </div>
                        ) : interactiveWorkflow && deNovoGenerator === 'ppiflow' && !isRefinementMode ? (
                            <div className="space-y-2 rounded-lg border p-3" style={themedInsetStyle}>
                                <div className="text-xs font-medium text-[var(--text-primary)]">PPIFlow Review Loop</div>
                                <p className="text-xs text-[var(--text-secondary)]">
                                    Pause after generation/filtering, shortlist, then open <span className="font-medium text-[var(--text-primary)]">Antibody Refinement</span>.
                                </p>
                            </div>
                        ) : interactiveWorkflow && (
                            <div className="space-y-2 rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="block text-xs text-[var(--text-secondary)]">Pause After</label>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_rfantibody');
                                        }}
                                        className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                        style={interactiveGateStage === 'post_rfantibody' ? themedSelectedStyle('var(--success)') : themedMutedInsetStyle}
                                    >
                                        RFantibody Review
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_fampnn');
                                        }}
                                        className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                        style={interactiveGateStage === 'post_fampnn' ? themedSelectedStyle('var(--link)') : themedMutedInsetStyle}
                                    >
                                        FAMPNN Review
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_caliby');
                                        }}
                                        className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                        style={interactiveGateStage === 'post_caliby' ? themedSelectedStyle('var(--warning)') : themedMutedInsetStyle}
                                    >
                                        Caliby Review
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_structure_validation');
                                        }}
                                        className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                        style={interactiveGateStage === 'post_structure_validation' ? themedSelectedStyle('var(--accent-primary)') : themedMutedInsetStyle}
                                    >
                                        Structure Review
                                    </button>
                                </div>
                                <p className="text-xs text-[var(--text-secondary)]">
                                    {interactiveGateStage === 'post_rfantibody'
                                        ? 'Pause after RFantibody before sequence design and validation.'
                                        : interactiveGateStage === 'post_fampnn'
                                            ? 'Pause after FAMPNN filtering before structure validation.'
                                            : interactiveGateStage === 'post_caliby'
                                                ? 'Pause after Caliby sequence design before maturation or validation.'
                                            : 'Pause after validation; review metrics and relaunch refinement from Results.'}
                                </p>
                            </div>
                        )}
                    </div>
                    )}

                    {showStructureValidatorPanel && (
                    <div className="space-y-4 rounded-lg border p-4" style={themedPanelStyle}>
                        <div>
                            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Structure Validator</h3>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                Select the structural validation backend for post-FAMPNN candidate evaluation.
                            </p>
                        </div>

                        <div className="grid grid-cols-3 gap-3">
                            <button
                                type="button"
                                onClick={() => setStructureValidator('boltz2')}
                                className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                style={structureValidator === 'boltz2' ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                            >
                                Boltz-2
                            </button>
                            <button
                                type="button"
                                onClick={() => setStructureValidator('protenix')}
                                className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                style={structureValidator === 'protenix' ? themedSelectedStyle('var(--link)') : themedInsetStyle}
                            >
                                Protenix
                            </button>
                            <button
                                type="button"
                                onClick={() => setStructureValidator('esmfold2')}
                                className="rounded-lg border px-3 py-2 text-sm transition-colors"
                                style={structureValidator === 'esmfold2' ? themedSelectedStyle('var(--success)') : themedInsetStyle}
                            >
                                ESMFold2
                            </button>
                        </div>
                        <p className="text-xs text-[var(--text-secondary)]">
                            {structureValidator === 'protenix'
                                ? 'Protenix runtime controls live in Quality Settings; flexible co-fold is default.'
                                : structureValidator === 'esmfold2'
                                    ? 'ESMFold2 performs fast MSA-free sequence/complex co-folding. It does not provide ipSAE, and iPTM is not used as a substitute.'
                                    : 'Boltz-2 runtime controls and filters live in Quality Settings.'}
                        </p>
                    </div>
                    )}

                    {/* Quality Settings Panel */}
                    {showQualitySettingsPanel && (
                    <QualitySettingsPanel
                        settings={qualitySettings}
                        onSettingsChange={setQualitySettings}
                        structureValidator={structureValidator}
                        allowPostPpiFlowRetry={refinementSourceIsPpiFlow}
                        showRfantibodySettings={showRfQualitySettings}
                        showStructureValidationSettings={showStructureValidationQualitySettings}
                        showFampnnSettings={showFampnnQualitySettings}
                        showCalibySettings={showCalibyQualitySettings}
                        showPreValidationFiltering={effectiveSeqDesigner === 'fampnn' && effectiveRunStructureValidation}
                        showPostValidationFiltering={effectiveRunStructureValidation}
                    />
                    )}

                    {/* Physics Refinement Panel (OpenMM) */}
                    {showQcPanels && (
                    <PhysicsRefinementPanel
                        settings={physicsSettings}
                        onSettingsChange={setPhysicsSettings}
                        isAntibody={true}
                    />
                    )}

                    {/* ANARCII Polishing */}
                    {showQcPanels && (
                    <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <h3 className="text-sm font-semibold text-slate-200">ANARCII CDR Annotation</h3>
                                <p className="text-xs text-slate-500 mt-1">
                                    Post-pipeline CDR annotation for final designs.
                                </p>
                            </div>
                            <label className="flex items-center gap-2 text-sm text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={runAnarciiPost}
                                    onChange={(e) => setRunAnarciiPost(e.target.checked)}
                                    className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-amber-600"
                                />
                                Enable
                            </label>
                        </div>
                        {runAnarciiPost && (
                            <div className="mt-3 space-y-2 text-xs text-slate-500">
                                <label className="flex items-center gap-2 text-slate-300">
                                    <input
                                        type="checkbox"
                                        checked={anarciiIncludeChildren}
                                        onChange={(e) => setAnarciiIncludeChildren(e.target.checked)}
                                        className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-amber-600"
                                    />
                                    Include child jobs (recommended for orchestrated runs)
                                </label>
                            </div>
                        )}
                    </div>
                    )}

                    {/* FrustraMPNN QC */}
                    {showQcPanels && (
                    <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <h3 className="text-sm font-semibold text-slate-200">FrustraMPNN QC</h3>
                                <p className="text-xs text-slate-500 mt-1">
                                    Annotate final candidates with local frustration (post-pipeline, FIO only).
                                </p>
                            </div>
                            <label className="flex items-center gap-2 text-sm text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={runFrustrampnn}
                                    onChange={(e) => setRunFrustrampnn(e.target.checked)}
                                    className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-amber-600"
                                />
                                Enable
                            </label>
                        </div>
                    </div>
                    )}

                    {/* Number of Backbones */}
                    {!isRefinementMode && deNovoGenerator !== 'ppiflow' && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">
                                {deNovoGenerator === 'boltzgen' ? 'Number of Designs' : 'Number of Backbones'}
                            </label>
                            <input
                                type="number"
                                value={numDesigns}
                                onChange={(e) => setNumDesigns(parseInt(e.target.value) || 10)}
                                min={1}
                                max={100}
                                className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                            />
                        </div>
                    )}

                    {/* Sequences per Design */}
                    {showSequenceDesignerPanel && (
                    <>
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">
                                Sequences per Design
                                <span className="ml-2 text-xs text-slate-500 font-normal">({seqsPerDesign})</span>
                            </label>
                            <div className="flex items-center gap-4">
                                <input
                                    type="range"
                                    value={seqsPerDesign}
                                    onChange={(e) => setSeqsPerDesign(parseInt(e.target.value))}
                                    min={1}
                                    max={64}
                                    step={1}
                                    className="flex-1 h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                />
                                <input
                                    type="number"
                                    value={seqsPerDesign}
                                    onChange={(e) => setSeqsPerDesign(Math.max(1, Math.min(64, parseInt(e.target.value) || 8)))}
                                    min={1}
                                    max={64}
                                    className="w-16 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-white text-center"
                                />
                            </div>
                            <p className="mt-1 text-xs text-slate-500">Number of sequence variants to generate per backbone design</p>
                        </div>

                        {/* Sequence Designer */}
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Sequence Designer</label>
                            <div className="flex gap-3">
                                {([...(isRefinementMode ? (['none'] as const) : []), 'fampnn', 'caliby', 'antifold', 'proteinmpnn'] as const).map((designer) => (
                                    <button
                                        key={designer}
                                        onClick={() => {
                                            setSeqDesigner(designer);
                                            if (isRefinementMode) {
                                                setRefinementPreset('custom');
                                                setUseManualMutagenesis(false);
                                            }
                                        }}
                                        className={`px-4 py-2 rounded-lg font-medium transition-all ${seqDesigner === designer
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {designer === 'none' ? 'SKIP' : designer === 'caliby' ? 'CALIBY' : designer.toUpperCase()}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </>
                    )}

                    {showSequenceDesignerPanel && effectiveSeqDesigner === 'fampnn' && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">FAMPNN Constraints</label>
                            <div className="flex gap-3">
                                {(['generic', 'antibody'] as const).map((mode) => (
                                    <button
                                        key={mode}
                                        onClick={() => setFampnnConstraintMode(mode)}
                                        className={`px-4 py-2 rounded-lg font-medium transition-all ${fampnnConstraintMode === mode
                                            ? 'bg-emerald-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {mode === 'generic' ? 'GENERIC' : 'ANTIBODY (CDR)'}
                                    </button>
                                ))}
                            </div>
                            <p className="mt-1 text-xs text-slate-500">
                                Generic applies no fixed positions; Antibody uses CDR-aware constraints.
                            </p>
                        </div>
                    )}

                    {/* Validation Options - removed, now controlled via QualitySettingsPanel */}

                    {/* Orchestrator Parallelism Settings */}
                    {showOrchestratorPanel && (
                    <div className="space-y-4 rounded-lg border p-4" style={themedPanelStyle}>
                        {!isRefinementMode && deNovoGenerator === 'boltzgen' ? (
                            <>
                                <div>
                                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Batch &amp; Parallelization</h3>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                        Tune how the initial BoltzGen nanobody batch is chunked. These controls apply to the generator pass only; downstream refinement still happens after you reopen selected outputs.
                                    </p>
                                </div>

                                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                                    <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                        <label className="block text-sm font-medium text-[var(--text-primary)]">BoltzGen Batch Size</label>
                                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                            Number of designs sampled per BoltzGen generator batch before the job moves to the next batch.
                                        </p>
                                        <div className="mt-3 flex items-center gap-3">
                                            <input
                                                type="range"
                                                min="1"
                                                max="16"
                                                value={boltzgenBatchSize}
                                                onChange={(e) => setBoltzgenBatchSize(Math.max(1, parseInt(e.target.value) || 1))}
                                                className="flex-1 accent-amber-500"
                                            />
                                            <input
                                                type="number"
                                                min="1"
                                                max="64"
                                                value={boltzgenBatchSize}
                                                onChange={(e) => setBoltzgenBatchSize(Math.max(1, parseInt(e.target.value) || 1))}
                                                className="w-20 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white"
                                            />
                                        </div>
                                    </div>

                                    <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                        <label className="block text-sm font-medium text-[var(--text-primary)]">Campaign Execution</label>
                                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                            Choose whether the full batch runs as one BoltzGen campaign or is split into queue-managed child jobs for larger screens.
                                        </p>
                                        <div className="mt-3 flex gap-3">
                                            <button
                                                type="button"
                                                onClick={() => setBoltzgenParallelMode(false)}
                                                className="rounded-lg border px-4 py-2 text-sm transition-colors"
                                                style={!boltzgenParallelMode ? themedSelectedStyle('var(--success)') : themedInsetStyle}
                                            >
                                                Single Job
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => setBoltzgenParallelMode(true)}
                                                className="rounded-lg border px-4 py-2 text-sm transition-colors"
                                                style={boltzgenParallelMode ? themedSelectedStyle('var(--warning)') : themedInsetStyle}
                                            >
                                                Child Jobs
                                            </button>
                                        </div>
                                    </div>
                                </div>

                                {boltzgenParallelMode && (
                                    <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
                                        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                            <div>
                                                <div className="text-sm font-medium text-amber-200">BoltzGen Child-Job Chunking</div>
                                                <p className="mt-1 text-xs text-slate-400">
                                                    Split large campaigns into queue-managed child jobs. Each child generates this many designs before the outputs are collected into the parent campaign.
                                                </p>
                                            </div>
                                            <div className="min-w-[260px] lg:max-w-sm">
                                                <label className="text-xs text-slate-500">Designs per child job</label>
                                                <input
                                                    type="range"
                                                    min="1"
                                                    max="1000"
                                                    value={boltzgenDesignsPerJob}
                                                    onChange={(e) => setBoltzgenDesignsPerJob(Math.max(1, parseInt(e.target.value) || 1))}
                                                    className="mt-2 w-full accent-amber-500"
                                                />
                                                <div className="mt-1 flex items-center justify-between text-xs text-slate-400">
                                                    <span>1</span>
                                                    <span className="font-medium text-amber-200">{boltzgenDesignsPerJob}</span>
                                                    <span>1000</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </>
                        ) : !isRefinementMode && deNovoGenerator === 'ppiflow' ? (
                            <>
                                <div>
                                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Seeded Batch Execution</h3>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                        Seeded PPIFlow runs as a generator pass over the provided seed complexes. Downstream fanout happens after you shortlist and reopen outputs in Antibody Refinement.
                                    </p>
                                </div>
                                <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                    <div className="text-sm font-medium text-[var(--text-primary)]">Per-seed sampling</div>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                        `Samples per seed` in the left column controls how many backbones PPIFlow generates for each seed complex. Use the review gate to prune the batch before unknown heavier downstream work.
                                    </p>
                                </div>
                            </>
                        ) : (
                            <>
                                <div>
                                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Orchestrator Mode</h3>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                        Control how the de novo or refinement workflow fans work out across GPUs and child jobs.
                                    </p>
                                </div>
                                <div className="flex gap-3">
                                    <button
                                        onClick={() => setParallelMode('standard')}
                                        className={`px-4 py-2 rounded-lg font-medium transition-all ${parallelMode === 'standard'
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        Nextflow Split
                                    </button>
                                    <button
                                        onClick={() => setParallelMode('full_orchestrator')}
                                        className={`px-4 py-2 rounded-lg font-medium transition-all ${parallelMode === 'full_orchestrator'
                                            ? 'bg-orange-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        Orchestrator Jobs
                                    </button>
                                </div>
                                <p className="text-xs text-slate-500">
                                    {parallelMode === 'standard'
                                        ? 'Standard: split work across pinned GPUs within Nextflow.'
                                        : 'Orchestrator: spawn child jobs that move through the GPU queue independently.'}
                                </p>

                                {anyPpiFlowStageEnabled && (
                                    <div className="rounded-lg border border-teal-500/20 bg-teal-500/5 p-4">
                                        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                            <div>
                                                <div className="text-sm font-medium text-teal-200">PPIFlow Child-Job Chunking</div>
                                                <p className="mt-1 text-xs text-slate-400">
                                                    Both PPIFlow backbone refinement and PPIFlow maturation run through orchestrated child jobs, independent of the RFantibody/FAMPNN parallel mode above.
                                                    This setting controls how many input PDBs each PPIFlow child processes serially on its assigned GPU.
                                                </p>
                                            </div>
                                            <div className="min-w-[260px] lg:max-w-sm">
                                                <label className="text-xs text-slate-500">PDBs per PPIFlow job</label>
                                                <input
                                                    type="range"
                                                    min="1"
                                                    max="1000"
                                                    value={qualitySettings.maturation_designs_per_job}
                                                    onChange={(e) => setQualitySettings((current) => ({
                                                        ...current,
                                                        maturation_designs_per_job: parseInt(e.target.value),
                                                    }))}
                                                    className="mt-2 w-full accent-teal-500"
                                                />
                                                <div className="mt-1 flex items-center justify-between text-xs text-slate-400">
                                                    <span>1</span>
                                                    <span className="font-medium text-teal-200">{qualitySettings.maturation_designs_per_job}</span>
                                                    <span>1000</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {parallelMode === 'full_orchestrator' && (
                                    <div className="grid grid-cols-2 gap-4">
                                        <div>
                                            <label className="text-xs text-slate-500">Backbones per job</label>
                                            <input
                                                type="range"
                                                min="1"
                                                max="1000"
                                                value={designsPerJob}
                                                onChange={(e) => setDesignsPerJob(parseInt(e.target.value))}
                                                className="w-full accent-orange-500"
                                            />
                                            <span className="text-sm text-slate-300">{designsPerJob}</span>
                                        </div>
                                        <div>
                                            <label className="text-xs text-slate-500">PDBs per FAMPNN job</label>
                                            <input
                                                type="range"
                                                min="1"
                                                max="1000"
                                                value={pdBsPerJob}
                                                onChange={(e) => setPdBsPerJob(parseInt(e.target.value))}
                                                className="w-full accent-orange-500"
                                            />
                                            <span className="text-sm text-slate-300">{pdBsPerJob}</span>
                                        </div>
                                        <div>
                                            <label className="text-xs text-slate-500">Sequences per validation job</label>
                                            <input
                                                type="range"
                                                min="1"
                                                max="1000"
                                                value={seqsPerBoltzJob}
                                                onChange={(e) => setSeqsPerBoltzJob(parseInt(e.target.value))}
                                                className="w-full accent-orange-500"
                                            />
                                            <span className="text-sm text-slate-300">{seqsPerBoltzJob}</span>
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                    )}

                    {/* Debug Settings Panel - Hidden by default */}
                    {showDebugPanel && (
                    <div className="mt-6 border border-amber-600/30 rounded-lg overflow-hidden">
                        <button
                            onClick={() => setShowDebugSettings(!showDebugSettings)}
                            className={`w-full px-4 py-3 flex items-center justify-between text-left transition-colors ${showDebugSettings
                                ? 'bg-amber-600/20 text-amber-400'
                                : 'bg-slate-900/50 text-slate-500 hover:bg-slate-800/50'
                                }`}
                        >
                            <div className="flex items-center gap-2">
                                <span className="font-medium">Debug &amp; Overrides</span>
                                {(skipRFantibody || skipFampnn || customOutputDir || boltzgenReuseExisting) && (
                                    <span className="px-2 py-0.5 text-xs bg-amber-600 text-white rounded">ACTIVE</span>
                                )}
                            </div>
                            <span className="text-lg">{showDebugSettings ? '-' : '+'}</span>
                        </button>

                        {showDebugSettings && (
                            <div className="p-4 bg-slate-900/30 space-y-4">
                                <div className="text-xs text-amber-500/80 mb-3">
                                    Override paths, reuse behaviors, or skip stages for recovery runs. Some options are generator-specific.
                                </div>

                                {(isRefinementMode || deNovoGenerator === 'rfantibody') && (
                                    <>
                                        <div className="space-y-2">
                                            <label className="flex items-center gap-2 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={skipRFantibody}
                                                    onChange={e => {
                                                        setSkipRFantibody(e.target.checked);
                                                        if (!e.target.checked) setRfantibodyInputPdbs('');
                                                    }}
                                                    className="w-4 h-4 rounded border-amber-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                                                />
                                                <span className="text-sm text-slate-300">Skip RFantibody (use pre-existing backbone PDBs)</span>
                                            </label>
                                            {skipRFantibody && (
                                                <input
                                                    type="text"
                                                    value={rfantibodyInputPdbs}
                                                    onChange={e => setRfantibodyInputPdbs(e.target.value)}
                                                    placeholder="/path/to/backbone/pdbs"
                                                    className="w-full bg-slate-900 border border-amber-600/50 rounded-lg px-4 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none font-mono"
                                                />
                                            )}
                                        </div>

                                        <div className="space-y-2">
                                            <label className="flex items-center gap-2 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={skipFampnn}
                                                    onChange={e => {
                                                        setSkipFampnn(e.target.checked);
                                                        if (!e.target.checked) setFampnnCollectedPdbs('');
                                                    }}
                                                    className="w-4 h-4 rounded border-amber-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                                                />
                                                <span className="text-sm text-slate-300">Skip FAMPNN (use pre-existing sequenced PDBs)</span>
                                            </label>
                                            {skipFampnn && (
                                                <input
                                                    type="text"
                                                    value={fampnnCollectedPdbs}
                                                    onChange={e => setFampnnCollectedPdbs(e.target.value)}
                                                    placeholder="/path/to/fampnn/output/pdbs"
                                                    className="w-full bg-slate-900 border border-amber-600/50 rounded-lg px-4 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none font-mono"
                                                />
                                            )}
                                        </div>
                                    </>
                                )}

                                {!isRefinementMode && deNovoGenerator === 'boltzgen' && (
                                    <div className="space-y-2 rounded-lg border border-amber-600/20 bg-amber-500/5 p-3">
                                        <label className="flex items-center gap-2 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={boltzgenReuseExisting}
                                                onChange={e => setBoltzgenReuseExisting(e.target.checked)}
                                                className="w-4 h-4 rounded border-amber-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                                            />
                                            <span className="text-sm text-slate-300">Reuse existing BoltzGen outputs when the output directory already contains a matching campaign</span>
                                        </label>
                                        <p className="text-xs text-slate-500">
                                            Useful for recovery or repeated review passes against the same batch directory.
                                        </p>
                                    </div>
                                )}

                                {/* Custom Output Directory */}
                                <div className="space-y-2">
                                    <label className="text-sm text-slate-400">Custom Output Directory (optional)</label>
                                    <input
                                        type="text"
                                        value={customOutputDir}
                                        onChange={e => setCustomOutputDir(e.target.value)}
                                        placeholder="/path/to/bms_results/custom_run"
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2 text-white text-sm focus:ring-2 focus:ring-slate-500 outline-none font-mono"
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                    )}
                </div> {/* End RIGHT COLUMN */}
            </div > {/* End grid */}

            {/* Submit Button */}
            <div className="mt-8 flex justify-end gap-3">
                {/* Template Manager Button */}
                <button
                    type="button"
                    onClick={() => setShowTemplateManager(true)}
                    className="px-6 py-3 text-accent bg-accent/20 hover:bg-accent/30 border border-accent/30 font-medium rounded-lg transition-colors flex items-center gap-2"
                >
                    Save Template
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={
                        submitMutation.isPending ||
                        launchMutagenesisMutation.isPending ||
                        isUploading ||
                        (isRefinementMode && !refinementHasLaunchSource) ||
                        // Refinement mode and skip modes don't require target PDB or hotspots
                        (!(isRefinementMode || deNovoGenerator === 'ppiflow' || (deNovoGenerator === 'rfantibody' && (skipRFantibody || skipFampnn))) && (!(targetPdb || targetSource?.path || uploadedPath) || selectedResidues.size === 0)) ||
                        // When skipping, require the skip paths
                        (!isRefinementMode && deNovoGenerator === 'rfantibody' && skipRFantibody && !rfantibodyInputPdbs.trim()) ||
                        (!isRefinementMode && deNovoGenerator === 'rfantibody' && skipFampnn && !fampnnCollectedPdbs.trim()) ||
                        (!isRefinementMode && deNovoGenerator === 'ppiflow' && !hasPpiFlowSeedLaunchInput) ||
                        (isRefinementMode && !useManualMutagenesis && effectiveSeqDesigner === 'none' && !anyPpiFlowStageEnabled && !effectiveRunStructureValidation && !effectiveRunFrustrampnn)
                    }
                    className="px-6 py-3 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-500 text-white font-medium rounded-lg transition-colors flex items-center gap-2"
                >
                    {isUploading ? (
                        <>
                            Uploading PDB...
                        </>
                    ) : (submitMutation.isPending || launchMutagenesisMutation.isPending) ? (
                        <>
                            Submitting...
                        </>
                    ) : isRefinementMode ? (
                        <>
                            {useManualMutagenesis
                                ? (mutagenesisLaunchMode === 'seeded_refinement' ? 'Launch Mutation-Seeded Refinement' : 'Launch Exact Mutant Evaluation')
                                : 'Launch Antibody Refinement'} ({refinementInputCount} outputs)
                        </>
                    ) : (deNovoGenerator === 'rfantibody' && (skipRFantibody || skipFampnn)) ? (
                        <>
                            Run Skipped Workflow
                        </>
                    ) : deNovoGenerator === 'boltzgen' ? (
                        <>
                            Launch BoltzGen Nanobody Batch
                        </>
                    ) : deNovoGenerator === 'ppiflow' ? (
                        <>
                            Launch PPIFlow Seeded Batch
                        </>
                    ) : showOnlyCoreGeneratorStep ? (
                        <>
                            Launch RFantibody Batch ({selectedResidues.size} hotspots)
                        </>
                    ) : (
                        <>
                            Launch De Novo Nanobody Pipeline ({selectedResidues.size} hotspots)
                        </>
                    )}
                </button>
            </div>

            {/* Template Manager Modal */}
            <TemplateManagerModal
                isOpen={showTemplateManager}
                onClose={() => setShowTemplateManager(false)}
                onSelect={(template) => {
                    console.log('[TEMPLATE_LOAD] ======= LOADING TEMPLATE =======');
                    console.log('[TEMPLATE_LOAD] Template name:', template.name);
                    console.log('[TEMPLATE_LOAD] Template id:', template.id);
                    console.log('[TEMPLATE_LOAD] All params:', JSON.stringify(template.params, null, 2));
                    try {
                        // Load template params into state
                        const p = template.params || {};
                        const loaded: string[] = [];
                        const skipped: string[] = [];
                        interactiveWorkflowTouchedRef.current = false;
                        interactiveGateStageTouchedRef.current = false;

                        // Core settings (check both new and old field names for backward compatibility)
                        if (p.job_name) { setJobName(p.job_name); loaded.push('job_name'); } else { skipped.push('job_name'); }
                        if (!isRefinementMode) {
                            const restoringPpiFlowGenerator =
                                p.denovo_generator === 'ppiflow' ||
                                p.generator === 'ppiflow' ||
                                (p.stage_family === 'ppiflow' && p.stage_mode === 'generator_backbone_refine') ||
                                p.mode === 'generator_backbone_refine';
                            const restoringBoltzgenGenerator =
                                p.denovo_generator === 'boltzgen' ||
                                p.generator === 'boltzgen' ||
                                p.boltzgen_mode === 'nanobody_binder';
                            if (restoringBoltzgenGenerator) {
                                setDeNovoGenerator('boltzgen');
                                loaded.push('denovo_generator');
                            } else if (restoringPpiFlowGenerator) {
                                setDeNovoGenerator('ppiflow');
                                loaded.push('denovo_generator');
                            } else if (p.denovo_generator === 'rfantibody' || p.generator === 'rfantibody') {
                                setDeNovoGenerator('rfantibody');
                                loaded.push('denovo_generator');
                            }
                            setDeNovoStageSelection({
                                sequence_design: (restoringBoltzgenGenerator || restoringPpiFlowGenerator) ? false : p.initial_orchestration_sequence_design === true,
                                ppiflow: (restoringBoltzgenGenerator || restoringPpiFlowGenerator) ? false : p.initial_orchestration_ppiflow === true,
                                validation: (restoringBoltzgenGenerator || restoringPpiFlowGenerator) ? false : p.initial_orchestration_validation === true,
                                qc: (restoringBoltzgenGenerator || restoringPpiFlowGenerator) ? false : p.initial_orchestration_qc === true,
                            });
                            if (
                                p.initial_orchestration_sequence_design !== undefined ||
                                p.initial_orchestration_ppiflow !== undefined ||
                                p.initial_orchestration_validation !== undefined ||
                                p.initial_orchestration_qc !== undefined
                            ) {
                                loaded.push('initial_orchestration_*');
                            }
                        }
                        if (p.framework_type) { setFrameworkType(p.framework_type); loaded.push('framework_type'); } else { skipped.push('framework_type'); }
                        if (p.seq_designer) { setSeqDesigner(p.seq_designer); loaded.push('seq_designer'); }
                        else if (p.seq_design_caliby === true) { setSeqDesigner('caliby'); loaded.push('seq_design_caliby'); }
                        else if (p.seq_design_fampnn === false && p.seq_design_caliby === false && p.seq_design_antifold === false && p.seq_design_proteinmpnn === false) { setSeqDesigner('none'); loaded.push('seq_designer:none'); }
                        else { skipped.push('seq_designer'); }
                        if (p.rfantibody_num_designs) { setNumDesigns(p.rfantibody_num_designs); loaded.push('rfantibody_num_designs'); } else { skipped.push('rfantibody_num_designs'); }
                        if (p.seqs_per_design) { setSeqsPerDesign(p.seqs_per_design); loaded.push('seqs_per_design'); } else { skipped.push('seqs_per_design'); }
                        if (typeof p.run_immunogenicity_scoring === 'boolean') { setUseAntiberty(p.run_immunogenicity_scoring); loaded.push('run_immunogenicity_scoring'); }
                        if (typeof p.run_thermompnn === 'boolean') { setUseThermoMPNN(p.run_thermompnn); loaded.push('run_thermompnn'); }
                        else if (typeof p.run_stability_scoring === 'boolean') { setUseThermoMPNN(p.run_stability_scoring); loaded.push('run_stability_scoring'); }
                        if (typeof p.run_frustrampnn === 'boolean') { setRunFrustrampnn(p.run_frustrampnn); loaded.push('run_frustrampnn'); }
                        if (typeof p.run_structure_validation === 'boolean') { setRunStructureValidation(p.run_structure_validation); loaded.push('run_structure_validation'); }
                        if (typeof p.run_anarcii_post === 'boolean') { setRunAnarciiPost(p.run_anarcii_post); loaded.push('run_anarcii_post'); }
                        if (typeof p.anarcii_include_children === 'boolean') { setAnarciiIncludeChildren(p.anarcii_include_children); loaded.push('anarcii_include_children'); }
                        if (typeof p.boltzgen_use_framework_template === 'boolean') {
                            setBoltzgenUseFrameworkTemplate(p.boltzgen_use_framework_template);
                            loaded.push('boltzgen_use_framework_template');
                        }
                        if (typeof p.boltzgen_batch_size === 'number') {
                            setBoltzgenBatchSize(p.boltzgen_batch_size);
                            loaded.push('boltzgen_batch_size');
                        } else if (typeof p.batch_size === 'number') {
                            setBoltzgenBatchSize(p.batch_size);
                            loaded.push('batch_size');
                        }
                        if (typeof p.boltzgen_parallel_mode === 'boolean') {
                            setBoltzgenParallelMode(p.boltzgen_parallel_mode);
                            loaded.push('boltzgen_parallel_mode');
                        }
                        if (typeof p.boltzgen_designs_per_job === 'number') {
                            setBoltzgenDesignsPerJob(p.boltzgen_designs_per_job);
                            loaded.push('boltzgen_designs_per_job');
                        }
                        if (typeof p.boltzgen_view_reference_structure === 'boolean') {
                            setBoltzgenViewReferenceStructure(p.boltzgen_view_reference_structure);
                            loaded.push('boltzgen_view_reference_structure');
                        }
                        if (typeof p.boltzgen_reuse === 'boolean') {
                            setBoltzgenReuseExisting(p.boltzgen_reuse);
                            loaded.push('boltzgen_reuse');
                        }
                        if (typeof p.boltzgen_nanobody_framework === 'string') {
                            setBoltzgenNanobodyFramework(p.boltzgen_nanobody_framework);
                            loaded.push('boltzgen_nanobody_framework');
                        }
                        if (p.boltzgen_scaffold_source === 'default_ensemble' || p.boltzgen_scaffold_source === 'selected_scaffold' || p.boltzgen_scaffold_source === 'sequence_template') {
                            setBoltzgenScaffoldSource(p.boltzgen_scaffold_source);
                            loaded.push('boltzgen_scaffold_source');
                        }
                        if (typeof p.boltzgen_scaffold_length === 'string') {
                            setBoltzgenScaffoldLength(p.boltzgen_scaffold_length);
                            loaded.push('boltzgen_scaffold_length');
                        }
                        if (typeof p.boltzgen_cdr_h1_length === 'string') { setBoltzgenCdrH1Length(p.boltzgen_cdr_h1_length); loaded.push('boltzgen_cdr_h1_length'); }
                        if (typeof p.boltzgen_cdr_h2_length === 'string') { setBoltzgenCdrH2Length(p.boltzgen_cdr_h2_length); loaded.push('boltzgen_cdr_h2_length'); }
                        if (typeof p.boltzgen_cdr_h3_length === 'string') { setBoltzgenCdrH3Length(p.boltzgen_cdr_h3_length); loaded.push('boltzgen_cdr_h3_length'); }
                        if (p.boltzgen_checkpoint_mode === 'both' || p.boltzgen_checkpoint_mode === 'diverse' || p.boltzgen_checkpoint_mode === 'adherence') {
                            setBoltzgenCheckpointMode(p.boltzgen_checkpoint_mode);
                            loaded.push('boltzgen_checkpoint_mode');
                        }
                        if (typeof p.boltzgen_skip_inverse_folding === 'boolean') { setBoltzgenSkipInverseFolding(p.boltzgen_skip_inverse_folding); loaded.push('boltzgen_skip_inverse_folding'); }
                        if (typeof p.boltzgen_inverse_fold_avoid === 'string') { setBoltzgenInverseFoldAvoid(p.boltzgen_inverse_fold_avoid); loaded.push('boltzgen_inverse_fold_avoid'); }
                        if (typeof p.boltzgen_inverse_fold_num_sequences === 'number') { setBoltzgenInverseFoldNumSequences(p.boltzgen_inverse_fold_num_sequences); loaded.push('boltzgen_inverse_fold_num_sequences'); }
                        if (typeof p.boltzgen_avoid_cysteine === 'boolean') { setBoltzgenAvoidCysteine(p.boltzgen_avoid_cysteine); loaded.push('boltzgen_avoid_cysteine'); }
                        if (typeof p.boltzgen_step_scale === 'number') { setBoltzgenStepScale(p.boltzgen_step_scale); loaded.push('boltzgen_step_scale'); }
                        if (typeof p.boltzgen_noise_scale === 'number') { setBoltzgenNoiseScale(p.boltzgen_noise_scale); loaded.push('boltzgen_noise_scale'); }
                        if (typeof p.boltzgen_budget === 'number') { setBoltzgenBudget(p.boltzgen_budget); loaded.push('boltzgen_budget'); }
                        if (typeof p.boltzgen_alpha === 'number') { setBoltzgenAlpha(p.boltzgen_alpha); loaded.push('boltzgen_alpha'); }
                        if (typeof p.boltzgen_max_rmsd === 'number') { setBoltzgenMaxRmsd(p.boltzgen_max_rmsd); loaded.push('boltzgen_max_rmsd'); }
                        if (typeof p.boltzgen_min_plddt === 'number') { setBoltzgenMinPlddt(p.boltzgen_min_plddt); loaded.push('boltzgen_min_plddt'); }
                        if (typeof p.boltzgen_min_conf_score === 'number') { setBoltzgenMinConfScore(p.boltzgen_min_conf_score); loaded.push('boltzgen_min_conf_score'); }
                        if (typeof p.boltzgen_filter_biased === 'boolean') { setBoltzgenFilterBiased(p.boltzgen_filter_biased); loaded.push('boltzgen_filter_biased'); }
                        if (typeof p.boltzgen_metrics_override === 'string') { setBoltzgenMetricsOverride(p.boltzgen_metrics_override); loaded.push('boltzgen_metrics_override'); }
                        if (typeof p.boltzgen_additional_filters === 'string') { setBoltzgenAdditionalFilters(p.boltzgen_additional_filters); loaded.push('boltzgen_additional_filters'); }
                        if (typeof p.boltzgen_size_buckets === 'string') { setBoltzgenSizeBuckets(p.boltzgen_size_buckets); loaded.push('boltzgen_size_buckets'); }
                        if (typeof p.ppiflow_seed_input_dir === 'string') { setPpiflowSeedInputDir(p.ppiflow_seed_input_dir); loaded.push('ppiflow_seed_input_dir'); }
                        else if (typeof p.selected_input_dir === 'string' && p.stage_family === 'ppiflow') { setPpiflowSeedInputDir(p.selected_input_dir); loaded.push('selected_input_dir'); }
                        if (typeof p.ppiflow_seed_complex_path === 'string') { setPpiflowSeedComplexPath(p.ppiflow_seed_complex_path); loaded.push('ppiflow_seed_complex_path'); }
                        if (typeof p.antibody_chains === 'string' && p.antibody_chains.trim()) { setPpiflowSeedAntibodyChains(p.antibody_chains); loaded.push('antibody_chains'); }
                        if (typeof p.antigen_chains === 'string' && p.antigen_chains.trim()) { setPpiflowSeedAntigenChains(p.antigen_chains); loaded.push('antigen_chains'); }
                        if (typeof p.interactive_swa === 'boolean') { setInteractiveWorkflow(p.interactive_swa); loaded.push('interactive_swa'); }
                        else if (typeof p.interactive_gating === 'boolean') { setInteractiveWorkflow(p.interactive_gating); loaded.push('interactive_gating'); }
                        if (
                            p.interactive_gate_stage === 'post_rfantibody' ||
                            p.interactive_gate_stage === 'post_boltzgen' ||
                            p.interactive_gate_stage === 'post_ppiflow_generator' ||
                            p.interactive_gate_stage === 'post_caliby' ||
                            p.interactive_gate_stage === 'post_structure_validation' ||
                            p.interactive_gate_stage === 'post_fampnn'
                        ) {
                            setInteractiveGateStage(p.interactive_gate_stage);
                            loaded.push('interactive_gate_stage');
                        }
                        if (p.structure_validator === 'protenix' || p.structure_validator === 'boltz2') { setStructureValidator(p.structure_validator); loaded.push('structure_validator'); }
                        if (p.parallel_mode) { setParallelMode(p.parallel_mode); loaded.push('parallel_mode'); } else { skipped.push('parallel_mode'); }
                        if (p.designs_per_job) { setDesignsPerJob(p.designs_per_job); loaded.push('designs_per_job'); }
                        if (p.pdbs_per_job) { setPdBsPerJob(p.pdbs_per_job); loaded.push('pdbs_per_job'); }
                        else if (p.seqs_per_job) { setPdBsPerJob(p.seqs_per_job); loaded.push('seqs_per_job'); }
                        if (p.seqs_per_validation_job) { setSeqsPerBoltzJob(p.seqs_per_validation_job); loaded.push('seqs_per_validation_job'); }
                        else if (p.seqs_per_boltz_job) { setSeqsPerBoltzJob(p.seqs_per_boltz_job); loaded.push('seqs_per_boltz_job'); }
                        if (Array.isArray(p.pinned_gpus)) { setPinnedGpus(p.pinned_gpus); loaded.push('pinned_gpus'); }
                        if (typeof p.lock_gpus === 'boolean') { setLockGpus(p.lock_gpus); loaded.push('lock_gpus'); }
                        if (typeof p.out_dir === 'string') { setCustomOutputDir(p.out_dir); loaded.push('out_dir'); }
                        // Design mode
                        if (p.design_mode) { setDesignMode(p.design_mode); loaded.push('design_mode'); } else { skipped.push('design_mode'); }
                        if (Array.isArray(p.selected_cdr_loops)) { setSelectedCDRLoops(new Set(p.selected_cdr_loops)); loaded.push('selected_cdr_loops'); }
                        if (p.rfantibody_loop_length_mode === 'custom_ranges' || p.rfantibody_loop_length_mode === 'defaults') {
                            setRfantibodyLoopLengthMode(p.rfantibody_loop_length_mode);
                            loaded.push('rfantibody_loop_length_mode');
                        }
                        if (p.rfantibody_loop_length_ranges_config || p.rfantibody_loop_length_ranges) {
                            setRfantibodyLoopLengthRanges(
                                parseLoopLengthRanges(p.rfantibody_loop_length_ranges_config || p.rfantibody_loop_length_ranges)
                            );
                            loaded.push('rfantibody_loop_length_ranges');
                        }
                        if (!isRefinementMode && typeof p.enable_rfantibody_filter === 'boolean') {
                            setEnableRfantibodyFilter(p.enable_rfantibody_filter);
                            loaded.push('enable_rfantibody_filter');
                        }
                        if (!isRefinementMode && p.rfantibody_screen_reference_scope !== undefined) {
                            setRfantibodyScreenReferenceScope(normalizeRfScreeningScope(p.rfantibody_screen_reference_scope));
                            loaded.push('rfantibody_screen_reference_scope');
                        }
                        if (!isRefinementMode && p.rfantibody_min_epitope_contacts !== undefined) {
                            setRfantibodyMinEpitopeContacts(Math.max(0, Number(p.rfantibody_min_epitope_contacts) || 0));
                            loaded.push('rfantibody_min_epitope_contacts');
                        }
                        if (!isRefinementMode && p.rfantibody_max_epitope_distance !== undefined) {
                            setRfantibodyMaxEpitopeDistance(Math.max(0, Number(p.rfantibody_max_epitope_distance) || 0));
                            loaded.push('rfantibody_max_epitope_distance');
                        }
                        if (!isRefinementMode && p.rfantibody_min_target_contacts !== undefined) {
                            setRfantibodyMinTargetContacts(Math.max(0, Number(p.rfantibody_min_target_contacts) || 0));
                            loaded.push('rfantibody_min_target_contacts');
                        }
                        if (!isRefinementMode && (p as UntypedApiValue).rfantibody_max_target_distance !== undefined) {
                            setRfantibodyMaxTargetDistance(Math.max(0, Number((p as UntypedApiValue).rfantibody_max_target_distance) || 0));
                            loaded.push('rfantibody_max_target_distance');
                        }
                        if (!isRefinementMode && p.rfantibody_max_epitope_centroid_distance !== undefined) {
                            setRfantibodyMaxEpitopeCentroidDistance(Math.max(0, Number(p.rfantibody_max_epitope_centroid_distance) || 0));
                            loaded.push('rfantibody_max_epitope_centroid_distance');
                        }
                        if (!isRefinementMode && p.rfantibody_contact_distance_threshold !== undefined) {
                            setRfantibodyContactDistanceThreshold(Math.max(0, Number(p.rfantibody_contact_distance_threshold) || 0));
                            loaded.push('rfantibody_contact_distance_threshold');
                        }
                        if (!isRefinementMode && p.rfantibody_target_contact_distance_threshold !== undefined) {
                            setRfantibodyTargetContactDistanceThreshold(Math.max(0, Number(p.rfantibody_target_contact_distance_threshold) || 0));
                            loaded.push('rfantibody_target_contact_distance_threshold');
                        }
                        if (typeof p.protect_tetrad === 'boolean') { setProtectTetrad(p.protect_tetrad); loaded.push('protect_tetrad'); }
                        else if (typeof p.protect_vhh_tetrad === 'boolean') { setProtectTetrad(p.protect_vhh_tetrad); loaded.push('protect_vhh_tetrad'); }
                        if (p.uploaded_path) { loaded.push('uploaded_path'); } else { skipped.push('uploaded_path'); }
                        if (p.target_source) {
                            loaded.push('target_source');
                        }
                        queueRestoredSelection(p);
                        restoreTargetFromSaved(p).catch((err) => console.error('[TEMPLATE_LOAD] Failed to restore target state:', err));
                        if (p.selected_chain || p.antigen_chains) { loaded.push('selected_chain'); } else { skipped.push('selected_chain'); }
                        if (getSavedResidueSelection(p).length > 0) { loaded.push('selected_residues'); } else { skipped.push('selected_residues'); }
                        if (p.target_dna_seq) { setTargetDnaSeq(p.target_dna_seq); setShowDnaInput(true); loaded.push('target_dna_seq'); }
                        // Quality settings - check both old and new field names
                        const hasQualityOverrides = Boolean(
                            p.quality_settings ||
                            p.qualitySettings ||
                            (Object.keys(PRESETS.balanced) as Array<keyof QualitySettings>).some((key) => p[key] !== undefined)
                        );
                        if (hasQualityOverrides) {
                            setQualitySettings(mergeQualitySettingsFromParams(p));
                            loaded.push('quality_settings');
                        }
                        // Manual CDR definitions - deserialize from arrays
                        if (Array.isArray(p.manual_cdr_definitions)) {
                            const defs = p.manual_cdr_definitions.map((d: UntypedApiValue) => ({
                                ...d,
                                residues: new Set(d.residues || [])
                            }));
                            setManualCDRDefinitions(defs);
                            setShowCDREditor(defs.length > 0);
                            loaded.push('manual_cdr_definitions');
                        }
                        if (p.custom_framework_path && p.framework_type !== 'sabdab') {
                            setCustomFrameworkPath(p.custom_framework_path);
                            loaded.push('custom_framework_path');
                        }
                        if (p.sabdab_framework) {
                            setSabdabFramework(p.sabdab_framework);
                            loaded.push('sabdab_framework');
                        }
                        restoreFrameworkPreview(p).catch((err) => console.error('[TEMPLATE_LOAD] Failed to restore framework state:', err));

                        console.log('[TEMPLATE_LOAD] Loaded fields:', loaded.join(', '));
                        console.log('[TEMPLATE_LOAD] Skipped fields (not in template):', skipped.join(', '));
                        console.log('[TEMPLATE_LOAD] Successfully loaded template ✓');
                    } catch (err) {
                        console.error('[TEMPLATE_LOAD] Error loading template:', err);
                    }
                }}
                currentParams={{
                    // Core settings
                    job_name: jobName,
                    denovo_generator: deNovoGenerator,
                    generator: deNovoGenerator,
                    stage_family: deNovoGenerator === 'ppiflow' ? 'ppiflow' : deNovoGenerator === 'boltzgen' ? 'boltzgen' : undefined,
                    stage_mode: deNovoGenerator === 'ppiflow' ? 'generator_backbone_refine' : deNovoGenerator === 'boltzgen' ? 'nanobody_binder' : undefined,
                    initial_orchestration_sequence_design: deNovoStageSelection.sequence_design,
                    initial_orchestration_ppiflow: deNovoStageSelection.ppiflow,
                    initial_orchestration_validation: deNovoStageSelection.validation,
                    initial_orchestration_qc: deNovoStageSelection.qc,
                    framework_type: (deNovoGenerator === 'boltzgen' || deNovoGenerator === 'ppiflow') ? 'nanobody' : frameworkType,
                    framework_pdb: frameworkType === 'sabdab'
                        ? (sabdabFramework?.filePath || customFrameworkPath || undefined)
                        : (customFrameworkPath || undefined),
                    seq_designer: seqDesigner,
                    rfantibody_num_designs: numDesigns,
                    seqs_per_design: seqsPerDesign,
                    run_immunogenicity_scoring: useAntiberty,
                    run_thermompnn: qualitySettings.run_thermompnn,
                    run_stability_scoring: qualitySettings.run_thermompnn,
                    run_structure_validation: runStructureValidation,
                    msa_preset: qualitySettings.msa_preset,
                    structure_validator: structureValidator,
                    protenix_model_weights: qualitySettings.protenix_model_weights,
                    protenix_seeds: qualitySettings.protenix_seeds,
                    protenix_n_sample: qualitySettings.protenix_n_sample,
                    protenix_n_step: qualitySettings.protenix_n_step,
                    protenix_n_cycle: qualitySettings.protenix_n_cycle,
                    protenix_use_msa: qualitySettings.protenix_use_msa,
                    protenix_msa_backend: qualitySettings.protenix_msa_backend,
                    protenix_use_template: qualitySettings.protenix_use_template,
                    protenix_anchor_target: qualitySettings.protenix_anchor_target,
                    protenix_anchor_strict: qualitySettings.protenix_anchor_strict,
                    protenix_enable_cache: qualitySettings.protenix_enable_cache,
                    protenix_enable_fusion: qualitySettings.protenix_enable_fusion,
                    boltz_anchor_target: qualitySettings.boltz_anchor_target,
                    boltz_anchor_strict: qualitySettings.boltz_anchor_strict,
                    protenix_auto_oom_retry: qualitySettings.protenix_auto_oom_retry,
                    protenix_oom_retry_attempts: qualitySettings.protenix_oom_retry_attempts,
                    colabfold_api_host: qualitySettings.colabfold_api_host.trim() || undefined,
                    msa_use_gpu: qualitySettings.msa_use_gpu,
                    msa_local_db: qualitySettings.msa_local_db.trim() || undefined,
                    msa_cache_dir: qualitySettings.msa_cache_dir.trim() || undefined,
                    msa_threads: qualitySettings.msa_threads ?? undefined,
                    msa_gpu_mode: qualitySettings.msa_gpu_mode,
                    msa_gpu_threshold: qualitySettings.msa_gpu_threshold,
                    msa_preferred_gpus: qualitySettings.msa_preferred_gpus.trim() || undefined,
                    msa_excluded_gpus: qualitySettings.msa_excluded_gpus.trim() || undefined,
                    msa_gpu_server_mode: qualitySettings.msa_gpu_server_mode,
                    msa_gpu_server_wait_timeout: qualitySettings.msa_gpu_server_wait_timeout,
                    msa_gpu_server_db_load_mode: qualitySettings.msa_gpu_server_db_load_mode,
                    msa_gpu_server_startup_wait: qualitySettings.msa_gpu_server_startup_wait,
                    fampnn_checkpoint: resolvedFampnnCheckpoint,
                    fampnn_checkpoint_path: qualitySettings.fampnn_checkpoint_path,
                    lock_target_chains: qualitySettings.lock_target_chains,
                    lock_antibody_framework: qualitySettings.lock_antibody_framework,
                    run_ppiflow_backbone_refine: runPpiFlowBackboneRefine,
                    run_ppiflow_maturation: runPpiFlowMaturation,
                    run_maturation: runPpiFlowMaturation,
                    ppiflow_stage_mode: ppiflowStageMode,
                    ppiflow_tuning_profile: qualitySettings.ppiflow_tuning_profile,
                    ppiflow_backbone_region_mode: ppiflowBackboneRegionMode,
                    ppiflow_maturation_region_mode: ppiflowMaturationRegionMode,
                    ppiflow_backbone_loop_scope: effectivePpiFlowBackboneLoopScope || undefined,
                    ppiflow_maturation_loop_scope: effectivePpiFlowMaturationLoopScope || undefined,
                    ppiflow_objective_mode: qualitySettings.ppiflow_objective_mode,
                    ppiflow_objective_threshold: qualitySettings.ppiflow_objective_threshold,
                    run_post_validation_maturation: false,
                    run_post_boltz_maturation: false,
                    run_frustrampnn: runFrustrampnn,
                    run_anarcii_post: runAnarciiPost,
                    anarcii_include_children: anarciiIncludeChildren,
                    interactive_swa: interactiveWorkflow,
                    interactive_gating: interactiveWorkflow,
                    interactive_gate_stage: interactiveGateStage,
                    parallel_mode: parallelMode,
                    designs_per_job: designsPerJob,
                    pdbs_per_job: pdBsPerJob,
                    seqs_per_boltz_job: seqsPerBoltzJob,
                    seqs_per_validation_job: seqsPerBoltzJob,
                    // Design mode
                    design_mode: designMode,
                    antibody_design_mode: designMode,
                    selected_cdr_loops: Array.from(selectedCDRLoops),
                    antibody_design_loops: Array.from(selectedCDRLoops).join(','),
                    rfantibody_loop_length_mode: rfantibodyLoopLengthMode,
                    rfantibody_loop_length_ranges_config: rfantibodyLoopLengthRanges,
                    rfantibody_loop_length_ranges: rfantibodyLoopLengthMode === 'custom_ranges'
                        ? `[${Array.from(selectedCDRLoops)
                            .sort()
                            .filter((loopId) => frameworkType !== 'nanobody' || loopId.startsWith('H'))
                            .map((loopId) => {
                                const range = rfantibodyLoopLengthRanges[loopId] || DEFAULT_RFA_LOOP_LENGTH_RANGES[loopId];
                                const min = Math.max(1, Number(range?.min) || 1);
                                const max = Math.max(min, Number(range?.max) || min);
                                return `${loopId}:${min}${max !== min ? `-${max}` : ''}`;
                            })
                            .join(',')}]`
                        : undefined,
                    enable_rfantibody_filter: enableRfantibodyFilter,
                    rfantibody_screen_reference_scope: rfantibodyScreenReferenceScope,
                    rfantibody_min_epitope_contacts: rfantibodyMinEpitopeContacts,
                    rfantibody_max_epitope_distance: rfantibodyMaxEpitopeDistance,
                    rfantibody_min_target_contacts: rfantibodyMinTargetContacts,
                    rfantibody_max_target_distance: rfantibodyMaxTargetDistance > 0 ? rfantibodyMaxTargetDistance : undefined,
                    rfantibody_max_epitope_centroid_distance: rfantibodyMaxEpitopeCentroidDistance,
                    rfantibody_contact_distance_threshold: rfantibodyContactDistanceThreshold,
                    rfantibody_target_contact_distance_threshold: rfantibodyTargetContactDistanceThreshold,
                    protect_tetrad: protectTetrad,
                    protect_vhh_tetrad: protectTetrad,
                    boltzgen_mode: deNovoGenerator === 'boltzgen' ? 'nanobody_binder' : undefined,
                    boltzgen_use_framework_template: deNovoGenerator === 'boltzgen' ? boltzgenUseFrameworkTemplate : undefined,
                    boltzgen_scaffold_source: deNovoGenerator === 'boltzgen' ? boltzgenScaffoldSource : undefined,
                    boltzgen_view_reference_structure: deNovoGenerator === 'boltzgen' ? boltzgenViewReferenceStructure : undefined,
                    boltzgen_batch_size: deNovoGenerator === 'boltzgen' ? boltzgenBatchSize : undefined,
                    boltzgen_parallel_mode: deNovoGenerator === 'boltzgen' ? boltzgenParallelMode : undefined,
                    boltzgen_designs_per_job: deNovoGenerator === 'boltzgen' ? boltzgenDesignsPerJob : undefined,
                    boltzgen_reuse: deNovoGenerator === 'boltzgen' ? boltzgenReuseExisting : undefined,
                    boltzgen_scaffold_length: deNovoGenerator === 'boltzgen' ? boltzgenScaffoldLength : undefined,
                    boltzgen_nanobody_framework: deNovoGenerator === 'boltzgen' && boltzgenUseFrameworkTemplate ? boltzgenNanobodyFramework : undefined,
                    boltzgen_cdr_h1_length: deNovoGenerator === 'boltzgen' ? boltzgenCdrH1Length : undefined,
                    boltzgen_cdr_h2_length: deNovoGenerator === 'boltzgen' ? boltzgenCdrH2Length : undefined,
                    boltzgen_cdr_h3_length: deNovoGenerator === 'boltzgen' ? boltzgenCdrH3Length : undefined,
                    boltzgen_checkpoint_mode: deNovoGenerator === 'boltzgen' ? boltzgenCheckpointMode : undefined,
                    boltzgen_skip_inverse_folding: deNovoGenerator === 'boltzgen' ? boltzgenSkipInverseFolding : undefined,
                    boltzgen_inverse_fold_avoid: deNovoGenerator === 'boltzgen' ? boltzgenInverseFoldAvoid : undefined,
                    boltzgen_inverse_fold_num_sequences: deNovoGenerator === 'boltzgen' ? boltzgenInverseFoldNumSequences : undefined,
                    boltzgen_avoid_cysteine: deNovoGenerator === 'boltzgen' ? boltzgenAvoidCysteine : undefined,
                    boltzgen_step_scale: deNovoGenerator === 'boltzgen' ? boltzgenStepScale : undefined,
                    boltzgen_noise_scale: deNovoGenerator === 'boltzgen' ? boltzgenNoiseScale : undefined,
                    boltzgen_budget: deNovoGenerator === 'boltzgen' ? boltzgenBudget : undefined,
                    boltzgen_alpha: deNovoGenerator === 'boltzgen' ? boltzgenAlpha : undefined,
                    boltzgen_max_rmsd: deNovoGenerator === 'boltzgen' ? boltzgenMaxRmsd : undefined,
                    boltzgen_min_plddt: deNovoGenerator === 'boltzgen' ? boltzgenMinPlddt : undefined,
                    boltzgen_min_conf_score: deNovoGenerator === 'boltzgen' ? boltzgenMinConfScore : undefined,
                    boltzgen_filter_biased: deNovoGenerator === 'boltzgen' ? boltzgenFilterBiased : undefined,
                    boltzgen_metrics_override: deNovoGenerator === 'boltzgen' ? (boltzgenMetricsOverride.trim() || undefined) : undefined,
                    boltzgen_additional_filters: deNovoGenerator === 'boltzgen' ? (boltzgenAdditionalFilters.trim() || undefined) : undefined,
                    boltzgen_size_buckets: deNovoGenerator === 'boltzgen' ? (boltzgenSizeBuckets.trim() || undefined) : undefined,
                    ppiflow_seed_input_dir: ppiflowSeedInputDir.trim() || undefined,
                    ppiflow_seed_complex_path: ppiflowSeedComplexPath || undefined,
                    // Framework protection (for framework_allowed and full_design modes)
                    protected_positions: frameworkProtection.protectedPositions.join(','),
                    protect_disulfides: frameworkProtection.protectDisulfides,
                    protect_fr_contacts: frameworkProtection.protectFrContacts,
                    // Target info - now includes full source context
                    target_pdb: uploadedPath || targetSource?.path || undefined,
                    target_model_number: selectedTargetModel || undefined,
                    target_source: targetSource,
                    uploaded_path: uploadedPath,
                    selected_chain: selectedChain,
                    antigen_chains: deNovoGenerator === 'ppiflow' ? (ppiflowSeedAntigenChains.trim() || selectedChain || undefined) : selectedChain,
                    antibody_chains: deNovoGenerator === 'ppiflow' ? (ppiflowSeedAntibodyChains.trim() || 'H') : undefined,
                    selected_residues: Array.from(selectedResidues),
                    epitope_residues: Array.from(selectedResidues).sort().join(','),
                    target_dna_seq: targetDnaSeq.trim() || undefined,
                    pinned_gpus: pinnedGpus,
                    lock_gpus: lockGpus,
                    out_dir: customOutputDir.trim() || undefined,
                    // Quality settings
                    quality_settings: qualitySettings,
                    sabdab_framework: sabdabFramework ? {
                        type: sabdabFramework.type,
                        id: sabdabFramework.id,
                        name: sabdabFramework.name,
                        pdbCode: sabdabFramework.pdbCode,
                        sequence: sabdabFramework.sequence,
                        filePath: sabdabFramework.filePath,
                        cdrH3Length: sabdabFramework.cdrH3Length,
                        hChain: sabdabFramework.hChain,
                        lChain: sabdabFramework.lChain,
                        antigenChain: sabdabFramework.antigenChain,
                    } : null,
                    custom_framework_path: frameworkType === 'sabdab'
                        ? (sabdabFramework?.filePath || customFrameworkPath || undefined)
                        : customFrameworkPath,
                    // Manual CDR definitions - serialize Sets to arrays
                    manual_cdr_definitions: manualCDRDefinitions.map(d => ({
                        ...d,
                        residues: Array.from(d.residues)
                    })),
                }}
                currentModelId="template_antibody_denovo"
                currentMode={isRefinementMode ? ANTIBODY_REFINEMENT_PIPELINE_MODE : ANTIBODY_DENOVO_PIPELINE_MODE}
                baseTemplateId="antibody_denovo"
            />
        </div >
    );
};

export default AntibodyDenovoTemplate;
