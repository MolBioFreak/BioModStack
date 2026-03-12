import React, { useState, useEffect, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob, uploadFile, extractChain, annotateFrameworkCdrs, downloadSabdabFramework, launchAntibodyIteration, launchManualMutagenesis, type CDRAnnotationResponse } from '../lib/api';
import { useNavigate, useLocation } from 'react-router-dom';
import { parsePDBFile, type Chain } from '../utils/pdbUtils';
import { EpitopeSelector } from './EpitopeSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { DesignModeSelector } from './DesignModeSelector';
import { QualitySettingsPanel, PRESETS, type QualitySettings, type QualityPreset } from './QualitySettingsPanel';
import { TemplateManagerModal } from './TemplateManagerModal';
import { FrameworkBrowser, type SelectedFramework } from './FrameworkBrowser';
import { FrameworkEditor, type FrameworkEditorState } from './FrameworkEditor';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings, DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './PhysicsRefinementPanel';
import { CDRRangeSelector, type CDRDefinition } from './CDRRangeSelector';

interface AntibodyDenovoTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, any>;
}

type DesignMode = 'cdr_only' | 'cdr_selective' | 'framework_allowed' | 'full_design';
type LoopLengthMode = 'defaults' | 'custom_ranges';
type LoopLengthRange = { min: number; max: number };
type InteractiveGateStage = 'post_rfantibody' | 'post_fampnn' | 'post_structure_validation';
type SeqDesigner = 'none' | 'fampnn' | 'antifold' | 'proteinmpnn';
type RefinementPreset = 'full_loop' | 'fampnn_only' | 'validation_only' | 'ppiflow_only' | 'manual_mutagenesis' | 'custom';
type MutagenesisMethod = 'explicit_substitutions' | 'cdr_indels';

const DEFAULT_RFA_LOOP_LENGTH_RANGES: Record<string, LoopLengthRange> = {
    H1: { min: 7, max: 10 },
    H2: { min: 6, max: 8 },
    H3: { min: 5, max: 15 },
    L1: { min: 8, max: 13 },
    L2: { min: 7, max: 7 },
    L3: { min: 9, max: 11 },
};

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
        Object.entries(raw as Record<string, any>).forEach(([loopId, value]) => {
            if (!parsed[loopId] || !value || typeof value !== 'object') return;
            const min = Number((value as any).min);
            const max = Number((value as any).max);
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

export const AntibodyDenovoTemplate: React.FC<AntibodyDenovoTemplateProps> = ({ onBack, initialValues }) => {
    const location = useLocation();
    const refinementState = location.state as {
        refinementMode?: boolean;
        sourceJobId?: string;
        selectedDesignIds?: string[];
    } | null;
    const isRefinementMode = !!refinementState?.refinementMode;
    const refinementParentJobId = refinementState?.sourceJobId;
    const refinementDesignIds = refinementState?.selectedDesignIds;

    const restoringSelectionRef = useRef<{ chain: string | null; residues: string[] } | null>(null);

    const normalizeProtenixModel = (model?: string) => {
        if (!model) return 'protenix_base_20250630_v1.0.0';
        if (model === 'protenix_base_20241211_v0.2.1') return 'protenix_base_default_v1.0.0';
        if (model === 'protenix_esm_20241211_v0.2.1') return 'protenix_mini_esm_v0.5.0';
        return model;
    };
    const mergeQualitySettingsFromParams = (params?: Record<string, any>): QualitySettings => {
        const merged = {
            ...PRESETS.balanced,
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
                (merged as any)[key] = key === 'protenix_model_weights'
                    ? normalizeProtenixModel(params[key])
                    : params[key];
            }
        });

        return merged;
    };

    const [jobName, setJobName] = useState('antibody_design');
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(initialValues?.pinned_gpus ?? []);
    const [lockGpus, setLockGpus] = useState(false);
    const [targetPdb, setTargetPdb] = useState<File | null>(null);
    const [targetSource, setTargetSource] = useState<{ type: string; url?: string; path?: string; designId?: string; pdbId?: string; name?: string } | null>(null);
    const [numDesigns, setNumDesigns] = useState(10);
    const [seqDesigner, setSeqDesigner] = useState<SeqDesigner>('fampnn');
    const [fampnnConstraintMode, setFampnnConstraintMode] = useState<'generic' | 'antibody'>('antibody');
    const [useAntiberty, setUseAntiberty] = useState(false);  // Disabled by default, planned for removal
    const [useThermoMPNN, setUseThermoMPNN] = useState(true);  // Controlled via qualitySettings.run_thermompnn
    const [runFrustrampnn, setRunFrustrampnn] = useState(false);
    const [runStructureValidation, setRunStructureValidation] = useState(initialValues?.run_structure_validation !== false);
    const [runAnarciiPost, setRunAnarciiPost] = useState(false);
    const [anarciiIncludeChildren, setAnarciiIncludeChildren] = useState(true);
    const [interactiveWorkflow, setInteractiveWorkflow] = useState(
        initialValues?.interactive_swa ?? initialValues?.interactive_gating ?? false
    );
    const [interactiveGateStage, setInteractiveGateStage] = useState<InteractiveGateStage>(
        initialValues?.interactive_gate_stage === 'post_structure_validation'
            ? 'post_structure_validation'
            : initialValues?.interactive_gate_stage === 'post_rfantibody'
                ? 'post_rfantibody'
                : 'post_fampnn'
    );
    const [structureValidator, setStructureValidator] = useState<'boltz2' | 'protenix'>(
        initialValues?.structure_validator === 'protenix' ? 'protenix' : 'boltz2'
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
    // Manual CDR definitions (for custom loop positions)
    const [manualCDRDefinitions, setManualCDRDefinitions] = useState<CDRDefinition[]>([]);
    const [showCDREditor, setShowCDREditor] = useState(false);

    // Quality settings
    const [qualityPreset, setQualityPreset] = useState<QualityPreset>((initialValues?.quality_preset as QualityPreset) || 'balanced');
    const [qualitySettings, setQualitySettings] = useState<QualitySettings>(() => mergeQualitySettingsFromParams(initialValues));
    const resolvedFampnnCheckpoint = qualitySettings.fampnn_checkpoint.trim() || PRESETS.balanced.fampnn_checkpoint;
    const resolvedPpiFlowCheckpoint = qualitySettings.ppiflow_checkpoint.trim() || PRESETS.balanced.ppiflow_checkpoint;

    // Physics refinement settings (OpenMM)
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

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

    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [parsedFrameworkChains, setParsedFrameworkChains] = useState<Chain[]>([]);
    const [selectedChain, setSelectedChain] = useState<string | null>(null);
    const [selectedResidues, setSelectedResidues] = useState<Set<string>>(new Set());
    const [isParsing, setIsParsing] = useState(false);
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);
    const [show3DViewer, setShow3DViewer] = useState(false);  // 3D viewer toggle, off by default

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
    const [customOutputDir, setCustomOutputDir] = useState<string>('');
    const [refinementPreset, setRefinementPreset] = useState<RefinementPreset>(isRefinementMode ? 'full_loop' : 'custom');
    const [useManualMutagenesis, setUseManualMutagenesis] = useState(false);
    const [mutagenesisMethod, setMutagenesisMethod] = useState<MutagenesisMethod>('explicit_substitutions');
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
    const availableDesignLoops = isSingleDomainFramework
        ? ['H1', 'H2', 'H3']
        : ['H1', 'H2', 'H3', 'L1', 'L2', 'L3'];
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
    }, [availableDesignLoopKey]);

    const buildFilesApiUrl = (mode: 'download' | 'pdb', path: string) =>
        `/api/files/${mode}/${encodeURIComponent(path)}`;

    const loadPdbFileFromUrl = async (url: string, fallbackName: string) => {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const fileName = fallbackName.toLowerCase().endsWith('.pdb') ? fallbackName : `${fallbackName}.pdb`;
        return new File([blob], fileName, { type: 'chemical/x-pdb' });
    };

    const loadSabdabFrameworkFile = async (pdbCode: string, fallbackName: string) => {
        const response = await downloadSabdabFramework(pdbCode, {
            scheme: 'imgt',
            convert_hlt: true,
            include_content: true,
        });
        const data = response.data as any;
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
    };

    const restoreFrameworkPreview = async (saved: Record<string, any>) => {
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
                setFrameworkPdbUrl(url);
                const fwFile = new File([blob], `${savedFramework.pdbCode || savedFramework.name || 'framework'}.pdb`);
                const parsed = await parsePDBFile(fwFile);
                setParsedFrameworkChains(parsed.chains);
                return;
            }

            if (!savedFramework.pdbCode) return;

            const hydrated = await loadSabdabFrameworkFile(
                savedFramework.pdbCode,
                `${savedFramework.pdbCode || savedFramework.name || 'framework'}.pdb`
            );
            setSabdabFramework((prev) => prev ? { ...prev, filePath: hydrated.filePath || prev.filePath } : prev);
            setCustomFrameworkPath(hydrated.filePath || preferredSabdabPath);
            setFrameworkPdbUrl(hydrated.url);
            const fwFile = hydrated.file;
            const parsed = await parsePDBFile(fwFile);
            setParsedFrameworkChains(parsed.chains);
            return;
        }

        if (saved.framework_type === 'custom' && savedFrameworkPath) {
            setSabdabFramework(null);
            setCustomFrameworkPath(savedFrameworkPath);
            setViewerMode('framework');
            setShow3DViewer(true);
            const fwUrl = buildFilesApiUrl('download', savedFrameworkPath);
            setFrameworkPdbUrl(fwUrl);
            const fwFile = await loadPdbFileFromUrl(fwUrl, savedFrameworkPath.split('/').pop() || 'framework.pdb');
            setCustomFrameworkFile(fwFile);
            const parsed = await parsePDBFile(fwFile);
            setParsedFrameworkChains(parsed.chains);
            return;
        }

        setSabdabFramework(null);
    };

    const getSavedResidueSelection = (saved: Record<string, any>): string[] => {
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
    };

    const queueRestoredSelection = (saved: Record<string, any>) => {
        const residues = getSavedResidueSelection(saved);
        const chain = saved.selected_chain || saved.antigen_chains || null;
        restoringSelectionRef.current = { chain, residues };
        if (chain) {
            setSelectedChain(chain);
        }
        setSelectedResidues(new Set(residues));
    };

    useEffect(() => {
        const queuedRestore = restoringSelectionRef.current;
        if (!queuedRestore || parsedChains.length === 0) {
            return;
        }
        const chainIds = parsedChains.map((chain) => chain.id);
        if (!queuedRestore.chain || chainIds.includes(queuedRestore.chain)) {
            if (queuedRestore.chain) {
                setSelectedChain(queuedRestore.chain);
            }
            setSelectedResidues(new Set(queuedRestore.residues));
            restoringSelectionRef.current = null;
        }
    }, [parsedChains]);

    const restoreTargetFromSaved = async (saved: Record<string, any>) => {
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
    };

    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const submitMutation = useMutation({
        mutationFn: async (data: any) => submitJob(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        }
    });

    const launchMutagenesisMutation = useMutation({
        mutationFn: async (data: any) => launchManualMutagenesis(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
    });

    const applyRefinementPreset = (preset: RefinementPreset) => {
        setRefinementPreset(preset);
        setUseManualMutagenesis(preset === 'manual_mutagenesis');
        if (preset === 'manual_mutagenesis') {
            setManualMutagenesisConfig((current) => ({ ...current, predictor: structureValidator }));
            setSeqDesigner('none');
            setRunStructureValidation(false);
            setRunFrustrampnn(false);
            setQualitySettings((current) => ({ ...current, run_maturation: false }));
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
            setQualitySettings((current) => ({ ...current, run_maturation: false }));
            setInteractiveWorkflow(true);
            setInteractiveGateStage('post_fampnn');
            return;
        }
        if (preset === 'validation_only') {
            setSeqDesigner('none');
            setRunStructureValidation(true);
            setRunFrustrampnn(false);
            setQualitySettings((current) => ({ ...current, run_maturation: false }));
            setInteractiveWorkflow(true);
            setInteractiveGateStage('post_structure_validation');
            return;
        }
        if (preset === 'ppiflow_only') {
            setSeqDesigner('none');
            setRunStructureValidation(false);
            setRunFrustrampnn(false);
            setQualitySettings((current) => ({ ...current, run_maturation: true }));
            setInteractiveWorkflow(false);
        }
    };

    // Initialize from initialValues (Clone Job)
    useEffect(() => {
        if (initialValues) {
            console.log('[ANTIBODY_DENOVO] Initializing from values:', initialValues);
            setQualitySettings(mergeQualitySettingsFromParams(initialValues));
            if (initialValues.quality_preset) {
                setQualityPreset(initialValues.quality_preset);
            }

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
                    initialValues.interactive_gate_stage === 'post_structure_validation' ||
                    initialValues.interactive_gate_stage === 'post_fampnn'
                )
            ) {
                setInteractiveGateStage(initialValues.interactive_gate_stage);
            }
            // Handling renamed/mapped boolean params if any
            if (initialValues.use_antiberty !== undefined) setUseAntiberty(initialValues.use_antiberty);
            if (initialValues.use_thermompnn !== undefined) setUseThermoMPNN(initialValues.use_thermompnn);
            if (Array.isArray(initialValues.pinned_gpus)) setPinnedGpus(initialValues.pinned_gpus);
            if (typeof initialValues.lock_gpus === 'boolean') setLockGpus(initialValues.lock_gpus);
            if (initialValues.parallel_mode) setParallelMode(initialValues.parallel_mode);
            if (initialValues.designs_per_job) setDesignsPerJob(initialValues.designs_per_job);
            if (initialValues.pdbs_per_job) setPdBsPerJob(initialValues.pdbs_per_job);
            else if (initialValues.seqs_per_job) setPdBsPerJob(initialValues.seqs_per_job);
            if (initialValues.target_dna_seq) {
                setTargetDnaSeq(initialValues.target_dna_seq);
                setShowDnaInput(true);
            }

            // Sequence Designer
            if (initialValues.seq_design_fampnn) setSeqDesigner('fampnn');
            else if (initialValues.seq_design_antifold) setSeqDesigner('antifold');
            else if (initialValues.seq_design_proteinmpnn) setSeqDesigner('proteinmpnn');
            else if (
                initialValues.seq_design_fampnn === false &&
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
                const defs = initialValues.manual_cdr_definitions.map((d: any) => ({
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
    }, [initialValues]);

    // Auto-parse PDB when file is selected
    useEffect(() => {
        // Clean up old blob URL
        if (pdbBlobUrl) {
            URL.revokeObjectURL(pdbBlobUrl);
            setPdbBlobUrl(null);
        }

        if (targetPdb) {
            setIsParsing(true);

            // Create blob URL for Molstar viewer
            const blobUrl = URL.createObjectURL(targetPdb);
            setPdbBlobUrl(blobUrl);

            parsePDBFile(targetPdb)
                .then(result => {
                    setParsedChains(result.chains);
                    // Auto-select first chain with most residues IF NOT ALREADY SELECTED (e.g. by clone)
                    // If clone set selectedChain, verify it exists, otherwise fallback
                    if (result.chains.length > 0) {
                        const chainIds = result.chains.map(c => c.id);
                        const queuedRestore = restoringSelectionRef.current;
                        if (queuedRestore?.chain && chainIds.includes(queuedRestore.chain)) {
                            setSelectedChain(queuedRestore.chain);
                            setSelectedResidues(new Set(queuedRestore.residues));
                            restoringSelectionRef.current = null;
                        } else if (!selectedChain || !chainIds.includes(selectedChain)) {
                            const longestChain = result.chains.reduce((a, b) =>
                                a.length > b.length ? a : b
                            );
                            setSelectedChain(longestChain.id);
                            // Clear selection only if we CHANGED the chain automatically
                            if (!queuedRestore) setSelectedResidues(new Set());
                            restoringSelectionRef.current = null;
                        } else if (queuedRestore) {
                            setSelectedResidues(new Set(queuedRestore.residues));
                            restoringSelectionRef.current = null;
                        }
                    }
                    if (!uploadedPath && !initialValues) setUploadedPath(null); // Only clear if new upload
                    console.log('[ANTIBODY_DENOVO] Parsed PDB:', result.chains.map(c => `${c.id}:${c.length}aa`));
                })
                .catch(err => {
                    console.error('[ANTIBODY_DENOVO] Failed to parse PDB:', err);
                    setParsedChains([]);
                })
                .finally(() => setIsParsing(false));
        } else {
            setParsedChains([]);
            setSelectedChain(null);
            if (!restoringSelectionRef.current) {
                setSelectedResidues(new Set());
            }
        }
    }, [targetPdb]);

    // Parse custom framework PDB for accurate CDR mapping when uploaded.
    useEffect(() => {
        if (frameworkType !== 'custom') return;
        if (!customFrameworkFile) {
            setParsedFrameworkChains([]);
            return;
        }

        parsePDBFile(customFrameworkFile)
            .then((result) => setParsedFrameworkChains(result.chains))
            .catch((err) => {
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
        const skippingEarlySteps = skipRFantibody || skipFampnn;
        const runSequenceDesign = seqDesigner !== 'none';

        if (!skippingEarlySteps && !targetPdb) {
            alert('Please upload a target PDB file');
            return;
        }
        if (!skippingEarlySteps && selectedResidues.size === 0) {
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
        const needsFampnnCheckpoint = seqDesigner === 'fampnn' || qualitySettings.run_maturation;
        if (needsFampnnCheckpoint && !fampnnCheckpointSpecified) {
            alert('Please choose FAMPNN weights or provide a checkpoint path before submitting.');
            return;
        }

        if (isRefinementMode && !useManualMutagenesis && !runSequenceDesign && !qualitySettings.run_maturation && !runStructureValidation && !runFrustrampnn) {
            alert('Enable at least one refinement stage before launching.');
            return;
        }

        // Validate that a SAbDab framework was actually selected
        if (frameworkType === 'sabdab' && !sabdabFramework?.pdbCode) {
            alert('Please select a specific framework from the SAbDab database before submitting, or select a different framework preset.');
            return;
        }

        try {
            // Step 1: Determine PDB path based on source
            // - targetSource.path: file from previous run, preset, or RCSB PDB  
            // - uploadedPath: manually uploaded file (already on server)
            // - handleFileUpload: new file upload (needs to be uploaded first)
            // - When skipping, use a placeholder or the input dir path
            let pdbPath = targetSource?.path || uploadedPath;
            if (!pdbPath && targetPdb) {
                pdbPath = await handleFileUpload(targetPdb);
            }
            // When skipping or in refinement mode, don't require a target PDB
            if (!pdbPath && (skippingEarlySteps || isRefinementMode)) {
                pdbPath = isRefinementMode ? 'refinement_mode' : skipRFantibody ? rfantibodyInputPdbs : fampnnCollectedPdbs;
            }

            if (!pdbPath) {
                alert('Failed to determine PDB file path');
                return;
            }

            // Step 1b: Extract selected chain if multi-chain PDB with specific chain selected
            // This ensures only the target chain is sent to design pipelines
            if (selectedChain && parsedChains.length > 1) {
                console.log(`[ANTIBODY_DENOVO] Extracting chain ${selectedChain} from multi-chain PDB`);
                try {
                    const extractResult = await extractChain(pdbPath, selectedChain);
                    pdbPath = extractResult.data.output_path;
                    console.log(`[ANTIBODY_DENOVO] Extracted chain to: ${pdbPath}`);
                } catch (err) {
                    console.error('[ANTIBODY_DENOVO] Chain extraction failed:', err);
                    alert(`Failed to extract chain ${selectedChain}: ${err}`);
                    return;
                }
            }

            // Format selected residues for backend
            const epitopeString = Array.from(selectedResidues).sort().join(',');

            // Determine pipeline steps
            const pipelineSteps = [isRefinementMode ? 'selected_inputs' : 'rfantibody'];
            if (runSequenceDesign) pipelineSteps.push(seqDesigner);
            if (qualitySettings.run_maturation) pipelineSteps.push('ppiflow');
            if (useAntiberty) pipelineSteps.push('antiberty');
            if (useThermoMPNN) pipelineSteps.push('thermompnn');
            if (runStructureValidation) pipelineSteps.push(structureValidator === 'protenix' ? 'protenix' : 'boltz2');
            if (runFrustrampnn) pipelineSteps.push('frustrampnn');

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

            const jobData = {
                name: jobName,
                model_id: 'template_antibody_denovo',
                mode: 'antibody_denovo_pipeline', // Matches main.nf logic
                pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null,
                params: {
                    target_pdb: isRefinementMode ? undefined : pdbPath,
                    pdb_source: isRefinementMode ? undefined : 'upload',
                    epitope_residues: isRefinementMode ? undefined : epitopeString,
                    antigen_chains: isRefinementMode ? undefined : selectedChain || undefined, // Send selected chain
                    pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                    lock_gpus: lockGpus && pinnedGpus.length > 0, // GPU locking
                    // Framework configuration
                    framework_type: effectiveFrameworkType,
                    framework_pdb: frameworkPath || undefined, // Only if custom or sabdab
                    // Pipeline configuration
                    rfd_mode: 'antibody_denovo_pipeline', // Explicitly set for backend mapping
                    antibody_pipeline_steps: pipelineSteps,
                    rfantibody_num_designs: numDesigns,
                    seq_design_fampnn: seqDesigner === 'fampnn',
                    seq_design_antifold: seqDesigner === 'antifold',
                    seq_design_proteinmpnn: seqDesigner === 'proteinmpnn',
                    run_immunogenicity_scoring: useAntiberty,
                    run_stability_scoring: qualitySettings.run_thermompnn,
                    run_structure_validation: runStructureValidation,
                    structure_validator: structureValidator,
                    run_frustrampnn: runFrustrampnn,
                    run_anarcii_post: runAnarciiPost,
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
                    rfantibody_min_epitope_contacts: enableRfantibodyFilter ? rfantibodyMinEpitopeContacts : undefined,
                    rfantibody_max_epitope_distance: enableRfantibodyFilter ? rfantibodyMaxEpitopeDistance : undefined,
                    rfantibody_min_target_contacts: enableRfantibodyFilter ? rfantibodyMinTargetContacts : undefined,
                    rfantibody_max_epitope_centroid_distance: enableRfantibodyFilter ? rfantibodyMaxEpitopeCentroidDistance : undefined,
                    rfantibody_contact_distance_threshold: enableRfantibodyFilter ? rfantibodyContactDistanceThreshold : undefined,
                    rfantibody_target_contact_distance_threshold: enableRfantibodyFilter ? rfantibodyTargetContactDistanceThreshold : undefined,
                    protect_vhh_tetrad: protectTetrad,
                    antibody_chains: effectiveAntibodyType === 'vhh' ? 'H' : 'H,L',
                    // Quality settings - RFantibody (backbone diffusion)
                    rfantibody_diffusion_steps: Math.min(qualitySettings.rfantibody_diffusion_steps, 50),
                    rfantibody_noise_scale_ca: qualitySettings.rfantibody_noise_scale_ca,
                    rfantibody_noise_scale_frame: qualitySettings.rfantibody_noise_scale_frame,
                    rfantibody_guide_scale: qualitySettings.rfantibody_guide_scale,
                    // Structure validation settings
                    msa_preset: qualitySettings.msa_preset,
                    boltz_sampling_steps: qualitySettings.boltz_sampling_steps,
                    boltz_recycling_steps: qualitySettings.boltz_recycling_steps,
                    boltz_num_samples: qualitySettings.boltz_num_samples,
                    boltz_use_potentials: qualitySettings.boltz_use_potentials,
                    boltz_use_msa: qualitySettings.boltz_use_msa,
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
                    fampnn_constraint_mode: seqDesigner === 'fampnn' ? fampnnConstraintMode : undefined,
                    // PPIFlow maturation settings
                    run_maturation: qualitySettings.run_maturation,
                    run_post_validation_maturation: qualitySettings.run_maturation,
                    run_post_boltz_maturation: qualitySettings.run_maturation,
                    ppiflow_start_t: qualitySettings.ppiflow_start_t,
                    ppiflow_samples_per_target: qualitySettings.ppiflow_samples_per_target,
                    ppiflow_retry_limit: qualitySettings.ppiflow_retry_limit,
                    ppiflow_config: qualitySettings.ppiflow_config,
                    ppiflow_weights_dir: qualitySettings.ppiflow_weights_dir,
                    ppiflow_checkpoint_path: qualitySettings.ppiflow_checkpoint_path,
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
                    run_thermompnn: qualitySettings.run_thermompnn,
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
                    openmm_enabled: physicsSettings.enabled,
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

            if (isRefinementMode && refinementParentJobId && refinementDesignIds) {
                // Determine action based on UI settings
                // Nextflow determines the correct start based on skip flags which jobs.py injects
                const refinementOverrides = { ...jobData.params } as Record<string, any>;
                for (const key of [
                    'enable_rfantibody_filter',
                    'rfantibody_min_epitope_contacts',
                    'rfantibody_max_epitope_distance',
                    'rfantibody_min_target_contacts',
                    'rfantibody_max_epitope_centroid_distance',
                    'rfantibody_contact_distance_threshold',
                    'rfantibody_target_contact_distance_threshold',
                ]) {
                    delete refinementOverrides[key];
                }
                if (enableRfantibodyFilter) {
                    refinementOverrides.enable_rfantibody_filter = true;
                    refinementOverrides.rfantibody_min_epitope_contacts = rfantibodyMinEpitopeContacts;
                    refinementOverrides.rfantibody_max_epitope_distance = rfantibodyMaxEpitopeDistance;
                    refinementOverrides.rfantibody_min_target_contacts = rfantibodyMinTargetContacts;
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
                            action: 'cdr_indel_round',
                            design_ids: refinementDesignIds,
                            cdr_indel_config: cdrIndelConfig,
                            param_overrides: refinementOverrides,
                        });
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

                    await launchMutagenesisMutation.mutateAsync({
                        source_job_id: refinementParentJobId,
                        design_ids: refinementDesignIds,
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
                    design_ids: refinementDesignIds,
                    param_overrides: refinementOverrides,
                });
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
                        <h2 className="text-lg font-semibold text-slate-200">
                            {isRefinementMode ? 'Custom Refinement Round' : 'De Novo Antibody Design'}
                        </h2>
                        <p className="text-sm text-slate-500">
                            {isRefinementMode ? `Configuring a downstream orchestrator run for ${refinementDesignIds?.length} designs.` : 'Generate novel antibodies targeting an antigen'}
                        </p>
                    </div>
                </div>
            </div>

            {isRefinementMode && (
                <div className="bg-indigo-500/20 text-indigo-200 p-4 rounded-lg mb-6 border border-indigo-500/40 animate-in fade-in slide-in-from-top-4">
                    <div className="flex items-center gap-2 mb-2">
                        <svg className="w-5 h-5 text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                        <h3 className="font-semibold text-indigo-200 text-sm">Target & Epitope configuration disabled</h3>
                    </div>
                    <p className="text-xs opacity-90 leading-relaxed max-w-3xl">
                        You arrived here from an active interactive job (<code>{refinementParentJobId}</code>). The structural targets and complexes are fixed to the selected designs. Configure exactly how you want your {refinementDesignIds?.length} selections to be processed below.
                    </p>
                </div>
            )}

            {/* Pipeline Visualization */}
            <div className="mb-6 p-4 bg-slate-900/50 rounded-lg border border-slate-700/50">
                <div className="flex items-start justify-between gap-4 mb-3">
                    <div>
                        <h3 className="text-sm font-medium text-slate-300">Workflow Pipeline</h3>
                        <p className="mt-1 text-xs text-slate-500">
                            {isRefinementMode
                                ? 'Selected designs are re-queued through the workflow UI only. Choose which stages to rerun below.'
                                : 'Backbone generation, sequence design, optional maturation, structural validation, then optional review/QC.'}
                        </p>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2 text-[11px]">
                        <span className="rounded-full border border-slate-700 bg-slate-800/80 px-2.5 py-1 text-slate-300">
                            Validator: <span className="font-medium text-cyan-300">{structureValidator === 'protenix' ? 'Protenix' : 'Boltz2'}</span>
                        </span>
                        {interactiveWorkflow && (
                            <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-amber-200">
                                Review Gate: {interactiveGateStage === 'post_structure_validation'
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
                        const steps: Array<{ title: string; detail: string; tone: string; optional?: boolean }> = [
                            {
                                title: isRefinementMode ? 'Selected Inputs' : 'RFantibody',
                                detail: isRefinementMode ? 'Reuse selected backbones or re-screen inputs' : 'Generate backbone ensemble',
                                tone: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
                            },
                            {
                                title: seqDesigner === 'none' ? 'Sequence Design' : seqDesigner.toUpperCase(),
                                detail: seqDesigner === 'none' ? 'Skipped in this round' : 'Sequence redesign + filter',
                                tone: seqDesigner === 'none'
                                    ? 'border-slate-700 bg-slate-800/60 text-slate-500'
                                    : 'border-blue-500/30 bg-blue-500/10 text-blue-200',
                                optional: true,
                            },
                            {
                                title: 'PPIFlow',
                                detail: qualitySettings.run_maturation ? 'Maturation loop enabled' : 'Optional maturation loop',
                                tone: qualitySettings.run_maturation
                                    ? 'border-teal-500/30 bg-teal-500/10 text-teal-200'
                                    : 'border-slate-700 bg-slate-800/60 text-slate-500',
                                optional: true,
                            },
                            {
                                title: structureValidator === 'protenix' ? 'Protenix' : 'Boltz2',
                                detail: runStructureValidation ? 'Structure validation' : 'Skipped in this round',
                                tone: runStructureValidation
                                    ? 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-200'
                                    : 'border-slate-700 bg-slate-800/60 text-slate-500',
                                optional: true,
                            },
                        ];

                        return steps.map((step, idx) => (
                            <React.Fragment key={step.title}>
                                {idx > 0 && <span className="self-center text-slate-600">-&gt;</span>}
                                <div className={`min-w-[150px] rounded-xl border px-3 py-2 ${step.tone}`}>
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-sm font-semibold">{idx + 1}. {step.title}</span>
                                        {step.optional && (
                                            <span className="rounded-full border border-current/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wide opacity-80">
                                                Optional
                                            </span>
                                        )}
                                    </div>
                                    <div className="mt-1 text-[11px] opacity-85">{step.detail}</div>
                                </div>
                            </React.Fragment>
                        ));
                    })()}
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                    {interactiveWorkflow && (
                        <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-amber-200">
                            Interactive review enabled
                        </span>
                    )}
                    {runFrustrampnn && (
                        <span className="rounded-full border border-rose-500/30 bg-rose-500/10 px-2.5 py-1 text-rose-200">
                            FrustraMPNN QC
                        </span>
                    )}
                    {useManualMutagenesis && (
                        <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-emerald-200">
                            Manual mutation sets
                        </span>
                    )}
                    {useAntiberty && (
                        <span className="rounded-full border border-yellow-500/30 bg-yellow-500/10 px-2.5 py-1 text-yellow-200">
                            AntiBERTy scoring
                        </span>
                    )}
                    {useThermoMPNN && (
                        <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1 text-indigo-200">
                            ThermoMPNN stability
                        </span>
                    )}
                    {!runFrustrampnn && !useAntiberty && !useThermoMPNN && (
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
                                <option value="antifold">AntiFold</option>
                                <option value="proteinmpnn">ProteinMPNN</option>
                            </select>
                        </label>

                        <label className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
                            <div className="flex items-center justify-between gap-3">
                                <span>PPIFlow maturation</span>
                                <input
                                    type="checkbox"
                                    checked={qualitySettings.run_maturation}
                                    onChange={(e) => {
                                        setRefinementPreset('custom');
                                        setUseManualMutagenesis(false);
                                        setQualitySettings((current) => ({ ...current, run_maturation: e.target.checked }));
                                    }}
                                    className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-indigo-500 focus:ring-indigo-500"
                                />
                            </div>
                            <div className="mt-2 text-[11px] text-slate-500">
                                Uses selected structures directly when sequence redesign is off.
                            </div>
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
                            <span>Manual mutagenesis methodology</span>
                            <input
                                type="checkbox"
                                checked={useManualMutagenesis}
                                onChange={(e) => {
                                    const enabled = e.target.checked;
                                    setUseManualMutagenesis(enabled);
                                    setRefinementPreset(enabled ? 'manual_mutagenesis' : 'custom');
                                    if (enabled) {
                                        setSeqDesigner('none');
                                        setRunStructureValidation(false);
                                        setRunFrustrampnn(false);
                                        setQualitySettings((current) => ({ ...current, run_maturation: false }));
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
                                                <div>{refinementDesignIds?.length || 0} selected design{(refinementDesignIds?.length || 0) === 1 ? '' : 's'}</div>
                                                <div>{cdrIndelConfig.variants_per_design} variant{cdrIndelConfig.variants_per_design === 1 ? '' : 's'} per design</div>
                                                <div>{cdrIndelConfig.loop_ids.join(', ') || 'No loops selected'}</div>
                                                <div className="mt-1 text-fuchsia-200">
                                                    {(refinementDesignIds?.length || 0) * cdrIndelConfig.variants_per_design} total variant predictions
                                                </div>
                                                {cdrIndelConfig.msa_provider === 'colabfold_api' && (refinementDesignIds?.length || 0) * cdrIndelConfig.variants_per_design > 1 && (
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
                            <label className="block text-sm font-medium text-slate-400 mb-2">Job Name</label>
                            <input
                                type="text"
                                value={jobName}
                                onChange={(e) => setJobName(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                                placeholder="antibody_design"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">
                                GPU Pinning {pinnedGpus.length > 0 && <span className="text-accent">({pinnedGpus.length} selected)</span>}
                            </label>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setPinnedGpus([])}
                                    className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${pinnedGpus.length === 0
                                        ? 'bg-slate-600 text-white ring-2 ring-slate-400'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    Auto
                                </button>
                                {[
                                    { id: 0, name: '5090' },
                                    { id: 1, name: '5060Ti' },
                                    { id: 2, name: '3090#1' },
                                    { id: 3, name: '3090#2' },
                                ].map(gpu => (
                                    <button
                                        key={gpu.id}
                                        onClick={() => {
                                            setPinnedGpus(prev =>
                                                prev.includes(gpu.id)
                                                    ? prev.filter(g => g !== gpu.id)
                                                    : [...prev, gpu.id].sort()
                                            );
                                        }}
                                        className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${pinnedGpus.includes(gpu.id)
                                            ? 'bg-accent text-white ring-2 ring-accent'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {gpu.name}
                                    </button>
                                ))}
                            </div>
                            {pinnedGpus.length > 0 && (
                                <label className="flex items-center gap-2 mt-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={lockGpus}
                                        onChange={e => setLockGpus(e.target.checked)}
                                        className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-accent focus:ring-accent"
                                    />
                                    <span className="text-sm text-slate-400">Lock selected GPU(s) exclusively during workflow</span>
                                </label>
                            )}
                        </div>
                    </div>

                    {/* Target PDB Selection - Now with multiple sources */}
                    {!isRefinementMode && (
                        <TargetAntigenSelector
                            onSelect={(target) => {
                                if (target) {
                                    if (target.type === 'upload' && target.file) {
                                        setTargetPdb(target.file);
                                        setTargetSource({ type: 'upload' });
                                    } else if (target.url) {
                                        // For URL-based sources (runs, presets, rcsb), we need to fetch and parse
                                        setTargetSource({
                                            type: target.type,
                                            url: target.url,
                                            path: target.path,
                                            designId: target.designId,
                                            pdbId: target.pdbId
                                        });
                                        // Fetch the PDB content and create a File object for parsing
                                        fetch(target.url)
                                            .then(res => res.blob())
                                            .then(blob => {
                                                const file = new File([blob], target.name + '.pdb', { type: 'chemical/x-pdb' });
                                                setTargetPdb(file);
                                            })
                                            .catch(err => {
                                                console.error('[ANTIBODY_DENOVO] Failed to fetch PDB:', err);
                                                alert('Failed to load PDB from source');
                                            });
                                    }
                                } else {
                                    setTargetPdb(null);
                                    setTargetSource(null);
                                }
                            }}
                            selectedTarget={targetPdb ? { type: (targetSource?.type || 'upload') as 'upload' | 'run' | 'preset' | 'rcsb', name: targetPdb.name } : undefined}
                        />
                    )}

                    {/* Framework Selection */}
                    {!isRefinementMode && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Antibody Framework</label>
                            <div className="grid grid-cols-2 gap-3 mb-3">
                                {[
                                    { id: 'standard-fv', name: 'Standard Fv', desc: 'hu-4D5-8 (Herceptin)', color: 'blue' },
                                    { id: 'nanobody', name: 'Nanobody', desc: 'VHH single-domain', color: 'purple' },
                                    { id: 'sabdab', name: 'SAbDab', desc: 'Browse database', color: 'emerald' },
                                    { id: 'custom', name: 'Custom', desc: 'Upload HLT PDB', color: 'amber' },
                                ].map((fw) => (
                                    <button
                                        key={fw.id}
                                        onClick={() => setFrameworkType(fw.id as FrameworkType)}
                                        className={`p-3 rounded-lg border transition-all ${frameworkType === fw.id
                                            ? fw.id === 'standard-fv'
                                                ? 'bg-blue-600/20 border-blue-500 text-blue-400'
                                                : fw.id === 'nanobody'
                                                    ? 'bg-accent/20 border-accent text-accent'
                                                    : fw.id === 'sabdab'
                                                        ? 'bg-emerald-600/20 border-emerald-500 text-emerald-400'
                                                        : 'bg-amber-600/20 border-amber-500 text-amber-400'
                                            : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="text-sm font-medium">{fw.name}</div>
                                        <div className="text-xs opacity-75">{fw.desc}</div>
                                    </button>
                                ))}
                            </div>

                            {/* SAbDab Framework Browser */}
                            {frameworkType === 'sabdab' && (
                                <div className="mt-3 p-3 bg-slate-900/50 rounded-lg border border-slate-700">
                                    <FrameworkBrowser
                                        onSelect={(fw) => {
                                            setSabdabFramework(fw);
                                            setDetectedCDRs(null); // Clear previous detection
                                            // Set framework PDB URL for 3D preview if pdbCode available
                                            if (fw?.pdbContent) {
                                                const blob = new Blob([fw.pdbContent], { type: 'text/plain' });
                                                const url = URL.createObjectURL(blob);
                                                setFrameworkPdbUrl(url);
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                                setParsedFrameworkChains([]);

                                                const fwFile = new File([blob], `${fw.pdbCode || 'framework'}.pdb`);
                                                import('../utils/pdbUtils').then(({ parsePDBFile }) =>
                                                    parsePDBFile(fwFile)
                                                )
                                                    .then((parsed) => setParsedFrameworkChains(parsed.chains))
                                                    .catch((err) => {
                                                        console.error('Failed to parse selected framework PDB:', err);
                                                        setParsedFrameworkChains([]);
                                                    });
                                            } else if (fw?.filePath || fw?.pdbCode) {
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                                setParsedFrameworkChains([]);

                                                loadSabdabFrameworkFile(fw.pdbCode || fw.id, `${fw.pdbCode || 'framework'}.pdb`)
                                                    .then(({ file, url, filePath }) => {
                                                        if (filePath) {
                                                            setSabdabFramework((prev) => prev ? { ...prev, filePath } : prev);
                                                        }
                                                        setFrameworkPdbUrl(url);
                                                        return import('../utils/pdbUtils').then(({ parsePDBFile }) =>
                                                            parsePDBFile(file)
                                                        );
                                                    })
                                                    .then((parsed) => setParsedFrameworkChains(parsed.chains))
                                                    .catch((err) => {
                                                        console.error('Failed to parse cached framework PDB:', err);
                                                        setParsedFrameworkChains([]);
                                                    });
                                            } else if (fw?.pdbCode) {
                                                // Fallback: Use RCSB PDB download URL for Mol* viewer
                                                const fwUrl = `https://files.rcsb.org/download/${fw.pdbCode.toUpperCase()}.pdb`;
                                                setFrameworkPdbUrl(fwUrl);
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                                setParsedFrameworkChains([]);

                                                fetch(fwUrl)
                                                    .then((res) => {
                                                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                                                        return res.blob();
                                                    })
                                                    .then((blob) => {
                                                        const fwFile = new File([blob], `${fw.pdbCode}.pdb`);
                                                        return import('../utils/pdbUtils').then(({ parsePDBFile }) =>
                                                            parsePDBFile(fwFile)
                                                        );
                                                    })
                                                    .then((parsed) => setParsedFrameworkChains(parsed.chains))
                                                    .catch((err) => {
                                                        console.error('Failed to parse fallback framework PDB:', err);
                                                        setParsedFrameworkChains([]);
                                                    });
                                            } else {
                                                setFrameworkPdbUrl(null);
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
                                                                chain: import('../utils/pdbUtils').Chain | undefined,
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
                                            const file = e.target.files?.[0] || null;
                                            setCustomFrameworkFile(file);
                                            setCustomFrameworkPath(null);
                                            setDetectedCDRs(null);

                                            if (file) {
                                                const blobUrl = URL.createObjectURL(file);
                                                setFrameworkPdbUrl(blobUrl);
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                            } else {
                                                setFrameworkPdbUrl(null);
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
                    {parsedChains.length > 1 && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Antigen Chain</label>
                            <div className="flex gap-2 flex-wrap">
                                {parsedChains.map(chain => (
                                    <button
                                        key={chain.id}
                                        onClick={() => {
                                            setSelectedChain(chain.id);
                                            setSelectedResidues(new Set()); // Clear selection when chain changes
                                        }}
                                        className={`px-4 py-2 rounded-lg font-medium transition-all ${selectedChain === chain.id
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        Chain {chain.id} ({chain.length} aa)
                                    </button>
                                ))}
                            </div>
                            <p className="mt-1 text-xs text-slate-500">Select the chain representing the antigen/target</p>
                        </div>
                    )}

                    {/* Interactive Epitope Selector with 3D Viewer */}
                    {parsedChains.length > 0 && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <label className="block text-sm font-medium text-slate-400">
                                    Epitope Selection
                                    <span className="ml-2 text-xs text-slate-500 font-normal">
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
                                            className={`px-3 py-1.5 text-xs rounded-lg transition-all flex items-center gap-2 ${show3DViewer && viewerMode === 'target'
                                                ? 'bg-emerald-600/20 text-emerald-400 border border-emerald-500/50'
                                                : 'bg-slate-700 text-slate-400 hover:bg-slate-600 border border-slate-600/40'
                                                }`}
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
                                            className={`px-3 py-1.5 text-xs rounded-lg transition-all flex items-center gap-2 ${show3DViewer && viewerMode === 'framework'
                                                ? 'bg-accent/20 text-accent border border-accent/50'
                                                : 'bg-slate-700 text-slate-400 hover:bg-slate-600 border border-slate-600/40'
                                                }`}
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
                                    <div className="text-xs text-slate-500 mb-2">
                                        {viewerMode === 'framework' ? 'Framework Template Preview' : 'Target Antigen Preview'}
                                        {viewerMode === 'framework' && detectedCDRs && (
                                            <span className="ml-2 text-emerald-400">(CDRs highlighted)</span>
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
                                            setSelectedResidues(prev => {
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
                                <div className="text-xs text-slate-500 mb-1">2D Sequence View (shift+click for range)</div>
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
                            RFantibody loop-length variability is only used for de novo backbone generation. In refinement mode, use <span className="text-fuchsia-300">Manual mutagenesis methodology -> CDR indels</span> when you want loop insertions/deletions and downstream backbone rebuilding.
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
                                        ? 'Run the coarse RFantibody contact-distance screen on the designs selected for this refinement round. Leave it enabled when you selected a broad set and want the workflow to auto-reject obviously detached inputs before FAMPNN, PPIFlow, or validation. Turn it off if you already curated the set manually and want everything to pass through unchanged.'
                                        : 'Coarse pre-FAMPNN screen for obviously bad backbones. This is intentionally simple: keep backbones that at least approach the selected epitope before spending sequence-design and validator compute on them.'}
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

                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                <label className="text-xs text-slate-500">
                                    Min epitope contacts
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
                                    Max epitope distance (A)
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
                                    Min whole-target contacts
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
                                    Max epitope centroid distance (A)
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
                                    Epitope contact cutoff (A)
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
                                    Whole-target contact cutoff (A)
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
                                Recommended coarse screen: at least `1` epitope contact within `8 A`, minimum epitope distance below `20 A`, at least `3` loose whole-target contacts within `12 A`, and epitope centroid distance below `40 A`. This is meant to reject obviously detached or badly placed backbones, not to rank binders. If you pause at RFantibody review, screening summaries are still generated even when automatic filtering is off.
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

                </div> {/* End LEFT COLUMN */}

                {/* RIGHT COLUMN: Quality Settings & Debug */}
                <div className="space-y-5">
                    <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold text-slate-200">Execution Mode</h3>
                            <p className="text-xs text-slate-500 mt-1">
                                Choose whether the workflow pauses for manual review or runs through without intervention.
                            </p>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <button
                                type="button"
                                onClick={() => {
                                    interactiveWorkflowTouchedRef.current = true;
                                    setInteractiveWorkflow(false);
                                }}
                                className={`rounded-lg border px-3 py-2 text-sm transition-colors ${!interactiveWorkflow
                                    ? 'border-emerald-400 bg-emerald-400/10 text-emerald-300'
                                    : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                    }`}
                            >
                                Static
                            </button>
                            <button
                                type="button"
                                onClick={() => {
                                    interactiveWorkflowTouchedRef.current = true;
                                    setInteractiveWorkflow(true);
                                }}
                                className={`rounded-lg border px-3 py-2 text-sm transition-colors ${interactiveWorkflow
                                    ? 'border-amber-400 bg-amber-400/10 text-amber-300'
                                    : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                    }`}
                            >
                                Interactive
                            </button>
                        </div>

                        {interactiveWorkflow && (
                            <div className="space-y-2 rounded-lg border border-slate-700/50 bg-slate-950/40 p-3">
                                <label className="block text-xs text-slate-500">Pause After</label>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_rfantibody');
                                        }}
                                        className={`rounded-lg border px-3 py-2 text-sm transition-colors ${interactiveGateStage === 'post_rfantibody'
                                            ? 'border-emerald-400 bg-emerald-400/10 text-emerald-300'
                                            : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        RFantibody Review
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_fampnn');
                                        }}
                                        className={`rounded-lg border px-3 py-2 text-sm transition-colors ${interactiveGateStage === 'post_fampnn'
                                            ? 'border-blue-400 bg-blue-400/10 text-blue-300'
                                            : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        FAMPNN Review
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            interactiveGateStageTouchedRef.current = true;
                                            setInteractiveGateStage('post_structure_validation');
                                        }}
                                        className={`rounded-lg border px-3 py-2 text-sm transition-colors ${interactiveGateStage === 'post_structure_validation'
                                            ? 'border-cyan-400 bg-cyan-400/10 text-cyan-300'
                                            : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        Structure Review
                                    </button>
                                </div>
                                <p className="text-xs text-slate-500">
                                    {interactiveGateStage === 'post_rfantibody'
                                        ? 'Pause immediately after RFantibody backbone generation so you can reject visibly detached or malformed backbones before FAMPNN, MSA, and validator compute.'
                                        : interactiveGateStage === 'post_fampnn'
                                            ? 'Pause immediately after FAMPNN candidate generation/filtering so you can inspect the initial sequence pool before any structure validator is called.'
                                            : 'Pause after Boltz-2 or Protenix validation so the Results Viewer can be used to inspect metrics and launch the next refinement round.'}
                                </p>
                            </div>
                        )}
                    </div>

                    <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold text-slate-200">Structure Validator</h3>
                            <p className="text-xs text-slate-500 mt-1">
                                Select the structural validation backend for post-FAMPNN candidate evaluation.
                            </p>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <button
                                type="button"
                                onClick={() => setStructureValidator('boltz2')}
                                className={`rounded-lg border px-3 py-2 text-sm transition-colors ${structureValidator === 'boltz2'
                                    ? 'border-accent bg-accent/10 text-accent'
                                    : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                    }`}
                            >
                                Boltz-2
                            </button>
                            <button
                                type="button"
                                onClick={() => setStructureValidator('protenix')}
                                className={`rounded-lg border px-3 py-2 text-sm transition-colors ${structureValidator === 'protenix'
                                    ? 'border-cyan-400 bg-cyan-400/10 text-cyan-300'
                                    : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-600'
                                    }`}
                            >
                                Protenix
                            </button>
                        </div>
                        <p className="text-xs text-slate-500">
                            {structureValidator === 'protenix'
                                ? 'Protenix inference controls live in Quality Settings, and MSA controls are now shared with the other validator paths. AntiFold FASTA-only outputs remain excluded here. The default validator path is still a naive sequence-only co-fold.'
                                : 'Boltz-2 controls and post-validation filters live inside Quality Settings.'}
                        </p>
                    </div>

                    {/* Quality Settings Panel */}
                    <QualitySettingsPanel
                        settings={qualitySettings}
                        onSettingsChange={setQualitySettings}
                        preset={qualityPreset}
                        onPresetChange={setQualityPreset}
                        structureValidator={structureValidator}
                    />

                    {/* Physics Refinement Panel (OpenMM) */}
                    <PhysicsRefinementPanel
                        settings={physicsSettings}
                        onSettingsChange={setPhysicsSettings}
                        isAntibody={true}
                    />

                    {/* ANARCII Polishing */}
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

                    {/* FrustraMPNN QC */}
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

                    {/* Number of Backbones */}
                    {!isRefinementMode && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Number of Backbones</label>
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
                            {([...(isRefinementMode ? (['none'] as const) : []), 'fampnn', 'antifold', 'proteinmpnn'] as const).map((designer) => (
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
                                    {designer === 'none' ? 'SKIP' : designer.toUpperCase()}
                                </button>
                            ))}
                        </div>
                    </div>

                    {seqDesigner === 'fampnn' && (
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
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">Orchestrator Mode</label>
                        <div className="flex gap-3 mb-3">
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
                        <p className="text-xs text-slate-500 mb-3">
                            {parallelMode === 'standard'
                                ? "Standard: Split work across pinned GPUs within Nextflow"
                                : "Orchestrator: Spawn child jobs that go through GPU queue"}
                        </p>

                        {parallelMode === 'full_orchestrator' && (
                            <div className="grid grid-cols-2 gap-4 mt-3">
                                <div>
                                    <label className="text-xs text-slate-500">Backbones per job</label>
                                    <input
                                        type="range"
                                        min="1"
                                        max="500"
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
                                        max="500"
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
                                        max="500"
                                        value={seqsPerBoltzJob}
                                        onChange={(e) => setSeqsPerBoltzJob(parseInt(e.target.value))}
                                        className="w-full accent-orange-500"
                                    />
                                    <span className="text-sm text-slate-300">{seqsPerBoltzJob}</span>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Debug Settings Panel - Hidden by default */}
                    <div className="mt-6 border border-amber-600/30 rounded-lg overflow-hidden">
                        <button
                            onClick={() => setShowDebugSettings(!showDebugSettings)}
                            className={`w-full px-4 py-3 flex items-center justify-between text-left transition-colors ${showDebugSettings
                                ? 'bg-amber-600/20 text-amber-400'
                                : 'bg-slate-900/50 text-slate-500 hover:bg-slate-800/50'
                                }`}
                        >
                            <div className="flex items-center gap-2">
                                <span className="font-medium">Debug Settings</span>
                                {(skipRFantibody || skipFampnn || customOutputDir) && (
                                    <span className="px-2 py-0.5 text-xs bg-amber-600 text-white rounded">ACTIVE</span>
                                )}
                            </div>
                            <span className="text-lg">{showDebugSettings ? '-' : '+'}</span>
                        </button>

                        {showDebugSettings && (
                            <div className="p-4 bg-slate-900/30 space-y-4">
                                <div className="text-xs text-amber-500/80 mb-3">
                                    Warning: Debug settings allow skipping workflow steps. Use with caution.
                                </div>

                                {/* Skip RFantibody */}
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

                                {/* Skip FAMPNN */}
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
                        // Refinement mode and skip modes don't require target PDB or hotspots
                        (!(isRefinementMode || skipRFantibody || skipFampnn) && (!targetPdb || selectedResidues.size === 0)) ||
                        // When skipping, require the skip paths
                        (!isRefinementMode && skipRFantibody && !rfantibodyInputPdbs.trim()) ||
                        (!isRefinementMode && skipFampnn && !fampnnCollectedPdbs.trim()) ||
                        (isRefinementMode && !useManualMutagenesis && seqDesigner === 'none' && !qualitySettings.run_maturation && !runStructureValidation && !runFrustrampnn)
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
                            {useManualMutagenesis ? 'Launch Mutagenesis' : 'Launch Refinement'} ({refinementDesignIds?.length ?? 0} designs)
                        </>
                    ) : (skipRFantibody || skipFampnn) ? (
                        <>
                            Run Skipped Workflow
                        </>
                    ) : (
                        <>
                            Generate Antibodies ({selectedResidues.size} hotspots)
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
                        if (p.framework_type) { setFrameworkType(p.framework_type); loaded.push('framework_type'); } else { skipped.push('framework_type'); }
                        if (p.seq_designer) { setSeqDesigner(p.seq_designer); loaded.push('seq_designer'); }
                        else if (p.seq_design_fampnn === false && p.seq_design_antifold === false && p.seq_design_proteinmpnn === false) { setSeqDesigner('none'); loaded.push('seq_designer:none'); }
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
                        if (typeof p.interactive_swa === 'boolean') { setInteractiveWorkflow(p.interactive_swa); loaded.push('interactive_swa'); }
                        else if (typeof p.interactive_gating === 'boolean') { setInteractiveWorkflow(p.interactive_gating); loaded.push('interactive_gating'); }
                        if (
                            p.interactive_gate_stage === 'post_rfantibody' ||
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
                        if (p.quality_preset) { setQualityPreset(p.quality_preset); loaded.push('quality_preset'); }
                        // Manual CDR definitions - deserialize from arrays
                        if (Array.isArray(p.manual_cdr_definitions)) {
                            const defs = p.manual_cdr_definitions.map((d: any) => ({
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
                    framework_type: frameworkType,
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
                    fampnn_checkpoint: resolvedFampnnCheckpoint,
                    fampnn_checkpoint_path: qualitySettings.fampnn_checkpoint_path,
                    lock_target_chains: qualitySettings.lock_target_chains,
                    lock_antibody_framework: qualitySettings.lock_antibody_framework,
                    run_post_validation_maturation: qualitySettings.run_maturation,
                    run_post_boltz_maturation: qualitySettings.run_maturation,
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
                    rfantibody_min_epitope_contacts: rfantibodyMinEpitopeContacts,
                    rfantibody_max_epitope_distance: rfantibodyMaxEpitopeDistance,
                    rfantibody_min_target_contacts: rfantibodyMinTargetContacts,
                    rfantibody_max_epitope_centroid_distance: rfantibodyMaxEpitopeCentroidDistance,
                    rfantibody_contact_distance_threshold: rfantibodyContactDistanceThreshold,
                    rfantibody_target_contact_distance_threshold: rfantibodyTargetContactDistanceThreshold,
                    protect_tetrad: protectTetrad,
                    protect_vhh_tetrad: protectTetrad,
                    // Framework protection (for framework_allowed and full_design modes)
                    protected_positions: frameworkProtection.protectedPositions.join(','),
                    protect_disulfides: frameworkProtection.protectDisulfides,
                    protect_fr_contacts: frameworkProtection.protectFrContacts,
                    // Target info - now includes full source context
                    target_pdb: uploadedPath || targetSource?.path || undefined,
                    target_source: targetSource,
                    uploaded_path: uploadedPath,
                    selected_chain: selectedChain,
                    antigen_chains: selectedChain,
                    selected_residues: Array.from(selectedResidues),
                    epitope_residues: Array.from(selectedResidues).sort().join(','),
                    target_dna_seq: targetDnaSeq.trim() || undefined,
                    pinned_gpus: pinnedGpus,
                    lock_gpus: lockGpus,
                    // Quality settings
                    quality_preset: qualityPreset,
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
                currentMode="antibody_denovo_pipeline"
                baseTemplateId="antibody_denovo"
            />
        </div >
    );
};

export default AntibodyDenovoTemplate;
