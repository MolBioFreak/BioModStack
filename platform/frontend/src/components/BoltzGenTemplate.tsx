/**
 * BoltzGenTemplate - Guided workflow for ligand-aware binder design
 * 
 * Modes:
 * - Ligand Binder: Design protein binding to custom SMILES
 * - NTP Binder: Design protein binding nucleotides (polymerase-like)
 * - Scaffold Around Ligand: Build scaffold around fixed ligand pose
 * - Backbone Docking: Dock ligand to existing structure
 */

import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob } from '../lib/api';
import { StructureInput } from './StructureInput';

// NTP Templates - pre-defined nucleotide SMILES
const NTP_TEMPLATES = [
    { id: 'dATP', name: 'dATP (DNA)', smiles: 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3' },
    { id: 'dTTP', name: 'dTTP (DNA)', smiles: 'Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O' },
    { id: 'dGTP', name: 'dGTP (DNA)', smiles: 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1' },
    { id: 'dCTP', name: 'dCTP (DNA)', smiles: 'Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1' },
    { id: 'ATP', name: 'ATP (RNA/Energy)', smiles: 'Nc1ncnc2c1ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O' },
    { id: 'UTP', name: 'UTP (RNA)', smiles: 'O=c1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)[nH]1' },
    { id: 'GTP', name: 'GTP (RNA)', smiles: 'Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1' },
    { id: 'CTP', name: 'CTP (RNA)', smiles: 'Nc1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)n1' },
];

type DesignMode = 'ligand_binder' | 'ntp_binder' | 'scaffold_around_ligand' | 'backbone_docking' | 'peptide_binder';
type DockingMethod = 'none' | 'diffdock' | 'unidock' | 'both';
type Protocol = 'protein-anything' | 'peptide-anything' | 'protein-small_molecule' | 'nanobody-anything';

interface BoltzGenTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, any>;
}

export function BoltzGenTemplate({ onBack, initialValues }: BoltzGenTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // Mode selection
    const [mode, setMode] = useState<DesignMode>(initialValues?.mode || 'ligand_binder');

    // Job metadata
    const [jobName, setJobName] = useState(initialValues?.name || '');

    // Ligand inputs
    const [ligandSmiles, setLigandSmiles] = useState(initialValues?.ligand_smiles || initialValues?.boltzgen_ligand_smiles || '');
    const [selectedNtp, setSelectedNtp] = useState(initialValues?.ntp_type || initialValues?.boltzgen_ntp_type || '');
    const [ligandPdb, setLigandPdb] = useState(initialValues?.ligand_pdb || '');

    // Structure inputs
    const [inputPdb, setInputPdb] = useState(initialValues?.input_pdb || initialValues?.boltzgen_input_pdb || '');

    // Design parameters
    const [scaffoldLength, setScaffoldLength] = useState(initialValues?.scaffold_length || initialValues?.boltzgen_scaffold_length || '80-120');
    const [numDesigns, setNumDesigns] = useState(initialValues?.num_designs || initialValues?.boltzgen_num_designs || 10);
    const [batchSize, setBatchSize] = useState(initialValues?.batch_size || initialValues?.boltzgen_batch_size || 1);

    // Advanced options
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [bindingSiteResidues, setBindingSiteResidues] = useState(initialValues?.binding_site_residues || initialValues?.boltzgen_binding_site_residues || '');
    const [catalyticSite, setCatalyticSite] = useState(initialValues?.catalytic_site || initialValues?.boltzgen_catalytic_site || false);
    const [dockingMethod, setDockingMethod] = useState<DockingMethod>(
        initialValues?.docking_method ||
        (initialValues?.run_docking === false ? 'none' :
            initialValues?.run_unidock && initialValues?.run_diffdock ? 'both' :
                initialValues?.run_unidock ? 'unidock' : 'diffdock')
    );

    // Filtering parameters (new)
    const [budget, setBudget] = useState<number | ''>(initialValues?.boltzgen_budget || '');
    const [alpha, setAlpha] = useState(initialValues?.boltzgen_alpha || 0.01);
    const [maxRmsd, setMaxRmsd] = useState<number | ''>(initialValues?.boltzgen_max_rmsd || '');
    const [minPlddt, setMinPlddt] = useState<number | ''>(initialValues?.boltzgen_min_plddt || 70);

    // Secondary structure and protocol
    const [secondaryStructure, setSecondaryStructure] = useState(initialValues?.boltzgen_secondary_structure || '');
    const [protocol, setProtocol] = useState<Protocol>(initialValues?.boltzgen_protocol || 'protein-anything');

    // Derived state
    const effectiveSmiles = useMemo(() => {
        if (mode === 'ntp_binder' && selectedNtp) {
            return NTP_TEMPLATES.find(ntp => ntp.id === selectedNtp)?.smiles || '';
        }
        return ligandSmiles;
    }, [mode, selectedNtp, ligandSmiles]);

    // Validation
    const isValid = useMemo(() => {
        if (!jobName.trim()) return false;
        if (mode === 'ligand_binder' && !ligandSmiles.trim()) return false;
        if (mode === 'ntp_binder' && !selectedNtp) return false;
        if (mode === 'scaffold_around_ligand' && !ligandPdb) return false;
        if (mode === 'backbone_docking' && !inputPdb) return false;
        return true;
    }, [jobName, mode, ligandSmiles, selectedNtp, ligandPdb, inputPdb]);

    // Submit mutation
    const submitMutation = useMutation({
        mutationFn: submitJob,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
        onError: (error: any) => {
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object' ? JSON.stringify(detail, null, 2) : (detail || error.message);
            window.alert('Job submission failed:\n' + message);
        }
    });

    const handleSubmit = () => {
        if (!isValid) return;

        const params: Record<string, any> = {
            diffusion_method: 'boltzgen',
            run_boltzgen_only: dockingMethod === 'none',
            run_docking: dockingMethod !== 'none',
            run_diffdock: dockingMethod === 'diffdock' || dockingMethod === 'both',
            run_unidock: dockingMethod === 'unidock' || dockingMethod === 'both',
            boltzgen_scaffold_length: scaffoldLength,
            boltzgen_num_designs: numDesigns,
            boltzgen_batch_size: batchSize,
        };

        // Mode-specific params
        if (mode === 'ntp_binder') {
            params.boltzgen_ntp_type = selectedNtp;
            params.boltzgen_catalytic_site = catalyticSite;
        } else if (mode === 'ligand_binder') {
            params.boltzgen_ligand_smiles = ligandSmiles;
        } else if (mode === 'scaffold_around_ligand') {
            params.boltzgen_ligand_pdb = ligandPdb;
        } else if (mode === 'backbone_docking') {
            params.boltzgen_input_pdb = inputPdb;
            if (effectiveSmiles) params.boltzgen_ligand_smiles = effectiveSmiles;
        }

        // Advanced options
        if (bindingSiteResidues.trim()) {
            params.boltzgen_binding_site_residues = bindingSiteResidues;
        }

        // Filtering parameters
        if (budget) params.boltzgen_budget = budget;
        params.boltzgen_alpha = alpha;
        if (maxRmsd) params.boltzgen_max_rmsd = maxRmsd;
        if (minPlddt) params.boltzgen_min_plddt = minPlddt;

        // Secondary structure and protocol
        if (secondaryStructure.trim()) {
            params.boltzgen_secondary_structure = secondaryStructure;
        }
        params.boltzgen_protocol = protocol;

        submitMutation.mutate({
            name: jobName,
            model_id: 'boltzgen',
            mode: mode,
            params
        });
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center gap-4">
                <button
                    onClick={onBack}
                    className="p-2 hover:bg-slate-700 rounded-lg text-slate-400 hover:text-white transition-colors"
                >
                    ← Back
                </button>
                <div>
                    <h2 className="text-2xl font-bold text-white">Ligand-Aware Binder Design</h2>
                    <p className="text-slate-400 text-sm">Design proteins that bind small molecules using BoltzGen</p>
                </div>
            </div>

            {/* Job Name */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <label className="block text-sm font-medium text-slate-300 mb-2">Job Name</label>
                <input
                    type="text"
                    value={jobName}
                    onChange={e => setJobName(e.target.value)}
                    placeholder="e.g., atp_binder_design_01"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 focus:border-transparent outline-none"
                />
            </div>

            {/* Mode Selection */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <label className="block text-sm font-medium text-slate-300 mb-3">Design Mode</label>
                <div className="grid grid-cols-2 gap-3">
                    {[
                        { id: 'ligand_binder', name: 'Ligand Binder', desc: 'Custom SMILES input' },
                        { id: 'ntp_binder', name: 'NTP Binder', desc: 'Nucleotide binding (DNA/RNA polymerases)' },
                        { id: 'peptide_binder', name: 'Peptide Binder', desc: 'Short peptide design (uses peptide protocol)' },
                        { id: 'scaffold_around_ligand', name: 'Scaffold Around Ligand', desc: 'Build around fixed ligand pose' },
                        { id: 'backbone_docking', name: 'Backbone Docking', desc: 'Dock to existing structure' },
                    ].map(m => (
                        <button
                            key={m.id}
                            onClick={() => setMode(m.id as DesignMode)}
                            className={`p-4 rounded-xl border text-left transition-all ${mode === m.id
                                ? 'bg-amber-500/20 border-amber-500 shadow-lg shadow-amber-500/10'
                                : 'bg-slate-900/50 border-slate-700 hover:border-slate-600'
                                }`}
                        >
                            <div className="font-medium text-white">{m.name}</div>
                            <div className="text-xs text-slate-400 mt-1">{m.desc}</div>
                        </button>
                    ))}
                </div>
            </div>

            {/* Ligand Input - varies by mode */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <h3 className="text-lg font-semibold text-white mb-4">
                    {mode === 'ntp_binder' ? 'Nucleotide Selection' :
                        mode === 'scaffold_around_ligand' ? 'Ligand Structure' :
                            mode === 'backbone_docking' ? 'Target Structure' : 'Ligand Definition'}
                </h3>

                {mode === 'ntp_binder' ? (
                    <div className="space-y-4">
                        <label className="block text-sm font-medium text-slate-300 mb-2">Select Nucleotide</label>
                        <div className="grid grid-cols-4 gap-2">
                            {NTP_TEMPLATES.map(ntp => (
                                <button
                                    key={ntp.id}
                                    onClick={() => setSelectedNtp(ntp.id)}
                                    className={`p-3 rounded-lg border text-center transition-all ${selectedNtp === ntp.id
                                        ? 'bg-amber-500/20 border-amber-500 text-amber-300'
                                        : 'bg-slate-900/50 border-slate-700 text-slate-300 hover:border-slate-600'
                                        }`}
                                >
                                    <div className="font-mono font-bold">{ntp.id}</div>
                                    <div className="text-xs text-slate-500">{ntp.name.split(' ')[0]}</div>
                                </button>
                            ))}
                        </div>
                        {selectedNtp && (
                            <div className="mt-3 p-3 bg-slate-900/50 rounded-lg">
                                <span className="text-xs text-slate-400">Selected SMILES:</span>
                                <code className="block text-xs text-amber-400 font-mono mt-1 break-all">
                                    {NTP_TEMPLATES.find(n => n.id === selectedNtp)?.smiles}
                                </code>
                            </div>
                        )}
                    </div>
                ) : mode === 'scaffold_around_ligand' ? (
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Ligand PDB Structure</label>
                        <StructureInput
                            value={ligandPdb}
                            onChange={setLigandPdb}
                            onBrowse={() => { }}
                            enableMultiSelect={false}
                            enableDirectory={false}
                        />
                        <p className="text-xs text-slate-500 mt-2">Upload a PDB file containing your ligand's 3D coordinates</p>
                    </div>
                ) : mode === 'backbone_docking' ? (
                    <div className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Protein Backbone PDB</label>
                            <StructureInput
                                value={inputPdb}
                                onChange={setInputPdb}
                                onBrowse={() => { }}
                                enableMultiSelect={false}
                                enableDirectory={false}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Ligand SMILES (optional)</label>
                            <input
                                type="text"
                                value={ligandSmiles}
                                onChange={e => setLigandSmiles(e.target.value)}
                                placeholder="e.g., CCO for ethanol"
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                            />
                        </div>
                    </div>
                ) : (
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Ligand SMILES</label>
                        <input
                            type="text"
                            value={ligandSmiles}
                            onChange={e => setLigandSmiles(e.target.value)}
                            placeholder="e.g., CCO for ethanol, or paste any valid SMILES"
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                        />
                        <p className="text-xs text-slate-500 mt-2">
                            Enter the SMILES representation of your target molecule.
                            You can generate these from ChemDraw, PubChem, or similar tools.
                        </p>
                    </div>
                )}
            </div>

            {/* Design Parameters */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <h3 className="text-lg font-semibold text-white mb-4">Design Parameters</h3>
                <div className="grid grid-cols-3 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Scaffold Length</label>
                        <input
                            type="text"
                            value={scaffoldLength}
                            onChange={e => setScaffoldLength(e.target.value)}
                            placeholder="80-120"
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                        />
                        <p className="text-xs text-slate-500 mt-1">Range or exact value</p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Number of Designs</label>
                        <input
                            type="number"
                            value={numDesigns}
                            onChange={e => setNumDesigns(parseInt(e.target.value) || 10)}
                            min={1}
                            max={100}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                        />
                        <p className="text-xs text-slate-500 mt-1">1-100 designs</p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Batch Size</label>
                        <input
                            type="number"
                            value={batchSize}
                            onChange={e => setBatchSize(parseInt(e.target.value) || 1)}
                            min={1}
                            max={32}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                        />
                        <p className="text-xs text-slate-500 mt-1">Designs per GPU pass</p>
                    </div>
                </div>
            </div>

            {/* Advanced Options */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl overflow-hidden">
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="w-full px-6 py-4 flex items-center justify-between hover:bg-slate-700/30 transition-colors"
                >
                    <span className="font-medium text-slate-300">Advanced Options</span>
                    <span className="text-slate-500">{showAdvanced ? '▲' : '▼'}</span>
                </button>
                {showAdvanced && (
                    <div className="p-6 border-t border-slate-700 space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Binding Site Residues</label>
                            <input
                                type="text"
                                value={bindingSiteResidues}
                                onChange={e => setBindingSiteResidues(e.target.value)}
                                placeholder="e.g., A:45-52,A:78-85"
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                            />
                            <p className="text-xs text-slate-500 mt-1">Optional: Specify binding pocket residue ranges</p>
                        </div>

                        <div className="flex items-center gap-6">
                            <label className="flex items-center gap-3 cursor-pointer">
                                <div className={`w-10 h-6 rounded-full p-1 transition-colors ${catalyticSite ? 'bg-amber-500' : 'bg-slate-700'}`}>
                                    <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform ${catalyticSite ? 'translate-x-4' : ''}`} />
                                </div>
                                <input type="checkbox" className="hidden" checked={catalyticSite} onChange={e => setCatalyticSite(e.target.checked)} />
                                <span className="text-sm text-slate-300">Catalytic Site (Mg2+)</span>
                            </label>
                        </div>

                        {/* Secondary Structure Constraints */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Secondary Structure</label>
                            <input
                                type="text"
                                value={secondaryStructure}
                                onChange={e => setSecondaryStructure(e.target.value)}
                                placeholder="e.g., helix:1-20,sheet:25-35,loop:40-45"
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                            />
                            <p className="text-xs text-slate-500 mt-1">Optional: Specify helix/sheet/loop regions (residue ranges)</p>
                        </div>

                        {/* Protocol Selection */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Protocol</label>
                            <select
                                value={protocol}
                                onChange={e => setProtocol(e.target.value as Protocol)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                            >
                                <option value="protein-anything">Protein (general)</option>
                                <option value="peptide-anything">Peptide (short binders)</option>
                                <option value="protein-small_molecule">Protein + Small Molecule</option>
                                <option value="nanobody-anything">Nanobody</option>
                            </select>
                            <p className="text-xs text-slate-500 mt-1">BoltzGen protocol - affects filtering and defaults</p>
                        </div>

                        {/* Docking Method Selection */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-3">Docking Validation</label>
                            <div className="grid grid-cols-4 gap-2">
                                {[
                                    { id: 'none', name: 'None', desc: 'Skip docking' },
                                    { id: 'diffdock', name: 'DiffDock', desc: 'ML-based' },
                                    { id: 'unidock', name: 'Uni-Dock', desc: 'GPU AutoDock' },
                                    { id: 'both', name: 'Both', desc: 'Compare engines' },
                                ].map(m => (
                                    <button
                                        key={m.id}
                                        type="button"
                                        onClick={() => setDockingMethod(m.id as DockingMethod)}
                                        className={`p-3 rounded-lg border text-center transition-all ${dockingMethod === m.id
                                            ? 'bg-amber-500/20 border-amber-500 text-amber-300'
                                            : 'bg-slate-900/50 border-slate-700 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="font-medium text-sm">{m.name}</div>
                                        <div className="text-xs text-slate-500">{m.desc}</div>
                                    </button>
                                ))}
                            </div>
                            <p className="text-xs text-slate-500 mt-2">
                                {dockingMethod === 'both'
                                    ? 'Both engines will run - compare poses and scores for validation'
                                    : dockingMethod === 'unidock'
                                        ? 'Uni-Dock: GPU-accelerated AutoDock Vina for fast screening'
                                        : dockingMethod === 'diffdock'
                                            ? 'DiffDock: Diffusion-based ML docking with confidence scores'
                                            : 'No docking validation - designs will be filtered by BoltzGen metrics only'}
                            </p>
                        </div>

                        {/* Filtering Parameters */}
                        <div className="border-t border-slate-700 pt-4 mt-4">
                            <label className="block text-sm font-medium text-slate-300 mb-3">Filtering Parameters</label>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Budget (Final Count)</label>
                                    <input
                                        type="number"
                                        value={budget}
                                        onChange={e => setBudget(e.target.value ? parseInt(e.target.value) : '')}
                                        placeholder="e.g., 50"
                                        min={1}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                    />
                                    <p className="text-xs text-slate-500 mt-1">Final designs after diversity selection</p>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Min pLDDT</label>
                                    <input
                                        type="number"
                                        value={minPlddt}
                                        onChange={e => setMinPlddt(e.target.value ? parseInt(e.target.value) : '')}
                                        placeholder="70"
                                        min={0}
                                        max={100}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                    />
                                    <p className="text-xs text-slate-500 mt-1">Structure confidence threshold</p>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Max RMSD (A)</label>
                                    <input
                                        type="number"
                                        value={maxRmsd}
                                        onChange={e => setMaxRmsd(e.target.value ? parseFloat(e.target.value) : '')}
                                        placeholder="e.g., 2.0"
                                        min={0}
                                        step={0.1}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                    />
                                    <p className="text-xs text-slate-500 mt-1">Refolding RMSD threshold</p>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Diversity Alpha: {alpha.toFixed(2)}</label>
                                    <input
                                        type="range"
                                        value={alpha}
                                        onChange={e => setAlpha(parseFloat(e.target.value))}
                                        min={0}
                                        max={1}
                                        step={0.01}
                                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                    />
                                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                                        <span>Quality</span>
                                        <span>Diversity</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Submit */}
            <div className="flex justify-end gap-3">
                <button
                    onClick={onBack}
                    className="px-6 py-3 rounded-lg bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={!isValid || submitMutation.isPending}
                    className="px-8 py-3 rounded-lg bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-400 hover:to-orange-400 text-white font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg shadow-amber-500/20"
                >
                    {submitMutation.isPending ? 'Submitting...' : 'Launch BoltzGen'}
                </button>
            </div>
        </div>
    );
}

export default BoltzGenTemplate;
