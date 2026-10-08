
import { useState, useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchModels, fetchFiles, submitJob, uploadFile, fetchTemplates, fetchTemplateById } from '../lib/api';
import { SequenceManagerModal } from './SequenceManagerModal';
import { TemplateManagerModal } from './TemplateManagerModal';
import { MutagenesisTemplate } from './MutagenesisTemplate';
import { PresetSelector } from './PresetSelector';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { StructureInput } from './StructureInput';

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
                            className="text-xs bg-blue-600 hover:bg-blue-500 text-white px-3 py-1.5 rounded-md transition-colors"
                        >
                            {uploadMutation.isPending ? 'Uploading...' : '☁️ Upload'}
                        </button>
                        <button onClick={onCancel} className="text-slate-400 hover:text-white">✕</button>
                    </div>
                </div>

                <div className="p-2 border-b border-slate-700 bg-slate-800/30 flex items-center gap-2">
                    <button
                        onClick={handleUp}
                        className="px-2 py-1 bg-slate-700 rounded text-sm text-slate-300 hover:bg-slate-600 disabled:opacity-50"
                        disabled={path === '/'}
                    >
                        ↑ Up
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
                            <span className="text-lg">{entry.is_directory ? '📁' : '📄'}</span>
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

export function JobSubmission() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [wizardMode, setWizardMode] = useState<'templates' | 'manual'>('templates');
    const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
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

    const { data: modelsData } = useQuery({
        queryKey: ['models'],
        queryFn: () => fetchModels(),
    });

    const { data: templatesData } = useQuery({
        queryKey: ['templates'],
        queryFn: () => fetchTemplates(),
    });

    const { data: selectedTemplateData } = useQuery({
        queryKey: ['template', selectedTemplateId],
        queryFn: () => selectedTemplateId ? fetchTemplateById(selectedTemplateId) : null,
        enabled: !!selectedTemplateId,
    });

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

    const models = modelsData?.data ?? [];
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
        if (selectedTemplateData?.data?.user_params) {
            const defaults: Record<string, any> = {};
            selectedTemplateData.data.user_params.forEach((p: any) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            setParams(defaults);
        }
    }, [selectedTemplateData]);

    // Handle param change
    const updateParam = (key: string, value: any) => {
        setParams(prev => ({ ...prev, [key]: value }));
    };

    // Filter params for current mode
    const visibleParams = (selectedModel?.params || []).filter((p: any) => {
        if (!selectedMode) return false;
        if (selectedMode.params && selectedMode.params.length > 0) {
            return selectedMode.params.includes(p.name);
        }
        return !p.hidden;
    }) ?? [];

    // Check if ready to submit - works for both template mode and manual mode
    const isReady = jobName && (
        (wizardMode === 'templates' && selectedTemplateId) ||
        (wizardMode === 'manual' && selectedModelId && selectedModeId)
    );

    const handleSubmit = () => {
        if (!isReady) return;

        // Get template data - handle both axios response wrapper and direct data
        const templateData = selectedTemplateData?.data?.data ?? selectedTemplateData?.data;

        if (wizardMode === 'templates' && templateData) {
            // Template mode: merge preset params with user params
            const mergedParams = { ...templateData.preset_params, ...params };

            // Determine the Nextflow profile based on template type
            // Priority: rfd_mode (binder/monomer) > diffusion_method (boltzgen) > pred_method (structure prediction/validation) > skip_rfd (fampnn_predict)
            let nextflowProfile = '';
            let effectiveModelId = 'template_' + (selectedTemplateId || 'unknown');

            if (mergedParams.rfd_mode) {
                // Binder or monomer design templates
                nextflowProfile = mergedParams.rfd_mode;
                effectiveModelId = 'rfdiffusion';
            } else if (mergedParams.diffusion_method === 'boltzgen') {
                // BoltzGen ligand-aware template
                nextflowProfile = 'boltzgen';
                effectiveModelId = 'boltzgen';
            } else if (mergedParams.pred_method) {
                // Structure prediction templates - map pred_method to model_id and mode
                const predMethodMap: Record<string, { model_id: string; mode: string }> = {
                    'boltz': { model_id: 'boltz2', mode: 'predict' },
                    'rf3': { model_id: 'rf3', mode: 'predict' },
                    'both': { model_id: 'boltz2', mode: 'predict' }, // Primary model for "both" mode
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

            console.log('Submitting job:', { name: jobName, model_id: effectiveModelId, mode: nextflowProfile, params: mergedParams });

            // Add complex_components if ligands are selected
            const finalParams = ligands.length > 0 ? {
                ...mergedParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: mergedParams.sequence || params.sequence },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles }))
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
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles }))
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

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            <header className="mb-8 flex items-center gap-4">
                <Link
                    to="/"
                    className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
                >
                    ← Back
                </Link>
                <div>
                    <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
                        New Experiment
                    </h1>
                    <p className="text-slate-400 text-sm">Configure and launch a new job</p>
                </div>
            </header>

            <main className="max-w-4xl mx-auto space-y-8">
                {/* 1. Job Name */}
                <section>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Job Name</label>
                    <input
                        type="text"
                        value={jobName}
                        onChange={(e) => setJobName(e.target.value)}
                        placeholder="e.g., binder_design_test_01"
                        className="w-full bg-slate-800/50 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                    />
                </section>

                {/* 2. Mode Toggle: Templates vs Manual */}
                <section>
                    <div className="flex gap-2 mb-4">
                        <button
                            onClick={() => { setWizardMode('templates'); setSelectedModelId(null); setSelectedModeId(null); }}
                            className={`px-4 py-2 rounded-lg font-medium transition-all ${wizardMode === 'templates'
                                ? 'bg-blue-600 text-white'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            🧪 Experiment Templates
                        </button>
                        <button
                            onClick={() => { setWizardMode('manual'); setSelectedTemplateId(null); }}
                            className={`px-4 py-2 rounded-lg font-medium transition-all ${wizardMode === 'manual'
                                ? 'bg-blue-600 text-white'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            ⚙️ Advanced (Models)
                        </button>
                    </div>

                    {/* Templates Mode */}
                    {wizardMode === 'templates' && (
                        <div className="space-y-4">
                            {selectedTemplateId === 'mutagenesis' ? (
                                <MutagenesisTemplate
                                    onBack={() => setSelectedTemplateId(null)}
                                    onSubmit={async (jobNamePrefix, variants, predictorConfig) => {
                                        // Batch submit variants
                                        console.log('DEBUG: predictorConfig received:', predictorConfig);
                                        console.log('DEBUG: predictorConfig.ligands:', predictorConfig.ligands);
                                        console.log('DEBUG: ligands length check:', predictorConfig.ligands?.length);
                                        const promises = variants.map((variant) => {
                                            const jobParams = {
                                                sequence: variant.sequence,
                                                sequence_name: variant.name,
                                                // Map predictor params
                                                boltz_recycling_steps: predictorConfig.recycling_steps,
                                                boltz_num_samples: predictorConfig.diffusion_samples,
                                                boltz_sampling_steps: predictorConfig.sampling_steps,
                                                num_parallel_jobs: predictorConfig.num_parallel_jobs,
                                                boltz_use_msa: predictorConfig.use_msa,
                                                pred_method: predictorConfig.predictor,
                                                // Complex mode: include ligands/ions if any
                                                ...(predictorConfig.ligands?.length ? {
                                                    complex_components: [
                                                        { type: 'protein', id: 'A', sequence: variant.sequence },
                                                        ...predictorConfig.ligands
                                                    ]
                                                } : {})
                                            };
                                            console.log('DEBUG: Submitting job with params:', jobParams);
                                            console.log('DEBUG: complex_components in params:', jobParams.complex_components);
                                            return submitMutation.mutateAsync({
                                                name: `${jobNamePrefix}_${variant.name}`,
                                                model_id: predictorConfig.predictor === 'rf3' ? 'rf3' : 'boltz2',
                                                mode: 'predict',
                                                params: jobParams
                                            });
                                        });

                                        try {
                                            await Promise.all(promises);
                                            // Only navigate after all are done
                                            queryClient.invalidateQueries({ queryKey: ['jobs'] });
                                            navigate('/');
                                        } catch (error) {
                                            console.error("Batch submission failed", error);
                                            // TODO: Show error toast?
                                        }
                                    }}
                                />
                            ) : (
                                <>
                                    <p className="text-slate-400 text-sm">Choose a preset workflow for your experiment goal:</p>
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        {[
                                            // Dynamic templates from API
                                            ...(templatesData?.data ?? []),
                                            // Hardcoded Mutagenesis Template
                                            {
                                                id: 'mutagenesis',
                                                name: 'Mutagenesis Library',
                                                description: 'Generate amino acid variants and predict their structures. Supports random libraries and manual editing.',
                                                icon: 'dna',
                                                color: '#A855F7', // Purple
                                                stages: [
                                                    { tool: 'Library Gen' },
                                                    { tool: 'Structure Prediction' }
                                                ]
                                            }
                                        ].map((template: any) => (
                                            <div
                                                key={template.id}
                                                onClick={() => setSelectedTemplateId(template.id)}
                                                className={`cursor-pointer p-5 rounded-xl border transition-all ${selectedTemplateId === template.id
                                                    ? 'bg-slate-800 border-blue-500 shadow-lg shadow-blue-500/10'
                                                    : 'bg-slate-800/30 border-slate-700 hover:border-slate-600'
                                                    }`}
                                            >
                                                <div className="flex items-start gap-3 mb-3">
                                                    <div
                                                        className="w-10 h-10 rounded-lg flex items-center justify-center text-lg"
                                                        style={{ backgroundColor: `${template.color}20`, color: template.color }}
                                                    >
                                                        {template.icon === 'target' ? '🎯' :
                                                            template.icon === 'flask' ? '🧪' :
                                                                template.icon === 'dna' ? '🧬' :
                                                                    template.icon === 'microscope' ? '🔬' : '⚡'}
                                                    </div>
                                                    <div>
                                                        <h3 className="font-semibold text-slate-200">{template.name}</h3>
                                                        <p className="text-xs text-slate-500 line-clamp-2">{template.description}</p>
                                                    </div>
                                                </div>
                                                {/* Stage Pipeline Diagram */}
                                                <div className="flex items-center gap-1 mt-3">
                                                    {template.stages.map((stage: any, idx: number) => (
                                                        <div key={idx} className="flex items-center">
                                                            <div className="bg-slate-700/50 px-2 py-1 rounded text-[10px] text-slate-300 whitespace-nowrap">
                                                                {stage.tool}
                                                            </div>
                                                            {idx < template.stages.length - 1 && (
                                                                <span className="text-slate-600 mx-1">→</span>
                                                            )}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
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
                                                {/* Simple icon fallback */}
                                                {model.ui_icon === 'dna' ? '🧬' : model.ui_icon === 'cube' ? '🧊' : '⚡'}
                                            </div>
                                            {model.experimental && (
                                                <span className="text-[10px] uppercase font-bold text-orange-400 bg-orange-400/10 px-2 py-0.5 rounded-full">
                                                    Beta
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

                {/* 3. Template Configuration - Only show if template selected and NOT mutagenesis */}
                {selectedTemplateId && selectedTemplateId !== 'mutagenesis' && selectedTemplateData?.data && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6">
                            <h2 className="text-lg font-semibold text-slate-200 mb-4 flex items-center gap-2">
                                <span className="w-1.5 h-6 bg-green-500 rounded-full" />
                                {selectedTemplateData.data.name} - Configuration
                            </h2>

                            {/* Stage Explanation */}
                            <div className="mb-6 p-4 bg-slate-900/50 rounded-lg">
                                <p className="text-sm text-slate-400 mb-3">This template runs the following stages:</p>
                                <div className="flex flex-wrap items-center gap-2">
                                    {selectedTemplateData.data.stages.map((stage: any, idx: number) => (
                                        <div key={idx} className="flex items-center">
                                            <div className="bg-slate-700 px-3 py-1.5 rounded-lg">
                                                <span className="text-sm font-medium text-slate-200">{idx + 1}. {stage.name}</span>
                                                <span className="text-xs text-slate-400 ml-2">({stage.tool})</span>
                                            </div>
                                            {idx < selectedTemplateData.data.stages.length - 1 && (
                                                <span className="text-blue-400 mx-2 text-lg">→</span>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* User Parameters */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                {selectedTemplateData.data.user_params.map((param: any) => {
                                    // Conditional Rendering Logic
                                    if (param.condition) {
                                        const controllingParam = selectedTemplateData.data.user_params.find((p: any) => p.name === param.condition.param);
                                        const controllingValue = params[param.condition.param] ?? controllingParam?.default;

                                        if (controllingValue && !param.condition.values.includes(controllingValue)) {
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
                                                            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-300 text-sm rounded-lg transition-colors flex items-center gap-2"
                                                        >
                                                            📚 Sequence Library
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
                                                                className="px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 text-sm rounded-lg transition-colors flex items-center gap-1.5 border border-emerald-600/30"
                                                            >
                                                                💾 Save to Library
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
                            {(selectedTemplateData?.data?.preset_params?.pred_method ||
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
                                        {(selectedModel.modes || []).map((mode: any) => (
                                            <option key={mode.id} value={mode.id}>
                                                {mode.name}
                                            </option>
                                        ))}
                                    </select>
                                    {selectedMode && (
                                        <p className="mt-2 text-sm text-slate-500">{selectedMode.description}</p>
                                    )}
                                </div>

                                {/* Dynamic Parameters */}
                                {selectedMode && visibleParams.length > 0 && (
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-6 border-t border-slate-700/50">
                                        {visibleParams.map((param: any) => {
                                            return (
                                                <div key={param.name} className={param.type === 'file' || param.type === 'directory' ? 'col-span-full' : ''}>
                                                    <label className="block text-sm font-medium text-slate-400 mb-1">
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
                                                            value={params[param.name] || ''}
                                                            onChange={(e) => updateParam(param.name, e.target.value)}
                                                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                                                        >
                                                            {param.enum.map((opt: string) => (
                                                                <option key={opt} value={opt}>{opt}</option>
                                                            ))}
                                                        </select>
                                                    ) : param.type === 'text' || param.preset_type === 'sequence' ? (
                                                        /* Sequence textarea with library button */
                                                        <div className="space-y-2">
                                                            <div className="flex gap-2">
                                                                <button
                                                                    type="button"
                                                                    onClick={() => {
                                                                        setActiveSequenceField(param.name);
                                                                        setShowSequenceManager(true);
                                                                    }}
                                                                    className="px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 rounded-lg text-sm transition-colors border border-emerald-600/30"
                                                                >
                                                                    📚 Sequence Library
                                                                </button>
                                                                {params[param.name] && (
                                                                    <span className="text-xs text-slate-500 self-center">
                                                                        {(params[param.name] || '').length} aa
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <textarea
                                                                value={params[param.name] || ''}
                                                                onChange={(e) => updateParam(param.name, e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                                                                rows={4}
                                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                                                                placeholder="Enter amino acid sequence (A-Z) or use Sequence Library..."
                                                            />
                                                        </div>
                                                    ) : param.preset_type === 'pdb' ? (
                                                        /* Enhanced PDB Structure Input */
                                                        <StructureInput
                                                            value={params[param.name] || ''}
                                                            onChange={(v) => updateParam(param.name, v)}
                                                            onBrowse={() => setShowFileBrowser(param.name)}
                                                            showChainSelectors={selectedModel?.id === 'fampnn'}
                                                            designChain={params['design_chain'] || 'A'}
                                                            targetChain={params['target_chain'] || ''}
                                                            onDesignChainChange={(c) => updateParam('design_chain', c)}
                                                            onTargetChainChange={(c) => updateParam('target_chain', c)}
                                                            enableMultiSelect={false}
                                                            enableDirectory={false}
                                                        />
                                                    ) : param.preset_type === 'ligand' ? (
                                                        /* Ligand/SMILES with preset dropdown and nucleotide converter */
                                                        <div className="space-y-3">
                                                            <select
                                                                value=""
                                                                onChange={(e) => updateParam(param.name, e.target.value)}
                                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                                                            >
                                                                <option value="">Select preset ligand...</option>
                                                                <optgroup label="DNA Nucleotides (dNTPs)">
                                                                    <option value="Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3">dATP</option>
                                                                    <option value="Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O">dTTP</option>
                                                                    <option value="Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1">dGTP</option>
                                                                    <option value="Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1">dCTP</option>
                                                                </optgroup>
                                                                <optgroup label="RNA Nucleotides (NTPs)">
                                                                    <option value="Nc1ncnc2c1ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O">ATP</option>
                                                                    <option value="O=c1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)[nH]1">UTP</option>
                                                                    <option value="Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1">GTP</option>
                                                                    <option value="Nc1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)n1">CTP</option>
                                                                </optgroup>
                                                                <optgroup label="Common Small Molecules">
                                                                    <option value="CC(=O)Oc1ccccc1C(=O)O">Aspirin</option>
                                                                    <option value="CC(C)Cc1ccc(C(C)C(=O)O)cc1">Ibuprofen</option>
                                                                    <option value="Cn1cnc2c1c(=O)[nH]c(=O)n2C">Caffeine</option>
                                                                </optgroup>
                                                            </select>

                                                            {/* DNA/RNA Sequence Converter */}
                                                            <div className="p-3 bg-slate-900/50 rounded-lg border border-slate-700/50">
                                                                <label className="block text-xs text-slate-500 mb-1">🧬 Convert DNA/RNA sequence to SMILES</label>
                                                                <div className="flex gap-2">
                                                                    <input
                                                                        type="text"
                                                                        placeholder="Enter DNA (ACGT) or RNA (ACGU)..."
                                                                        className="flex-1 bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-white text-sm font-mono outline-none"
                                                                        onKeyDown={async (e) => {
                                                                            if (e.key === 'Enter') {
                                                                                const input = e.currentTarget;
                                                                                const seq = input.value.toUpperCase().replace(/[^ACGTU]/g, '');
                                                                                if (seq.length > 0 && seq.length <= 15) {
                                                                                    try {
                                                                                        const seqType = seq.includes('U') ? 'rna' : 'dna';
                                                                                        const res = await fetch('http://localhost:8000/api/smiles/convert', {
                                                                                            method: 'POST',
                                                                                            headers: { 'Content-Type': 'application/json' },
                                                                                            body: JSON.stringify({ sequence: seq, sequence_type: seqType })
                                                                                        });
                                                                                        const data = await res.json();
                                                                                        if (data.smiles) {
                                                                                            updateParam(param.name, data.smiles);
                                                                                            input.value = '';
                                                                                        }
                                                                                    } catch (err) { console.error(err); }
                                                                                }
                                                                            }
                                                                        }}
                                                                    />
                                                                    <button
                                                                        type="button"
                                                                        onClick={async (e) => {
                                                                            const input = e.currentTarget.previousElementSibling as HTMLInputElement;
                                                                            const seq = input.value.toUpperCase().replace(/[^ACGTU]/g, '');
                                                                            if (seq.length > 0 && seq.length <= 15) {
                                                                                try {
                                                                                    const seqType = seq.includes('U') ? 'rna' : 'dna';
                                                                                    const res = await fetch('http://localhost:8000/api/smiles/convert', {
                                                                                        method: 'POST',
                                                                                        headers: { 'Content-Type': 'application/json' },
                                                                                        body: JSON.stringify({ sequence: seq, sequence_type: seqType })
                                                                                    });
                                                                                    const data = await res.json();
                                                                                    if (data.smiles) {
                                                                                        updateParam(param.name, data.smiles);
                                                                                        input.value = '';
                                                                                    }
                                                                                } catch (err) { console.error(err); }
                                                                            }
                                                                        }}
                                                                        className="px-2 py-1.5 bg-purple-600/30 hover:bg-purple-600/50 text-purple-300 rounded text-xs transition-colors"
                                                                    >
                                                                        Convert
                                                                    </button>
                                                                </div>
                                                                <p className="text-[10px] text-slate-600 mt-1">Max 15 nt. Press Enter or click Convert.</p>
                                                            </div>

                                                            <div className="flex gap-2">
                                                                <input
                                                                    type="text"
                                                                    value={params[param.name] || ''}
                                                                    onChange={(e) => updateParam(param.name, e.target.value)}
                                                                    className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm outline-none font-mono"
                                                                    placeholder="Or enter custom SMILES string..."
                                                                />
                                                                <button
                                                                    type="button"
                                                                    onClick={async () => {
                                                                        const smiles = params[param.name];
                                                                        if (!smiles) return;
                                                                        try {
                                                                            const res = await fetch('http://localhost:8000/api/smiles/generate-3d', {
                                                                                method: 'POST',
                                                                                headers: { 'Content-Type': 'application/json' },
                                                                                body: JSON.stringify({
                                                                                    smiles,
                                                                                    name: 'ligand_' + Date.now(),
                                                                                    energy_minimize: true
                                                                                })
                                                                            });
                                                                            const data = await res.json();
                                                                            if (data.success && data.file_path) {
                                                                                updateParam('ligand_pdb', data.file_path);
                                                                                alert(`✓ 3D coordinates generated!\n${data.num_atoms} atoms, ${data.energy?.toFixed(1)} kcal/mol\nSaved: ${data.file_path}`);
                                                                            } else {
                                                                                alert('Error: ' + (data.error || 'Failed to generate 3D'));
                                                                            }
                                                                        } catch (err) {
                                                                            console.error(err);
                                                                            alert('Failed to generate 3D coordinates');
                                                                        }
                                                                    }}
                                                                    className="px-3 py-2 bg-green-600/30 hover:bg-green-600/50 text-green-300 rounded-lg text-sm transition-colors whitespace-nowrap"
                                                                    title="Generate 3D coordinates from SMILES using RDKit"
                                                                >
                                                                    🧪 Generate 3D
                                                                </button>
                                                            </div>
                                                        </div>
                                                    ) : (param.type === 'file' || param.type === 'directory') ? (
                                                        <div className="flex gap-2">
                                                            <input
                                                                type="text"
                                                                value={params[param.name] || ''}
                                                                onChange={(e) => updateParam(param.name, e.target.value)}
                                                                className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm outline-none font-mono"
                                                                placeholder={param.type === 'file' ? '/path/to/file' : '/path/to/directory'}
                                                            />
                                                            <button
                                                                onClick={() => setShowFileBrowser(param.name)}
                                                                className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                                                            >
                                                                Browse
                                                            </button>
                                                        </div>
                                                    ) : (
                                                        <input
                                                            type={param.type === 'integer' || param.type === 'number' ? 'number' : 'text'}
                                                            value={params[param.name] || ''}
                                                            onChange={(e) => updateParam(param.name, param.type === 'integer' ? parseInt(e.target.value) : param.type === 'number' ? parseFloat(e.target.value) : e.target.value)}
                                                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                                                        />
                                                    )}
                                                </div>
                                            );
                                        })
                                        }
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

                {/* Submit Button - Hide if Mutagenesis Template is active (it has its own) */}
                {selectedTemplateId !== 'mutagenesis' && (
                    <div className="flex justify-end gap-3 pt-4 pb-12">
                        {/* Save as Template Button */}
                        {(wizardMode === 'templates' || (wizardMode === 'manual' && selectedModelId)) && (
                            <button
                                onClick={() => setShowTemplateManager(true)}
                                className="px-6 py-4 rounded-xl font-semibold text-purple-400 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 transition-all flex items-center gap-2"
                            >
                                📋 Template Manager
                            </button>
                        )}
                        <button
                            onClick={handleSubmit}
                            disabled={!isReady || submitMutation.isPending}
                            className={`px-8 py-4 rounded-xl font-semibold text-white shadow-xl transition-all ${isReady
                                ? 'bg-gradient-to-r from-blue-600 to-purple-600 hover:scale-[1.02] active:scale-[0.98] shadow-blue-500/25'
                                : 'bg-slate-800 text-slate-500 cursor-not-allowed'
                                }`}
                        >
                            {submitMutation.isPending ? 'Launching Job...' : 'Launch Experiment 🚀'}
                        </button>
                    </div>
                )}
            </main>

            {/* Loading Overlay for Batch Submission */}
            {submitMutation.isPending && selectedTemplateId === 'mutagenesis' && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] flex items-center justify-center">
                    <div className="bg-slate-900 border border-slate-700 p-8 rounded-2xl shadow-2xl flex flex-col items-center">
                        <div className="w-16 h-16 border-4 border-purple-500/30 border-t-purple-500 rounded-full animate-spin mb-4" />
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
                onSelect={(template) => {
                    // Load template params
                    setParams(template.params);
                    if (template.model_id) setSelectedModelId(template.model_id);
                    if (template.mode) setSelectedModeId(template.mode);
                }}
                currentParams={params}
                currentModelId={selectedModelId || undefined}
                currentMode={selectedModeId || undefined}
                baseTemplateId={selectedTemplateId || undefined}
            />
        </div>
    );
}
