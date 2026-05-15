

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchModels, fetchFiles, submitJob, uploadFile, fetchTemplates, fetchTemplateById, fetchInputPresets } from '../lib/api';
import { SequenceManagerModal } from './SequenceManagerModal';
import { TemplateManagerModal } from './TemplateManagerModal';
import { MutagenesisTemplate } from './MutagenesisTemplate';
import { AntibodyDenovoTemplate } from './AntibodyDenovoTemplate';
import { StructurePredictionTemplate } from './StructurePredictionTemplate';
import { BoltzGenTemplate } from './BoltzGenTemplate';
import { BindCraftTemplate } from './BindCraftTemplate';
import { OligoDesignerTemplate } from './OligoDesignerTemplate';
import { ProteinLocalRedesignTemplate } from './ProteinLocalRedesignTemplate';
import { PresetSelector } from './PresetSelector';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { StructureInput } from './StructureInput';
import { ModelDocumentationLinks, type ModelDocumentationTopic } from './ModelDocumentationLinks';
import { getDedicatedTemplateInitialValues, isDedicatedLauncherTemplate } from './jobSubmissionTemplateState.js';
import { getWorkflowModelTopics } from './workflowModelInventory.js';
import { isAntibodyPipelineMode } from '../lib/antibodyModes';

interface FileBrowserProps {
    onSelect: (path: string) => void;
    onCancel: () => void;
}

function FileBrowser({ onSelect, onCancel }: FileBrowserProps) {
    const [path, setPath] = useState('/');
    const fileInputRef = useRef<HTMLInputElement>(null);
    const queryClient = useQueryClient();

    const { data: files } = useQuery({
        queryKey: ['files', path],
        queryFn: () => fetchFiles(path),
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => uploadFile(path === '/' ? 'inputs' : path, file),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['files', path] });
        },
    });

    const handleNavigate = (newPath: string) => {
        setPath(newPath);
    };

    const handleUp = () => {
        const parts = path.split('/').filter(p => p);
        parts.pop();
        setPath('/' + parts.join('/'));
    };

    const handleUploadClick = () => {
        if (fileInputRef.current) {
            fileInputRef.current.click();
        }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            uploadMutation.mutate(e.target.files[0]);
        }
        // Reset input
        e.target.value = '';
    };

    return (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl h-[80vh] flex flex-col shadow-2xl">
                <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50 rounded-t-xl">
                    <h3 className="font-semibold text-slate-200">Select File</h3>
                    <div className="flex gap-3 items-center">
                        <input
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileChange}
                            className="hidden"
                        />
                        <button
                            onClick={handleUploadClick}
                            disabled={uploadMutation.isPending}
                            className="rounded-md border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700"
                        >
                            {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
                        </button>
                        <button onClick={onCancel} className="text-slate-400 hover:text-white">✕</button>
                    </div>
                </div>

                <div className="p-2 border-b border-slate-700 bg-slate-800/30 flex items-center gap-2">
                    <button
                        onClick={handleUp}
                        className="rounded border border-slate-600 bg-slate-800 px-2.5 py-1 text-sm font-medium text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                        disabled={path === '/'}
                    >
                        Up
                    </button>
                    <input
                        type="text"
                        value={path}
                        readOnly
                        className="flex-1 bg-transparent text-sm text-slate-400 outline-none"
                    />
                </div>

                <div className="flex-1 overflow-auto p-2">
                    {files?.data.entries.map((entry: UntypedApiValue) => (
                        <div
                            key={entry.path}
                            onClick={() => entry.is_directory ? handleNavigate(entry.path) : onSelect(entry.path)}
                            className={`flex items-center gap-3 p-2 rounded cursor-pointer ${entry.is_directory
                                ? 'text-blue-400 hover:bg-blue-500/10'
                                : 'text-slate-300 hover:bg-slate-700'
                                }`}
                        >
                            <span className={`inline-flex h-7 min-w-10 items-center justify-center rounded border text-[10px] font-semibold uppercase tracking-[0.14em] ${
                                entry.is_directory
                                    ? 'border-blue-500/30 bg-blue-500/10 text-blue-300'
                                    : 'border-slate-600 bg-slate-800 text-slate-300'
                            }`}>
                                {entry.is_directory ? 'Dir' : 'File'}
                            </span>
                            <span className="flex-1 truncate">{entry.name}</span>
                            {!entry.is_directory && (
                                <span className="text-xs text-slate-500">
                                    {(entry.size_bytes / 1024).toFixed(1)} KB
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

const compactUiCopy = (value: unknown, maxLength = 118): string => {
    if (typeof value !== 'string') return '';
    const text = value.trim().replace(/\s+/g, ' ');
    if (!text || text.length <= maxLength) return text;
    const firstSentence = text.match(/^.{1,118}?[.!?](?:\s|$)/)?.[0]?.trim();
    if (firstSentence && firstSentence.length <= maxLength) return firstSentence;
    return `${text.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
};

// Reusable param field component for grouped rendering
function ParamField({
    param,
    params,
    updateParam,
    setShowFileBrowser,
    setActiveSequenceField,
    setShowSequenceManager,
    setSequenceToSave,
    ligandPresets
}: {
    param: UntypedApiValue;
    params: Record<string, UntypedApiValue>;
    updateParam: (key: string, value: UntypedApiValue) => void;
    setShowFileBrowser: (name: string | null) => void;
    setActiveSequenceField: (name: string) => void;
    setShowSequenceManager: (show: boolean) => void;
    setSequenceToSave?: (sequence: { sequence: string; name?: string } | null) => void;
    ligandPresets: UntypedApiValue[];
}) {
    const isSequenceField = param.preset_type === 'sequence' || param.name === 'sequence' || param.type === 'text';
    const isContigField = typeof param.name === 'string' && param.name.includes('contig');
    const isPdbPresetField = param.preset_type === 'pdb';
    const isPathField = param.type === 'file' || param.type === 'directory';
    const isNumericField = param.type === 'integer' || param.type === 'number';
    const isSliderField = param.ui_control === 'slider' && isNumericField && param.minimum !== undefined && param.maximum !== undefined;
    const isWide = isSequenceField || isPathField || param.preset_type === 'ligand' || isSliderField;
    const label = param.label || param.name;
    const description = compactUiCopy(param.description, 112);
    const value = params[param.name] ?? param.default ?? (param.type === 'boolean' ? false : '');
    const numericValue = (() => {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
        const fallback = Number(param.default ?? param.minimum ?? 0);
        return Number.isFinite(fallback) ? fallback : 0;
    })();
    const numericStep = param.step ?? (param.type === 'integer' ? 1 : 0.01);
    const pathPlaceholder = param.ui_placeholder || param.placeholder || (param.type === 'directory' ? '/path/to/directory' : '/path/to/file');
    const updateNumeric = (raw: string) => {
        const parsed = param.type === 'integer' ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
        if (Number.isFinite(parsed)) {
            updateParam(param.name, parsed);
        } else {
            updateParam(param.name, param.default ?? param.minimum ?? 0);
        }
    };

    return (
        <div className={isWide ? 'col-span-full' : ''}>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">
                {label}
                {param.required && <span className="text-red-400 ml-1">*</span>}
            </label>
            {description && <p className="text-xs text-slate-500 mb-2">{description}</p>}

            {param.type === 'boolean' ? (
                <label className="flex items-center gap-3 rounded-lg border border-slate-700/70 bg-slate-900/40 px-3 py-2.5 cursor-pointer hover:border-slate-500 transition-colors">
                    <input
                        type="checkbox"
                        checked={Boolean(value)}
                        onChange={(e) => updateParam(param.name, e.target.checked)}
                        className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-blue-500 focus:ring-blue-500"
                    />
                    <span className="text-sm text-slate-200">{value ? 'Enabled' : 'Disabled'}</span>
                </label>
            ) : param.enum ? (
                <select
                    value={value ?? ''}
                    onChange={(e) => updateParam(param.name, e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                >
                    {param.enum.map((opt: string) => (
                        <option key={opt || '__empty'} value={opt}>{param.enum_labels?.[opt] || opt || 'Auto / none'}</option>
                    ))}
                </select>
            ) : isContigField ? (
                <PresetSelector
                    presetType="contig"
                    value={value || ''}
                    onChange={(val) => updateParam(param.name, val)}
                    onBrowse={() => { }}
                    placeholder={param.ui_placeholder || param.placeholder || "Select a contig preset..."}
                />
            ) : isSliderField ? (
                <div className="rounded-lg border border-blue-500/15 bg-blue-500/5 p-3 space-y-3">
                    <div className="flex items-center justify-between gap-3">
                        <span className="text-sm text-slate-200 font-medium">{numericValue}</span>
                        <input
                            type="number"
                            value={numericValue}
                            onChange={(e) => updateNumeric(e.target.value)}
                            min={param.minimum}
                            max={param.maximum}
                            step={numericStep}
                            className="w-28 bg-slate-950 border border-slate-700 rounded px-2 py-1 text-white text-sm text-right"
                        />
                    </div>
                    <input
                        type="range"
                        min={param.minimum}
                        max={param.maximum}
                        step={numericStep}
                        value={numericValue}
                        onChange={(e) => updateNumeric(e.target.value)}
                        className="w-full accent-blue-500"
                    />
                    <div className="flex justify-between text-[11px] text-slate-500">
                        <span>{param.minimum}</span>
                        <span>{param.maximum}</span>
                    </div>
                </div>
            ) : isPdbPresetField ? (
                <StructureInput
                    value={value || ''}
                    onChange={(v) => updateParam(param.name, v)}
                    onBrowse={() => setShowFileBrowser(param.name)}
                    targetChain={params['target_chain'] || params['chain_id'] || ''}
                    onTargetChainChange={(c) => updateParam('target_chain', c)}
                    enableMultiSelect={false}
                    enableDirectory={param.type === 'directory'}
                />
            ) : param.preset_type === 'ligand' ? (
                <div className="space-y-2">
                    <select
                        value=""
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">Select preset ligand...</option>
                        {ligandPresets.map((preset: UntypedApiValue) => (
                            <option key={preset.id} value={preset.smiles}>
                                {preset.name}
                            </option>
                        ))}
                    </select>
                    <input
                        type="text"
                        value={value || ''}
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder={param.ui_placeholder || "Or enter SMILES string..."}
                    />
                </div>
            ) : isSequenceField ? (
                <div className="space-y-2">
                    <div className="flex gap-2 items-center flex-wrap">
                        <button
                            type="button"
                            onClick={() => {
                                setSequenceToSave?.(null);
                                setActiveSequenceField(param.name);
                                setShowSequenceManager(true);
                            }}
                            className="rounded-lg border border-emerald-600/30 bg-emerald-600/12 px-3 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-600/20"
                        >
                            Sequence Library
                        </button>
                        <span className="text-xs text-slate-500 bg-slate-800/50 px-2 py-1 rounded">
                            {String(value || '').length} aa
                        </span>
                        {String(value || '').length > 0 && setSequenceToSave && (
                            <button
                                type="button"
                                onClick={() => {
                                    setSequenceToSave({ sequence: String(value || ''), name: params['sequence_name'] || params['job_name'] || '' });
                                    setActiveSequenceField(param.name);
                                    setShowSequenceManager(true);
                                }}
                                className="rounded-lg border border-emerald-600/30 bg-emerald-600/12 px-3 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-600/20"
                            >
                                Save to Library
                            </button>
                        )}
                        {String(value || '').length > 0 && (
                            <button
                                type="button"
                                onClick={() => updateParam(param.name, '')}
                                className="px-2 py-2 text-slate-500 hover:text-red-400 text-sm transition-colors"
                                title="Clear sequence"
                            >
                                ✕
                            </button>
                        )}
                    </div>
                    <textarea
                        value={value || ''}
                        onChange={(e) => updateParam(param.name, e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                        rows={6}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder={param.ui_placeholder || param.placeholder || 'Enter amino acid sequence...'}
                    />
                </div>
            ) : isPathField ? (
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={value || ''}
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        placeholder={pathPlaceholder}
                    />
                    <button
                        type="button"
                        onClick={() => setShowFileBrowser(param.name)}
                        className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                    >
                        Browse
                    </button>
                </div>
            ) : (
                <input
                    type={isNumericField ? 'number' : 'text'}
                    value={value ?? ''}
                    onChange={(e) => isNumericField ? updateNumeric(e.target.value) : updateParam(param.name, e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    placeholder={param.ui_placeholder || param.placeholder || ''}
                    min={param.minimum}
                    max={param.maximum}
                    step={numericStep}
                />
            )}
        </div>
    );
}

const getTemplateDocumentationTopics = (template: UntypedApiValue | null | undefined): ModelDocumentationTopic[] => {
    const workflowTopics = getWorkflowModelTopics(template?.id || template?.model_id);
    if (workflowTopics.length > 0) return workflowTopics;

    const identity = `${template?.id || ''} ${template?.model_id || ''} ${template?.name || ''}`.toLowerCase();
    if (identity.includes('boltz_cp_experimental') || identity.includes('fold-cp')) return ['fold_cp', 'boltz2'];
    if (identity.includes('confornets')) return ['confornets'];
    if (identity.includes('protein_local_redesign') || identity.includes('local redesign')) return ['rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2'];
    if (identity.includes('antibody_denovo') || identity.includes('nanobody') || identity.includes('rfantibody')) return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2'];
    if (identity.includes('structure_prediction') || identity.includes('structure prediction')) return ['boltz2', 'rf3', 'protenix'];
    if (identity.includes('boltz')) return ['boltz2'];
    if (identity.includes('rfdiffusion') || identity.includes('diffusion')) return ['rfdiffusion'];
    return [];
};

const getModelDocumentationTopics = (model: UntypedApiValue | null | undefined): ModelDocumentationTopic[] => {
    const workflowTopics = getWorkflowModelTopics(model?.id);
    if (workflowTopics.length > 0) return workflowTopics;

    const identity = `${model?.id || ''} ${model?.name || ''} ${model?.category || ''}`.toLowerCase();
    if (identity.includes('boltz_cp_experimental') || identity.includes('fold-cp')) return ['fold_cp', 'boltz2'];
    if (identity.includes('confornets')) return ['confornets'];
    if (identity.includes('protenix')) return ['protenix'];
    if (identity.includes('rf3') || identity.includes('rosettafold')) return ['rf3'];
    if (identity.includes('antibody') || identity.includes('rfantibody')) return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2'];
    if (identity.includes('rfdiffusion')) return ['rfdiffusion'];
    if (identity.includes('boltzgen')) return ['boltzgen'];
    if (identity.includes('boltz2') || identity.includes('boltz-2')) return ['boltz2'];
    return [];
};

const getCompactTemplateDescription = (template: UntypedApiValue): string => {
    switch (template.id) {
        case 'structure_prediction':
            return 'Predict proteins, nucleic acids, and complexes.';
        case 'antibody_denovo':
            return 'Generate, refine, validate, and review nanobody candidates.';
        case 'protein_local_redesign':
            return 'Remodel a selected structure region, then redesign and validate.';
        case 'boltz_cp_experimental':
            return 'Experimental Fold-CP path for large Boltz-2 folds.';
        case 'confornets_experimental':
            return 'Experimental single-chain conformer landscape workflow.';
        case 'mutagenesis':
            return 'Build variant libraries and predict structures.';
        case 'oligo_design':
            return 'Design nucleoprotein assemblies with validation.';
        default:
            return template.short_description || template.summary || template.description || '';
    }
};

const getCompactModelDescription = (model: UntypedApiValue): string => {
    switch (model.id) {
        case 'boltz2':
            return 'Structure and complex prediction validator.';
        case 'boltz_cp_experimental':
            return 'Experimental Fold-CP large-protein path.';
        case 'confornets_experimental':
            return 'Experimental monomer conformer sampling.';
        case 'antibody_denovo':
            return 'Nanobody generation and refinement toolkit.';
        case 'rfdiffusion':
            return 'Backbone generation and local redesign.';
        default:
            return model.short_description || model.summary || model.description || '';
    }
};

const getExperimentalStatusSummary = (template: UntypedApiValue): string => {
    if (template.id === 'confornets_experimental') return 'Monomer-only alpha; real upstream wrapper, compact controls.';
    if (template.id === 'boltz_cp_experimental') return 'Alpha data-plane; logical shard plan plus current GPU bridge.';
    return template.status_short || 'Active alpha; backend wired, review carefully.';
};

export function JobSubmission() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const [wizardMode, setWizardMode] = useState<'templates' | 'experimental' | 'manual'>('templates');

    // Read template from URL, allows page refresh and bookmarking
    const urlTemplate = searchParams.get('template');
    const [selectedTemplateId, setSelectedTemplateIdInternal] = useState<string | null>(urlTemplate);

    // Wrapper to sync state with URL
    const setSelectedTemplateId = useCallback((id: string | null) => {
        setSelectedTemplateIdInternal(id);
        if (id) {
            setSearchParams({ template: id }, { replace: true });
        } else {
            setSearchParams({}, { replace: true });
        }
    }, [setSearchParams]);
    const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
    const [selectedModeId, setSelectedModeId] = useState<string | null>(null);
    const [jobName, setJobName] = useState('');
    const [params, setParams] = useState<Record<string, UntypedApiValue>>({});
    const [showFileBrowser, setShowFileBrowser] = useState<string | null>(null);
    const [showSequenceManager, setShowSequenceManager] = useState(false);
    const [showTemplateManager, setShowTemplateManager] = useState(false);
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name?: string } | null>(null);
    const [activeSequenceField, setActiveSequenceField] = useState<string>('sequence');
    const [ligands, setLigands] = useState<LigandEntry[]>([]);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [clonedValues, setClonedValues] = useState<Record<string, UntypedApiValue> | undefined>(undefined);
    const [dedicatedTemplateVersion, setDedicatedTemplateVersion] = useState(0);
    const [templateManagerContext, setTemplateManagerContext] = useState<{
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }>({});

    const openTemplateManager = (context: {
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }) => {
        setTemplateManagerContext(context);
        setShowTemplateManager(true);
    };

    // Dedicated templates should not retain stale clone params once user navigates away.
    const handleDedicatedTemplateBack = () => {
        setSelectedTemplateId(null);
        setClonedValues(undefined);
    };

    const handleTemplateCardSelect = (templateId: string) => {
        setClonedValues(getDedicatedTemplateInitialValues(templateId));
        if (isDedicatedLauncherTemplate(templateId)) {
            setDedicatedTemplateVersion((prev) => prev + 1);
        }
        setSelectedTemplateId(templateId);
    };

    // Check for cloned job data on mount
    useEffect(() => {
        const stored = localStorage.getItem('clonedJobData');
        if (stored) {
            try {
                const data = JSON.parse(stored);
                console.log('Loading cloned job data:', data);

                // Set common fields
                if (data.name) setJobName(data.name);

                // Determine routing
                // 1. Antibody De Novo Template
                if (data.mode === 'antibody_denovo' || isAntibodyPipelineMode(data.mode) || data.params?.antibody_pipeline_steps) {
                    setWizardMode('templates');
                    setSelectedTemplateId('antibody_denovo');
                    setClonedValues({ ...data.params, name: data.name });
                }
                // 2. Mutagenesis Template
                else if (data.params?.mutagenesis_variants) {
                    setWizardMode('templates');
                    setSelectedTemplateId('mutagenesis');
                    // Mutagenesis logic might need updates for pre-filling too, but focusing on Antibody first
                }
                // 3. Boltz-CP experimental reuses the structure-prediction template with a fixed launch variant.
                else if (data.model_id === 'boltz_cp_experimental') {
                    setWizardMode('experimental');
                    setSelectedTemplateId('boltz_cp_experimental');
                    setClonedValues({
                        ...data.params,
                        name: data.name,
                        template_model_id: 'boltz_cp_experimental',
                        structure_launch_variant: data.params?.structure_launch_variant || 'boltz_cp_experimental',
                    });
                }
                // 4. Manual Mode
                else {
                    setWizardMode('manual');
                    setSelectedModelId(data.model_id);
                    setSelectedModeId(data.mode);
                    setParams(data.params);
                }

                // Clear storage
                localStorage.removeItem('clonedJobData');
            } catch (e) {
                console.error("Failed to parse cloned job data", e);
            }
        }
    }, [setSelectedTemplateId]);

    const { data: modelsData } = useQuery({
        queryKey: ['models'],
        queryFn: () => fetchModels(),
    });

    const { data: templatesData } = useQuery({
        queryKey: ['templates'],
        queryFn: () => fetchTemplates(),
    });

    // Dedicated launcher templates that use specialized components instead of API-driven config
    const dedicatedTemplateByModelId: Record<string, string> = {
        template_antibody_denovo: 'antibody_denovo',
        boltzgen: 'boltzgen_design',
        bindcraft: 'bindcraft',
        protein_local_redesign: 'protein_local_redesign',
        boltz_cp_experimental: 'boltz_cp_experimental',
    };
    const hardcodedWorkflowTemplates = useMemo(() => [
        {
            id: 'mutagenesis',
            name: 'Mutagenesis Library',
            description: 'Build variant libraries and predict structures.',
            icon: 'dna',
            color: '#8B5CF6',
            stages: [{ tool: 'Library Gen' }, { tool: 'Structure Prediction' }],
        },
        {
            id: 'structure_prediction',
            name: 'Structure Prediction',
            description: 'Predict proteins, nucleic acids, and complexes.',
            icon: 'microscope',
            color: '#F59E0B',
            stages: [{ tool: 'Boltz-2 / RF3 / Protenix' }],
        },
        {
            id: 'antibody_denovo',
            name: 'De Novo Nanobody Toolkit',
            description: 'Generate, refine, validate, and review nanobody candidates.',
            icon: 'flask',
            color: '#14B8A6',
            stages: [
                { tool: 'RFantibody / BoltzGen / PPIFlow' },
                { tool: 'FAMPNN' },
                { tool: 'PPIFlow (Opt.)' },
                { tool: 'Protenix / Boltz2' },
                { tool: 'Review + QC' }
            ],
        },
        {
            id: 'boltzgen_design',
            name: 'BoltzGEN',
            description: 'Generate ligand-aware binder candidates.',
            icon: 'pill',
            color: '#EC4899',
            stages: [{ tool: 'BoltzGen' }, { tool: 'Filtering' }, { tool: 'Docking' }],
        },
        {
            id: 'bindcraft',
            name: 'BindCraft',
            description: 'Design minibinders and peptides via AF2 hallucination, ProteinMPNN sequence optimization, and PyRosetta filtering.',
            icon: 'binder',
            color: '#10B981',
            stages: [{ tool: 'AF2 Hallucination' }, { tool: 'MPNN' }, { tool: 'Filtering' }],
        },
        {
            id: 'oligo_design',
            name: 'Oligo Designer',
            description: 'Design nucleoprotein assemblies with validation.',
            icon: 'dna',
            color: '#6366F1',
            stages: [{ tool: 'RFDpoly' }, { tool: 'Boltz-2' }, { tool: 'Filtering' }],
        },
    ], []);
    const hardcodedExperimentalTemplates = useMemo(() => [
        {
            id: 'protein_local_redesign',
            name: 'Protein Local Redesign',
            description: 'Remodel a selected region, redesign sequence, and validate.',
            icon: 'cube',
            color: '#22C55E',
            experimental: true,
            stages: [
                { tool: 'Region Resolve' },
                { tool: 'RFdiffusion3' },
                { tool: 'FAMPNN / MPNN' },
                { tool: 'Boltz-2 (Opt.)' }
            ],
        }
    ], []);
    const visibleApiTemplates = useMemo(() => {
        const templates = templatesData?.data ?? [];
        return templates.filter((t: UntypedApiValue) =>
            !['boltzgen_ligand', 'binder_design', 'structure_validation', 'structure_prediction'].includes(t.id) &&
            (t.id !== 'dna_polymerase' || (window as UntypedApiValue).__DEBUG_MODE__)
        );
    }, [templatesData]);
    const workflowTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: UntypedApiValue) => !t.experimental), ...hardcodedWorkflowTemplates],
        [hardcodedWorkflowTemplates, visibleApiTemplates]
    );
    const experimentalTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: UntypedApiValue) => t.experimental), ...hardcodedExperimentalTemplates],
        [hardcodedExperimentalTemplates, visibleApiTemplates]
    );

    const routeUserTemplate = (template: UntypedApiValue) => {
        const dedicatedTemplateId =
            (isDedicatedLauncherTemplate(template.base_template_id) && template.base_template_id) ||
            (template.model_id ? dedicatedTemplateByModelId[template.model_id] : null);

        if (dedicatedTemplateId) {
            const loadedJobName = template.params?.job_name || template.params?.name || template.name || '';
            const templateModelId = template.model_id || template.params?.template_model_id;
            setWizardMode(dedicatedTemplateId === 'boltz_cp_experimental' ? 'experimental' : 'templates');
            setSelectedTemplateId(dedicatedTemplateId);
            setDedicatedTemplateVersion((prev) => prev + 1);
            setClonedValues({
                ...template.params,
                name: loadedJobName,
                job_name: loadedJobName,
                template_model_id: templateModelId,
                structure_launch_variant: dedicatedTemplateId === 'boltz_cp_experimental'
                    ? (template.params?.structure_launch_variant || 'boltz_cp_experimental')
                    : template.params?.structure_launch_variant,
            });
            setJobName(loadedJobName);
            setSelectedModelId(null);
            setSelectedModeId(null);
            setParams({});
            return;
        }

        setWizardMode('manual');
        setSelectedTemplateId(null);
        setClonedValues(undefined);
        setParams(template.params || {});
        if (template.model_id) setSelectedModelId(template.model_id);
        if (template.mode) setSelectedModeId(template.mode);
        setJobName(template.params?.job_name || template.name || '');
    };

    const { data: selectedTemplateData } = useQuery({
        queryKey: ['template', selectedTemplateId],
        queryFn: () => selectedTemplateId ? fetchTemplateById(selectedTemplateId) : null,
        // Skip fetch for hardcoded templates - they don't exist in the API
        enabled: !!selectedTemplateId && !isDedicatedLauncherTemplate(selectedTemplateId),
    });
    const templateDetail = selectedTemplateData?.data?.data ?? selectedTemplateData?.data;

    // Fetch ligand presets for dynamic dropdown
    const { data: ligandPresetsData } = useQuery({
        queryKey: ['presets', 'ligand'],
        queryFn: () => fetchInputPresets('ligand'),
    });
    const ligandPresets = ligandPresetsData?.data ?? [];

    const submitMutation = useMutation({
        mutationFn: submitJob,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
        onError: (error: UntypedApiValue) => {
            console.error('Job submission failed:', error);
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object'
                ? JSON.stringify(detail, null, 2)
                : (detail || error.message || error);
            window.alert('Job Submission Failed:\n' + message);
        }
    });

    const models = (modelsData?.data ?? []).filter((model: UntypedApiValue) => !['protein_cad_experimental', 'protein_local_redesign', 'caliby_experimental', 'protein_hunter_experimental', 'boltz_cp_experimental', 'confornets_experimental'].includes(model.id));
    const selectedModel = models.find((m: UntypedApiValue) => m.id === selectedModelId);
    const selectedMode = selectedModel?.modes.find((m: UntypedApiValue) => m.id === selectedModeId);

    // Initialize params when model/mode changes (manual mode)
    useEffect(() => {
        if (selectedModel) {
            const defaults: Record<string, UntypedApiValue> = {};
            (selectedModel.params || []).forEach((p: UntypedApiValue) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            setParams(defaults);
        }
    }, [selectedModel, selectedModelId]);

    // Initialize params when template changes (template mode)
    useEffect(() => {
        if (templateDetail?.user_params) {
            const defaults: Record<string, UntypedApiValue> = {};
            templateDetail.user_params.forEach((p: UntypedApiValue) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            setParams(defaults);
        }
    }, [templateDetail]);

    useEffect(() => {
        if (!selectedTemplateId || isDedicatedLauncherTemplate(selectedTemplateId)) {
            return;
        }
        const matchedTemplate = visibleApiTemplates.find((template: UntypedApiValue) => template.id === selectedTemplateId);
        if (matchedTemplate) {
            setWizardMode(matchedTemplate.experimental ? 'experimental' : 'templates');
        }
    }, [selectedTemplateId, visibleApiTemplates]);

    // Handle param change
    const updateParam = (key: string, value: UntypedApiValue) => {
        setParams(prev => ({ ...prev, [key]: value }));
    };

    const getTemplateIconLabel = (template: UntypedApiValue) => {
        if (template.id === 'protein_cad_experimental') return 'PC';
        if (template.id === 'caliby_experimental') return 'CB';
        if (template.id === 'protein_hunter_experimental') return 'PH';
        if (template.id === 'boltz_cp_experimental') return 'CP';
        if (template.id === 'confornets_experimental') return 'CN';
        return template.icon === 'target' ? 'TG'
            : template.icon === 'flask' ? 'RF'
                : template.icon === 'dna' ? 'MU'
                    : template.icon === 'microscope' ? 'SP'
                        : template.icon === 'pill' ? 'BG'
                            : template.icon === 'binder' ? 'BC'
                                : template.icon === 'cube' ? 'PL'
                                    : 'OL';
    };

    const renderTemplateCard = (template: UntypedApiValue) => {
        const isSelected = selectedTemplateId === template.id;
        const docTopics = getTemplateDocumentationTopics(template);
        return (
            <div
                key={template.id}
                onClick={() => handleTemplateCardSelect(template.id)}
                className={`cursor-pointer rounded-lg border-2 p-4 transition-all ${
                    template.experimental
                        ? isSelected
                            ? 'border-orange-400/60 bg-orange-500/10 shadow-xl'
                            : 'border-orange-500/25 bg-orange-500/5 hover:border-orange-400/50 hover:shadow-lg'
                        : isSelected
                            ? 'scale-[1.02] border-[var(--accent-primary)] shadow-xl'
                            : 'border-[var(--border-primary)] hover:scale-[1.01] hover:border-[var(--border-secondary)] hover:shadow-lg'
                } bg-[var(--card-bg)] text-[var(--text-primary)]`}
                style={{
                    boxShadow: isSelected
                        ? template.experimental
                            ? '0 10px 34px rgba(251, 146, 60, 0.18)'
                            : '0 8px 30px color-mix(in srgb, var(--accent-primary) 35%, transparent)'
                        : undefined
                }}
            >
                <div className="mb-2 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div
                            className="flex h-10 w-10 items-center justify-center rounded text-sm font-bold"
                            style={{ backgroundColor: `${template.color}20`, color: template.color }}
                        >
                            {getTemplateIconLabel(template)}
                        </div>
                        <h3 className="font-bold text-base" style={{ color: template.color }}>{template.name}</h3>
                    </div>
                    {template.experimental && (
                        <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                            Experimental Alpha
                        </span>
                    )}
                </div>
                <p className="mb-2 text-xs opacity-70 line-clamp-2">{getCompactTemplateDescription(template)}</p>
                {docTopics.length > 0 && (
                    <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                        Docs available
                    </div>
                )}
                <div className="flex items-center gap-0.5 flex-wrap text-[10px]">
                    {template.stages.map((stage: UntypedApiValue, idx: number) => (
                        <div key={idx} className="flex items-center">
                            <span
                                className="rounded px-1.5 py-0.5 font-medium"
                                style={{ backgroundColor: `${template.color}15`, color: template.color }}
                            >
                                {stage.tool}
                            </span>
                            {idx < template.stages.length - 1 && (
                                <span className="mx-0.5 opacity-40" style={{ color: template.color }}>→</span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        );
    };

    const getModelCardBadge = (model: UntypedApiValue) => {
        const identity = `${model.id ?? ''} ${model.name ?? ''}`.toLowerCase();
        if (identity.includes('proteinmpnn') || identity.includes('ligandmpnn') || identity.includes('fampnn') || identity.includes('full-atom mpnn')) {
            return 'SEQ';
        }
        if (identity.includes('bindcraft') || identity.includes('rfantibody') || identity.includes('antibody')) {
            return 'BIND';
        }
        if (identity.includes('boltz2') || identity.includes('alphafold') || identity.includes('rosettafold') || identity.includes('protenix') || identity.includes('rf3')) {
            return 'FOLD';
        }
        if (identity.includes('boltzgen')) {
            return 'GEN';
        }
        if (identity.includes('diffdock') || identity.includes('uni-dock') || identity.includes('unidock')) {
            return 'DOCK';
        }
        if (identity.includes('nanopore') || identity.includes('ngs')) {
            return 'NGS';
        }
        if (identity.includes('oligo') || identity.includes('dna') || identity.includes('rna')) {
            return 'NA';
        }
        if (identity.includes('rfdiffusion') || identity.includes('redesign') || identity.includes('design')) {
            return 'DES';
        }
        return model.ui_icon === 'cube' ? '3D' : 'ML';
    };

    // Filter params for current mode
    const visibleParams = useMemo(() => (selectedModel?.params || []).filter((p: UntypedApiValue) => {
        if (!selectedMode) return false;
        if (selectedMode.params && selectedMode.params.length > 0) {
            return selectedMode.params.includes(p.name);
        }
        return !p.hidden;
    }) ?? [], [selectedMode, selectedModel?.params]);

    // Group visible params by ui_group
    const groupedParams = useMemo(() => {
        const groups: Record<string, UntypedApiValue[]> = {};
        visibleParams.forEach((p: UntypedApiValue) => {
            const group = p.ui_group || 'General';
            if (!groups[group]) groups[group] = [];
            groups[group].push(p);
        });
        // Sort params within each group by ui_order
        Object.values(groups).forEach(grp => {
            grp.sort((a, b) => (a.ui_order ?? 99) - (b.ui_order ?? 99));
        });
        return groups;
    }, [visibleParams]);

    // Check if ready to submit - works for both template mode and manual mode
    const isTemplateMode = wizardMode === 'templates' || wizardMode === 'experimental';
    const isReady = jobName && (
        (isTemplateMode && selectedTemplateId) ||
        (wizardMode === 'manual' && selectedModelId && selectedModeId)
    );

    const handleSubmit = () => {
        if (!isReady) return;

        // Get template data - handle both axios response wrapper and direct data
        const templateData = templateDetail;

        if (isTemplateMode && templateData) {
            // Template mode: merge preset params with user params
            const mergedParams = { ...templateData.preset_params, ...params };
            const templateModelIdOverride = mergedParams.template_model_id;
            const templateModeIdOverride = mergedParams.template_mode_id;
            delete mergedParams.template_model_id;
            delete mergedParams.template_mode_id;

            // Determine the Nextflow profile based on template type
            // Priority: rfd_mode (binder/monomer) > diffusion_method (boltzgen) > pred_method (structure prediction/validation) > skip_rfd (fampnn_predict)
            let nextflowProfile = '';
            let effectiveModelId = 'template_' + (selectedTemplateId || 'unknown');

            if (templateModelIdOverride && templateModeIdOverride) {
                effectiveModelId = templateModelIdOverride;
                nextflowProfile = templateModeIdOverride;
            } else if (mergedParams.rfd_mode) {
                // Binder or monomer design templates
                nextflowProfile = mergedParams.rfd_mode;
                effectiveModelId = 'rfdiffusion';
            } else if (mergedParams.diffusion_method === 'boltzgen') {
                // Check if this is complex PREDICTION (DNA/RNA present) vs DESIGN
                const hasNucleicAcid = ligands.some(l => l.type === 'dna' || l.type === 'rna');
                if (hasNucleicAcid) {
                    // DNA/RNA complex prediction - use Boltz-2, NOT BoltzGen
                    nextflowProfile = 'complex';
                    effectiveModelId = 'boltz2';
                } else {
                    // BoltzGen ligand-aware binder design template
                    nextflowProfile = 'boltzgen';
                    effectiveModelId = 'boltzgen';
                }
            } else if (mergedParams.pred_method) {
                // Structure prediction templates - map pred_method to model_id and mode
                const predMethodMap: Record<string, { model_id: string; mode: string }> = {
                    'boltz': { model_id: 'boltz2', mode: 'predict' },
                    'rf3': { model_id: 'rf3', mode: 'predict' },
                    'protenix': { model_id: 'protenix', mode: 'predict' },
                    'both': { model_id: 'boltz2', mode: 'predict' }, // Primary model for "both" mode
                    'all': { model_id: 'boltz2', mode: 'predict' },  // Primary model for "all" mode
                };
                const mapping = predMethodMap[mergedParams.pred_method];
                if (mapping) {
                    effectiveModelId = mapping.model_id;
                    nextflowProfile = mapping.mode;
                } else {
                    nextflowProfile = mergedParams.pred_method;
                }
            } else if (mergedParams.skip_rfd === true) {
                // DNA polymerase or similar - skip diffusion, just sequence design + prediction
                nextflowProfile = 'fampnn_predict';
                effectiveModelId = 'proteinmpnn';
            } else {
                // Fallback to template ID
                nextflowProfile = selectedTemplateId || 'binder_denovo';
            }

            console.log('DEBUG params state:', params);
            console.log('DEBUG num_parallel_jobs from params:', params.num_parallel_jobs);
            console.log('DEBUG mergedParams:', mergedParams);
            console.log('DEBUG num_parallel_jobs from mergedParams:', mergedParams.num_parallel_jobs);
            console.log('Submitting job:', { name: jobName, model_id: effectiveModelId, mode: nextflowProfile, params: mergedParams });

            // Add complex_components if ligands are selected
            const finalParams = ligands.length > 0 ? {
                ...mergedParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: mergedParams.sequence || params.sequence },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence, name: l.name }))
                ]
            } : mergedParams;

            submitMutation.mutate({
                name: jobName,
                model_id: effectiveModelId,
                mode: nextflowProfile,
                params: finalParams,
            });
        } else if (selectedModelId && selectedModeId) {
            // Manual mode

            // Filter params to only include those defined in the selected mode
            const filteredParams: Record<string, UntypedApiValue> = {};
            if (selectedMode && selectedMode.params) {
                selectedMode.params.forEach((paramName: string) => {
                    if (params[paramName] !== undefined && params[paramName] !== '') {
                        filteredParams[paramName] = params[paramName];
                    }
                });
            } else {
                // Fallback if no params defined in mode (shouldn't happen for well-defined models)
                Object.assign(filteredParams, params);
            }

            // specific check for ntp_type to ensure it's not sent if empty even if in params list
            if (filteredParams['ntp_type'] === '') {
                delete filteredParams['ntp_type'];
            }

            // Add complex_components if ligands are selected (e.g. for Complex Prediction)
            const proteinSeq = filteredParams.sequence || filteredParams.protein_sequence;

            const finalParams = ligands.length > 0 ? {
                ...filteredParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: proteinSeq },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence, name: l.name }))
                ]
            } : filteredParams;

            submitMutation.mutate({
                name: jobName,
                model_id: selectedModelId,
                mode: selectedModeId,
                params: finalParams,
            });
        } else {
            console.error('Submit failed: Template data not loaded or invalid mode', { wizardMode, templateData, selectedTemplateData });
        }
    };

    // Dedicated templates that handle their own header/navigation
    const dedicatedTemplates = ['mutagenesis', 'antibody_denovo', 'structure_prediction', 'boltz_cp_experimental', 'boltzgen_design', 'bindcraft', 'oligo_design', 'protein_local_redesign'];
    const showMainHeader = !selectedTemplateId || !dedicatedTemplates.includes(selectedTemplateId);

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            {/* Main header - hidden when dedicated templates are active */}
            {showMainHeader && (
                <header className="mb-8 flex items-center gap-4">
                    <Link
                        to="/"
                        className="inline-flex items-center rounded-lg border border-slate-700 px-3 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
                    >
                        Back
                    </Link>
                    <div>
                        <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-accent bg-clip-text text-transparent">
                            New Experiment
                        </h1>
                        <p className="text-slate-400 text-sm">Configure and launch a new job</p>
                    </div>
                </header>
            )}

            <main className="max-w-[104rem] mx-auto space-y-8">

                {/* 2. Mode Toggle: workflow cards only; the raw model-picker tab stays hidden for now. */}
                <section>
                    <div className="flex gap-2 mb-4">
                        <button
                            onClick={() => {
                                setWizardMode('templates');
                                setSelectedModelId(null);
                                setSelectedModeId(null);
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'templates'
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Workflows
                        </button>
                        <button
                            onClick={() => {
                                setWizardMode('experimental');
                                setSelectedModelId(null);
                                setSelectedModeId(null);
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'experimental'
                                ? 'border-orange-400/40 bg-orange-500/12 text-orange-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Experimental
                        </button>
                    </div>

                    {/* Templates Mode */}
                    {(wizardMode === 'templates' || wizardMode === 'experimental') && (
                        <div className="space-y-4">
                            {selectedTemplateId === 'mutagenesis' ? (
                                <MutagenesisTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    onSubmit={async (jobNamePrefix, variants, predictorConfig) => {
                                        // MUTAGENESIS BATCH: Single API call with all variants
                                        // Each variant regenerates its own MSA (no shared reference MSA)

                                        console.log('[MUTAGENESIS BATCH] Submitting', variants.length, 'variants as single batch');
                                        if (predictorConfig.msa_reference_sequence) {
                                            console.log('[MUTAGENESIS BATCH] Ignoring reference MSA (mutants regenerate MSAs)');
                                        }

                                        // Build params with mutagenesis_variants array
                                        const batchParams = {
                                            // Always regenerate MSAs for mutants (no shared reference MSA)
                                            msa_force_refresh: true,
                                            // Array of variants (each with name + sequence)
                                            mutagenesis_variants: variants.map(v => ({
                                                name: v.name,
                                                sequence: v.sequence
                                            })),
                                            // Predictor params (same for all variants)
                                            boltz_recycling_steps: predictorConfig.recycling_steps,
                                            boltz_num_samples: predictorConfig.diffusion_samples,
                                            boltz_sampling_steps: predictorConfig.sampling_steps,
                                            boltz_use_msa: predictorConfig.use_msa,
                                            boltz_use_potentials: predictorConfig.use_potentials,
                                            boltz_step_scale: predictorConfig.step_scale,
                                            pred_method: predictorConfig.predictor,
                                            run_frustrampnn: predictorConfig.run_frustrampnn,
                                            // Complex components: ligands array now includes DNA/RNA with sequence field
                                            ...(predictorConfig.ligands?.length ? {
                                                ligands: predictorConfig.ligands
                                            } : {})
                                        };

                                        try {
                                            await submitMutation.mutateAsync({
                                                name: jobNamePrefix,
                                                model_id: predictorConfig.predictor === 'rf3' ? 'rf3' : 'boltz2',
                                                mode: 'predict',
                                                params: batchParams
                                            });
                                            queryClient.invalidateQueries({ queryKey: ['jobs'] });
                                            navigate('/');
                                        } catch (error) {
                                            console.error("[MUTAGENESIS BATCH] Submission failed", error);
                                        }
                                    }}
                                />
                            ) : selectedTemplateId === 'antibody_denovo' ? (
                                <AntibodyDenovoTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'structure_prediction' || selectedTemplateId === 'boltz_cp_experimental' ? (
                                <StructurePredictionTemplate
                                    key={`${selectedTemplateId}:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    onOpenTemplateManager={openTemplateManager}
                                    initialValues={selectedTemplateId === 'boltz_cp_experimental'
                                        ? {
                                            ...(getDedicatedTemplateInitialValues('boltz_cp_experimental') || {}),
                                            ...(templateDetail?.preset_params || {}),
                                            ...(clonedValues || {}),
                                        }
                                        : clonedValues}
                                />
                            ) : selectedTemplateId === 'boltzgen_design' ? (
                                <BoltzGenTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'bindcraft' ? (
                                <BindCraftTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'oligo_design' ? (
                                <OligoDesignerTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'protein_local_redesign' ? (
                                <ProteinLocalRedesignTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : (
                                <>
                                    <p className="text-slate-300 text-base font-medium mb-4">
                                        {wizardMode === 'experimental'
                                            ? 'Choose an active alpha workflow:'
                                            : 'Choose a preset workflow for your experiment goal:'}
                                    </p>
                                    {wizardMode === 'experimental' && (
                                        <div className="rounded-xl border border-orange-400/20 bg-orange-500/8 px-4 py-3 text-sm text-orange-100">
                                            <span className="font-semibold text-orange-200">Alpha:</span> real launchers, concise cards, method detail in docs.
                                        </div>
                                    )}
                                    <div className="grid grid-cols-2 gap-3">
                                        {(wizardMode === 'experimental' ? experimentalTemplateCards : workflowTemplateCards).map((template: UntypedApiValue) =>
                                            renderTemplateCard(template)
                                        )}
                                    </div>
                                    {wizardMode === 'experimental' && experimentalTemplateCards.length === 0 && (
                                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-5 text-sm text-slate-400">
                                            No experimental workflows are currently exposed in this branch.
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    )}

                    {/* Manual Mode: Select Model */}
                    {wizardMode === 'manual' && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Select Model</label>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                {models.map((model: UntypedApiValue) => (
                                    <div
                                        key={model.id}
                                        onClick={() => {
                                            setSelectedModelId(model.id);
                                            setSelectedModeId(null); // Reset mode
                                        }}
                                        className={`cursor-pointer p-4 rounded-xl border transition-all relative overflow-hidden group ${selectedModelId === model.id
                                            ? 'bg-slate-800 border-blue-500 shadow-lg shadow-blue-500/10'
                                            : 'bg-slate-800/30 border-slate-700 hover:border-slate-600 hover:bg-slate-800/50'
                                            }`}
                                    >
                                        <div className="flex justify-between items-start mb-2">
                                            <div
                                                className="w-10 h-10 rounded-lg flex items-center justify-center text-lg shadow-inner"
                                                style={{ backgroundColor: `${model.ui_color}20`, color: model.ui_color }}
                                            >
                                                {getModelCardBadge(model)}
                                            </div>
                                            {model.experimental && (
                                                <span className="text-[10px] uppercase font-bold text-orange-400 bg-orange-400/10 px-2 py-0.5 rounded-full">
                                                    Experimental
                                                </span>
                                            )}
                                        </div>
                                        <h3 className="font-semibold text-slate-200 mb-1">{model.name}</h3>
                                        <p className="text-xs text-slate-500 line-clamp-2">{getCompactModelDescription(model)}</p>
                                        {getModelDocumentationTopics(model).length > 0 && (
                                            <div className="mt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                                                Docs available
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </section>

                {/* 3. Template Configuration - Only show if template selected and NOT a dedicated template */}
                {selectedTemplateId && !isDedicatedLauncherTemplate(selectedTemplateId) && templateDetail && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6">
                            <h2 className="text-lg font-semibold text-slate-200 mb-4 flex items-center gap-2">
                                <span className={`w-1.5 h-6 rounded-full ${templateDetail.experimental ? 'bg-orange-400' : 'bg-green-500'}`} />
                                {templateDetail.name} - Configuration
                                {templateDetail.experimental && (
                                    <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                                        Experimental Alpha
                                    </span>
                                )}
                            </h2>

                            {templateDetail.experimental && (
                                <div className="mb-6 space-y-3 rounded-xl border border-orange-400/20 bg-orange-500/8 p-4">
                                    <div className="flex flex-wrap items-center gap-3 text-sm">
                                        <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                                            Active alpha
                                        </span>
                                        <span className="text-slate-300">{getExperimentalStatusSummary(templateDetail)}</span>
                                    </div>
                                    <ModelDocumentationLinks
                                        topics={getTemplateDocumentationTopics(templateDetail)}
                                        summary="Method detail lives in maintained docs, not inline launcher prose."
                                        compact
                                    />
                                </div>
                            )}

                            {/* Stage Explanation */}
                            <div className="mb-6 p-4 bg-slate-900/50 rounded-lg">
                                <p className="text-sm text-slate-400 mb-3">This template runs the following stages:</p>
                                <div className="flex flex-wrap items-center gap-2">
                                    {templateDetail.stages.map((stage: UntypedApiValue, idx: number) => (
                                        <div key={idx} className="flex items-center">
                                            <div className="bg-slate-700 px-3 py-1.5 rounded-lg">
                                                <span className="text-sm font-medium text-slate-200">{idx + 1}. {stage.name}</span>
                                                <span className="text-xs text-slate-400 ml-2">({stage.tool})</span>
                                            </div>
                                            {idx < templateDetail.stages.length - 1 && (
                                                <span className="text-blue-400 mx-2 text-lg">→</span>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* User Parameters */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                {templateDetail.user_params.map((param: UntypedApiValue) => {
                                    // Conditional Rendering Logic
                                    if (param.condition) {
                                        const controllingParam = templateDetail.user_params.find((p: UntypedApiValue) => p.name === param.condition.param);
                                        // Use params value if set, otherwise fall back to the controlling param's default
                                        const controllingValue = params[param.condition.param] !== undefined
                                            ? params[param.condition.param]
                                            : controllingParam?.default;

                                        // Hide this field if the controlling value doesn't match allowed values
                                        if (!param.condition.values.includes(controllingValue)) {
                                            return null;
                                        }
                                    }

                                    return (
                                        <ParamField
                                            key={param.name}
                                            param={param}
                                            params={params}
                                            updateParam={updateParam}
                                            setShowFileBrowser={setShowFileBrowser}
                                            setActiveSequenceField={setActiveSequenceField}
                                            setShowSequenceManager={setShowSequenceManager}
                                            setSequenceToSave={setSequenceToSave}
                                            ligandPresets={ligandPresets}
                                        />
                                    );
                                })}
                            </div>

                            {/* Ligand Selector - Show for structure prediction templates */}
                            {(templateDetail?.preset_params?.pred_method ||
                                selectedTemplateId?.includes('structure') ||
                                selectedTemplateId?.includes('predict')) && (
                                    <LigandSelector
                                        ligands={ligands}
                                        setLigands={setLigands}
                                        showCustomSmiles={true}
                                    />
                                )}
                        </div>
                    </section>
                )}

                {/* 4. Configure - Only show if model selected (Manual Mode) */}
                {wizardMode === 'manual' && selectedModel && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6">
                            <h2 className="text-lg font-semibold text-slate-200 mb-6 flex items-center gap-2">
                                <span className="w-1.5 h-6 bg-blue-500 rounded-full" />
                                Configuration
                            </h2>

                            <ModelDocumentationLinks
                                topics={getModelDocumentationTopics(selectedModel)}
                                summary="Method background is linked out; this panel stays focused on launch controls."
                                compact
                                className="mb-6"
                            />

                            <div className="space-y-6">
                                {/* Mode Selection */}
                                <div>
                                    <label className="block text-sm font-medium text-slate-400 mb-2">
                                        Workflow Mode
                                    </label>
                                    <select
                                        value={selectedModeId || ''}
                                        onChange={(e) => setSelectedModeId(e.target.value)}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                                    >
                                        <option value="" disabled>Select a mode...</option>
                                        {(selectedModel.modes || [])
                                            .filter((mode: UntypedApiValue) => mode.id !== 'dna_complex') // Deprecated: use Boltz-2 Complex Prediction instead
                                            .map((mode: UntypedApiValue) => (
                                                <option key={mode.id} value={mode.id}>
                                                    {mode.name}
                                                </option>
                                            ))}
                                    </select>
                                    {selectedMode && (
                                        <p className="mt-2 text-sm text-slate-500">{compactUiCopy(selectedMode.description, 120)}</p>
                                    )}
                                </div>

                                {/* Dynamic Parameters - Grouped */}
                                {selectedMode && Object.keys(groupedParams).length > 0 && (
                                    <div className="space-y-6 pt-6 border-t border-slate-700/50">
                                        {/* Render groups in preferred order */}
                                        {['Inputs', 'Docking Settings', 'General'].filter(g => groupedParams[g]).map(groupName => (
                                            <div key={groupName}>
                                                {groupName !== 'General' && (
                                                    <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                        <span className={`w-1 h-4 rounded-full ${groupName === 'Inputs' ? 'bg-emerald-500' : 'bg-blue-500'}`} />
                                                        {groupName}
                                                    </h3>
                                                )}
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    {groupedParams[groupName].map((param: UntypedApiValue) => (
                                                        <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} setSequenceToSave={setSequenceToSave} ligandPresets={ligandPresets} />
                                                    ))}
                                                </div>
                                            </div>
                                        ))}

                                        {/* Advanced section - collapsible */}
                                        {groupedParams['Advanced'] && (
                                            <div className="border border-slate-700/50 rounded-lg overflow-hidden">
                                                <button
                                                    type="button"
                                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                                    className="w-full flex items-center justify-between px-4 py-3 bg-slate-800/50 hover:bg-slate-800/70 transition-colors"
                                                >
                                                    <span className="text-sm font-medium text-slate-400 flex items-center gap-2">
                                                        <span className="w-1 h-4 rounded-full bg-slate-500" />
                                                        Advanced Settings
                                                    </span>
                                                    <span className="text-slate-500 text-xs">{showAdvanced ? '▲' : '▼'}</span>
                                                </button>
                                                {showAdvanced && (
                                                    <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                                                        {groupedParams['Advanced'].map((param: UntypedApiValue) => (
                                                            <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} setSequenceToSave={setSequenceToSave} ligandPresets={ligandPresets} />
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )
                                }

                                {/* Ligand Selector for Complex Prediction mode in manual/advanced mode */}
                                {selectedModeId === 'complex' && (
                                    <div className="pt-6 border-t border-slate-700/50">
                                        <LigandSelector
                                            ligands={ligands}
                                            setLigands={setLigands}
                                            showCustomSmiles={true}
                                        />
                                    </div>
                                )}
                            </div>
                        </div>
                    </section>
                )}

                {/* Submit Button - Hide if Mutagenesis, Antibody De Novo, or Structure Prediction Template is active (they have their own) */}
                {!isDedicatedLauncherTemplate(selectedTemplateId) && (
                    <div className="flex justify-end gap-3 pt-4 pb-12">
                        {/* Save as Template Button */}
                        {(isTemplateMode || (wizardMode === 'manual' && selectedModelId)) && (
                            <button
                                onClick={() => openTemplateManager({
                                    currentParams: params,
                                    currentModelId: selectedModelId || undefined,
                                    currentMode: selectedModeId || undefined,
                                    baseTemplateId: selectedTemplateId || undefined,
                                })}
                                className="inline-flex min-w-[12rem] items-center justify-center rounded-xl border border-slate-600 bg-slate-900/60 px-6 py-3.5 text-sm font-semibold text-slate-100 transition-all hover:bg-slate-800"
                            >
                                Template Manager
                            </button>
                        )}
                        <button
                            onClick={handleSubmit}
                            disabled={!isReady || submitMutation.isPending}
                            className={`inline-flex min-w-[12rem] items-center justify-center rounded-xl border px-6 py-3.5 text-sm font-semibold transition-all ${isReady
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-200 hover:bg-blue-500/20'
                                : 'border-slate-700 bg-slate-900/60 text-slate-500 cursor-not-allowed'
                                }`}
                        >
                            {submitMutation.isPending ? 'Launching Job...' : 'Launch Experiment'}
                        </button>
                    </div>
                )}
            </main>

            {/* Loading Overlay for Batch Submission */}
            {submitMutation.isPending && selectedTemplateId === 'mutagenesis' && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] flex items-center justify-center">
                    <div className="bg-slate-900 border border-slate-700 p-8 rounded-2xl shadow-2xl flex flex-col items-center">
                        <div className="w-16 h-16 border-4 border-accent/30 border-t-accent rounded-full animate-spin mb-4" />
                        <h3 className="text-xl font-bold text-white mb-2">Submitting Batch Jobs...</h3>
                        <p className="text-slate-400">Please wait while we launch your variant library.</p>
                    </div>
                </div>
            )}

            {/* File Browser Modal */}
            {showFileBrowser && (
                <FileBrowser
                    onSelect={(path) => {
                        updateParam(showFileBrowser, path);
                        setShowFileBrowser(null);
                    }}
                    onCancel={() => setShowFileBrowser(null)}
                />
            )}

            {/* Sequence Manager Modal */}
            <SequenceManagerModal
                isOpen={showSequenceManager}
                onClose={() => {
                    setShowSequenceManager(false);
                    setSequenceToSave(null);
                }}
                onSelect={(seq) => {
                    // Load selected sequence into the current sequence param
                    updateParam(activeSequenceField, seq.sequence);
                    if (seq.name && activeSequenceField === 'sequence') updateParam('sequence_name', seq.name);
                }}
                initialSequence={sequenceToSave?.sequence || ''}
                initialName={sequenceToSave?.name || ''}
            />

            {/* Template Manager Modal */}
            <TemplateManagerModal
                isOpen={showTemplateManager}
                onClose={() => {
                    setShowTemplateManager(false);
                    setTemplateManagerContext({});
                }}
                onSelect={routeUserTemplate}
                currentParams={templateManagerContext.currentParams}
                currentModelId={templateManagerContext.currentModelId}
                currentMode={templateManagerContext.currentMode}
                baseTemplateId={templateManagerContext.baseTemplateId}
            />
        </div>
    );
}
