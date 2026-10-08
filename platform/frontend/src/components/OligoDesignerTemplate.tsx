/**
 * OligoDesignerTemplate - Multi-polymer design (DNA, RNA, Protein)
 * 
 * Uses RFDpoly for de novo structure generation with Boltz-2 validation.
 * Supports: RNA aptamers, DNA aptamers, protein-DNA complexes, RNP complexes.
 */

import React, { useState, useMemo } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings, DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './PhysicsRefinementPanel';

// Design mode presets
type DesignMode = 'rna_aptamer' | 'dna_aptamer' | 'protein_dna' | 'protein_rna' | 'custom';
type PolymerType = 'dna' | 'rna' | 'protein';
type QualityPreset = 'fast' | 'standard' | 'high_quality';

interface ChainConfig {
    id: string;
    type: PolymerType;
    length: number;
    lengthMin?: number;
    lengthMax?: number;
    useRange: boolean;
}

interface OligoDesignerTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, any>;
}

const DESIGN_MODE_INFO: Record<DesignMode, { label: string; icon: string; description: string; defaultChains: ChainConfig[] }> = {
    rna_aptamer: {
        label: 'RNA Aptamer',
        icon: '🧬',
        description: 'Design RNA molecules with specific 3D structures (riboswitches, aptamers)',
        defaultChains: [{ id: '1', type: 'rna', length: 40, useRange: false }]
    },
    dna_aptamer: {
        label: 'DNA Aptamer',
        icon: '🔷',
        description: 'Design DNA sequences with target-binding capability',
        defaultChains: [{ id: '1', type: 'dna', length: 40, useRange: false }]
    },
    protein_dna: {
        label: 'Protein-DNA Complex',
        icon: '📎',
        description: 'Design transcription factor-like proteins with cognate DNA',
        defaultChains: [
            { id: '1', type: 'dna', length: 20, useRange: false },
            { id: '2', type: 'protein', length: 80, useRange: false }
        ]
    },
    protein_rna: {
        label: 'Protein-RNA Complex',
        icon: '🧪',
        description: 'Design ribonucleoprotein assemblies (RNA-binding proteins)',
        defaultChains: [
            { id: '1', type: 'rna', length: 30, useRange: false },
            { id: '2', type: 'protein', length: 100, useRange: false }
        ]
    },
    custom: {
        label: 'Custom Multi-Polymer',
        icon: '⚙️',
        description: 'Define custom chain configurations (DNA + RNA + Protein)',
        defaultChains: [{ id: '1', type: 'protein', length: 100, useRange: false }]
    }
};

const QUALITY_PRESETS: Record<QualityPreset, { steps: number; label: string }> = {
    fast: { steps: 25, label: 'Fast (25 steps)' },
    standard: { steps: 50, label: 'Standard (50 steps)' },
    high_quality: { steps: 100, label: 'High Quality (100 steps)' }
};

export function OligoDesignerTemplate({ onBack, initialValues }: OligoDesignerTemplateProps) {
    const navigate = useNavigate();

    // Basic settings
    const [designName, setDesignName] = useState(initialValues?.designName || '');
    const [designMode, setDesignMode] = useState<DesignMode>(initialValues?.designMode || 'rna_aptamer');
    const [chains, setChains] = useState<ChainConfig[]>(DESIGN_MODE_INFO[designMode].defaultChains);

    // Generation settings
    const [numDesigns, setNumDesigns] = useState(4);
    const [qualityPreset, setQualityPreset] = useState<QualityPreset>('standard');
    const [checkpoint, setCheckpoint] = useState<'generalized' | 'rna_optimized'>('generalized');

    // Validation settings
    const [validateWithBoltz, setValidateWithBoltz] = useState(true);
    const [minPlddt, setMinPlddt] = useState(70);
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

    // Advanced (collapsed by default)
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [temperature, setTemperature] = useState(1.0);
    const [seed, setSeed] = useState<number | null>(null);

    // Handle design mode change
    const handleModeChange = (mode: DesignMode) => {
        setDesignMode(mode);
        setChains(DESIGN_MODE_INFO[mode].defaultChains.map((c, i) => ({ ...c, id: String(i + 1) })));
    };

    // Chain management
    const addChain = () => {
        const newId = String(chains.length + 1);
        setChains([...chains, { id: newId, type: 'protein', length: 50, useRange: false }]);
    };

    const removeChain = (id: string) => {
        if (chains.length > 1) {
            setChains(chains.filter(c => c.id !== id));
        }
    };

    const updateChain = (id: string, updates: Partial<ChainConfig>) => {
        setChains(chains.map(c => c.id === id ? { ...c, ...updates } : c));
    };

    // Build contigs string for Nextflow
    const contigsString = useMemo(() => {
        return chains.map(c => {
            if (c.useRange && c.lengthMin && c.lengthMax) {
                return `${c.lengthMin}-${c.lengthMax}`;
            }
            return String(c.length);
        }).join(' ');
    }, [chains]);

    const polymerChainsString = useMemo(() => {
        return chains.map(c => c.type).join(',');
    }, [chains]);

    // Submit mutation
    const submitMutation = useMutation({
        mutationFn: async () => {
            const jobParams = {
                mode: 'oligo_design',
                design_name: designName || `oligo_${Date.now()}`,
                rfdpoly_enabled: true,
                rfdpoly_contigs: contigsString,
                rfdpoly_polymer_chains: polymerChainsString,
                rfdpoly_num_designs: numDesigns,
                rfdpoly_diffusion_steps: QUALITY_PRESETS[qualityPreset].steps,
                rfdpoly_checkpoint: checkpoint,
                oligo_validate_boltz: validateWithBoltz,
                oligo_min_plddt: minPlddt,
                // Physics
                openmm_enabled: physicsSettings.enabled,
                openmm_compute_tier: physicsSettings.computeTier,
                // Advanced
                ...(temperature !== 1.0 && { rfdpoly_temperature: temperature }),
                ...(seed !== null && { rfdpoly_seed: seed }),
            };

            return api.submitJob(jobParams);
        },
        onSuccess: (data) => {
            navigate(`/results/${data.job_id}`);
        }
    });

    const handleSubmit = () => {
        if (!designName.trim()) {
            alert('Please enter a design name');
            return;
        }
        submitMutation.mutate();
    };

    return (
        <div className="oligo-designer-template p-6 space-y-6 max-w-4xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between">
                <button onClick={onBack} className="text-slate-400 hover:text-white flex items-center gap-2">
                    ← Back to Catalog
                </button>
                <h1 className="text-2xl font-bold text-white">Oligo Designer</h1>
            </div>

            {/* Design Name */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-2">Design Name *</label>
                <input
                    type="text"
                    value={designName}
                    onChange={(e) => setDesignName(e.target.value)}
                    placeholder="my_rna_aptamer"
                    className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                />
            </div>

            {/* Design Mode Selector */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Design Mode</label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    {(Object.entries(DESIGN_MODE_INFO) as [DesignMode, typeof DESIGN_MODE_INFO[DesignMode]][]).map(([mode, info]) => (
                        <button
                            key={mode}
                            onClick={() => handleModeChange(mode)}
                            className={`p-4 rounded-lg border-2 transition-all text-left ${designMode === mode
                                    ? 'border-emerald-500 bg-emerald-500/10'
                                    : 'border-slate-600 hover:border-slate-500 bg-slate-700/50'
                                }`}
                        >
                            <div className="text-2xl mb-1">{info.icon}</div>
                            <div className="font-medium text-white text-sm">{info.label}</div>
                            <div className="text-xs text-slate-400 mt-1">{info.description}</div>
                        </button>
                    ))}
                </div>
            </div>

            {/* Chain Configuration */}
            <div className="bg-slate-800 rounded-lg p-4">
                <div className="flex justify-between items-center mb-3">
                    <label className="text-sm font-medium text-slate-300">Chain Configuration</label>
                    <button
                        onClick={addChain}
                        className="text-sm text-emerald-400 hover:text-emerald-300"
                    >
                        + Add Chain
                    </button>
                </div>
                <div className="space-y-3">
                    {chains.map((chain, index) => (
                        <div key={chain.id} className="flex items-center gap-3 bg-slate-700/50 rounded p-3">
                            <span className="text-slate-400 w-16">Chain {index + 1}:</span>
                            <select
                                value={chain.type}
                                onChange={(e) => updateChain(chain.id, { type: e.target.value as PolymerType })}
                                className="bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                            >
                                <option value="dna">DNA</option>
                                <option value="rna">RNA</option>
                                <option value="protein">Protein</option>
                            </select>
                            <span className="text-slate-400">Length:</span>
                            {chain.useRange ? (
                                <>
                                    <input
                                        type="number"
                                        value={chain.lengthMin || chain.length}
                                        onChange={(e) => updateChain(chain.id, { lengthMin: parseInt(e.target.value) || 20 })}
                                        className="w-16 bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                    />
                                    <span className="text-slate-500">-</span>
                                    <input
                                        type="number"
                                        value={chain.lengthMax || chain.length + 20}
                                        onChange={(e) => updateChain(chain.id, { lengthMax: parseInt(e.target.value) || 60 })}
                                        className="w-16 bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                    />
                                </>
                            ) : (
                                <input
                                    type="number"
                                    value={chain.length}
                                    onChange={(e) => updateChain(chain.id, { length: parseInt(e.target.value) || 50 })}
                                    className="w-20 bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                />
                            )}
                            <span className="text-slate-500 text-sm">{chain.type === 'protein' ? 'residues' : 'bases'}</span>
                            <label className="flex items-center gap-1 text-xs text-slate-400 ml-auto">
                                <input
                                    type="checkbox"
                                    checked={chain.useRange}
                                    onChange={(e) => updateChain(chain.id, {
                                        useRange: e.target.checked,
                                        lengthMin: chain.length - 10,
                                        lengthMax: chain.length + 10
                                    })}
                                    className="rounded"
                                />
                                Range
                            </label>
                            {chains.length > 1 && (
                                <button
                                    onClick={() => removeChain(chain.id)}
                                    className="text-red-400 hover:text-red-300 text-sm"
                                >
                                    ✕
                                </button>
                            )}
                        </div>
                    ))}
                </div>
                <div className="mt-2 text-xs text-slate-500">
                    Contigs: <code className="bg-slate-700 px-1 rounded">{contigsString}</code> |
                    Chains: <code className="bg-slate-700 px-1 rounded">{polymerChainsString}</code>
                </div>
            </div>

            {/* Generation Settings */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Generation Settings</label>
                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Number of Designs</label>
                        <input
                            type="number"
                            value={numDesigns}
                            onChange={(e) => setNumDesigns(parseInt(e.target.value) || 4)}
                            min={1}
                            max={64}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        />
                    </div>
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Quality Preset</label>
                        <select
                            value={qualityPreset}
                            onChange={(e) => setQualityPreset(e.target.value as QualityPreset)}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        >
                            {Object.entries(QUALITY_PRESETS).map(([key, preset]) => (
                                <option key={key} value={key}>{preset.label}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Model Checkpoint</label>
                        <select
                            value={checkpoint}
                            onChange={(e) => setCheckpoint(e.target.value as 'generalized' | 'rna_optimized')}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        >
                            <option value="generalized">Generalized (All Polymers)</option>
                            <option value="rna_optimized">RNA-Optimized</option>
                        </select>
                    </div>
                </div>
            </div>

            {/* Validation Settings */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Validation</label>
                <div className="space-y-3">
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={validateWithBoltz}
                            onChange={(e) => setValidateWithBoltz(e.target.checked)}
                            className="rounded"
                        />
                        <span className="text-white">Validate with Boltz-2</span>
                    </label>
                    {validateWithBoltz && (
                        <div className="pl-6">
                            <label className="text-xs text-slate-400 block mb-1">Min pLDDT: {minPlddt}</label>
                            <input
                                type="range"
                                min={50}
                                max={90}
                                value={minPlddt}
                                onChange={(e) => setMinPlddt(parseInt(e.target.value))}
                                className="w-full"
                            />
                        </div>
                    )}
                </div>
            </div>

            {/* Physics Refinement Panel */}
            <PhysicsRefinementPanel
                settings={physicsSettings}
                onChange={setPhysicsSettings}
            />

            {/* Advanced Options */}
            <div className="bg-slate-800 rounded-lg p-4">
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="text-sm text-slate-400 hover:text-white flex items-center gap-2"
                >
                    {showAdvanced ? '▼' : '▸'} Advanced Options
                </button>
                {showAdvanced && (
                    <div className="mt-4 grid grid-cols-2 gap-4">
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Temperature</label>
                            <input
                                type="number"
                                step={0.1}
                                value={temperature}
                                onChange={(e) => setTemperature(parseFloat(e.target.value) || 1.0)}
                                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Random Seed (optional)</label>
                            <input
                                type="number"
                                value={seed ?? ''}
                                onChange={(e) => setSeed(e.target.value ? parseInt(e.target.value) : null)}
                                placeholder="Auto"
                                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Submit Button */}
            <div className="flex justify-end gap-4">
                <button
                    onClick={onBack}
                    className="px-6 py-2 bg-slate-700 text-white rounded hover:bg-slate-600"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={submitMutation.isPending || !designName.trim()}
                    className="px-6 py-2 bg-emerald-600 text-white rounded hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    {submitMutation.isPending ? 'Submitting...' : 'Submit Design Job'}
                </button>
            </div>

            {submitMutation.isError && (
                <div className="bg-red-500/20 border border-red-500 rounded p-3 text-red-300">
                    Error: {(submitMutation.error as Error)?.message || 'Failed to submit job'}
                </div>
            )}
        </div>
    );
}

export default OligoDesignerTemplate;
