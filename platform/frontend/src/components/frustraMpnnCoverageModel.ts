interface ResidueSupport {
    expected: number;
    mapped: number;
    scoreable: number;
    ambiguous: number;
    excluded: number;
}

interface SlotSupport {
    expected: number;
    observed: number;
    scoreable: number;
}

export interface FrustraMpnnCoverageReadiness {
    status: 'Complete' | 'Review missing data';
    residueCoverage: number;
    slotCoverage: number;
    missingResidues: number;
    missingSlots: number;
    issueCount: number;
    missingness: Array<[string, number]>;
}

const ratio = (value: number, expected: number) => expected > 0 ? value / expected : 0;

export function buildFrustraMpnnCoverageReadiness(
    residues: ResidueSupport,
    slots: SlotSupport,
    missingnessByReason: Record<string, number>,
): FrustraMpnnCoverageReadiness {
    const missingResidues = Math.max(0, residues.expected - residues.scoreable);
    const missingSlots = Math.max(0, slots.expected - slots.scoreable);
    const issueCount = Math.max(0, residues.ambiguous) + Math.max(0, residues.excluded);
    const missingness = Object.entries(missingnessByReason).filter(([, count]) => count > 0);
    const complete = missingResidues === 0 && missingSlots === 0 && issueCount === 0 && missingness.length === 0;
    return {
        status: complete ? 'Complete' : 'Review missing data',
        residueCoverage: ratio(residues.scoreable, residues.expected),
        slotCoverage: ratio(slots.scoreable, slots.expected),
        missingResidues,
        missingSlots,
        issueCount,
        missingness,
    };
}
