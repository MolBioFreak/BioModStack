import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob } from '../lib/api';
import { useNavigate } from 'react-router-dom';

interface AntibodyDenovoTemplateProps {
    onBack: () => void;
}

export const AntibodyDenovoTemplate: React.FC<AntibodyDenovoTemplateProps> = ({ onBack }) => {
    const [jobName, setJobName] = useState('antibody_design');
    const [targetPdb, setTargetPdb] = useState<File | null>(null);
    const [epitopeResidues, setEpitopeResidues] = useState('');
    const [numDesigns, setNumDesigns] = useState(10);
    const [seqDesigner, setSeqDesigner] = useState<'fampnn' | 'antifold' | 'proteinmpnn'>('fampnn');
    const [useAntiberty, setUseAntiberty] = useState(true);
    const [useThermoMPNN, setUseThermoMPNN] = useState(true);

    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const submitMutation = useMutation({
        mutationFn: async (data: any) => submitJob(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        }
    });

    const handleSubmit = async () => {
        if (!targetPdb) {
            alert('Please upload a target PDB file');
            return;
        }
        if (!epitopeResidues.trim()) {
            alert('Please specify epitope residues (e.g., A:45-60,A:100-115)');
            return;
        }

        // For now, submit as a simple job - the full implementation will need file upload handling
        try {
            await submitMutation.mutateAsync({
                name: jobName,
                model_id: 'rfantibody',
                mode: 'antibody_denovo_pipeline',
                params: {
                    target_pdb: targetPdb.name, // Will need proper file upload
                    epitope_residues: epitopeResidues,
                    num_designs: numDesigns,
                    sequence_designer: seqDesigner,
                    enable_antiberty: useAntiberty,
                    enable_thermompnn: useThermoMPNN
                }
            });
        } catch (error) {
            console.error('[ANTIBODY_DENOVO] Submission failed', error);
        }
    };

    return (
        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <button
                        onClick={onBack}
                        className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
                    >
                        ← Back
                    </button>
                    <div>
                        <h2 className="text-lg font-semibold text-slate-200">De Novo Antibody Design</h2>
                        <p className="text-sm text-slate-500">Generate novel antibodies targeting an antigen</p>
                    </div>
                </div>
            </div>

            {/* Pipeline Visualization */}
            <div className="mb-6 p-4 bg-slate-900/50 rounded-lg border border-slate-700/50">
                <h3 className="text-sm font-medium text-slate-400 mb-3">Workflow Pipeline</h3>
                <div className="flex items-center gap-2 flex-wrap">
                    <div className="bg-emerald-500/20 text-emerald-400 px-3 py-1.5 rounded-lg text-sm font-medium">
                        1. RFantibody
                    </div>
                    <span className="text-slate-600">→</span>
                    <div className="bg-blue-500/20 text-blue-400 px-3 py-1.5 rounded-lg text-sm font-medium">
                        2. {seqDesigner.toUpperCase()}
                    </div>
                    <span className="text-slate-600">→</span>
                    <div className="bg-purple-500/20 text-purple-400 px-3 py-1.5 rounded-lg text-sm font-medium">
                        3. Boltz2
                    </div>
                    {useAntiberty && (
                        <>
                            <span className="text-slate-600">→</span>
                            <div className="bg-amber-500/20 text-amber-400 px-3 py-1.5 rounded-lg text-sm font-medium">
                                4. AntiBERTy
                            </div>
                        </>
                    )}
                    {useThermoMPNN && (
                        <>
                            <span className="text-slate-600">→</span>
                            <div className="bg-rose-500/20 text-rose-400 px-3 py-1.5 rounded-lg text-sm font-medium">
                                5. ThermoMPNN
                            </div>
                        </>
                    )}
                </div>
            </div>

            {/* Form */}
            <div className="space-y-6">
                {/* Job Name */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Job Name</label>
                    <input
                        type="text"
                        value={jobName}
                        onChange={(e) => setJobName(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder="antibody_design"
                    />
                </div>

                {/* Target PDB Upload */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Target Antigen PDB</label>
                    <div className="flex items-center gap-4">
                        <input
                            type="file"
                            accept=".pdb,.cif"
                            onChange={(e) => setTargetPdb(e.target.files?.[0] || null)}
                            className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer"
                        />
                        {targetPdb && (
                            <span className="text-sm text-emerald-400">✓ {targetPdb.name}</span>
                        )}
                    </div>
                    <p className="mt-1 text-xs text-slate-500">Upload the antigen structure you want to design antibodies against</p>
                </div>

                {/* Epitope Residues */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Epitope Residues (Hotspots)</label>
                    <input
                        type="text"
                        value={epitopeResidues}
                        onChange={(e) => setEpitopeResidues(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder="A:45-60,A:100-115"
                    />
                    <p className="mt-1 text-xs text-slate-500">Specify residues on the antigen to target (format: Chain:Start-End)</p>
                </div>

                {/* Number of Designs */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Number of Designs</label>
                    <input
                        type="number"
                        value={numDesigns}
                        onChange={(e) => setNumDesigns(parseInt(e.target.value) || 10)}
                        min={1}
                        max={100}
                        className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                    />
                </div>

                {/* Sequence Designer */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Sequence Designer</label>
                    <div className="flex gap-3">
                        {(['fampnn', 'antifold', 'proteinmpnn'] as const).map((designer) => (
                            <button
                                key={designer}
                                onClick={() => setSeqDesigner(designer)}
                                className={`px-4 py-2 rounded-lg font-medium transition-all ${seqDesigner === designer
                                    ? 'bg-blue-600 text-white'
                                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                            >
                                {designer.toUpperCase()}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Validation Options */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Validation Steps</label>
                    <div className="flex gap-4">
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useAntiberty}
                                onChange={(e) => setUseAntiberty(e.target.checked)}
                                className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-slate-300">AntiBERTy (Immunogenicity)</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useThermoMPNN}
                                onChange={(e) => setUseThermoMPNN(e.target.checked)}
                                className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-slate-300">ThermoMPNN (Stability)</span>
                        </label>
                    </div>
                </div>
            </div>

            {/* Submit Button */}
            <div className="mt-8 flex justify-end">
                <button
                    onClick={handleSubmit}
                    disabled={submitMutation.isPending || !targetPdb}
                    className="px-6 py-3 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-500 text-white font-medium rounded-lg transition-colors flex items-center gap-2"
                >
                    {submitMutation.isPending ? (
                        <>
                            <span className="animate-spin">⚙️</span>
                            Submitting...
                        </>
                    ) : (
                        <>
                            🧬 Generate Antibodies
                        </>
                    )}
                </button>
            </div>
        </div>
    );
};

export default AntibodyDenovoTemplate;
