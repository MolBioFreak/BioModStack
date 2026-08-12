import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { completeCurrentLaunchContext, fetchDesigns, fetchJobById, submitJob, uploadFile, type Design, type Job } from '../lib/api';
import { jobPollingInterval } from '../lib/queryPolling';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import { EpitopeSelector } from './EpitopeSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { componentIdFromIndex } from './ligandSelectorData';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { getModelByNumber, parseStructureFile, type Chain, type ParsedPDB } from '../utils/pdbUtils';
import { getProteinLocalRedesignUiState } from './proteinLocalRedesignUiState';
import { useLiveGpuCatalog } from './useLiveGpuCatalog';

interface ProteinLocalRedesignTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
    submissionModelId?: string;
    submissionMode?: string;
    requiredPinnedGpu?: number | null;
}

type RegionMode = 'manual_ranges' | 'interface_shell';
type SequenceMethod = 'skip' | 'fampnn' | 'mpnn';
type NativeRedesignMode = 'partial_diffusion' | 'minimal_insertion';
type SourcePredictor = 'boltz' | 'all';
type ChainType = 'protein' | 'dna' | 'rna' | 'other';
type ReviewPauseStage = 'post_rfantibody' | 'post_fampnn' | 'post_structure_validation';

interface ChainSummary {
    id: string;
    residueCount: number;
    type: ChainType;
}

const PROTEIN_RESIDUES = new Set([
    'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'ILE',
    'LEU', 'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL',
    'MSE', 'SEC', 'PYL', 'HYP',
]);

const DNA_RESIDUES = new Set(['DA', 'DC', 'DG', 'DT', 'DI', 'ADE', 'CYT', 'GUA', 'THY']);
const RNA_RESIDUES = new Set(['A', 'C', 'G', 'U', 'I', 'URA', 'PSU', '1MA', '5MC']);

const themedPanelStyle: CSSProperties = {
    backgroundColor: 'var(--bg-secondary)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
};

const themedInsetStyle: CSSProperties = {
    backgroundColor: 'color-mix(in srgb, var(--bg-tertiary) 58%, transparent)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
};

const themedMutedInsetStyle: CSSProperties = {
    backgroundColor: 'color-mix(in srgb, var(--bg-tertiary) 42%, transparent)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-secondary)',
};

const themedSelectedStyle = (accent: string): CSSProperties => ({
    backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`,
    borderColor: `color-mix(in srgb, ${accent} 72%, var(--border-primary))`,
    color: 'var(--text-primary)',
});

const themedTagStyle = (accent: string): CSSProperties => ({
    backgroundColor: `color-mix(in srgb, ${accent} 12%, transparent)`,
    borderColor: `color-mix(in srgb, ${accent} 56%, var(--border-primary))`,
    color: 'var(--text-primary)',
});

const themedInputStyle: CSSProperties = {
    backgroundColor: 'var(--bg-tertiary)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
    caretColor: 'var(--accent-primary)',
};

const getChainTypeAccent = (type: ChainType): string => {
    switch (type) {
        case 'protein':
            return 'var(--accent-primary)';
        case 'dna':
            return 'var(--link)';
        case 'rna':
            return 'var(--warning)';
        default:
            return 'var(--text-secondary)';
    }
};

const normalizeChainList = (value: unknown): string[] => {
    if (typeof value !== 'string') return [];
    return value.split(',').map((token) => token.trim()).filter(Boolean);
};

const residueKey = (chain: Chain, residue: Chain['residues'][number]) => `${chain.id}${residue.resNum}${residue.iCode || ''}`;

const summarizeChainsFromPdbContent = (content: string): ChainSummary[] => {
    const chainMap = new Map<string, { residueKeys: Set<string>; counts: Record<ChainType, number> }>();

    for (const rawLine of content.split(/\r?\n/)) {
        if (!rawLine.startsWith('ATOM') && !rawLine.startsWith('HETATM')) continue;
        const chainId = rawLine.slice(21, 22).trim() || 'A';
        const resName = rawLine.slice(17, 20).trim().toUpperCase();
        const resNum = rawLine.slice(22, 26).trim();
        const iCode = rawLine.slice(26, 27).trim();
        const residueId = `${resNum}${iCode}`;

        if (!chainMap.has(chainId)) {
            chainMap.set(chainId, {
                residueKeys: new Set<string>(),
                counts: { protein: 0, dna: 0, rna: 0, other: 0 },
            });
        }

        const chain = chainMap.get(chainId)!;
        const uniqueKey = `${chainId}:${residueId}`;
        if (chain.residueKeys.has(uniqueKey)) continue;
        chain.residueKeys.add(uniqueKey);

        const type: ChainType = PROTEIN_RESIDUES.has(resName)
            ? 'protein'
            : DNA_RESIDUES.has(resName)
                ? 'dna'
                : RNA_RESIDUES.has(resName)
                    ? 'rna'
                    : 'other';
        chain.counts[type] += 1;
    }

    const typePriority: ChainType[] = ['protein', 'dna', 'rna', 'other'];

    return Array.from(chainMap.entries())
        .map(([id, entry]) => {
            const type = typePriority.reduce((best, candidate) => (
                entry.counts[candidate] > entry.counts[best] ? candidate : best
            ), 'other' as ChainType);
            return {
                id,
                residueCount: entry.residueKeys.size,
                type,
            };
        })
        .sort((a, b) => a.id.localeCompare(b.id));
};

const buildManualRangeString = (chain: Chain | null, selectedResidues: Set<string>): string => {
    if (!chain) return '';
    const ordered = chain.residues.filter((residue) => selectedResidues.has(residueKey(chain, residue)));
    if (!ordered.length) return '';

    const blocks: string[] = [];
    let blockStart = ordered[0];
    let previous = ordered[0];

    const flush = () => {
        if (!blockStart || !previous) return;
        const startToken = `${chain.id}${blockStart.resNum}${blockStart.iCode || ''}`;
        if (blockStart.resNum === previous.resNum && (blockStart.iCode || '') === (previous.iCode || '')) {
            blocks.push(startToken);
            return;
        }
        if (!blockStart.iCode && !previous.iCode) {
            blocks.push(`${chain.id}${blockStart.resNum}-${previous.resNum}`);
            return;
        }
        blocks.push(startToken);
        blocks.push(`${chain.id}${previous.resNum}${previous.iCode || ''}`);
    };

    for (const residue of ordered.slice(1)) {
        const isConsecutive = !previous.iCode && !residue.iCode && residue.resNum === previous.resNum + 1;
        if (isConsecutive) {
            previous = residue;
            continue;
        }
        flush();
        blockStart = residue;
        previous = residue;
    }
    flush();

    return blocks.join(',');
};

const formatSelectionSummary = (count: number, rangeText: string): string => {
    if (!count) return 'No editable residues selected yet';
    return rangeText || `${count} residues selected`;
};

const getSelectionName = (target: SelectedTarget | null): string => {
    if (!target) return 'No structure selected';
    return target.name || target.path || target.pdbId || 'Selected structure';
};

const toSyntheticSelectedTarget = (inputPath: string): SelectedTarget | null => {
    const trimmed = inputPath.trim();
    if (!trimmed || trimmed.startsWith('/')) return null;
    return {
        type: 'preset',
        path: trimmed,
        url: `/api/files/pdb/${trimmed}`,
        name: trimmed.split('/').pop() || trimmed,
    };
};

const canonicalStructureSourceName = (target: SelectedTarget): string => {
    const base = (target.name || target.pdbId || 'protein_local_redesign_input')
        .replace(/[^\w.-]+/g, '_')
        .replace(/_+/g, '_')
        .replace(/^_+|_+$/g, '');
    return /\.(pdb|cif|mmcif)$/i.test(base) ? base : `${base}.pdb`;
};

const sanitizeSequenceInput = (value: string) => value.toUpperCase().replace(/[^A-Z]/g, '');
const parseOptionalIntegerInput = (value: string): number | undefined => {
    const trimmed = value.trim();
    if (!trimmed) return undefined;
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isFinite(parsed) ? parsed : undefined;
};
const parseOptionalNumberInput = (value: string): number | undefined => {
    const trimmed = value.trim();
    if (!trimmed) return undefined;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : undefined;
};
const parseExplicitGpuPin = (value: unknown): number | null => (
    typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
);

const buildSourceComplexComponents = (
    sourcePrimaryChainId: string,
    sourceSequence: string,
    sourceSequenceName: string,
    sourceLigands: LigandEntry[],
) => {
    const components: Array<Record<string, unknown>> = [];
    const usedIds = new Set<string>();

    const reserveId = (preferred?: string) => {
        const normalized = (preferred || '').trim();
        if (normalized && !usedIds.has(normalized)) {
            usedIds.add(normalized);
            return normalized;
        }
        let index = 0;
        let fallback = componentIdFromIndex(index);
        while (usedIds.has(fallback)) {
            index += 1;
            fallback = componentIdFromIndex(index);
        }
        usedIds.add(fallback);
        return fallback;
    };

    const resolvedPrimaryId = reserveId(sourcePrimaryChainId || 'A');
    components.push({
        type: 'protein',
        id: resolvedPrimaryId,
        sequence: sourceSequence.trim(),
        name: sourceSequenceName.trim() || 'source_primary',
    });

    const binderIds: string[] = [];
    sourceLigands.forEach((ligand) => {
        const resolvedId = reserveId(ligand.id);
        const component: Record<string, unknown> = {
            type: ligand.type,
            id: resolvedId,
            name: ligand.name,
        };
        if (ligand.sequence) component.sequence = ligand.sequence;
        if (ligand.ccd) component.ccd = ligand.ccd;
        if (ligand.smiles) component.smiles = ligand.smiles;
        components.push(component);
        if (ligand.type === 'protein' || ligand.type === 'peptide') {
            binderIds.push(resolvedId);
        }
    });

    return {
        components,
        resolvedPrimaryId,
        binderIds,
    };
};

export function ProteinLocalRedesignTemplate({
    onBack,
    initialValues,
    submissionModelId = 'protein_local_redesign',
    submissionMode = 'local_redesign',
    requiredPinnedGpu = null,
}: ProteinLocalRedesignTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const {
        gpuOptions,
        isLoading: gpuCatalogLoading,
        isError: gpuCatalogError,
    } = useLiveGpuCatalog({ requireFresh: true });
    const isNativeLocalRedesign = submissionModelId === 'protein_local_redesign';

    const [jobName, setJobName] = useState('protein_local_redesign');
    const [selectedTarget, setSelectedTarget] = useState<SelectedTarget | null>(null);
    const [sourcePath, setSourcePath] = useState<string | null>(null);
    const [parsedStructure, setParsedStructure] = useState<ParsedPDB | null>(null);
    const [selectedModelNumber, setSelectedModelNumber] = useState<number | null>(null);
    const [structureLoading, setStructureLoading] = useState(false);
    const [structureError, setStructureError] = useState<string | null>(null);
    const [showSourceSimulation, setShowSourceSimulation] = useState(!isNativeLocalRedesign);
    const [sourceSimulationJobName, setSourceSimulationJobName] = useState('protein_local_source');
    const [sourceSequence, setSourceSequence] = useState('');
    const [sourceSequenceName, setSourceSequenceName] = useState('source_complex');
    const [sourcePrimaryChainId, setSourcePrimaryChainId] = useState('A');
    const [sourceLigands, setSourceLigands] = useState<LigandEntry[]>([]);
    const [sourcePredictor, setSourcePredictor] = useState<SourcePredictor>('boltz');
    const [sourceNumParallelJobs, setSourceNumParallelJobs] = useState(1);
    const [sourceBoltzUseMsa, setSourceBoltzUseMsa] = useState(true);
    const [sourceBoltzRecyclingSteps, setSourceBoltzRecyclingSteps] = useState(3);
    const [sourceBoltzSamplingSteps, setSourceBoltzSamplingSteps] = useState(200);
    const [sourceBoltzNumSamples, setSourceBoltzNumSamples] = useState(1);
    const [sourceBoltzMaxParallelSamples, setSourceBoltzMaxParallelSamples] = useState(1);
    const [sourceSimulationJobId, setSourceSimulationJobId] = useState<string | null>(null);
    const [sourceLaunchError, setSourceLaunchError] = useState<string | null>(null);
    const [rfd3BatchesPerDesign, setRfd3BatchesPerDesign] = useState(1);
    const [rfd3ExtraConfig, setRfd3ExtraConfig] = useState('');
    const [nativeRedesignMode, setNativeRedesignMode] = useState<NativeRedesignMode>('partial_diffusion');
    const [nativeProfileId, setNativeProfileId] = useState('generic_local_redesign_v1');
    const [nativePartialT, setNativePartialT] = useState(2.0);

    const [nativeInsertionAnchor, setNativeInsertionAnchor] = useState('');
    const [nativeInsertionMinLength, setNativeInsertionMinLength] = useState('3');
    const [nativeInsertionMaxLength, setNativeInsertionMaxLength] = useState('6');

    const [nativeLigand, setNativeLigand] = useState('');
    const [nativeHotspots, setNativeHotspots] = useState('');
    const [nativeHbondDonors, setNativeHbondDonors] = useState('');
    const [nativeHbondAcceptors, setNativeHbondAcceptors] = useState('');
    const [nativeSeed, setNativeSeed] = useState('');
    const [nativeDumpTrajectories, setNativeDumpTrajectories] = useState(false);
    const [nativePinnedGpu, setNativePinnedGpu] = useState<number | null>(() => (
        parseExplicitGpuPin(initialValues?.pinned_gpu)
    ));
    const effectiveNativePinnedGpu = requiredPinnedGpu ?? nativePinnedGpu;

    const [rfdMinHelices, setRfdMinHelices] = useState('');
    const [rfdMaxHelices, setRfdMaxHelices] = useState('');
    const [rfdMinStrands, setRfdMinStrands] = useState('');
    const [rfdMaxStrands, setRfdMaxStrands] = useState('');
    const [rfdMinSs, setRfdMinSs] = useState('');
    const [rfdMaxSs, setRfdMaxSs] = useState('');
    const [rfdMinRog, setRfdMinRog] = useState('');
    const [rfdMaxRog, setRfdMaxRog] = useState('');

    const [designChain, setDesignChain] = useState('A');
    const [contextChains, setContextChains] = useState<string[]>([]);
    const [regionMode, setRegionMode] = useState<RegionMode>('manual_ranges');
    const [selectedEditableResidues, setSelectedEditableResidues] = useState<Set<string>>(new Set());
    const [manualRangesText, setManualRangesText] = useState('');
    const [interfaceCutoff, setInterfaceCutoff] = useState(6.0);
    const [regionPadding, setRegionPadding] = useState(2);
    const [numDesigns, setNumDesigns] = useState(8);
    const [seqMethod, setSeqMethod] = useState<SequenceMethod>(isNativeLocalRedesign ? 'skip' : 'fampnn');
    const [seqsPerDesign, setSeqsPerDesign] = useState(8);
    const [fixFixedSidechains, setFixFixedSidechains] = useState(true);
    const [runBoltzValidation, setRunBoltzValidation] = useState(true);
    const [boltzSamplingSteps, setBoltzSamplingSteps] = useState(200);
    const [boltzRecyclingSteps, setBoltzRecyclingSteps] = useState(3);
    const [interactiveGating, setInteractiveGating] = useState(true);
    const [interactiveGateStage, setInteractiveGateStage] = useState<ReviewPauseStage>('post_structure_validation');
    const [showStructureViewer, setShowStructureViewer] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const proteinLocalRedesignUiState = getProteinLocalRedesignUiState(isNativeLocalRedesign, seqMethod);
    const workflowSteps = isNativeLocalRedesign
        ? ['Source Complex', 'Visual Region Pick', 'Native RFD3', 'Optional Sequence Design']
        : ['Source Complex', 'Visual Region Pick', 'Sequence Redesign', 'Boltz Validation'];

    useEffect(() => {
        if (!initialValues) return;
        if (typeof initialValues.job_name === 'string' && initialValues.job_name.trim()) setJobName(initialValues.job_name);
        if (typeof initialValues.input_pdb === 'string' && initialValues.input_pdb.trim()) {
            setSourcePath(initialValues.input_pdb);
            const synthetic = toSyntheticSelectedTarget(initialValues.input_pdb);
            if (synthetic) {
                setSelectedTarget(synthetic);
            }
        }
        if (typeof initialValues.model_number === 'number') setSelectedModelNumber(initialValues.model_number);
        if (typeof initialValues.design_chains === 'string' && initialValues.design_chains.trim()) setDesignChain(initialValues.design_chains.trim());
        if (typeof initialValues.context_chains === 'string') setContextChains(normalizeChainList(initialValues.context_chains));
        if (initialValues.region_mode === 'manual_ranges' || initialValues.region_mode === 'interface_shell') {
            setRegionMode(initialValues.region_mode);
        }
        if (typeof initialValues.redesign_ranges === 'string') setManualRangesText(initialValues.redesign_ranges);
        if (typeof initialValues.interface_cutoff === 'number') setInterfaceCutoff(initialValues.interface_cutoff);
        if (typeof initialValues.region_padding === 'number') setRegionPadding(initialValues.region_padding);
        if (typeof initialValues.num_designs === 'number') setNumDesigns(initialValues.num_designs);
        if (initialValues.seq_method === 'fampnn' || initialValues.seq_method === 'mpnn') setSeqMethod(initialValues.seq_method);
        if (typeof initialValues.seqs_per_design === 'number') setSeqsPerDesign(initialValues.seqs_per_design);
        if (typeof initialValues.fix_fixed_sidechains === 'boolean') setFixFixedSidechains(initialValues.fix_fixed_sidechains);
        if (typeof initialValues.run_boltz_validation === 'boolean') setRunBoltzValidation(initialValues.run_boltz_validation);
        if (typeof initialValues.boltz_sampling_steps === 'number') setBoltzSamplingSteps(initialValues.boltz_sampling_steps);
        if (typeof initialValues.boltz_recycling_steps === 'number') setBoltzRecyclingSteps(initialValues.boltz_recycling_steps);
        if (typeof initialValues.interactive_gating === 'boolean') setInteractiveGating(initialValues.interactive_gating);
        if (
            initialValues.interactive_gate_stage === 'post_rfantibody'
            || initialValues.interactive_gate_stage === 'post_fampnn'
            || initialValues.interactive_gate_stage === 'post_structure_validation'
        ) {
            setInteractiveGateStage(initialValues.interactive_gate_stage);
        }
        if (typeof initialValues.rfd3_batches_per_design === 'number') setRfd3BatchesPerDesign(initialValues.rfd3_batches_per_design);
        if (typeof initialValues.rfd3_extra_config === 'string') setRfd3ExtraConfig(initialValues.rfd3_extra_config);
        if (initialValues.redesign_mode === 'partial_diffusion' || initialValues.redesign_mode === 'minimal_insertion') setNativeRedesignMode(initialValues.redesign_mode);
        if (initialValues.profile_id === 'generic_local_redesign_v1' || initialValues.profile_id === 'drt4_datp_gate_v1') setNativeProfileId(initialValues.profile_id);
        if (typeof initialValues.partial_t === 'number') setNativePartialT(initialValues.partial_t);

        if (typeof initialValues.insertion_anchor === 'string') setNativeInsertionAnchor(initialValues.insertion_anchor);
        if (typeof initialValues.insertion_min_length === 'number') setNativeInsertionMinLength(String(initialValues.insertion_min_length));
        if (typeof initialValues.insertion_max_length === 'number') setNativeInsertionMaxLength(String(initialValues.insertion_max_length));

        if (typeof initialValues.ligand === 'string') setNativeLigand(initialValues.ligand);
        if (typeof initialValues.select_hotspots === 'string') setNativeHotspots(initialValues.select_hotspots);
        if (typeof initialValues.select_hbond_donor === 'string') setNativeHbondDonors(initialValues.select_hbond_donor);
        if (typeof initialValues.select_hbond_acceptor === 'string') setNativeHbondAcceptors(initialValues.select_hbond_acceptor);
        if (typeof initialValues.seed === 'number') setNativeSeed(String(initialValues.seed));
        if (typeof initialValues.dump_trajectories === 'boolean') setNativeDumpTrajectories(initialValues.dump_trajectories);
        const initialPinnedGpu = parseExplicitGpuPin(initialValues.pinned_gpu);
        if (initialPinnedGpu !== null) setNativePinnedGpu(initialPinnedGpu);

        if (typeof initialValues.rfd_min_helices === 'number') setRfdMinHelices(String(initialValues.rfd_min_helices));
        if (typeof initialValues.rfd_max_helices === 'number') setRfdMaxHelices(String(initialValues.rfd_max_helices));
        if (typeof initialValues.rfd_min_strands === 'number') setRfdMinStrands(String(initialValues.rfd_min_strands));
        if (typeof initialValues.rfd_max_strands === 'number') setRfdMaxStrands(String(initialValues.rfd_max_strands));
        if (typeof initialValues.rfd_min_ss === 'number') setRfdMinSs(String(initialValues.rfd_min_ss));
        if (typeof initialValues.rfd_max_ss === 'number') setRfdMaxSs(String(initialValues.rfd_max_ss));
        if (typeof initialValues.rfd_min_rog === 'number') setRfdMinRog(String(initialValues.rfd_min_rog));
        if (typeof initialValues.rfd_max_rog === 'number') setRfdMaxRog(String(initialValues.rfd_max_rog));
    }, [initialValues]);


    useEffect(() => {
        if (runBoltzValidation) return;
        if (interactiveGateStage === 'post_structure_validation') {
            setInteractiveGateStage('post_fampnn');
        }
    }, [interactiveGateStage, runBoltzValidation]);

    useEffect(() => {
        let cancelled = false;

        const loadSelectedTarget = async () => {
            if (!selectedTarget) {
                setParsedStructure(null);
                setStructureError(null);
                return;
            }

            setStructureLoading(true);
            setStructureError(null);

            try {
                if (selectedTarget.path) {
                    setSourcePath(selectedTarget.path);
                }

                let sourceFile = selectedTarget.file ?? null;
                if (!sourceFile && selectedTarget.url) {
                    const response = await fetch(selectedTarget.url);
                    if (!response.ok) {
                        throw new Error(`Failed to load structure (${response.status})`);
                    }
                    const blob = await response.blob();
                    sourceFile = new File([blob], canonicalStructureSourceName(selectedTarget), {
                        type: blob.type || 'chemical/x-pdb',
                    });
                }

                if (!sourceFile) {
                    throw new Error('No source structure file was available for preview.');
                }

                const parsed = await parseStructureFile(sourceFile);
                if (cancelled) return;

                setParsedStructure(parsed);
                setSelectedModelNumber((current) => {
                    if (current != null && parsed.models.some((model) => model.modelNumber === current)) {
                        return current;
                    }
                    return parsed.models[0]?.modelNumber ?? null;
                });
            } catch (err: unknown) {
                if (cancelled) return;
                setParsedStructure(null);
                setStructureError(err instanceof Error ? err.message : 'Failed to parse source structure');
            } finally {
                if (!cancelled) {
                    setStructureLoading(false);
                }
            }
        };

        void loadSelectedTarget();

        return () => {
            cancelled = true;
        };
    }, [selectedTarget]);

    const activeModel = useMemo(
        () => (parsedStructure ? getModelByNumber(parsedStructure, selectedModelNumber) : null),
        [parsedStructure, selectedModelNumber],
    );

    const activeProteinChains = useMemo(
        () => activeModel?.chains ?? parsedStructure?.chains ?? [],
        [activeModel, parsedStructure],
    );

    const activeModelContent = useMemo(() => activeModel?.content || null, [activeModel]);

    const viewerStructureUrl = useMemo(() => {
        if (!activeModelContent) return null;
        return URL.createObjectURL(new Blob([activeModelContent], { type: 'chemical/x-pdb' }));
    }, [activeModelContent]);

    useEffect(() => {
        if (!viewerStructureUrl) return;
        return () => {
            URL.revokeObjectURL(viewerStructureUrl);
        };
    }, [viewerStructureUrl]);

    const allChainSummaries = useMemo(() => {
        if (activeModelContent) {
            return summarizeChainsFromPdbContent(activeModelContent);
        }
        return activeProteinChains.map((chain) => ({
            id: chain.id,
            residueCount: chain.length,
            type: 'protein' as ChainType,
        }));
    }, [activeModelContent, activeProteinChains]);

    const designableProteinChains = useMemo(
        () => allChainSummaries.filter((chain) => chain.type === 'protein'),
        [allChainSummaries],
    );

    const activeDesignChain = useMemo(
        () => activeProteinChains.find((chain) => chain.id === designChain) ?? null,
        [activeProteinChains, designChain],
    );

    const generatedInsertionContig = useMemo(() => {
        const anchorText = nativeInsertionAnchor.trim();
        if (!anchorText || !activeDesignChain) return '';
        const anchorIndex = activeDesignChain.residues.findIndex((residue) => {
            const token = `${activeDesignChain.id}${residue.resNum}${residue.iCode || ''}`;
            return token === anchorText || String(residue.resNum) === anchorText;
        });
        if (anchorIndex < 0) return '';
        const minLength = Number.parseInt(nativeInsertionMinLength, 10);
        const maxLength = Number.parseInt(nativeInsertionMaxLength, 10);
        if (!Number.isFinite(minLength) || !Number.isFinite(maxLength) || minLength < 1 || maxLength < minLength) return '';
        const chainRange = (chain: Chain, first: Chain['residues'][number], last: Chain['residues'][number]) => {
            const start = `${chain.id}${first.resNum}${first.iCode || ''}`;
            const end = `${chain.id}${last.resNum}${last.iCode || ''}`;
            return start === end ? start : `${start}-${end.replace(chain.id, '')}`;
        };
        const parts: string[] = [];
        if (anchorIndex >= 0) parts.push(chainRange(activeDesignChain, activeDesignChain.residues[0], activeDesignChain.residues[anchorIndex]));
        parts.push(`${minLength}-${maxLength}`);
        if (anchorIndex + 1 < activeDesignChain.residues.length) {
            parts.push(chainRange(activeDesignChain, activeDesignChain.residues[anchorIndex + 1], activeDesignChain.residues[activeDesignChain.residues.length - 1]));
        }
        const contextRanges = contextChains
            .map((chainId) => activeProteinChains.find((chain) => chain.id === chainId))
            .filter((chain): chain is Chain => Boolean(chain && chain.residues.length))
            .map((chain) => chainRange(chain, chain.residues[0], chain.residues[chain.residues.length - 1]));
        if (contextRanges.length) parts.push('/0', ...contextRanges);
        return parts.join(',');
    }, [activeDesignChain, activeProteinChains, contextChains, nativeInsertionAnchor, nativeInsertionMaxLength, nativeInsertionMinLength]);

    const designResidueKeys = useMemo(() => {
        if (!activeDesignChain) return new Set<string>();
        return new Set(activeDesignChain.residues.map((residue) => residueKey(activeDesignChain, residue)));
    }, [activeDesignChain]);

    useEffect(() => {
        if (!designableProteinChains.length) return;
        if (!designableProteinChains.some((chain) => chain.id === designChain)) {
            setDesignChain(designableProteinChains[0].id);
        }
    }, [designChain, designableProteinChains]);

    useEffect(() => {
        setContextChains((current) =>
            current.filter((chainId) => chainId !== designChain && allChainSummaries.some((chain) => chain.id === chainId))
        );
    }, [designChain, allChainSummaries]);

    useEffect(() => {
        setSelectedEditableResidues((current) =>
            new Set(Array.from(current).filter((key) => designResidueKeys.has(key)))
        );
    }, [designResidueKeys]);

    const derivedManualRanges = useMemo(
        () => buildManualRangeString(activeDesignChain, selectedEditableResidues),
        [activeDesignChain, selectedEditableResidues],
    );

    useEffect(() => {
        if (regionMode !== 'manual_ranges') return;
        if (selectedEditableResidues.size === 0) return;
        setManualRangesText(derivedManualRanges);
    }, [regionMode, selectedEditableResidues, derivedManualRanges]);

    const handleResidueSelectionChange = (residues: Set<string>) => {
        const filtered = new Set(Array.from(residues).filter((key) => designResidueKeys.has(key)));
        setSelectedEditableResidues(filtered);
    };

    const handleViewerResidueClick = (residueKeyValue: string) => {
        if (!designResidueKeys.has(residueKeyValue)) return;
        setSelectedEditableResidues((current) => {
            const next = new Set(current);
            if (next.has(residueKeyValue)) {
                next.delete(residueKeyValue);
            } else {
                next.add(residueKeyValue);
            }
            return next;
        });
    };

    const toggleContextChain = (chainId: string) => {
        setContextChains((current) => (
            current.includes(chainId)
                ? current.filter((value) => value !== chainId)
                : [...current, chainId]
        ));
    };

    const selectionSummary = useMemo(
        () => formatSelectionSummary(selectedEditableResidues.size, manualRangesText || derivedManualRanges),
        [selectedEditableResidues.size, manualRangesText, derivedManualRanges],
    );

    const resolveSourceStructurePath = async () => {
        if (sourcePath) return sourcePath;
        if (!selectedTarget) return null;
        if (selectedTarget.path) {
            setSourcePath(selectedTarget.path);
            return selectedTarget.path;
        }

        let sourceFile = selectedTarget.file ?? null;
        if (!sourceFile && selectedTarget.url) {
            const response = await fetch(selectedTarget.url);
            if (!response.ok) {
                throw new Error(`Failed to fetch structure source (${response.status})`);
            }
            const blob = await response.blob();
            sourceFile = new File([blob], canonicalStructureSourceName(selectedTarget), {
                type: blob.type || 'chemical/x-pdb',
            });
        }

        if (!sourceFile) {
            return null;
        }

        const response = await uploadFile('inputs/protein_local_redesign', sourceFile);
        const uploadedPath = response.data?.path || `inputs/protein_local_redesign/${sourceFile.name}`;
        setSourcePath(uploadedPath);
        return uploadedPath;
    };

    const submitMutation = useMutation({
        mutationFn: async (payload: Record<string, unknown>) => submitJob(payload as Partial<Job>),
        onSuccess: async (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(await completeCurrentLaunchContext(response.data) ?? '/');
        },
        onError: (err: Error) => {
            setError(err.message || 'Failed to submit protein local redesign job');
        },
    });

    const sourceSimulationMutation = useMutation({
        mutationFn: async (payload: Record<string, unknown>) => submitJob(payload as Partial<Job>, { launchContext: false }),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            const createdJob = response.data as Job | undefined;
            if (createdJob?.id) {
                setSourceSimulationJobId(createdJob.id);
            }
            setSourceLaunchError(null);
        },
        onError: (err: Error) => {
            setSourceLaunchError(err.message || 'Failed to launch source simulation');
        },
    });

    const { data: sourceSimulationJobResponse } = useQuery({
        queryKey: ['job', sourceSimulationJobId],
        queryFn: () => fetchJobById(sourceSimulationJobId as string),
        enabled: !!sourceSimulationJobId,
        refetchInterval: (query) => {
            const status = (query.state.data?.data as Job | undefined)?.status;
            return status && ['completed', 'failed', 'cancelled'].includes(status) ? false : jobPollingInterval(4000, query);
        },
    });
    const sourceSimulationJob = sourceSimulationJobResponse?.data ?? null;

    const { data: sourceSimulationDesignsResponse } = useQuery({
        queryKey: ['designs', 'protein-local-source', sourceSimulationJobId],
        queryFn: () =>
            fetchDesigns({
                job_id: sourceSimulationJobId as string,
                limit: 24,
            }),
        enabled: !!sourceSimulationJobId,
        refetchInterval: (query) => {
            const designs = (query.state.data?.data as { designs?: Design[] } | undefined)?.designs ?? [];
            if (designs.length > 0) return false;
            const status = sourceSimulationJob?.status;
            return status && ['failed', 'cancelled'].includes(status) ? false : jobPollingInterval(5000, query);
        },
    });

    const reusableSourceDesigns = useMemo(
        () => (sourceSimulationDesignsResponse?.data?.designs ?? []).filter((design) => !!design.pdb_path),
        [sourceSimulationDesignsResponse],
    );

    const handleLaunchSourceSimulation = async () => {
        setSourceLaunchError(null);
        const normalizedSequence = sanitizeSequenceInput(sourceSequence);
        if (!normalizedSequence) {
            setSourceLaunchError('Primary protein sequence is required to launch source simulation.');
            return;
        }

        const complexMode = sourceLigands.length > 0;
        const { components, resolvedPrimaryId, binderIds } = buildSourceComplexComponents(
            sourcePrimaryChainId,
            normalizedSequence,
            sourceSequenceName,
            sourceLigands,
        );

        const params: Record<string, unknown> = {
            sequence: normalizedSequence,
            sequence_name: sourceSequenceName.trim() || 'source_complex',
            pred_method: sourcePredictor,
            num_parallel_jobs: Math.max(1, sourceNumParallelJobs),
            boltz_use_msa: sourceBoltzUseMsa,
            boltz_recycling_steps: sourceBoltzRecyclingSteps,
            boltz_sampling_steps: sourceBoltzSamplingSteps,
            boltz_num_samples: sourceBoltzNumSamples,
            boltz_max_parallel_samples: Math.max(1, Math.min(sourceBoltzNumSamples, sourceBoltzMaxParallelSamples)),
        };

        if (complexMode) {
            params.complex_components = components;
            params.primary_chain_id = resolvedPrimaryId;
            params.target_chains = resolvedPrimaryId;
            if (binderIds.length > 0) {
                params.binder_chains = binderIds.join(',');
            }
        }

        await sourceSimulationMutation.mutateAsync({
            name: sourceSimulationJobName.trim() || `${jobName.trim() || 'protein_local_redesign'}_source`,
            model_id: 'boltz2',
            mode: complexMode ? 'complex' : 'predict',
            params,
        });
    };

    const handlePredictedSourceDesign = (design: Design) => {
        setSelectedTarget({
            type: 'run',
            url: `/api/designs/${design.id}/pdb`,
            name: design.name,
            designId: design.id,
        });
        setSourcePath(design.pdb_path || null);
        setParsedStructure(null);
        setSelectedEditableResidues(new Set());
        setStructureError(null);
        setShowSourceSimulation(false);
    };

    const handleSubmit = async () => {
        setError(null);
        if (!jobName.trim()) {
            setError('Job name is required');
            return;
        }
        if (!isNativeLocalRedesign && !designChain.trim()) {
            setError('Choose a design chain before submitting.');
            return;
        }
        if (!selectedTarget && !sourcePath) {
            setError('Choose a source complex before submitting.');
            return;
        }
        if (isNativeLocalRedesign) {
            if (effectiveNativePinnedGpu === null) {
                setError('Choose one physical GPU for native RFD3 before submitting.');
                return;
            }
            if (!gpuOptions.some((gpu) => gpu.index === effectiveNativePinnedGpu)) {
                setError('The selected native RFD3 GPU is absent from the live GPU inventory.');
                return;
            }
        }

        const effectiveRanges = (manualRangesText || derivedManualRanges).trim();
        if (!isNativeLocalRedesign && regionMode === 'manual_ranges' && !effectiveRanges) {
            setError('Select editable residues visually or provide a redesign range string.');
            return;
        }
        if (!isNativeLocalRedesign && regionMode === 'interface_shell' && contextChains.length === 0) {
            setError('Choose at least one context chain or nucleic-acid partner for interface-shell mode.');
            return;
        }

        let resolvedPath: string | null = null;
        try {
            resolvedPath = await resolveSourceStructurePath();
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Failed to resolve source structure path');
            return;
        }

        if (!resolvedPath) {
            setError('Failed to determine the source structure path for the workflow.');
            return;
        }

        if (isNativeLocalRedesign) {
            if (nativeRedesignMode === 'partial_diffusion' && !effectiveRanges) {
                setError('Partial diffusion requires at least one selected editable residue.');
                return;
            }
            if (nativeRedesignMode === 'minimal_insertion' && !generatedInsertionContig) {
                setError('Minimal insertion requires a valid source-bound anchor and insertion length range.');
                return;
            }

            const nativeParams: Record<string, unknown> = {
                input_structure: resolvedPath,
                redesign_mode: nativeRedesignMode,
                region_mode: 'manual_ranges',
                design_chains: designChain.trim() || undefined,
                context_chains: contextChains,
                redesign_ranges: effectiveRanges || undefined,
                source_residue_identities: activeProteinChains.map((chain) => ({
                    chain_id: chain.id,
                    residues: chain.residues.map((residue) => ({
                        res_num: residue.resNum,
                        insertion_code: residue.iCode || '',
                        residue_name: residue.resName,
                    })),
                })),
                profile_id: nativeProfileId,
                sequence_policy: 'skip',
                insertion_anchor: nativeRedesignMode === 'minimal_insertion' ? nativeInsertionAnchor.trim() || undefined : undefined,
                insertion_min_length: nativeRedesignMode === 'minimal_insertion' ? Number.parseInt(nativeInsertionMinLength, 10) : undefined,
                insertion_max_length: nativeRedesignMode === 'minimal_insertion' ? Number.parseInt(nativeInsertionMaxLength, 10) : undefined,
                partial_t: nativeRedesignMode === 'partial_diffusion' ? nativePartialT : undefined,
                ligand: nativeLigand.trim() || undefined,
                select_hotspots: nativeHotspots.split(',').map((value) => value.trim()).filter(Boolean),
                select_hbond_donor: nativeHbondDonors.split(',').map((value) => value.trim()).filter(Boolean),
                select_hbond_acceptor: nativeHbondAcceptors.split(',').map((value) => value.trim()).filter(Boolean),
                num_designs: numDesigns,
                seed: parseOptionalIntegerInput(nativeSeed),
                dump_trajectories: nativeDumpTrajectories,
                write_full_json: true,
            };
            await submitMutation.mutateAsync({
                name: jobName.trim(),
                model_id: 'protein_local_redesign',
                mode: 'local_redesign',
                pinned_gpu: effectiveNativePinnedGpu,
                params: nativeParams,
            });
            return;
        }

        await submitMutation.mutateAsync({
            name: jobName.trim(),
            model_id: submissionModelId,
            mode: submissionMode,
            params: {
                input_pdb: resolvedPath,
                model_number: selectedModelNumber ?? undefined,
                design_chains: designChain.trim(),
                context_chains: contextChains.length > 0 ? contextChains.join(',') : null,
                region_mode: regionMode,
                redesign_ranges: regionMode === 'manual_ranges' ? effectiveRanges : null,
                interface_cutoff: interfaceCutoff,
                region_padding: regionPadding,
                num_designs: numDesigns,
                seq_method: seqMethod,
                seqs_per_design: seqsPerDesign,
                fix_fixed_sidechains: fixFixedSidechains,
                run_boltz_validation: runBoltzValidation,
                boltz_sampling_steps: boltzSamplingSteps,
                boltz_recycling_steps: boltzRecyclingSteps,
                interactive_gating: interactiveGating,
                interactive_gate_stage: interactiveGateStage,
                rfd3_batches_per_design: rfd3BatchesPerDesign,
                rfd3_extra_config: rfd3ExtraConfig.trim() || undefined,
                rfd_min_helices: parseOptionalIntegerInput(rfdMinHelices),
                rfd_max_helices: parseOptionalIntegerInput(rfdMaxHelices),
                rfd_min_strands: parseOptionalIntegerInput(rfdMinStrands),
                rfd_max_strands: parseOptionalIntegerInput(rfdMaxStrands),
                rfd_min_ss: parseOptionalIntegerInput(rfdMinSs),
                rfd_max_ss: parseOptionalIntegerInput(rfdMaxSs),
                rfd_min_rog: parseOptionalNumberInput(rfdMinRog),
                rfd_max_rog: parseOptionalNumberInput(rfdMaxRog),
            },
        });
    };

    return (
        <div className="space-y-6 text-[var(--text-primary)]">
            <div className="space-y-4 rounded-xl border p-5" style={themedPanelStyle}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <button
                            onClick={onBack}
                            className="rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors"
                            style={themedMutedInsetStyle}
                        >
                            Back
                        </button>
                        <span className="rounded-full border px-2.5 py-1 text-xs font-semibold uppercase tracking-[0.18em]" style={themedTagStyle('var(--warning)')}>
                            RFD3 Local Editing
                        </span>
                        <span className="rounded-full border px-2.5 py-1 text-xs font-semibold uppercase tracking-[0.18em]" style={themedTagStyle('var(--warning)')}>
                            Experimental Alpha
                        </span>
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs">
                        {workflowSteps.map((step, index) => (
                            <span
                                key={step}
                                className="rounded-full border px-2.5 py-1"
                                style={index === 1 ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                            >
                                {step}
                            </span>
                        ))}
                    </div>
                </div>
                <div>
                    <h1 className="text-3xl font-semibold">Protein Local Redesign</h1>
                    {isNativeLocalRedesign && (
                        <div className="mt-4 space-y-4 rounded-xl border p-4" style={themedInsetStyle}>
                            <div>
                                <div className="text-sm font-semibold">Native RFD3 redesign contract</div>
                                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                    Sequence design is not requested in this lane. RFD3 receives a source-bound partial-diffusion or minimal-insertion contract.
                                </p>
                            </div>
                            <div className="grid gap-3 md:grid-cols-3">
                                <label className="space-y-1 text-xs font-medium">
                                    Redesign mode
                                    <select className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} value={nativeRedesignMode} onChange={(event) => setNativeRedesignMode(event.target.value as NativeRedesignMode)}>
                                        <option value="partial_diffusion">Fixed-sequence partial diffusion</option>
                                        <option value="minimal_insertion">Minimal insertion</option>
                                    </select>
                                </label>
                                <label className="space-y-1 text-xs font-medium">
                                    Acceptance profile
                                    <select className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} value={nativeProfileId} onChange={(event) => setNativeProfileId(event.target.value)}>
                                        <option value="generic_local_redesign_v1">Generic local redesign</option>
                                        <option value="drt4_datp_gate_v1">DRT4 dATP gate</option>
                                    </select>
                                </label>
                                <label className="space-y-1 text-xs font-medium">
                                    Partial t
                                    <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} type="number" min="0" step="0.1" value={nativePartialT} onChange={(event) => setNativePartialT(Number(event.target.value))} disabled={nativeRedesignMode !== 'partial_diffusion'} />
                                </label>
                            </div>

                            {nativeRedesignMode === 'minimal_insertion' && (
                                <>
                                    <div className="grid gap-3 md:grid-cols-3">
                                        <label className="space-y-1 text-xs font-medium">
                                            Insert after residue
                                            <input className="w-full rounded-lg border px-3 py-2 font-mono text-sm" style={themedInputStyle} value={nativeInsertionAnchor} onChange={(event) => setNativeInsertionAnchor(event.target.value)} placeholder="A310" />
                                        </label>
                                        <label className="space-y-1 text-xs font-medium">
                                            Minimum inserted length
                                            <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} type="number" min="1" value={nativeInsertionMinLength} onChange={(event) => setNativeInsertionMinLength(event.target.value)} />
                                        </label>
                                        <label className="space-y-1 text-xs font-medium">
                                            Maximum inserted length
                                            <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} type="number" min="1" value={nativeInsertionMaxLength} onChange={(event) => setNativeInsertionMaxLength(event.target.value)} />
                                        </label>
                                    </div>
                                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 font-mono text-xs text-emerald-100">
                                        {generatedInsertionContig || 'Select a valid source residue anchor.'}
                                    </div>
                                </>
                            )}
                            <div className="grid gap-3 md:grid-cols-2">
                                <label className="space-y-1 text-xs font-medium">
                                    Ligand or context IDs
                                    <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} value={nativeLigand} onChange={(event) => setNativeLigand(event.target.value)} placeholder="DATP_ID,METAL_ID" />
                                </label>
                                <label className="space-y-1 text-xs font-medium">
                                    Hotspots
                                    <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} value={nativeHotspots} onChange={(event) => setNativeHotspots(event.target.value)} placeholder="A310,A315" />
                                </label>
                                <label className="space-y-1 text-xs font-medium">
                                    Desired H-bond donors
                                    <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} value={nativeHbondDonors} onChange={(event) => setNativeHbondDonors(event.target.value)} placeholder="DATP_ID:N1" />
                                </label>
                                <label className="space-y-1 text-xs font-medium">
                                    Desired H-bond acceptors
                                    <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} value={nativeHbondAcceptors} onChange={(event) => setNativeHbondAcceptors(event.target.value)} placeholder="DATP_ID:N6" />
                                </label>
                            </div>
                            <div className="grid gap-3 md:grid-cols-3">
                                <label className="space-y-1 text-xs font-medium">
                                    Seed
                                    <input className="w-full rounded-lg border px-3 py-2 text-sm" style={themedInputStyle} type="number" min="0" value={nativeSeed} onChange={(event) => setNativeSeed(event.target.value)} placeholder="RFD3 default" />
                                </label>
                                <label className="space-y-1 text-xs font-medium">
                                    Physical GPU (required)
                                    <select
                                        className="w-full rounded-lg border px-3 py-2 text-sm"
                                        style={themedInputStyle}
                                        value={effectiveNativePinnedGpu ?? ''}
                                        onChange={(event) => setNativePinnedGpu(
                                            event.target.value === '' ? null : parseExplicitGpuPin(Number(event.target.value))
                                        )}
                                        disabled={
                                            requiredPinnedGpu !== null
                                            || gpuCatalogLoading
                                            || gpuCatalogError
                                            || gpuOptions.length === 0
                                        }
                                    >
                                        <option value="">Select GPU</option>
                                        {gpuOptions.map((gpu) => (
                                            <option key={gpu.index} value={gpu.index}>
                                                GPU {gpu.index} · {gpu.label}
                                            </option>
                                        ))}
                                    </select>
                                    <span className="block text-[11px] text-[var(--text-secondary)]">
                                        {gpuCatalogLoading
                                            ? 'Loading the live physical GPU inventory.'
                                            : gpuCatalogError || gpuOptions.length === 0
                                                ? 'Live physical GPU inventory unavailable. Submission is blocked.'
                                                : 'Scheduler assignment must match this exact physical GPU.'}
                                    </span>
                                </label>
                                <div className="space-y-2 rounded-lg border px-3 py-2" style={themedMutedInsetStyle}>
                                    <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                                        <input type="checkbox" checked={nativeDumpTrajectories} onChange={(event) => setNativeDumpTrajectories(event.target.checked)} />
                                        Retain noisy and denoised RFD3 trajectories
                                    </label>
                                    <div className="text-xs text-[var(--text-secondary)]">Native JSON metadata is required for typed result ingestion.</div>
                                </div>
                            </div>
                            <div className="rounded-lg border px-3 py-2 text-xs text-[var(--text-secondary)]" style={themedMutedInsetStyle}>
                                `select_unfixed_sequence` stays empty. Sequence design remains explicitly not requested in the native skip path.
                            </div>
                        </div>
                    )}
                    {!isNativeLocalRedesign && (
                        <p className="mt-2 max-w-4xl text-sm leading-6 text-[var(--text-secondary)]">
                            Visual region pick → local remodeling → sequence redesign → optional validator.
                        </p>
                    )}
                    <div className="mt-4 max-w-4xl space-y-3 rounded-xl border px-4 py-3" style={themedSelectedStyle('var(--warning)')}>
                        <div className="flex flex-wrap items-center gap-3 text-sm">
                            <span className="rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em]" style={themedTagStyle('var(--warning)')}>
                                Active alpha
                            </span>
                            <span className="text-[var(--text-secondary)]">Real structure-driven loop; inspect outputs before reuse.</span>
                        </div>
                        <ModelDocumentationLinks
                            topics={['rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2']}
                            summary="Method background and upstream references are linked out; the launcher stays focused on source, region, and validation controls."
                            compact
                        />
                    </div>
                </div>
            </div>

            {(error || structureError) && (
                <div
                    className="rounded-xl border px-4 py-3 text-sm"
                    style={{
                        backgroundColor: 'color-mix(in srgb, var(--danger) 14%, transparent)',
                        borderColor: 'color-mix(in srgb, var(--danger) 42%, var(--border-primary))',
                        color: 'var(--text-primary)',
                    }}
                >
                    {error || structureError}
                </div>
            )}

            <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Initial Structure Simulation</h2>
                        <p className="mt-1 max-w-4xl text-sm text-[var(--text-secondary)]">
                            Optional source-complex simulation; promote completed PDB-backed outputs into redesign.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => setShowSourceSimulation((current) => !current)}
                        className="rounded-lg border px-3 py-2 text-xs transition-colors"
                        style={showSourceSimulation ? themedSelectedStyle('var(--link)') : themedInsetStyle}
                    >
                        {showSourceSimulation ? 'Hide Source Sim' : 'Show Source Sim'}
                    </button>
                </div>

                {showSourceSimulation && (
                    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                        <div className="space-y-4">
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <div className="grid gap-4 md:grid-cols-2">
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Source Job Name</label>
                                        <input
                                            value={sourceSimulationJobName}
                                            onChange={(event) => setSourceSimulationJobName(event.target.value)}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                            placeholder="tDT_source_complex"
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Primary Chain ID</label>
                                        <input
                                            value={sourcePrimaryChainId}
                                            onChange={(event) => setSourcePrimaryChainId(event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 2) || 'A')}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                            placeholder="A"
                                        />
                                    </div>
                                    <div className="md:col-span-2">
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Primary Protein Sequence</label>
                                        <textarea
                                            value={sourceSequence}
                                            onChange={(event) => setSourceSequence(sanitizeSequenceInput(event.target.value))}
                                            rows={5}
                                            className="w-full rounded-lg border px-3 py-2 text-sm font-mono outline-none"
                                            style={themedInputStyle}
                                            placeholder="Paste the protein sequence you want to simulate before redesign."
                                        />
                                        <div className="mt-2 flex items-center justify-between text-xs text-[var(--text-secondary)]">
                                            <span>{sourceSequence.length} aa</span>
                                            <input
                                                value={sourceSequenceName}
                                                onChange={(event) => setSourceSequenceName(event.target.value)}
                                                className="w-56 rounded-lg border px-3 py-1.5 text-xs outline-none"
                                                style={themedInputStyle}
                                                placeholder="source_complex"
                                            />
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <LigandSelector
                                ligands={sourceLigands}
                                setLigands={setSourceLigands}
                                showCustomSmiles={true}
                            />
                        </div>

                        <div className="space-y-4">
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <div className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Source Predictor</div>
                                <div className="grid gap-2 md:grid-cols-2">
                                    <button
                                        type="button"
                                        onClick={() => setSourcePredictor('boltz')}
                                        className="rounded-lg border px-3 py-2 text-left text-sm transition-colors"
                                        style={sourcePredictor === 'boltz' ? themedSelectedStyle('var(--accent-primary)') : themedMutedInsetStyle}
                                    >
                                        <div className="font-medium">Boltz-2</div>
                                        <div className="mt-1 text-xs text-[var(--text-secondary)]">Directly reusable PDB outputs for the redesign stage.</div>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setSourcePredictor('all')}
                                        className="rounded-lg border px-3 py-2 text-left text-sm transition-colors"
                                        style={sourcePredictor === 'all' ? themedSelectedStyle('var(--warning)') : themedMutedInsetStyle}
                                    >
                                        <div className="font-medium">Boltz + Protenix</div>
                                        <div className="mt-1 text-xs text-[var(--text-secondary)]">Launch both; reusable source pickers will surface the PDB-backed outputs.</div>
                                    </button>
                                </div>
                            </div>

                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <div className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Simulation Settings</div>
                                <div className="grid gap-3 md:grid-cols-2">
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Parallel Jobs</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={24}
                                            value={sourceNumParallelJobs}
                                            onChange={(event) => setSourceNumParallelJobs(Math.max(1, Math.min(24, Number(event.target.value) || 1)))}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Boltz Num Samples</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={12}
                                            value={sourceBoltzNumSamples}
                                            onChange={(event) => setSourceBoltzNumSamples(Math.max(1, Math.min(12, Number(event.target.value) || 1)))}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Boltz Sampling Steps</label>
                                        <input
                                            type="number"
                                            min={10}
                                            max={1000}
                                            value={sourceBoltzSamplingSteps}
                                            onChange={(event) => setSourceBoltzSamplingSteps(Math.max(10, Math.min(1000, Number(event.target.value) || 200)))}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Boltz Recycling Steps</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={10}
                                            value={sourceBoltzRecyclingSteps}
                                            onChange={(event) => setSourceBoltzRecyclingSteps(Math.max(1, Math.min(10, Number(event.target.value) || 3)))}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Boltz Max Parallel</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={12}
                                            value={sourceBoltzMaxParallelSamples}
                                            onChange={(event) => setSourceBoltzMaxParallelSamples(Math.max(1, Math.min(12, Number(event.target.value) || 1)))}
                                            className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                        />
                                    </div>
                                    <label className="flex items-start gap-3 rounded-lg border p-3 text-sm md:col-span-2" style={themedMutedInsetStyle}>
                                        <input
                                            type="checkbox"
                                            checked={sourceBoltzUseMsa}
                                            onChange={(event) => setSourceBoltzUseMsa(event.target.checked)}
                                            className="mt-0.5"
                                        />
                                        <span>Use MSA during the source simulation step for the primary protein component.</span>
                                    </label>
                                </div>
                            </div>

                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <div className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Launch And Reuse</div>
                                <button
                                    type="button"
                                    onClick={() => void handleLaunchSourceSimulation()}
                                    disabled={sourceSimulationMutation.isPending}
                                    className="w-full rounded-lg border px-4 py-3 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                                    style={themedSelectedStyle('var(--link)')}
                                >
                                    {sourceSimulationMutation.isPending ? 'Launching Source Simulation…' : 'Launch Source Simulation'}
                                </button>
                                <p className="mt-3 text-xs text-[var(--text-secondary)]">
                                    Creates a source-structure job; promote completed PDB-backed designs below.
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {(sourceLaunchError || sourceSimulationJobId) && (
                    <div className="rounded-lg border p-3" style={sourceLaunchError ? themedSelectedStyle('var(--danger)') : themedInsetStyle}>
                        {sourceLaunchError && (
                            <div className="mb-3 text-sm">{sourceLaunchError}</div>
                        )}
                        {sourceSimulationJobId && (
                            <div className="space-y-3">
                                <div className="flex flex-wrap items-center justify-between gap-3">
                                    <div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Source Simulation Job</div>
                                        <div className="mt-1 text-sm font-medium">{sourceSimulationJob?.name || sourceSimulationJobName}</div>
                                    </div>
                                    <div className="rounded-lg border px-3 py-2 text-xs" style={themedMutedInsetStyle}>
                                        Status: <span className="font-semibold text-[var(--text-primary)]">{sourceSimulationJob?.status || 'queued'}</span>
                                    </div>
                                </div>

                                {reusableSourceDesigns.length > 0 ? (
                                    <div className="space-y-2">
                                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Promote A Predicted Source</div>
                                        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                                            {reusableSourceDesigns.map((design) => (
                                                <button
                                                    key={design.id}
                                                    type="button"
                                                    onClick={() => handlePredictedSourceDesign(design)}
                                                    className="rounded-lg border px-3 py-3 text-left transition-colors"
                                                    style={themedMutedInsetStyle}
                                                >
                                                    <div className="font-medium">{design.name}</div>
                                                    <div className="mt-1 text-xs text-[var(--text-secondary)]">
                                                        pLDDT {design.plddt_overall != null ? design.plddt_overall.toFixed(1) : '—'} · iPTM {design.iptm != null ? design.iptm.toFixed(2) : '—'}
                                                    </div>
                                                    <div className="mt-3 text-xs text-[var(--link)]">Use as source complex</div>
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                ) : (
                                    <div className="text-xs text-[var(--text-secondary)]">
                                        {sourceSimulationJob?.status === 'completed'
                                            ? 'The source job completed, but no PDB-backed designs are available to promote yet.'
                                            : 'Waiting for predicted designs. This panel refreshes automatically and will surface reusable source structures here.'}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </section>

            <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.18fr)_minmax(20rem,0.82fr)]">
                <div className="space-y-6">
                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <h2 className="text-lg font-semibold">Source Complex</h2>
                                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                    Upload, reuse a run, choose a preset, or fetch RCSB—no manual path typing.
                                </p>
                            </div>
                            <div className="rounded-lg border px-3 py-2 text-xs" style={themedInsetStyle}>
                                <div className="uppercase tracking-[0.18em] text-[var(--text-secondary)]">Selected</div>
                                <div className="mt-1 font-medium">{getSelectionName(selectedTarget) || (sourcePath ?? 'No structure selected')}</div>
                            </div>
                        </div>

                        <TargetAntigenSelector
                            label="Structure Source"
                            initialTab={sourceSimulationJobId && !selectedTarget ? 'runs' : 'upload'}
                            selectedTarget={selectedTarget}
                            onSelect={(target) => {
                                setSelectedTarget(target);
                                setSourcePath(target?.path || null);
                                setParsedStructure(null);
                                setSelectedEditableResidues(new Set());
                            }}
                        />

                        {sourcePath && (
                            <div className="rounded-lg border px-3 py-2 text-xs font-mono" style={themedInsetStyle}>
                                {sourcePath}
                            </div>
                        )}

                        {structureLoading && (
                            <div className="rounded-lg border px-4 py-6 text-sm text-[var(--text-secondary)]" style={themedInsetStyle}>
                                Parsing structure and building the visual chain map…
                            </div>
                        )}

                        {parsedStructure && (
                            <div className="grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <div className="text-sm font-medium">Structure Preview</div>
                                        <div className="flex gap-2">
                                            {parsedStructure.models.length > 1 && (
                                                <select
                                                    value={selectedModelNumber ?? parsedStructure.models[0]?.modelNumber ?? 1}
                                                    onChange={(event) => setSelectedModelNumber(Number(event.target.value))}
                                                    className="rounded-lg border px-3 py-2 text-sm outline-none"
                                                    style={themedInputStyle}
                                                >
                                                    {parsedStructure.models.map((model) => (
                                                        <option key={model.modelNumber} value={model.modelNumber}>
                                                            {model.label}
                                                        </option>
                                                    ))}
                                                </select>
                                            )}
                                            <button
                                                type="button"
                                                onClick={() => setShowStructureViewer((current) => !current)}
                                                className="rounded-lg border px-3 py-2 text-xs transition-colors"
                                                style={showStructureViewer ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                                            >
                                                {showStructureViewer ? 'Hide 3D' : 'Show 3D'}
                                            </button>
                                        </div>
                                    </div>

                                    {showStructureViewer && (
                                        <EpitopeMolstarViewer
                                            structureUrl={viewerStructureUrl || undefined}
                                            height={420}
                                            selectedResidues={regionMode === 'manual_ranges' ? selectedEditableResidues : new Set<string>()}
                                            onResidueClick={regionMode === 'manual_ranges' ? handleViewerResidueClick : undefined}
                                        />
                                    )}

                                    <div className="rounded-lg border px-3 py-2 text-xs text-[var(--text-secondary)]" style={themedInsetStyle}>
                                        {regionMode === 'manual_ranges'
                                            ? 'Click residues on the design chain in 3D, or use the residue grid below, to define the redesign window.'
                                            : 'Viewer stays in sync with the selected model while interface-shell mode derives the region from the chosen context chains.'}
                                    </div>
                                </div>

                                <div className="space-y-4">
                                    <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Job Name</div>
                                        <input
                                            value={jobName}
                                            onChange={(event) => setJobName(event.target.value)}
                                            className="mt-2 w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                            style={themedInputStyle}
                                            placeholder="tDT_selectivity_redesign"
                                        />
                                    </div>

                                    <div className="space-y-2 rounded-lg border p-3" style={themedInsetStyle}>
                                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Design Chain</div>
                                        <div className="flex flex-wrap gap-2">
                                            {designableProteinChains.map((chain) => (
                                                <button
                                                    key={chain.id}
                                                    type="button"
                                                    onClick={() => setDesignChain(chain.id)}
                                                    className="rounded-lg border px-3 py-2 text-sm transition-all"
                                                    style={designChain === chain.id ? themedSelectedStyle('var(--accent-primary)') : themedMutedInsetStyle}
                                                >
                                                    Chain {chain.id} ({chain.residueCount} aa)
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-xs text-[var(--text-secondary)]">
                                            Only protein chains are designable. The residue selector below follows this chain.
                                        </p>
                                    </div>

                                    <div className="space-y-2 rounded-lg border p-3" style={themedInsetStyle}>
                                        <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Context Chains</div>
                                        <div className="flex flex-wrap gap-2">
                                            {allChainSummaries
                                                .filter((chain) => chain.id !== designChain)
                                                .map((chain) => {
                                                    const accent = getChainTypeAccent(chain.type);
                                                    const selected = contextChains.includes(chain.id);
                                                    return (
                                                        <button
                                                            key={chain.id}
                                                            type="button"
                                                            onClick={() => toggleContextChain(chain.id)}
                                                            className="rounded-lg border px-3 py-2 text-sm transition-all"
                                                            style={selected ? themedSelectedStyle(accent) : themedMutedInsetStyle}
                                                        >
                                                            <span className="font-medium">Chain {chain.id}</span>
                                                            <span className="ml-2 text-xs uppercase tracking-[0.14em] text-[var(--text-secondary)]">{chain.type}</span>
                                                            <span className="ml-2 text-xs text-[var(--text-secondary)]">{chain.residueCount} residues</span>
                                                        </button>
                                                    );
                                                })}
                                        </div>
                                        <p className="text-xs text-[var(--text-secondary)]">
                                            These chains define the structural context for interface-shell mode. Include DNA or RNA partners here when relevant.
                                        </p>
                                    </div>
                                </div>
                            </div>
                        )}
                    </section>

                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <h2 className="text-lg font-semibold">Editable Region</h2>
                                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                    Manual mode is fully visual. Interface-shell mode derives the redesign window from the selected context chains and cutoff.
                                </p>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    type="button"
                                    onClick={() => setRegionMode('manual_ranges')}
                                    className="rounded-lg border px-3 py-2 text-xs transition-colors"
                                    style={regionMode === 'manual_ranges' ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}
                                >
                                    Visual Manual Selection
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setRegionMode('interface_shell')}
                                    className="rounded-lg border px-3 py-2 text-xs transition-colors"
                                    style={regionMode === 'interface_shell' ? themedSelectedStyle('var(--link)') : themedInsetStyle}
                                >
                                    Interface Shell
                                </button>
                            </div>
                        </div>

                        {regionMode === 'manual_ranges' ? (
                            <div className="space-y-4">
                                <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                    <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Current Selection</div>
                                    <div className="mt-1 text-sm">{selectionSummary}</div>
                                    <div className="mt-3">
                                        <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Range String</label>
                                        <input
                                            value={manualRangesText}
                                            onChange={(event) => setManualRangesText(event.target.value)}
                                            className="w-full rounded-lg border px-3 py-2 text-sm font-mono outline-none"
                                            style={themedInputStyle}
                                            placeholder="A45-58,A83-91"
                                        />
                                        <p className="mt-2 text-xs text-[var(--text-secondary)]">
                                            This field is auto-filled from the visual selector and remains editable if you want to refine the exact range string manually.
                                        </p>
                                    </div>
                                </div>

                                {activeDesignChain ? (
                                    <EpitopeSelector
                                        chains={activeProteinChains}
                                        activeChain={designChain}
                                        selectedResidues={selectedEditableResidues}
                                        onSelectionChange={handleResidueSelectionChange}
                                        selectedLabel="Selected redesign residues"
                                    />
                                ) : (
                                    <div className="rounded-lg border px-4 py-8 text-sm text-[var(--text-secondary)]" style={themedInsetStyle}>
                                        Choose a source structure and design chain to enable residue picking.
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                    <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Interface Cutoff (A)</label>
                                    <input
                                        type="number"
                                        min={2}
                                        max={15}
                                        step={0.5}
                                        value={interfaceCutoff}
                                        onChange={(event) => setInterfaceCutoff(Number(event.target.value))}
                                        className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                        style={themedInputStyle}
                                    />
                                </div>
                                <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                    <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Region Padding</label>
                                    <input
                                        type="number"
                                        min={0}
                                        max={12}
                                        value={regionPadding}
                                        onChange={(event) => setRegionPadding(Number(event.target.value))}
                                        className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                        style={themedInputStyle}
                                    />
                                </div>
                                <div className="md:col-span-2 rounded-lg border p-3" style={themedInsetStyle}>
                                    <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Derived Region Plan</div>
                                    <div className="mt-1 text-sm">
                                        {contextChains.length > 0
                                            ? `Chain ${designChain || '—'} vs ${contextChains.join(', ')} · ${interfaceCutoff.toFixed(1)} Å shell · +${regionPadding} residues.`
                                            : 'Select at least one context chain.'}
                                    </div>
                                </div>
                            </div>
                        )}
                    </section>
                </div>

                <div className="space-y-6">
                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div>
                            <h2 className="text-lg font-semibold">{proteinLocalRedesignUiState.sequenceSectionLabel}</h2>
                            <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                {isNativeLocalRedesign
                                    ? 'Sequence design is not requested or run by the native RFD3 lane.'
                                    : 'Choose the redesign backend and sampling depth for the remodeled backbones.'}
                            </p>
                        </div>

                        <div className="space-y-4">
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Sequence Method</label>
                                <select
                                    value={seqMethod}
                                    onChange={(event) => setSeqMethod(event.target.value as SequenceMethod)}
                                    disabled={isNativeLocalRedesign}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                    style={themedInputStyle}
                                >
                                    {isNativeLocalRedesign ? (
                                        <option value="skip">Skip sequence redesign</option>
                                    ) : (
                                        <>
                                            <option value="fampnn">FA-MPNN</option>
                                            <option value="mpnn">ProteinMPNN</option>
                                        </>
                                    )}
                                </select>
                            </div>

                            {proteinLocalRedesignUiState.showSequenceSampling && <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Backbone Designs</label>
                                <input
                                    type="number"
                                    min={1}
                                    max={128}
                                    value={numDesigns}
                                    onChange={(event) => setNumDesigns(Number(event.target.value))}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                    style={themedInputStyle}
                                />
                            </div>}

                            {proteinLocalRedesignUiState.showSequenceSampling && <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Sequences Per Backbone</label>
                                <input
                                    type="number"
                                    min={1}
                                    max={64}
                                    value={seqsPerDesign}
                                    onChange={(event) => setSeqsPerDesign(Number(event.target.value))}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                    style={themedInputStyle}
                                />
                            </div>}

                            {proteinLocalRedesignUiState.showSequenceSampling && <label className="flex items-start gap-3 rounded-lg border p-3 text-sm" style={themedInsetStyle}>
                                <input
                                    type="checkbox"
                                    checked={fixFixedSidechains}
                                    onChange={(event) => setFixFixedSidechains(event.target.checked)}
                                    className="mt-0.5"
                                />
                                <span>Keep sidechains fixed outside the editable region during FA-MPNN redesign.</span>
                            </label>}

                            {!proteinLocalRedesignUiState.showSequenceSampling && (
                                <div className="rounded-lg border p-3 text-sm text-[var(--text-secondary)]" style={themedMutedInsetStyle}>
                                    Sequence redesign is not requested. Native RFD3 candidates keep the source amino-acid identities.
                                </div>
                            )}
                        </div>
                    </section>

                    {proteinLocalRedesignUiState.showLegacyOptionalStages && (
                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div>
                            <h2 className="text-lg font-semibold">Review Gates</h2>
                            <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                Pause at checkpoints, filter in Results, then continue that subset.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <label className="flex items-start gap-3 rounded-lg border p-3 text-sm" style={themedInsetStyle}>
                                <input
                                    type="checkbox"
                                    checked={interactiveGating}
                                    onChange={(event) => setInteractiveGating(event.target.checked)}
                                    className="mt-0.5"
                                />
                                <span>Pause the workflow for interactive review before the next major stage.</span>
                            </label>

                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Pause After</label>
                                <select
                                    value={interactiveGateStage}
                                    onChange={(event) => setInteractiveGateStage(event.target.value as ReviewPauseStage)}
                                    disabled={!interactiveGating}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none disabled:opacity-50"
                                    style={themedInputStyle}
                                >
                                    <option value="post_rfantibody">RFD3 Remodel Backbones</option>
                                    <option value="post_fampnn">Sequence Redesign</option>
                                    <option value="post_structure_validation" disabled={!runBoltzValidation}>
                                        Boltz Validation
                                    </option>
                                </select>
                                {!runBoltzValidation && (
                                    <p className="mt-2 text-xs text-[var(--text-secondary)]">
                                        Validation pause is only available when Boltz validation is enabled.
                                    </p>
                                )}
                            </div>
                        </div>
                    </section>
                    )}

                    {proteinLocalRedesignUiState.showLegacyOptionalStages && (
                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div>
                            <h2 className="text-lg font-semibold">RFD3 Controls</h2>
                            <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                Expose the local-remodel settings instead of pinning everything to hidden defaults.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Batches Per Design</label>
                                <input
                                    type="number"
                                    min={1}
                                    max={16}
                                    value={rfd3BatchesPerDesign}
                                    onChange={(event) => setRfd3BatchesPerDesign(Math.max(1, Math.min(16, Number(event.target.value) || 1)))}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
                                    style={themedInputStyle}
                                />
                            </div>

                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Extra Config</label>
                                <textarea
                                    value={rfd3ExtraConfig}
                                    onChange={(event) => setRfd3ExtraConfig(event.target.value)}
                                    rows={4}
                                    className="w-full rounded-lg border px-3 py-2 text-sm font-mono outline-none"
                                    style={themedInputStyle}
                                    placeholder="Optional raw RFdiffusion3 overrides"
                                />
                            </div>
                        </div>
                    </section>
                    )}

                    {proteinLocalRedesignUiState.showLegacyOptionalStages && (
                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div>
                            <h2 className="text-lg font-semibold">Backbone Filters</h2>
                            <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                Optional post-RFD3 structure filters. Leave blank to keep the default permissive path.
                            </p>
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Min Helices</label>
                                <input value={rfdMinHelices} onChange={(event) => setRfdMinHelices(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Max Helices</label>
                                <input value={rfdMaxHelices} onChange={(event) => setRfdMaxHelices(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Min Strands</label>
                                <input value={rfdMinStrands} onChange={(event) => setRfdMinStrands(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Max Strands</label>
                                <input value={rfdMaxStrands} onChange={(event) => setRfdMaxStrands(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Min SS Elements</label>
                                <input value={rfdMinSs} onChange={(event) => setRfdMinSs(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Max SS Elements</label>
                                <input value={rfdMaxSs} onChange={(event) => setRfdMaxSs(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Min RoG</label>
                                <input value={rfdMinRog} onChange={(event) => setRfdMinRog(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Max RoG</label>
                                <input value={rfdMaxRog} onChange={(event) => setRfdMaxRog(event.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} placeholder="Optional" />
                            </div>
                        </div>
                    </section>
                    )}

                    {proteinLocalRedesignUiState.showLegacyOptionalStages && (
                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <div>
                            <h2 className="text-lg font-semibold">Validation</h2>
                            <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                Optionally pass the redesigned complexes through Boltz-2 after sequence optimization.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <label className="flex items-start gap-3 rounded-lg border p-3 text-sm" style={themedInsetStyle}>
                                <input
                                    type="checkbox"
                                    checked={runBoltzValidation}
                                    onChange={(event) => setRunBoltzValidation(event.target.checked)}
                                    className="mt-0.5"
                                />
                                <span>Run Boltz-2 on redesigned complexes after sequence design.</span>
                            </label>

                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Boltz Sampling Steps</label>
                                <input
                                    type="number"
                                    min={50}
                                    max={1000}
                                    value={boltzSamplingSteps}
                                    onChange={(event) => setBoltzSamplingSteps(Number(event.target.value))}
                                    disabled={!runBoltzValidation}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none disabled:opacity-50"
                                    style={themedInputStyle}
                                />
                            </div>

                            <div className="rounded-lg border p-3" style={themedInsetStyle}>
                                <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">Boltz Recycling Steps</label>
                                <input
                                    type="number"
                                    min={1}
                                    max={12}
                                    value={boltzRecyclingSteps}
                                    onChange={(event) => setBoltzRecyclingSteps(Number(event.target.value))}
                                    disabled={!runBoltzValidation}
                                    className="w-full rounded-lg border px-3 py-2 text-sm outline-none disabled:opacity-50"
                                    style={themedInputStyle}
                                />
                            </div>
                        </div>
                    </section>
                    )}

                    <section className="space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
                        <h2 className="text-lg font-semibold">Execution Summary</h2>
                        <dl className="space-y-3 text-sm">
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Structure</dt>
                                <dd className="text-right">{getSelectionName(selectedTarget) || (sourcePath ?? 'Not selected')}</dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Source Sim</dt>
                                <dd className="max-w-[18rem] text-right">
                                    {sourceSimulationJobId
                                        ? `${sourceSimulationJob?.name || sourceSimulationJobName} (${sourceSimulationJob?.status || 'queued'})`
                                        : 'Not launched'}
                                </dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Model</dt>
                                <dd className="text-right">{activeModel?.label || 'Model 1'}</dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Design Chain</dt>
                                <dd className="text-right">{designChain || '—'}</dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Context</dt>
                                <dd className="text-right">{contextChains.length > 0 ? contextChains.join(', ') : 'None selected'}</dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Region</dt>
                                <dd className="max-w-[18rem] text-right">
                                    {regionMode === 'manual_ranges'
                                        ? (manualRangesText || derivedManualRanges || 'Not set')
                                        : `Interface shell @ ${interfaceCutoff.toFixed(1)} A`}
                                </dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Redesign</dt>
                                <dd className="text-right">
                                    {proteinLocalRedesignUiState.sequenceDesignEnabled
                                        ? `${numDesigns} backbones × ${seqsPerDesign} seqs`
                                        : `${numDesigns} native RFD3 candidates`}
                                </dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Backend</dt>
                                <dd className="text-right">
                                    {seqMethod === 'skip' ? 'Sequence redesign skipped' : seqMethod === 'fampnn' ? 'FA-MPNN' : 'ProteinMPNN'}
                                </dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">RFD3</dt>
                                <dd className="text-right">
                                    {isNativeLocalRedesign
                                        ? `${numDesigns} exact samples`
                                        : `${rfd3BatchesPerDesign} batch${rfd3BatchesPerDesign === 1 ? '' : 'es'} per design`}
                                </dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Validation</dt>
                                <dd className="text-right">
                                    {isNativeLocalRedesign ? 'Not in native contract' : runBoltzValidation ? 'Boltz-2 enabled' : 'Skipped'}
                                </dd>
                            </div>
                            <div className="flex items-start justify-between gap-4">
                                <dt className="text-[var(--text-secondary)]">Pause</dt>
                                <dd className="text-right">
                                    {isNativeLocalRedesign
                                        ? 'Not configured'
                                        : !interactiveGating
                                        ? 'No pause'
                                        : interactiveGateStage === 'post_rfantibody'
                                            ? 'After remodel'
                                            : interactiveGateStage === 'post_fampnn'
                                                ? 'After sequence design'
                                                : 'After validation'}
                                </dd>
                            </div>
                        </dl>
                    </section>
                </div>
            </div>

            <div className="flex justify-end gap-3">
                <button
                    onClick={onBack}
                    className="rounded-lg border px-5 py-3 text-sm font-medium transition-colors"
                    style={themedInsetStyle}
                >
                    Cancel
                </button>
                <button
                    onClick={() => void handleSubmit()}
                    disabled={submitMutation.isPending || (
                        isNativeLocalRedesign && (
                            gpuCatalogLoading
                            || gpuCatalogError
                            || effectiveNativePinnedGpu === null
                            || !gpuOptions.some((gpu) => gpu.index === effectiveNativePinnedGpu)
                        )
                    )}
                    className="rounded-lg border px-5 py-3 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    style={themedSelectedStyle('var(--accent-primary)')}
                >
                    {submitMutation.isPending ? 'Submitting…' : 'Launch Local Redesign'}
                </button>
            </div>
        </div>
    );
}

export default ProteinLocalRedesignTemplate;
