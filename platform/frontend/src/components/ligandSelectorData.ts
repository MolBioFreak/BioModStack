// Shared ligand-selector runtime helpers/data. Kept outside the TSX component file so Fast Refresh only sees component exports.

export function componentIdFromIndex(index: number): string {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    let value = Math.max(0, Math.floor(index));
    let label = '';
    do {
        label = alphabet[value % alphabet.length] + label;
        value = Math.floor(value / alphabet.length) - 1;
    } while (value >= 0);
    return label;
}

export const AVAILABLE_LIGANDS = [
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
