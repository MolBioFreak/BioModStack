

import { useState, useEffect, useRef, useMemo } from 'react';
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
                    {files?.data.entries.map((entry: any) => (
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

// Reusable param field component for grouped rendering
function ParamField({
    param,
    params,
    updateParam,
    setShowFileBrowser,
    setActiveSequenceField,
    setShowSequenceManager,
    ligandPresets
}: {
    param: any;
    params: Record<string, any>;
    updateParam: (key: string, value: any) => void;
    setShowFileBrowser: (name: string | null) => void;
    setActiveSequenceField: (name: string) => void;
    setShowSequenceManager: (show: boolean) => void;
    ligandPresets: any[];
}) {
    const isWide = param.type === 'file' || param.type === 'directory' || param.preset_type === 'pdb' || param.preset_type === 'ligand';

    return (
        <div className={isWide ? 'col-span-full' : ''}>
            <label className="block text-sm font-medium text-slate-400 mb-1.5">
                {param.description}
                {param.required && <span className="text-red-400 ml-1">*</span>}
            </label>

            {param.type === 'boolean' ? (
                <label className="flex items-center gap-3 cursor-pointer">
                    <div className={`w-10 h-6 rounded-full p-1 transition-colors ${params[param.name] ? 'bg-blue-500' : 'bg-slate-700'}`}>
                        <div className={`w-4 h-4 bg-white rounded-full shadow-sm transition-transform ${params[param.name] ? 'translate-x-4' : ''}`} />
                    </div>
                    <input
                        type="checkbox"
                        className="hidden"
                        checked={params[param.name] || false}
                        onChange={(e) => updateParam(param.name, e.target.checked)}
                    />
                    <span className="text-sm text-slate-300">Enabled</span>
                </label>
            ) : param.enum ? (
                <select
                    value={params[param.name] ?? param.default ?? ''}
                    onChange={(e) => updateParam(param.name, e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                >
                    {param.enum.map((opt: string) => (
                        <option key={opt} value={opt}>{opt}</option>
                    ))}
                </select>
            ) : param.preset_type === 'pdb' ? (
                <StructureInput
                    value={params[param.name] || ''}
                    onChange={(v) => updateParam(param.name, v)}
                    onBrowse={() => setShowFileBrowser(param.name)}
                    targetChain={params['target_chain'] || ''}
                    onTargetChainChange={(c) => updateParam('target_chain', c)}
                    enableMultiSelect={false}
                    enableDirectory={false}
                />
            ) : param.preset_type === 'ligand' ? (
                <div className="space-y-2">
                    <select
                        value=""
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">Select preset ligand...</option>
                        {ligandPresets.map((preset: any) => (
                            <option key={preset.id} value={preset.smiles}>
                                {preset.name}
                            </option>
                        ))}
                    </select>
                    <input
                        type="text"
                        value={params[param.name] || ''}
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder={param.ui_placeholder || "Or enter SMILES string..."}
                    />
                </div>
            ) : param.preset_type === 'sequence' ? (
                <div className="space-y-2">
                    <button
                        type="button"
                        onClick={() => {
                            setActiveSequenceField(param.name);
                            setShowSequenceManager(true);
                        }}
                        className="rounded-lg border border-emerald-600/30 bg-emerald-600/12 px-3 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-600/20"
                    >
                        Sequence Library
                    </button>
                    <textarea
                        value={params[param.name] || ''}
                        onChange={(e) => updateParam(param.name, e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                        rows={4}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder="Enter amino acid sequence..."
                    />
                </div>
            ) : param.type === 'file' || param.type === 'directory' ? (
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={params[param.name] || ''}
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        placeholder={param.type === 'file' ? '/path/to/file' : '/path/to/directory'}
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
                    type={param.type === 'integer' || param.type === 'number' ? 'number' : 'text'}
                    value={params[param.name] ?? param.default ?? ''}
                    onChange={(e) => updateParam(param.name, param.type === 'integer' ? parseInt(e.target.value) : param.type === 'number' ? parseFloat(e.target.value) : e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    placeholder={param.ui_placeholder || ''}
                    min={param.minimum}
                    max={param.maximum}
                />
            )}
        </div>
    );
}

export function JobSubmission() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const [wizardMode, setWizardMode] = useState<'templates' | 'experimental' | 'manual'>('templates');

    // Read template from URL, allows page refresh and bookmarking
    const urlTemplate = searchParams.get('template');
    const [selectedTemplateId, setSelectedTemplateIdInternal] = useState<string | null>(urlTemplate);

    // Wrapper to sync state with URL
    const setSelectedTemplateId = (id: string | null) => {
        setSelectedTemplateIdInternal(id);
        if (id) {
            setSearchParams({ template: id }, { replace: true });
        } else {
            setSearchParams({}, { replace: true });
        }
    };
    const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
    const [selectedModeId, setSelectedModeId] = useState<string | null>(null);
    const [jobName, setJobName] = useState('');
    const [params, setParams] = useState<Record<string, any>>({});
    const [showFileBrowser, setShowFileBrowser] = useState<string | null>(null);
    const [showSequenceManager, setShowSequenceManager] = useState(false);
    const [showTemplateManager, setShowTemplateManager] = useState(false);
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name?: string } | null>(null);
    const [activeSequenceField, setActiveSequenceField] = useState<string>('sequence');
    const [ligands, setLigands] = useState<LigandEntry[]>([]);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [clonedValues, setClonedValues] = useState<Record<string, any> | undefined>(undefined);

    // Dedicated templates should not retain stale clone params once user navigates away.
    const handleDedicatedTemplateBack = () => {
        setSelectedTemplateId(null);
        setClonedValues(undefined);
    };

    const handleTemplateCardSelect = (templateId: string) => {
        setClonedValues(undefined);
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
    }, []);

    const { data: modelsData } = useQuery({
        queryKey: ['models'],
        queryFn: () => fetchModels(),
    });

    const { data: templatesData } = useQuery({
        queryKey: ['templates'],
        queryFn: () => fetchTemplates(),
    });

    // Hardcoded templates that use dedicated components instead of API-driven config
    const hardcodedTemplates = ['mutagenesis', 'antibody_denovo', 'structure_prediction', 'boltzgen_design', 'bindcraft', 'oligo_design', 'protein_local_redesign'];
    const dedicatedTemplateByModelId: Record<string, string> = {
        template_antibody_denovo: 'antibody_denovo',
        boltzgen: 'boltzgen_design',
        bindcraft: 'bindcraft',
        protein_local_redesign: 'protein_local_redesign',
        boltz_cp_experimental: 'boltz_cp_experimental',
    };
    const hardcodedWorkflowTemplates = [
        {
            id: 'mutagenesis',
            name: 'Mutagenesis Library',
            description: 'Generate amino acid variants and predict their structures using Boltz-2 or RoseTTAFold3.',
            icon: 'dna',
            color: '#8B5CF6',
            stages: [{ tool: 'Library Gen' }, { tool: 'Structure Prediction' }],
        },
        {
            id: 'structure_prediction',
            name: 'Structure Prediction',
            description: 'Predict 3D protein, RNA, DNA, or complex structures from sequences using Boltz-2, RoseTTAFold3, or Protenix.',
            icon: 'microscope',
            color: '#F59E0B',
            stages: [{ tool: 'Boltz-2 / RF3 / Protenix' }],
        },
        {
            id: 'antibody_denovo',
            name: 'De Novo Nanobody Toolkit',
            description: 'Launch RFantibody, BoltzGen nanobody, or seeded PPIFlow generation from one toolkit, then reopen selected outputs in Antibody Refinement for modular redesign, validation, and downstream review.',
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
            description: 'Design proteins that bind small molecules, NTPs, or other ligands. Uses BoltzGen for all-atom structure generation with optional docking validation.',
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
            description: 'Design DNA, RNA, proteins, and mixed nucleoprotein assemblies using RFDpoly diffusion with Boltz-2 validation.',
            icon: 'dna',
            color: '#6366F1',
            stages: [{ tool: 'RFDpoly' }, { tool: 'Boltz-2' }, { tool: 'Filtering' }],
        },
    ];
    const hardcodedExperimentalTemplates = [
        {
            id: 'protein_local_redesign',
            name: 'Protein Local Redesign',
            description: 'Use RFdiffusion3 to locally remodel a selected region of an existing structure, redesign sequence with FA-MPNN or ProteinMPNN, and optionally validate with Boltz-2.',
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
    ];
    const visibleApiTemplates = useMemo(() => {
        const templates = templatesData?.data ?? [];
        return templates.filter((t: any) =>
            !['boltzgen_ligand', 'binder_design', 'structure_validation', 'structure_prediction'].includes(t.id) &&
            (t.id !== 'dna_polymerase' || (window as any).__DEBUG_MODE__)
        );
    }, [templatesData]);
    const workflowTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: any) => !t.experimental), ...hardcodedWorkflowTemplates],
        [visibleApiTemplates]
    );
    const experimentalTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: any) => t.experimental), ...hardcodedExperimentalTemplates],
        [visibleApiTemplates]
    );

    const routeUserTemplate = (template: any) => {
        const dedicatedTemplateId =
            (template.base_template_id && hardcodedTemplates.includes(template.base_template_id) && template.base_template_id) ||
            (template.model_id ? dedicatedTemplateByModelId[template.model_id] : null);

        if (dedicatedTemplateId) {
            setWizardMode(dedicatedTemplateId === 'boltz_cp_experimental' ? 'experimental' : 'templates');
            setSelectedTemplateId(dedicatedTemplateId);
            setClonedValues({ ...template.params, name: template.name, template_model_id: template.model_id || template.params?.template_model_id });
            setJobName(template.params?.job_name || template.name || '');
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
        enabled: !!selectedTemplateId && !hardcodedTemplates.includes(selectedTemplateId),
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
        onError: (error: any) => {
            console.error('Job submission failed:', error);
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object'
                ? JSON.stringify(detail, null, 2)
                : (detail || error.message || error);
            window.alert('Job Submission Failed:\n' + message);
        }
    });

    const models = (modelsData?.data ?? []).filter((model: any) => !['protein_cad_experimental', 'protein_local_redesign', 'caliby_experimental', 'protein_hunter_experimental', 'boltz_cp_experimental'].includes(model.id));
    const selectedModel = models.find((m: any) => m.id === selectedModelId);
    const selectedMode = selectedModel?.modes.find((m: any) => m.id === selectedModeId);

    // Initialize params when model/mode changes (manual mode)
    useEffect(() => {
        if (selectedModel) {
            const defaults: Record<string, any> = {};
            (selectedModel.params || []).forEach((p: any) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            setParams(defaults);
        }
    }, [selectedModelId]);

    // Initialize params when template changes (template mode)
    useEffect(() => {
        if (templateDetail?.user_params) {
            const defaults: Record<string, any> = {};
            templateDetail.user_params.forEach((p: any) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            setParams(defaults);
        }
    }, [templateDetail]);

    useEffect(() => {
        if (!selectedTemplateId || hardcodedTemplates.includes(selectedTemplateId)) {
            return;
        }
        const matchedTemplate = visibleApiTemplates.find((template: any) => template.id === selectedTemplateId);
        if (matchedTemplate) {
            setWizardMode(matchedTemplate.experimental ? 'experimental' : 'templates');
        }
    }, [selectedTemplateId, visibleApiTemplates]);

    // Handle param change
    const updateParam = (key: string, value: any) => {
        setParams(prev => ({ ...prev, [key]: value }));
    };

    const getTemplateIconLabel = (template: any) => {
        if (template.id === 'protein_cad_experimental') return 'PC';
        if (template.id === 'caliby_experimental') return 'CB';
        if (template.id === 'protein_hunter_experimental') return 'PH';
        if (template.id === 'boltz_cp_experimental') return 'CP';
        return template.icon === 'target' ? 'TG'
            : template.icon === 'flask' ? 'RF'
                : template.icon === 'dna' ? 'MU'
                    : template.icon === 'microscope' ? 'SP'
                        : template.icon === 'pill' ? 'BG'
                            : template.icon === 'binder' ? 'BC'
                                : template.icon === 'cube' ? 'PL'
                                    : 'OL';
    };

    const renderTemplateCard = (template: any) => {
        const isSelected = selectedTemplateId === template.id;
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
                <p className="mb-2 text-xs opacity-70 line-clamp-2">{template.description}</p>
                <div className="flex items-center gap-0.5 flex-wrap text-[10px]">
                    {template.stages.map((stage: any, idx: number) => (
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

    const getModelCardBadge = (model: any) => {
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
    const visibleParams = (selectedModel?.params || []).filter((p: any) => {
        if (!selectedMode) return false;
        if (selectedMode.params && selectedMode.params.length > 0) {
            return selectedMode.params.includes(p.name);
        }
        return !p.hidden;
    }) ?? [];

    // Group visible params by ui_group
    const groupedParams = useMemo(() => {
        const groups: Record<string, any[]> = {};
        visibleParams.forEach((p: any) => {
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
            const filteredParams: Record<string, any> = {};
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

                {/* 2. Mode Toggle: Templates vs Manual */}
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
                        <button
                            onClick={() => {
                                setWizardMode('manual');
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'manual'
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Advanced (Models)
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
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={selectedTemplateId === 'boltz_cp_experimental'
                                        ? { ...(templateDetail?.preset_params || {}), ...(clonedValues || {}) }
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
                                            ? 'Experimental workflows are isolated here on purpose. These are real integrations, but they are still alpha-grade systems intended for iterative frontier work.'
                                            : 'Choose a preset workflow for your experiment goal:'}
                                    </p>
                                    {wizardMode === 'experimental' && (
                                        <div className="rounded-xl border border-orange-400/20 bg-orange-500/8 px-4 py-3 text-sm text-orange-100">
                                            <span className="font-semibold text-orange-200">Frontier mode:</span> this tab is reserved for workflows that are wired into BMS end to end, but are still evolving in interface, validation, and downstream review semantics.
                                        </div>
                                    )}
                                    <div className="grid grid-cols-2 gap-3">
                                        {(wizardMode === 'experimental' ? experimentalTemplateCards : workflowTemplateCards).map((template: any) =>
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
                                {models.map((model: any) => (
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
                                        <p className="text-xs text-slate-500 line-clamp-2">{model.description}</p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </section>

                {/* 3. Template Configuration - Only show if template selected and NOT a dedicated template */}
                {selectedTemplateId && selectedTemplateId !== 'mutagenesis' && selectedTemplateId !== 'antibody_denovo' && selectedTemplateId !== 'structure_prediction' && templateDetail && (
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
                                <div className="mb-6 rounded-xl border border-orange-400/20 bg-orange-500/8 p-4">
                                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-orange-300">Experimental Workflow</p>
                                    <p className="mt-2 text-sm text-slate-200">
                                        <span className="font-semibold text-orange-200">Goal:</span>{' '}
                                        {templateDetail.goal || templateDetail.description}
                                    </p>
                                    <p className="mt-2 text-sm text-slate-300">
                                        <span className="font-semibold text-orange-200">Current status:</span>{' '}
                                        {templateDetail.status || 'Active alpha integration with backend wiring in place and downstream iteration still in progress.'}
                                    </p>
                                </div>
                            )}

                            {/* Stage Explanation */}
                            <div className="mb-6 p-4 bg-slate-900/50 rounded-lg">
                                <p className="text-sm text-slate-400 mb-3">This template runs the following stages:</p>
                                <div className="flex flex-wrap items-center gap-2">
                                    {templateDetail.stages.map((stage: any, idx: number) => (
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
                                {templateDetail.user_params.map((param: any) => {
                                    // Conditional Rendering Logic
                                    if (param.condition) {
                                        const controllingParam = templateDetail.user_params.find((p: any) => p.name === param.condition.param);
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
                                        <div key={param.name} className={param.type === 'file' || param.type === 'directory' ? 'col-span-full' : ''}>
                                            <label className="block text-sm font-medium text-slate-400 mb-1">
                                                {param.label}
                                                {param.required && <span className="text-red-400 ml-1">*</span>}
                                            </label>
                                            <p className="text-xs text-slate-500 mb-2">{param.description}</p>

                                            {param.type === 'enum' ? (
                                                <select
                                                    value={params[param.name] || param.default || ''}
                                                    onChange={(e) => updateParam(param.name, e.target.value)}
                                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                                >
                                                    {param.enum.map((opt: string) => (
                                                        <option key={opt} value={opt}>
                                                            {param.enum_labels?.[opt] || opt}
                                                        </option>
                                                    ))}
                                                </select>
                                            ) : (param.type === 'file' || param.type === 'directory') ? (
                                                <PresetSelector
                                                    presetType="pdb"
                                                    value={params[param.name] || ''}
                                                    onChange={(val) => updateParam(param.name, val)}
                                                    onBrowse={() => setShowFileBrowser(param.name)}
                                                    placeholder="Select a preset PDB or directory..."
                                                    enableMultiSelect={true}
                                                    enableDirectory={true}
                                                />
                                            ) : param.name.includes('contig') ? (
                                                <PresetSelector
                                                    presetType="contig"
                                                    value={params[param.name] || param.default || ''}
                                                    onChange={(val) => updateParam(param.name, val)}
                                                    onBrowse={() => { }}
                                                    placeholder="Select a contig preset..."
                                                />
                                            ) : (param.type === 'text' || param.name === 'sequence') && param.name !== 'sequence_name' ? (
                                                /* Sequence Input with Action Buttons */
                                                <div className="space-y-2">
                                                    <div className="flex gap-2 items-center flex-wrap">
                                                        {/* Manage Library Button */}
                                                        <button
                                                            type="button"
                                                            onClick={() => {
                                                                setActiveSequenceField(param.name);
                                                                setShowSequenceManager(true);
                                                            }}
                                                            className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-700"
                                                        >
                                                            Sequence Library
                                                        </button>
                                                        {/* Character count */}
                                                        <span className="text-xs text-slate-500 bg-slate-800/50 px-2 py-1 rounded">
                                                            {(params[param.name] || '').length} aa
                                                        </span>
                                                        {/* Save Sequence Button - only show if has content */}
                                                        {params[param.name] && params[param.name].length > 0 && (
                                                            <button
                                                                type="button"
                                                                onClick={() => {
                                                                    setSequenceToSave({ sequence: params[param.name], name: params['sequence_name'] || '' });
                                                                    setActiveSequenceField(param.name);
                                                                    setShowSequenceManager(true);
                                                                }}
                                                                className="rounded-lg border border-emerald-600/30 bg-emerald-600/12 px-3 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-600/20"
                                                            >
                                                                Save to Library
                                                            </button>
                                                        )}
                                                        {/* Clear Button */}
                                                        {params[param.name] && params[param.name].length > 0 && (
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
                                                        value={params[param.name] || ''}
                                                        onChange={(e) => updateParam(param.name, e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                                                        placeholder={param.placeholder || 'Enter amino acid sequence (A-Z)...\n\nTip: Click "Sequence Library" to load a saved sequence or save your current one.'}
                                                        rows={6}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                                                    />
                                                </div>
                                            ) : (
                                                <input
                                                    type={param.type === 'integer' || param.type === 'number' ? 'number' : 'text'}
                                                    value={params[param.name] ?? param.default ?? ''}
                                                    onChange={(e) => updateParam(param.name, param.type === 'integer' ? parseInt(e.target.value) : param.type === 'number' ? parseFloat(e.target.value) : e.target.value)}
                                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                                />
                                            )}
                                        </div>
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
                                            .filter((mode: any) => mode.id !== 'dna_complex') // Deprecated: use Boltz-2 Complex Prediction instead
                                            .map((mode: any) => (
                                                <option key={mode.id} value={mode.id}>
                                                    {mode.name}
                                                </option>
                                            ))}
                                    </select>
                                    {selectedMode && (
                                        <p className="mt-2 text-sm text-slate-500">{selectedMode.description}</p>
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
                                                    {groupedParams[groupName].map((param: any) => (
                                                        <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} ligandPresets={ligandPresets} />
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
                                                        {groupedParams['Advanced'].map((param: any) => (
                                                            <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} ligandPresets={ligandPresets} />
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
                {selectedTemplateId !== 'mutagenesis' && selectedTemplateId !== 'antibody_denovo' && selectedTemplateId !== 'structure_prediction' && (
                    <div className="flex justify-end gap-3 pt-4 pb-12">
                        {/* Save as Template Button */}
                        {(isTemplateMode || (wizardMode === 'manual' && selectedModelId)) && (
                            <button
                                onClick={() => setShowTemplateManager(true)}
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
                onClose={() => setShowTemplateManager(false)}
                onSelect={routeUserTemplate}
                currentParams={params}
                currentModelId={selectedModelId || undefined}
                currentMode={selectedModeId || undefined}
                baseTemplateId={selectedTemplateId || undefined}
            />
        </div>
    );
}
