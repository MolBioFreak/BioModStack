import { useState, useEffect } from 'react';
import { OligoBuilderModal } from './OligoBuilderModal';
import { fetchJobs, fetchDesigns, type Job } from '../lib/api';
import { AVAILABLE_LIGANDS, componentIdFromIndex } from './ligandSelectorData';

export interface LigandEntry {
    id: string;
    type: 'ligand' | 'ion' | 'dna' | 'rna' | 'peptide' | 'protein';
    ccd?: string;
    smiles?: string;
    sequence?: string;  // For DNA/RNA/peptide sequences
    name: string;
}

export interface LigandSelectorProps {
    ligands: LigandEntry[];
    setLigands: React.Dispatch<React.SetStateAction<LigandEntry[]>>;
    showCustomSmiles?: boolean;
    onImportProtein?: () => void;  // Callback to open sequence library/import modal for adding protein
}

// Helper function to generate reverse complement
const reverseComplement = (seq: string, isRna: boolean = false): string => {
    const complement: Record<string, string> = isRna
        ? { 'A': 'U', 'U': 'A', 'C': 'G', 'G': 'C' }
        : { 'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C' };
    return seq.split('').reverse().map(base => complement[base] || base).join('');
};

export function LigandSelector({ ligands, setLigands, showCustomSmiles = false, onImportProtein }: LigandSelectorProps) {
    const [customSmiles, setCustomSmiles] = useState('');
    const [customName, setCustomName] = useState('');
    const [presetComponentCopies, setPresetComponentCopies] = useState(1);
    const [dnaSequence, setDnaSequence] = useState('');
    const [rnaSequence, setRnaSequence] = useState('');
    const [isDsDna, setIsDsDna] = useState(false);
    const [isDsRna, setIsDsRna] = useState(false);
    const [peptideSequence, setPeptideSequence] = useState('');
    const [proteinSequence, setProteinSequence] = useState('');
    const [proteinName, setProteinName] = useState('');
    const [showOligoBuilder, setShowOligoBuilder] = useState(false);

    // Import from Oligo Designer state
    const [showImportPicker, setShowImportPicker] = useState(false);
    const [oligoJobs, setOligoJobs] = useState<Job[]>([]);
    const [importLoading, setImportLoading] = useState(false);
    const [importError, setImportError] = useState<string | null>(null);

    // Fetch completed oligo_design jobs when picker opens
    useEffect(() => {
        if (!showImportPicker) return;
        setImportLoading(true);
        setImportError(null);
        fetchJobs({ status: 'completed', limit: 50, summary: true })
            .then(res => {
                const oligoOnly = res.data.jobs.filter(j => j.model_id === 'oligo_design');
                setOligoJobs(oligoOnly);
                if (oligoOnly.length === 0) setImportError('No completed Oligo Designer jobs found');
            })
            .catch(() => setImportError('Failed to fetch jobs'))
            .finally(() => setImportLoading(false));
    }, [showImportPicker]);

    const importFromJob = async (job: Job) => {
        setImportLoading(true);
        setImportError(null);
        try {
            const res = await fetchDesigns({ job_id: job.id, limit: 50 });
            const designs = res.data.designs;
            if (designs.length === 0) {
                setImportError('No designs found in this job');
                return;
            }
            // Import the first design's chain info — extract NA sequence from name/params
            // The design name typically contains the sequence type
            const newEntries: LigandEntry[] = [];
            for (const design of designs.slice(0, 5)) { // limit to 5
                // Detect type from job params or design name
                const naType: 'dna' | 'rna' =
                    design.name?.toLowerCase().includes('rna') ||
                        job.params?.rfdpoly_polymer_chains?.includes('rna')
                        ? 'rna' : 'dna';
                // Use the design name as identifier
                newEntries.push({
                    id: String.fromCharCode(66 + ligands.length + newEntries.length),
                    type: naType,
                    sequence: '', // Will be populated from PDB if available
                    name: `${design.name} (from ${job.name})`
                });
            }
            if (newEntries.length > 0) {
                setLigands(prev => [...prev, ...newEntries]);
                setShowImportPicker(false);
            }
        } catch {
            setImportError('Failed to fetch designs from job');
        } finally {
            setImportLoading(false);
        }
    };

    const addNucleicAcid = (type: 'dna' | 'rna', sequence: string, isDoubleStranded: boolean) => {
        if (sequence.trim()) {
            const isRna = type === 'rna';
            const validChars = isRna ? /[^AUCG]/g : /[^ATCG]/g;
            const validSeq = sequence.toUpperCase().replace(validChars, '');
            if (validSeq.length > 0) {
                const baseId = ligands.length;
                // Add template strand
                setLigands(prev => {
                    const newLigands = [...prev, {
                        id: componentIdFromIndex(1 + baseId),
                        type: type,
                        sequence: validSeq,
                        name: `${type.toUpperCase()} 5'→3' (${validSeq.length}nt)`
                    }];
                    // Add complementary strand for double-stranded
                    if (isDoubleStranded) {
                        const complement = reverseComplement(validSeq, isRna);
                        newLigands.push({
                            id: componentIdFromIndex(1 + baseId + 1),
                            type: type,
                            sequence: complement,
                            name: `${type.toUpperCase()} 3'→5' (${complement.length}nt)`
                        });
                    }
                    return newLigands;
                });
                if (type === 'dna') setDnaSequence('');
                else setRnaSequence('');
            }
        }
    };

    const addPeptide = () => {
        const validSeq = peptideSequence.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, '');
        if (validSeq.length >= 3 && validSeq.length <= 15) {
            setLigands(prev => [...prev, {
                id: componentIdFromIndex(1 + prev.length),
                type: 'peptide',
                sequence: validSeq,
                name: `Peptide (${validSeq.length}aa)`
            }]);
            setPeptideSequence('');
        }
    };

    const addProtein = () => {
        const validSeq = proteinSequence.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, '');
        if (validSeq.length >= 16) {
            setLigands(prev => [...prev, {
                id: componentIdFromIndex(1 + prev.length),
                type: 'protein',
                sequence: validSeq,
                name: proteinName.trim() || `Protein Chain (${validSeq.length}aa)`
            }]);
            setProteinSequence('');
            setProteinName('');
        }
    };


    const addLigandByCcd = (ccd: string, copies: number = 1) => {
        const selected = AVAILABLE_LIGANDS.find(l => l.ccd === ccd);
        const normalizedCopies = Number.isFinite(copies) ? Math.max(1, Math.min(12, Math.floor(copies))) : 1;
        if (selected) {
            setLigands(prev => {
                const next = [...prev];
                for (let idx = 0; idx < normalizedCopies; idx += 1) {
                    const displaySuffix = normalizedCopies > 1 ? ` #${idx + 1}` : '';
                    next.push({
                        id: componentIdFromIndex(1 + next.length),
                        type: selected.type,
                        ccd: selected.ccd,
                        name: `${selected.name}${displaySuffix}`,
                    });
                }
                return next;
            });
        }
    };

    const addCustomSmiles = () => {
        if (customSmiles.trim()) {
            setLigands(prev => [...prev, {
                id: componentIdFromIndex(1 + prev.length),
                type: 'ligand',
                smiles: customSmiles.trim(),
                name: customName.trim() || `Custom (${customSmiles.slice(0, 15)}...)`
            }]);
            setCustomSmiles('');
            setCustomName('');
        }
    };

    const removeLigand = (idx: number) => {
        setLigands(prev => prev.filter((_, i) => i !== idx));
    };

    return (
        <section className="pt-6 border-t border-slate-800">
            <div className="flex justify-between items-center mb-4">
                <div>
                    <h3 className="text-sm font-semibold text-slate-200">Complex Components</h3>
                    <p className="text-xs text-slate-500">Add DNA, RNA, ligands, or ions to the prediction</p>
                </div>
            </div>

            <div className="flex gap-4 items-start flex-wrap">
                {/* Ligand Dropdown */}
                <select
                    onChange={(e) => {
                        addLigandByCcd(e.target.value, presetComponentCopies);
                        e.target.value = '';
                    }}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm flex-1 min-w-[200px]"
                    defaultValue=""
                >
                    <option value="" disabled>+ Add Ligand/Ion...</option>
                    <optgroup label="Nucleotides">
                        {AVAILABLE_LIGANDS.filter(l => ['ATP', 'ADP', 'GTP', 'GDP', 'NAD', 'FAD', 'FMN', 'COA'].includes(l.ccd)).map(l => (
                            <option key={l.ccd} value={l.ccd}>{l.name}</option>
                        ))}
                    </optgroup>
                    <optgroup label="Ions">
                        {AVAILABLE_LIGANDS.filter(l => l.type === 'ion').map(l => (
                            <option key={l.ccd} value={l.ccd}>{l.name}</option>
                        ))}
                    </optgroup>
                    <optgroup label="Other Cofactors">
                        {AVAILABLE_LIGANDS.filter(l => l.ccd === 'HEM').map(l => (
                            <option key={l.ccd} value={l.ccd}>{l.name}</option>
                        ))}
                    </optgroup>
                </select>
                <div className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-2 py-2">
                    <span className="text-xs text-slate-400">copies</span>
                    <input
                        type="number"
                        min={1}
                        max={12}
                        step={1}
                        value={presetComponentCopies}
                        onChange={(e) => setPresetComponentCopies(Math.max(1, Math.min(12, parseInt(e.target.value || '1', 10) || 1)))}
                        className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-center text-sm text-white"
                    />
                </div>

                {/* Custom SMILES Input (Optional) */}
                {showCustomSmiles && (
                    <div className="flex gap-2 items-center">
                        <input
                            type="text"
                            value={customSmiles}
                            onChange={(e) => setCustomSmiles(e.target.value)}
                            placeholder="Custom SMILES..."
                            className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm w-48"
                        />
                        <input
                            type="text"
                            value={customName}
                            onChange={(e) => setCustomName(e.target.value)}
                            placeholder="Name (optional)"
                            className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm w-32"
                        />
                        <button
                            onClick={addCustomSmiles}
                            disabled={!customSmiles.trim()}
                            className="px-3 py-2 bg-emerald-500/20 text-emerald-400 rounded-lg text-sm hover:bg-emerald-500/30 transition-colors disabled:opacity-50"
                        >
                            + Add
                        </button>
                    </div>
                )}
            </div>

            {/* DNA/RNA Sequence Input */}
            <div className="mt-4 space-y-3">
                {/* DNA Input with SS/DS Toggle */}
                <div className="flex gap-2 items-center">
                    <span className="text-xs text-blue-400 w-12">DNA:</span>
                    <div className="flex items-center gap-1 bg-slate-800 rounded-lg p-0.5">
                        <button
                            onClick={() => setIsDsDna(false)}
                            className={`px-2 py-1 text-xs rounded transition-colors ${!isDsDna ? 'bg-blue-500 text-white' : 'text-slate-400 hover:text-white'}`}
                        >SS</button>
                        <button
                            onClick={() => setIsDsDna(true)}
                            className={`px-2 py-1 text-xs rounded transition-colors ${isDsDna ? 'bg-blue-500 text-white' : 'text-slate-400 hover:text-white'}`}
                        >DS</button>
                    </div>
                    <input
                        type="text"
                        value={dnaSequence}
                        onChange={(e) => setDnaSequence(e.target.value.toUpperCase().replace(/[^ATCG]/g, ''))}
                        placeholder={isDsDna ? "Template strand 5'→3' (A, T, C, G)..." : "DNA sequence (A, T, C, G)..."}
                        className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm flex-1 font-mono"
                    />
                    <button
                        onClick={() => addNucleicAcid('dna', dnaSequence, isDsDna)}
                        disabled={!dnaSequence.trim()}
                        className="px-3 py-2 bg-blue-500/20 text-blue-400 rounded-lg text-sm hover:bg-blue-500/30 transition-colors disabled:opacity-50"
                    >
                        + Add {isDsDna ? 'dsDNA' : 'ssDNA'}
                    </button>
                </div>

                {/* RNA Input with SS/DS Toggle */}
                <div className="flex gap-2 items-center">
                    <span className="text-xs text-accent w-12">RNA:</span>
                    <div className="flex items-center gap-1 bg-slate-800 rounded-lg p-0.5">
                        <button
                            onClick={() => setIsDsRna(false)}
                            className={`px-2 py-1 text-xs rounded transition-colors ${!isDsRna ? 'bg-accent text-white' : 'text-slate-400 hover:text-white'}`}
                        >SS</button>
                        <button
                            onClick={() => setIsDsRna(true)}
                            className={`px-2 py-1 text-xs rounded transition-colors ${isDsRna ? 'bg-accent text-white' : 'text-slate-400 hover:text-white'}`}
                        >DS</button>
                    </div>
                    <input
                        type="text"
                        value={rnaSequence}
                        onChange={(e) => setRnaSequence(e.target.value.toUpperCase().replace(/[^AUCG]/g, ''))}
                        placeholder={isDsRna ? "Template strand 5'→3' (A, U, C, G)..." : "RNA sequence (A, U, C, G)..."}
                        className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm flex-1 font-mono"
                    />
                    <button
                        onClick={() => addNucleicAcid('rna', rnaSequence, isDsRna)}
                        disabled={!rnaSequence.trim()}
                        className="px-3 py-2 bg-accent/20 text-accent rounded-lg text-sm hover:bg-accent/30 transition-colors disabled:opacity-50"
                    >
                        + Add {isDsRna ? 'dsRNA' : 'ssRNA'}
                    </button>
                </div>

                {/* Peptide Input */}
                <div className="flex gap-2 items-center">
                    <span className="text-xs text-emerald-400 w-12">Peptide:</span>
                    <input
                        type="text"
                        value={peptideSequence}
                        onChange={(e) => setPeptideSequence(e.target.value.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, ''))}
                        placeholder="Peptide sequence (3-15 AA)..."
                        className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm flex-1 font-mono"
                        maxLength={15}
                    />
                    <button
                        onClick={addPeptide}
                        disabled={peptideSequence.length < 3 || peptideSequence.length > 15}
                        className="px-3 py-2 bg-emerald-500/20 text-emerald-400 rounded-lg text-sm hover:bg-emerald-500/30 transition-colors disabled:opacity-50"
                    >
                        + Add Peptide
                    </button>
                </div>

                {/* Protein Chain Input */}
                <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-2">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-accent font-semibold">Additional Protein Chain</span>
                            <span className="text-xs text-content-muted">(for protein-protein complex prediction)</span>
                        </div>
                        {onImportProtein && (
                            <button
                                onClick={onImportProtein}
                                className="px-3 py-1.5 bg-surface-secondary hover:bg-surface-tertiary border border-accent/20 text-content text-xs rounded-lg transition-colors flex items-center gap-1.5"
                            >
                                Select Input / Import
                            </button>
                        )}
                    </div>
                    <div className="flex gap-2 items-start">
                        <input
                            type="text"
                            value={proteinName}
                            onChange={(e) => setProteinName(e.target.value)}
                            placeholder="Chain name (optional)"
                            className="bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm w-40"
                        />
                        <textarea
                            value={proteinSequence}
                            onChange={(e) => setProteinSequence(e.target.value.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, ''))}
                            placeholder="Additional protein sequence (16+ AA)..."
                            className="bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm flex-1 font-mono resize-y min-h-[60px]"
                            rows={2}
                        />
                        <button
                            onClick={addProtein}
                            disabled={proteinSequence.length < 16}
                            className="px-3 py-2 bg-accent-secondary/20 text-accent-secondary rounded-lg text-sm hover:bg-accent-secondary/30 transition-colors disabled:opacity-50 whitespace-nowrap"
                        >
                            + Add Protein
                        </button>
                    </div>
                    {proteinSequence.length > 0 && (
                        <div className="text-xs text-content-muted">
                            {proteinSequence.length} aa {proteinSequence.length < 16 && <span className="text-[var(--warning)]">(min 16 required)</span>}
                        </div>
                    )}
                </div>

                {/* Advanced Oligo Builder Section */}
                <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-2">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-accent font-semibold">Advanced Oligo Builder</span>
                            <span className="text-xs text-content-muted">(custom overhangs, mismatches, gaps)</span>
                        </div>
                        <button
                            onClick={() => setShowOligoBuilder(true)}
                            className="px-3 py-1.5 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                        >
                            Open Builder
                        </button>
                    </div>
                </div>

                {/* Import from Oligo Designer */}
                <div className="p-3 bg-surface-tertiary rounded-lg border border-blue-500/20 space-y-2">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-blue-400 font-semibold">Import Designed Oligo</span>
                            <span className="text-xs text-content-muted">(from RFDpoly/NA-MPNN jobs)</span>
                        </div>
                        <button
                            onClick={() => setShowImportPicker(!showImportPicker)}
                            className={`px-3 py-1.5 text-white text-xs rounded-lg transition-colors ${showImportPicker
                                    ? 'bg-blue-600 hover:bg-blue-500'
                                    : 'bg-blue-500/20 text-blue-400 border border-blue-500/30 hover:bg-blue-500/30'
                                }`}
                        >
                            {showImportPicker ? 'Close' : 'Browse Jobs'}
                        </button>
                    </div>
                    {showImportPicker && (
                        <div className="mt-2 space-y-2">
                            {importLoading && (
                                <div className="text-xs text-slate-400 animate-pulse">Loading completed oligo jobs...</div>
                            )}
                            {importError && (
                                <div className="text-xs text-amber-400">{importError}</div>
                            )}
                            {!importLoading && oligoJobs.length > 0 && (
                                <div className="max-h-48 overflow-y-auto space-y-1">
                                    {oligoJobs.map(job => (
                                        <button
                                            key={job.id}
                                            onClick={() => importFromJob(job)}
                                            className="w-full text-left px-3 py-2 rounded bg-slate-800/50 hover:bg-slate-700/50 transition-colors"
                                        >
                                            <div className="text-sm text-white">{job.name}</div>
                                            <div className="text-xs text-slate-400 flex gap-3">
                                                <span>{job.design_count} design{job.design_count !== 1 ? 's' : ''}</span>
                                                <span>{new Date(job.created_at).toLocaleDateString()}</span>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Oligo Builder Modal */}
            <OligoBuilderModal
                isOpen={showOligoBuilder}
                onClose={() => setShowOligoBuilder(false)}
                onSubmit={(entries: LigandEntry[]) => {
                    setLigands(prev => [...prev, ...entries]);
                    setShowOligoBuilder(false);
                }}
                ligandCount={ligands.length}
            />

            {/* Selected Ligands Pills */}
            {ligands.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                    {ligands.map((lig, idx) => (
                        <div
                            key={idx}
                            className={`px-3 py-1.5 rounded-full text-sm flex items-center gap-2 ${lig.type === 'dna'
                                ? 'bg-accent/20 text-accent border border-accent/30'
                                : lig.type === 'rna'
                                    ? 'bg-accent-secondary/20 text-accent-secondary border border-accent-secondary/30'
                                    : lig.type === 'ion'
                                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                        : lig.type === 'protein'
                                            ? 'bg-accent/20 text-accent border border-accent/30'
                                            : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                }`}
                        >
                            <span className="font-mono text-xs opacity-60">[{lig.id}]</span>
                            <span>{lig.name || lig.ccd}</span>
                            {lig.ccd && lig.name && lig.name !== lig.ccd && (
                                <span className="text-xs opacity-60">({lig.ccd})</span>
                            )}
                            {lig.smiles && <span className="text-xs opacity-60">(SMILES)</span>}
                            {lig.sequence && <span className="text-xs opacity-60">({lig.type === 'protein' || lig.type === 'peptide' ? `${lig.sequence.length}aa` : `${lig.sequence.length}nt`})</span>}
                            <button
                                onClick={() => removeLigand(idx)}
                                className="hover:text-[var(--error)] transition-colors"
                            >×</button>
                        </div>
                    ))}
                    {ligands.length > 0 && (
                        <button
                            onClick={() => setLigands([])}
                            className="text-xs text-content-muted hover:text-[var(--error)] transition-colors"
                        >
                            Clear All
                        </button>
                    )}
                </div>
            )}
        </section>
    );

}
