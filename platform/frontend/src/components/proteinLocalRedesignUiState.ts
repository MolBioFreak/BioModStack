export type ProteinLocalRedesignSequenceMethod = 'skip' | 'fampnn' | 'mpnn';

export interface ProteinLocalResidueIdentity {
    resNum: number;
    iCode?: string;
}

export const selectResidueKeysFromRanges = (
    chainId: string,
    residues: ProteinLocalResidueIdentity[],
    selector: string,
): Set<string> => {
    const selected = new Set<string>();
    for (const rawToken of selector.split(',')) {
        const token = rawToken.trim();
        if (!token) continue;
        let body = token;
        if (token.startsWith(chainId)) {
            body = token.slice(chainId.length);
        } else if (!/^-?\d/.test(token)) {
            continue;
        }
        const match = body.match(/^(-?\d+)([A-Za-z]?)(?:-(-?\d+)([A-Za-z]?))?$/);
        if (!match) continue;
        const start = Number(match[1]);
        const startICode = match[2] || '';
        const end = match[3] == null ? null : Number(match[3]);
        const endICode = match[4] || '';
        for (const residue of residues) {
            const residueICode = residue.iCode || '';
            const matches = end == null
                ? residue.resNum === start && residueICode === startICode
                : !startICode && !endICode && residue.resNum >= Math.min(start, end) && residue.resNum <= Math.max(start, end);
            if (matches) selected.add(`${chainId}${residue.resNum}${residueICode}`);
        }
    }
    return selected;
};

export const resolveProteinLocalRedesignSourcePath = (
    initialValues?: Record<string, unknown>,
): string | null => {
    if (!initialValues) return null;
    for (const key of ['input_pdb', 'input_structure'] as const) {
        const value = initialValues[key];
        if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return null;
};

export interface ProteinLocalRedesignUiState {
    sequenceDesignEnabled: boolean;
    showSequenceSampling: boolean;
    showLegacyOptionalStages: boolean;
    sequenceSectionLabel: 'Optional Sequence Redesign' | 'Sequence Redesign';
}

export type ProteinLocalChainType = 'protein' | 'dna' | 'rna' | 'other';

export interface ProteinLocalChainSummary {
    id: string;
    residueCount: number;
    type: ProteinLocalChainType;
}

const PROTEIN_RESIDUES = new Set([
    'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'ILE',
    'LEU', 'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL',
    'MSE', 'SEC', 'PYL', 'HYP',
]);
const DNA_RESIDUES = new Set(['DA', 'DC', 'DG', 'DT', 'DI', 'ADE', 'CYT', 'GUA', 'THY']);
const RNA_RESIDUES = new Set(['A', 'C', 'G', 'U', 'I', 'URA', 'PSU', '1MA', '5MC']);

const classifyPolymerResidue = (resName: string): ProteinLocalChainType => (
    PROTEIN_RESIDUES.has(resName)
        ? 'protein'
        : DNA_RESIDUES.has(resName)
            ? 'dna'
            : RNA_RESIDUES.has(resName)
                ? 'rna'
                : 'other'
);

const pdbResidueIdentity = (
    chainId: string,
    resNum: string,
    iCode: string,
    resName: string,
) => `${chainId}:${resNum}${iCode}:${resName}`;

export const summarizeChainsFromPdbContent = (content: string): ProteinLocalChainSummary[] => {
    const lines = content.split(/\r?\n/);
    const modifiedPolymerResidues = new Map<string, string>();

    for (const rawLine of lines) {
        if (!rawLine.startsWith('MODRES')) continue;
        const modifiedResName = rawLine.slice(12, 15).trim().toUpperCase();
        const rawChainId = rawLine.slice(16, 17);
        const resNum = rawLine.slice(18, 22).trim();
        const iCode = rawLine.slice(22, 23).trim();
        const standardResName = rawLine.slice(24, 27).trim().toUpperCase();
        if (!modifiedResName || !resNum || !standardResName) continue;
        modifiedPolymerResidues.set(
            pdbResidueIdentity(rawChainId, resNum, iCode, modifiedResName),
            standardResName,
        );
    }

    const chainMap = new Map<string, {
        residueKeys: Set<string>;
        counts: Record<ProteinLocalChainType, number>;
    }>();

    for (const rawLine of lines) {
        const isAtom = rawLine.startsWith('ATOM');
        if (!isAtom && !rawLine.startsWith('HETATM')) continue;
        const rawChainId = rawLine.slice(21, 22);
        const chainId = rawChainId.trim() || 'A';
        const resName = rawLine.slice(17, 20).trim().toUpperCase();
        const resNum = rawLine.slice(22, 26).trim();
        const iCode = rawLine.slice(26, 27).trim();
        const standardResName = isAtom
            ? resName
            : modifiedPolymerResidues.get(pdbResidueIdentity(rawChainId, resNum, iCode, resName));

        // Keep complete HETATM bytes in the submitted/viewed structure, while
        // admitting only exact MODRES-bound modified polymer residues here.
        if (!standardResName) continue;
        const type = classifyPolymerResidue(standardResName);

        const residueId = `${resNum}${iCode}`;
        if (!chainMap.has(chainId)) {
            chainMap.set(chainId, {
                residueKeys: new Set<string>(),
                counts: { protein: 0, dna: 0, rna: 0, other: 0 },
            });
        }
        const chain = chainMap.get(chainId)!;
        const uniqueKey = `${chainId}:${residueId}`;
        if (chain.residueKeys.has(uniqueKey)) continue;
        chain.residueKeys.add(uniqueKey);
        chain.counts[type] += 1;
    }

    const typePriority: ProteinLocalChainType[] = ['protein', 'dna', 'rna', 'other'];
    return Array.from(chainMap.entries())
        .map(([id, entry]) => ({
            id,
            residueCount: entry.residueKeys.size,
            type: typePriority.reduce((best, candidate) => (
                entry.counts[candidate] > entry.counts[best] ? candidate : best
            ), 'other' as ProteinLocalChainType),
        }))
        .sort((a, b) => a.id.localeCompare(b.id));
};

export const getProteinLocalRedesignUiState = (
    isNativeLocalRedesign: boolean,
    sequenceMethod: ProteinLocalRedesignSequenceMethod,
): ProteinLocalRedesignUiState => {
    const sequenceDesignEnabled = !isNativeLocalRedesign && sequenceMethod !== 'skip';

    return {
        sequenceDesignEnabled,
        showSequenceSampling: sequenceDesignEnabled,
        showLegacyOptionalStages: !isNativeLocalRedesign,
        sequenceSectionLabel: isNativeLocalRedesign ? 'Optional Sequence Redesign' : 'Sequence Redesign',
    };
};
