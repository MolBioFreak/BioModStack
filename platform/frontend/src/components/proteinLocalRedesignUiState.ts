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
