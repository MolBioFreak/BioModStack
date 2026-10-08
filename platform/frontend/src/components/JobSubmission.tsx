
import { useState, useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchModels, fetchFiles, submitJob, uploadFile, fetchTemplates, fetchTemplateById, fetchInputPresets, fetchPresetDirectories } from '../lib/api';

// Preset Selector Component - Enhanced with multi-select and directory modes
interface PresetSelectorProps {
    presetType: 'pdb' | 'sequence' | 'yaml' | 'contig' | 'ntp';
    value: string;
    onChange: (value: string) => void;
    onBrowse: () => void;
    label?: string;
    placeholder?: string;
    enableMultiSelect?: boolean;  // Show multi-select mode for PDB type
    enableDirectory?: boolean;    // Show directory mode for PDB type
}

function PresetSelector({ presetType, value, onChange, onBrowse, label: _label, placeholder, enableMultiSelect = false, enableDirectory = false }: PresetSelectorProps) {
    const { data: presetsData } = useQuery({
        queryKey: ['presets', presetType],
        queryFn: () => fetchInputPresets(presetType),
    });

    const { data: directoriesData } = useQuery({
        queryKey: ['preset-directories'],
        queryFn: () => fetchPresetDirectories(),
        enabled: enableDirectory && presetType === 'pdb',
    });

    const presets = presetsData?.data ?? [];
    const directories = directoriesData?.data ?? [];

    // Mode types: preset (single), multi (checkboxes), directory (batch dirs), manual (raw input)
    const [mode, setMode] = useState<'preset' | 'multi' | 'directory' | 'manual'>('preset');
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

    // Group presets by category
    const groupedPresets = presets.reduce((groups: Record<string, any[]>, preset: any) => {
        const cat = preset.category || 'general';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(preset);
        return groups;
    }, {});

    // Get display value for the selected preset
    const selectedPreset = presets.find((p: any) =>
        presetType === 'pdb' ? p.path === value :
            presetType === 'contig' ? p.value === value :
                presetType === 'sequence' ? p.sequence === value : p.id === value
    );

    // Handle multi-select toggle
    const togglePreset = (preset: any) => {
        const newSelected = new Set(selectedIds);
        if (newSelected.has(preset.id)) {
            newSelected.delete(preset.id);
        } else {
            newSelected.add(preset.id);
        }
        setSelectedIds(newSelected);

        // Update value with comma-separated paths for multi-select
        const selectedPaths = presets
            .filter((p: any) => newSelected.has(p.id))
            .map((p: any) => p.path)
            .join(',');
        onChange(selectedPaths);
    };

    // Check if all selected
    const allSelected = selectedIds.size === presets.length;
    const toggleAll = () => {
        if (allSelected) {
            setSelectedIds(new Set());
            onChange('');
        } else {
            const allIds = new Set(presets.map((p: any) => p.id));
            setSelectedIds(allIds);
            onChange(presets.map((p: any) => p.path).join(','));
        }
    };

    // Mode tabs based on enabled features
    const availableModes = [
        { id: 'preset', label: 'Presets', enabled: true },
        { id: 'multi', label: 'Multi-Select', enabled: enableMultiSelect && presetType === 'pdb' },
        { id: 'directory', label: 'Directory', enabled: enableDirectory && presetType === 'pdb' },
        { id: 'manual', label: 'Manual', enabled: true },
    ].filter(m => m.enabled);

    return (
        <div className="space-y-2">
            {/* Mode Toggle */}
            <div className="flex gap-1 text-xs flex-wrap">
                {availableModes.map(m => (
                    <button
                        key={m.id}
                        onClick={() => setMode(m.id as any)}
                        className={`px-2 py-1 rounded transition-colors ${mode === m.id ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600'}`}
                    >
                        {m.label}
                    </button>
                ))}
            </div>

            {/* Single Preset Mode */}
            {mode === 'preset' && (
                <div className="space-y-2">
                    <select
                        value={selectedPreset ? (presetType === 'pdb' ? selectedPreset.path : presetType === 'contig' ? selectedPreset.value : selectedPreset.id) : ''}
                        onChange={(e) => onChange(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">{placeholder || 'Select a preset...'}</option>
                        {presets.map((preset: any) => (
                            <option
                                key={preset.id}
                                value={presetType === 'pdb' ? preset.path : presetType === 'contig' ? preset.value : preset.id}
                            >
                                {preset.name}
                            </option>
                        ))}
                    </select>
                    {selectedPreset && (
                        <p className="text-xs text-slate-500">{selectedPreset.description}</p>
                    )}
                    {/* Editable value field */}
                    {value && (
                        <div className="mt-2">
                            <label className="text-xs text-slate-400 block mb-1">Edit value (modify as needed):</label>
                            <input
                                type="text"
                                value={value}
                                onChange={(e) => onChange(e.target.value)}
                                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none focus:ring-2 focus:ring-blue-500"
                            />
                        </div>
                    )}
                </div>
            )}

            {/* Multi-Select Mode (Checkboxes) */}
            {mode === 'multi' && (
                <div className="space-y-2">
                    <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 max-h-64 overflow-y-auto">
                        {/* Select All */}
                        <label className="flex items-center gap-2 p-2 hover:bg-slate-800 rounded cursor-pointer border-b border-slate-700 mb-2">
                            <input
                                type="checkbox"
                                checked={allSelected}
                                onChange={toggleAll}
                                className="w-4 h-4 rounded text-blue-600 bg-slate-700 border-slate-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-white font-medium">Select All ({presets.length})</span>
                        </label>

                        {/* Grouped by category */}
                        {Object.entries(groupedPresets).map(([category, catPresets]) => (
                            <div key={category} className="mb-2">
                                <div className="text-xs text-slate-400 uppercase tracking-wide mb-1 px-2">{category}</div>
                                {(catPresets as any[]).map((preset: any) => (
                                    <label
                                        key={preset.id}
                                        className="flex items-center gap-2 p-2 hover:bg-slate-800 rounded cursor-pointer"
                                    >
                                        <input
                                            type="checkbox"
                                            checked={selectedIds.has(preset.id)}
                                            onChange={() => togglePreset(preset)}
                                            className="w-4 h-4 rounded text-blue-600 bg-slate-700 border-slate-600 focus:ring-blue-500"
                                        />
                                        <span className="text-sm text-white">{preset.name}</span>
                                    </label>
                                ))}
                            </div>
                        ))}
                    </div>
                    <p className="text-xs text-slate-400">Selected: {selectedIds.size} files</p>
                </div>
            )}

            {/* Directory Mode */}
            {mode === 'directory' && (
                <div className="space-y-2">
                    <select
                        value={value}
                        onChange={(e) => onChange(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">Select a batch directory...</option>
                        {directories.map((dir: any) => (
                            <option key={dir.id} value={dir.path}>
                                {dir.name}
                            </option>
                        ))}
                    </select>
                    {directories.find((d: any) => d.path === value)?.description && (
                        <p className="text-xs text-slate-500">
                            {directories.find((d: any) => d.path === value)?.description}
                        </p>
                    )}
                    {/* Browse button for custom directory */}
                    <div className="flex gap-2 items-center">
                        <input
                            type="text"
                            value={value}
                            onChange={(e) => onChange(e.target.value)}
                            placeholder="/path/to/directory"
                            className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        />
                        <button
                            onClick={onBrowse}
                            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                        >
                            Browse
                        </button>
                    </div>
                </div>
            )}

            {/* Manual Mode */}
            {mode === 'manual' && (
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={value}
                        onChange={(e) => onChange(e.target.value)}
                        className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        placeholder={presetType === 'pdb' ? '/path/to/file.pdb or /path/to/directory' : 'Enter value...'}
                    />
                    {(presetType === 'pdb' || presetType === 'yaml') && (
                        <button
                            onClick={onBrowse}
                            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                        >
                            Browse
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

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
    });

    const models = modelsData?.data ?? [];
    const selectedModel = models.find((m: any) => m.id === selectedModelId);
    const selectedMode = selectedModel?.modes.find((m: any) => m.id === selectedModeId);

    // Initialize params when model/mode changes
    useEffect(() => {
        if (selectedModel) {
            const defaults: Record<string, any> = {};
            (selectedModel.params || []).forEach((p: any) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            setParams(defaults);
        }
    }, [selectedModelId]);

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

        if (wizardMode === 'templates' && selectedTemplateData?.data) {
            // Template mode: merge preset params with user params
            const templateData = selectedTemplateData.data;
            const mergedParams = { ...templateData.preset_params, ...params };

            // Determine the Nextflow profile based on template type
            // Priority: rfd_mode (binder/monomer) > diffusion_method (boltzgen) > pred_method (structure prediction/validation) > skip_rfd (fampnn_predict)
            let nextflowProfile = '';
            if (mergedParams.rfd_mode) {
                // Binder or monomer design templates
                nextflowProfile = mergedParams.rfd_mode;
            } else if (mergedParams.diffusion_method === 'boltzgen') {
                // BoltzGen ligand-aware template
                nextflowProfile = 'boltzgen';
            } else if (mergedParams.pred_method) {
                // Structure prediction/validation templates (af2, boltz, rf3, both)
                nextflowProfile = mergedParams.pred_method;
            } else if (mergedParams.skip_rfd === true) {
                // DNA polymerase or similar - skip diffusion, just sequence design + prediction
                nextflowProfile = 'fampnn_predict';
            } else {
                // Fallback to template ID
                nextflowProfile = selectedTemplateId || 'binder_denovo';
            }

            // Determine effective model ID for UI display
            let effectiveModelId = 'template_' + (selectedTemplateId || 'unknown');
            if (mergedParams.pred_method) {
                effectiveModelId = mergedParams.pred_method; // 'boltz', 'rf3', 'both'
            } else if (mergedParams.diffusion_method === 'boltzgen') {
                effectiveModelId = 'boltzgen';
            } else if (mergedParams.rfd_mode) {
                effectiveModelId = 'rf_diffusion';
            } else if (mergedParams.skip_rfd === true) {
                effectiveModelId = 'protein_mpnn';
            }

            submitMutation.mutate({
                name: jobName,
                model_id: effectiveModelId,
                mode: nextflowProfile,
                params: mergedParams,
            });
        } else if (selectedModelId && selectedModeId) {
            // Manual mode
            submitMutation.mutate({
                name: jobName,
                model_id: selectedModelId,
                mode: selectedModeId,
                params: params,
            });
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
                            <p className="text-slate-400 text-sm">Choose a preset workflow for your experiment goal:</p>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                {(templatesData?.data ?? []).map((template: any) => (
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

                {/* 3. Template Configuration - Only show if template selected */}
                {selectedTemplateId && selectedTemplateData?.data && (
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
                                                /* Text/Sequence Input with Preset Dropdown */
                                                <div className="space-y-2">
                                                    <div className="flex gap-2">
                                                        <select
                                                            onChange={async (e) => {
                                                                if (e.target.value) {
                                                                    try {
                                                                        const res = await fetch(`/api/inputs/presets?type=sequence`);
                                                                        const presets = await res.json();
                                                                        const preset = presets.find((p: any) => p.id === e.target.value);
                                                                        if (preset) {
                                                                            updateParam(param.name, preset.sequence.replace(/\s+/g, ''));
                                                                        }
                                                                    } catch (err) { console.error(err); }
                                                                }
                                                            }}
                                                            className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                                        >
                                                            <option value="">Load from preset...</option>
                                                            <option value="tdt_human">Human TdT (509 aa)</option>
                                                            <option value="klenow_ecoli">Klenow Fragment (605 aa)</option>
                                                            <option value="taq_ttaq">Taq Polymerase (832 aa)</option>
                                                            <option value="gfp_avictoria">GFP (238 aa)</option>
                                                            <option value="lysozyme_gallus">Lysozyme (147 aa)</option>
                                                        </select>
                                                        <span className="text-xs text-slate-500 self-center">
                                                            {(params[param.name] || '').length} aa
                                                        </span>
                                                    </div>
                                                    <textarea
                                                        value={params[param.name] || ''}
                                                        onChange={(e) => updateParam(param.name, e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                                                        placeholder={param.placeholder || 'Enter amino acid sequence (A-Z)...'}
                                                        rows={5}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y"
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
                                                    ) : (param.type === 'file' || param.type === 'directory') ? (
                                                        <div className="flex gap-2">
                                                            <input
                                                                type="text"
                                                                value={params[param.name] || ''}
                                                                onChange={(e) => updateParam(param.name, e.target.value)}
                                                                className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm outline-none font-mono"
                                                                placeholder={param.type === 'file' ? '/path/to/file.pdb' : '/path/to/directory'}
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
                                )}
                            </div>
                        </div>
                    </section>
                )}

                {/* Submit Button */}
                <div className="flex justify-end pt-4 pb-12">
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
            </main>

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
        </div>
    );
}
