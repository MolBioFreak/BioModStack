import { useState } from 'react';

export interface LigandEntry {
    id: string;
    type: 'ligand' | 'ion';
    ccd?: string;
    smiles?: string;
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

export function LigandSelector({ ligands, setLigands, showCustomSmiles = false }: LigandSelectorProps) {
    const [customSmiles, setCustomSmiles] = useState('');
    const [customName, setCustomName] = useState('');

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
                    <h3 className="text-sm font-semibold text-slate-200">Ligands & Cofactors</h3>
                    <p className="text-xs text-slate-500">Add small molecules or ions to the prediction (e.g., ATP, Mg²⁺)</p>
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

            {/* Selected Ligands Pills */}
            {ligands.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                    {ligands.map((lig, idx) => (
                        <div
                            key={idx}
                            className={`px-3 py-1.5 rounded-full text-sm flex items-center gap-2 ${lig.type === 'ion'
                                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                    : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                }`}
                        >
                            <span className="font-mono text-xs opacity-60">[{lig.id}]</span>
                            <span>{lig.ccd || lig.name}</span>
                            {lig.smiles && <span className="text-xs opacity-60">(SMILES)</span>}
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
