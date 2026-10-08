import { useState } from 'react';
import { OligoBuilderModal } from './OligoBuilderModal';

export interface LigandEntry {
    id: string;
    type: 'ligand' | 'ion' | 'dna' | 'rna' | 'peptide';
    ccd?: string;
    smiles?: string;
    sequence?: string;  // For DNA/RNA/peptide sequences
    name: string;
}

export interface LigandSelectorProps {
    ligands: LigandEntry[];
    setLigands: React.Dispatch<React.SetStateAction<LigandEntry[]>>;
    showCustomSmiles?: boolean;
}

const AVAILABLE_LIGANDS = [
    { ccd: 'ATP', name: 'ATP (Adenosine Triphosphate)', type: 'ligand' as const },
    { ccd: 'ADP', name: 'ADP (Adenosine Diphosphate)', type: 'ligand' as const },
    { ccd: 'GTP', name: 'GTP (Guanosine Triphosphate)', type: 'ligand' as const },
    { ccd: 'GDP', name: 'GDP (Guanosine Diphosphate)', type: 'ligand' as const },
    { ccd: 'NAD', name: 'NAD⁺ (Nicotinamide Adenine Dinucleotide)', type: 'ligand' as const },
    { ccd: 'FAD', name: 'FAD (Flavin Adenine Dinucleotide)', type: 'ligand' as const },
    { ccd: 'HEM', name: 'Heme', type: 'ligand' as const },
    { ccd: 'FMN', name: 'FMN (Flavin Mononucleotide)', type: 'ligand' as const },
    { ccd: 'COA', name: 'Coenzyme A', type: 'ligand' as const },
    { ccd: 'MG', name: 'Mg²⁺ (Magnesium Ion)', type: 'ion' as const },
    { ccd: 'CA', name: 'Ca²⁺ (Calcium Ion)', type: 'ion' as const },
    { ccd: 'ZN', name: 'Zn²⁺ (Zinc Ion)', type: 'ion' as const },
    { ccd: 'FE', name: 'Fe²⁺ (Iron Ion)', type: 'ion' as const },
    { ccd: 'MN', name: 'Mn²⁺ (Manganese Ion)', type: 'ion' as const },
    { ccd: 'CU', name: 'Cu²⁺ (Copper Ion)', type: 'ion' as const },
    { ccd: 'K', name: 'K⁺ (Potassium Ion)', type: 'ion' as const },
    { ccd: 'NA', name: 'Na⁺ (Sodium Ion)', type: 'ion' as const },
    { ccd: 'CL', name: 'Cl⁻ (Chloride Ion)', type: 'ion' as const },
];

// Helper function to generate reverse complement
const reverseComplement = (seq: string, isRna: boolean = false): string => {
    const complement: Record<string, string> = isRna
        ? { 'A': 'U', 'U': 'A', 'C': 'G', 'G': 'C' }
        : { 'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C' };
    return seq.split('').reverse().map(base => complement[base] || base).join('');
};

export function LigandSelector({ ligands, setLigands, showCustomSmiles = false }: LigandSelectorProps) {
    const [customSmiles, setCustomSmiles] = useState('');
    const [customName, setCustomName] = useState('');
    const [dnaSequence, setDnaSequence] = useState('');
    const [rnaSequence, setRnaSequence] = useState('');
    const [isDsDna, setIsDsDna] = useState(false);
    const [isDsRna, setIsDsRna] = useState(false);
    const [peptideSequence, setPeptideSequence] = useState('');
    const [showOligoBuilder, setShowOligoBuilder] = useState(false);

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
                        id: String.fromCharCode(66 + baseId), // B, C, D...
                        type: type,
                        sequence: validSeq,
                        name: `${type.toUpperCase()} 5'→3' (${validSeq.length}nt)`
                    }];
                    // Add complementary strand for double-stranded
                    if (isDoubleStranded) {
                        const complement = reverseComplement(validSeq, isRna);
                        newLigands.push({
                            id: String.fromCharCode(66 + baseId + 1),
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
                id: String.fromCharCode(66 + prev.length),
                type: 'peptide',
                sequence: validSeq,
                name: `Peptide (${validSeq.length}aa)`
            }]);
            setPeptideSequence('');
        }
    };


    const addLigandByCcd = (ccd: string) => {
        const selected = AVAILABLE_LIGANDS.find(l => l.ccd === ccd);
        if (selected && !ligands.find(l => l.ccd === selected.ccd)) {
            setLigands(prev => [...prev, {
                id: String.fromCharCode(66 + prev.length), // B, C, D...
                type: selected.type,
                ccd: selected.ccd,
                name: selected.name
            }]);
        }
    };

    const addCustomSmiles = () => {
        if (customSmiles.trim()) {
            setLigands(prev => [...prev, {
                id: String.fromCharCode(66 + prev.length),
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
                        addLigandByCcd(e.target.value);
                        e.target.value = '';
                    }}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm flex-1 min-w-[200px]"
                    defaultValue=""
                >
                    <option value="" disabled>+ Add Ligand/Ion...</option>
                    <optgroup label="Nucleotides">
                        {AVAILABLE_LIGANDS.filter(l => ['ATP', 'ADP', 'GTP', 'GDP', 'NAD', 'FAD', 'FMN', 'COA'].includes(l.ccd)).map(l => (
                            <option key={l.ccd} value={l.ccd} disabled={ligands.some(sl => sl.ccd === l.ccd)}>{l.name}</option>
                        ))}
                    </optgroup>
                    <optgroup label="Ions">
                        {AVAILABLE_LIGANDS.filter(l => l.type === 'ion').map(l => (
                            <option key={l.ccd} value={l.ccd} disabled={ligands.some(sl => sl.ccd === l.ccd)}>{l.name}</option>
                        ))}
                    </optgroup>
                    <optgroup label="Other Cofactors">
                        {AVAILABLE_LIGANDS.filter(l => l.ccd === 'HEM').map(l => (
                            <option key={l.ccd} value={l.ccd} disabled={ligands.some(sl => sl.ccd === l.ccd)}>{l.name}</option>
                        ))}
                    </optgroup>
                </select>

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
                    <span className="text-xs text-purple-400 w-12">RNA:</span>
                    <div className="flex items-center gap-1 bg-slate-800 rounded-lg p-0.5">
                        <button
                            onClick={() => setIsDsRna(false)}
                            className={`px-2 py-1 text-xs rounded transition-colors ${!isDsRna ? 'bg-purple-500 text-white' : 'text-slate-400 hover:text-white'}`}
                        >SS</button>
                        <button
                            onClick={() => setIsDsRna(true)}
                            className={`px-2 py-1 text-xs rounded transition-colors ${isDsRna ? 'bg-purple-500 text-white' : 'text-slate-400 hover:text-white'}`}
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
                        className="px-3 py-2 bg-purple-500/20 text-purple-400 rounded-lg text-sm hover:bg-purple-500/30 transition-colors disabled:opacity-50"
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

                {/* Advanced Oligo Builder Button */}
                <button
                    onClick={() => setShowOligoBuilder(true)}
                    className="w-full py-2 border border-dashed border-slate-600 rounded-lg text-slate-400 hover:text-blue-400 hover:border-blue-500 transition-colors text-sm"
                >
                    🧬 Advanced Oligo Builder (custom overhangs, mismatches, gaps)
                </button>
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
                                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                : lig.type === 'rna'
                                    ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30'
                                    : lig.type === 'ion'
                                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                        : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                }`}
                        >
                            <span className="font-mono text-xs opacity-60">[{lig.id}]</span>
                            <span>{lig.ccd || lig.name}</span>
                            {lig.smiles && <span className="text-xs opacity-60">(SMILES)</span>}
                            {lig.sequence && <span className="text-xs opacity-60">({lig.sequence.length}nt)</span>}
                            <button
                                onClick={() => removeLigand(idx)}
                                className="hover:text-red-400 transition-colors"
                            >×</button>
                        </div>
                    ))}
                    {ligands.length > 0 && (
                        <button
                            onClick={() => setLigands([])}
                            className="text-xs text-slate-500 hover:text-red-400 transition-colors"
                        >
                            Clear All
                        </button>
                    )}
                </div>
            )}
        </section>
    );
}

export { AVAILABLE_LIGANDS };
