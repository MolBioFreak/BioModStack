
export type MutationType = 'substitution' | 'insertion' | 'deletion';

export interface Mutation {
    position: number;
    from: string;
    to: string;
    type?: MutationType;
}

export interface MutationRegion {
    id: string;
    start: number;
    end: number;
    enabled: boolean;
}

export interface VariantSequence {
    name: string;
    sequence: string;
    mutations: Mutation[];
}

export type SubstitutionStrategy = 'random' | 'conservative' | 'nonconservative' | 'custom';

export const STANDARD_AMINO_ACIDS = ['A', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'V', 'W', 'Y'];

const CONSERVATIVE_GROUPS = [
    ['A', 'V', 'L', 'I', 'M'], // Aliphatic
    ['F', 'Y', 'W'],           // Aromatic
    ['S', 'T'],                // Hydroxyl
    ['K', 'R', 'H'],           // Basic
    ['D', 'E', 'N', 'Q'],      // Acidic/Amide
    ['C', 'G', 'P']            // Special
];

export interface MutationLibraryOptions {
    customAA?: string[];
    allowedAAs?: string[];
    blockedAAs?: string[];
    excludedPositions?: number[];
    excludeFromResidues?: string[];
    mutationCountMode?: 'range' | 'exact' | 'set';
    mutationCount?: number;
    mutationCountSet?: number[];
    allowInsertions?: boolean;
    allowDeletions?: boolean;
    indelSizes?: number[];
    indelProbability?: number;
}

// Parse region string "23-42, 67-72" -> Region objects
export function parseRegions(input: string): MutationRegion[] {
    if (!input.trim()) return [];
    
    return input.split(',').map((part, idx) => {
        const [start, end] = part.trim().split('-').map(n => parseInt(n));
        if (isNaN(start)) return null;
        return {
            id: `region-${idx}`,
            start,
            end: isNaN(end) ? start : end,
            enabled: true
        };
    }).filter(r => r !== null) as MutationRegion[];
}

export function parsePositionList(input: string): number[] {
    if (!input.trim()) return [];
    const positions = new Set<number>();
    input.split(',').forEach(part => {
        const trimmed = part.trim();
        if (!trimmed) return;
        if (trimmed.includes('-')) {
            const [startRaw, endRaw] = trimmed.split('-');
            const start = parseInt(startRaw);
            const end = parseInt(endRaw);
            if (Number.isFinite(start) && Number.isFinite(end)) {
                const min = Math.min(start, end);
                const max = Math.max(start, end);
                for (let i = min; i <= max; i++) positions.add(i);
            }
        } else {
            const pos = parseInt(trimmed);
            if (Number.isFinite(pos)) positions.add(pos);
        }
    });
    return Array.from(positions).sort((a, b) => a - b);
}

export function normalizeAminoAcids(input: string): string[] {
    if (!input.trim()) return [];
    const chars = input.toUpperCase().replace(/[^A-Z]/g, '').split('');
    const unique = Array.from(new Set(chars));
    return unique.filter(aa => STANDARD_AMINO_ACIDS.includes(aa));
}

export function formatMutationLabel(mutation: Mutation): string {
    if (mutation.type === 'insertion') {
        const inserted = mutation.to || '';
        const suffix = inserted.length > 0 ? inserted : '';
        return `ins${mutation.position}${suffix}`;
    }
    if (mutation.type === 'deletion') {
        const len = mutation.from?.length ?? 0;
        if (len > 1) {
            return `del${mutation.position}-${mutation.position + len - 1}`;
        }
        return `del${mutation.position}${mutation.from || ''}`;
    }
    return `${mutation.from}${mutation.position}${mutation.to}`;
}

// Generate variants based on regions and strategy
export function generateLibrary(
    baseSequence: string,
    regions: MutationRegion[],
    strategy: SubstitutionStrategy,
    numVariants: number,
    mutationsPerVariant: [number, number],
    options: MutationLibraryOptions = {}
): VariantSequence[] {
    const variants: VariantSequence[] = [];
    const activeRegions = regions.filter(r => r.enabled);
    const allowedAAs = options.allowedAAs ?? [];
    const blockedAAs = options.blockedAAs ?? [];
    const excludedPositions = new Set(options.excludedPositions ?? []);
    const excludeFromResidues = new Set(options.excludeFromResidues ?? []);
    const mutationCountMode = options.mutationCountMode ?? 'range';
    const mutationCountSet = (options.mutationCountSet ?? []).filter(v => Number.isFinite(v) && v > 0);
    const allowInsertions = options.allowInsertions ?? false;
    const allowDeletions = options.allowDeletions ?? false;
    const indelSizes = (options.indelSizes && options.indelSizes.length > 0) ? options.indelSizes : [];
    const indelProbability = Math.max(0, Math.min(1, options.indelProbability ?? 0));
    
    // Get all valid positions from enabled regions
    let validPositions: number[] = [];
    activeRegions.forEach(r => {
        // 1-based to 0-based for internal logic, but keep 1-based for mutations
        for (let i = r.start; i <= r.end; i++) {
            if (i > 0 && i <= baseSequence.length) {
                if (!excludedPositions.has(i)) {
                    validPositions.push(i);
                }
            }
        }
    });

    if (validPositions.length === 0) return [];

    const getPoolForPosition = (originalAA: string): string[] => {
        let pool: string[] = STANDARD_AMINO_ACIDS;
        if (strategy === 'custom') {
            pool = (options.customAA && options.customAA.length > 0) ? options.customAA : STANDARD_AMINO_ACIDS;
        } else if (strategy === 'conservative') {
            const group = CONSERVATIVE_GROUPS.find(g => g.includes(originalAA));
            pool = group || STANDARD_AMINO_ACIDS;
        } else if (strategy === 'nonconservative') {
            const group = CONSERVATIVE_GROUPS.find(g => g.includes(originalAA));
            pool = STANDARD_AMINO_ACIDS.filter(aa => !group?.includes(aa));
        }

        if (allowedAAs.length > 0) {
            pool = pool.filter(aa => allowedAAs.includes(aa));
        }
        if (blockedAAs.length > 0) {
            pool = pool.filter(aa => !blockedAAs.includes(aa));
        }
        return pool;
    };

    const getInsertionPool = (): string[] => {
        let pool = STANDARD_AMINO_ACIDS;
        if (allowedAAs.length > 0) {
            pool = pool.filter(aa => allowedAAs.includes(aa));
        }
        if (blockedAAs.length > 0) {
            pool = pool.filter(aa => !blockedAAs.includes(aa));
        }
        return pool.length > 0 ? pool : STANDARD_AMINO_ACIDS;
    };

    const positionPools = new Map<number, string[]>();
    validPositions = validPositions.filter(pos => {
        const originalAA = baseSequence[pos - 1];
        if (excludeFromResidues.has(originalAA)) return false;
        const pool = getPoolForPosition(originalAA).filter(aa => aa !== originalAA);
        if (pool.length === 0) return false;
        positionPools.set(pos, pool);
        return true;
    });

    if (validPositions.length === 0) return [];

    const regionRanges = activeRegions.map(r => ({ start: r.start, end: r.end }));
    const allowDeletionAt = (pos: number, size: number) =>
        regionRanges.some(r => pos >= r.start && (pos + size - 1) <= r.end);
    const allowInsertionAt = (pos: number) =>
        regionRanges.some(r => pos >= r.start && pos <= r.end);

    const pickMutationCount = () => {
        if (mutationCountMode === 'exact' && Number.isFinite(options.mutationCount) && (options.mutationCount as number) > 0) {
            return options.mutationCount as number;
        }
        if (mutationCountMode === 'set' && mutationCountSet.length > 0) {
            return mutationCountSet[Math.floor(Math.random() * mutationCountSet.length)];
        }
        const min = mutationsPerVariant[0];
        const max = mutationsPerVariant[1];
        return Math.floor(Math.random() * (max - min + 1)) + min;
    };

    for (let i = 0; i < numVariants; i++) {
        const numMutations = pickMutationCount();
        const selectedPositions = new Set<number>();
        
        let variantSeq = baseSequence.split('');
        const currentMutations: Mutation[] = [];

        let indel: { type: 'insertion' | 'deletion'; position: number; size: number } | null = null;
        if ((allowInsertions || allowDeletions) && indelSizes.length > 0 && indelProbability > 0 && Math.random() < indelProbability) {
            const indelTypes: Array<'insertion' | 'deletion'> = [];
            if (allowInsertions) indelTypes.push('insertion');
            if (allowDeletions) indelTypes.push('deletion');
            const indelType = indelTypes[Math.floor(Math.random() * indelTypes.length)];
            const indelSize = indelSizes[Math.floor(Math.random() * indelSizes.length)];

            if (indelType === 'insertion') {
                const insertionCandidates = validPositions.filter(pos => allowInsertionAt(pos));
                if (insertionCandidates.length > 0) {
                    const pos = insertionCandidates[Math.floor(Math.random() * insertionCandidates.length)];
                    const pool = getInsertionPool();
                    const inserted: string[] = [];
                    for (let j = 0; j < indelSize; j++) {
                        inserted.push(pool[Math.floor(Math.random() * pool.length)]);
                    }
                    variantSeq.splice(pos, 0, ...inserted);
                    currentMutations.push({
                        position: pos,
                        from: '',
                        to: inserted.join(''),
                        type: 'insertion'
                    });
                    indel = { type: 'insertion', position: pos, size: indelSize };
                }
            } else {
                const deletionCandidates = validPositions.filter(pos => allowDeletionAt(pos, indelSize) && pos + indelSize - 1 <= baseSequence.length);
                if (deletionCandidates.length > 0) {
                    const pos = deletionCandidates[Math.floor(Math.random() * deletionCandidates.length)];
                    const deleted = variantSeq.slice(pos - 1, pos - 1 + indelSize).join('');
                    variantSeq.splice(pos - 1, indelSize);
                    currentMutations.push({
                        position: pos,
                        from: deleted,
                        to: '',
                        type: 'deletion'
                    });
                    indel = { type: 'deletion', position: pos, size: indelSize };
                }
            }
        }

        const isDeletedPosition = (pos: number) => {
            if (!indel || indel.type !== 'deletion') return false;
            return pos >= indel.position && pos <= (indel.position + indel.size - 1);
        };

        const mapPosition = (pos: number) => {
            if (!indel) return pos;
            if (indel.type === 'insertion' && pos > indel.position) return pos + indel.size;
            if (indel.type === 'deletion' && pos > (indel.position + indel.size - 1)) return pos - indel.size;
            return pos;
        };

        const substitutionPositions = validPositions.filter(pos => !isDeletedPosition(pos));

        // Select unique positions
        while (selectedPositions.size < Math.min(numMutations, substitutionPositions.length)) {
            const pos = substitutionPositions[Math.floor(Math.random() * substitutionPositions.length)];
            selectedPositions.add(pos);
        }

        selectedPositions.forEach(pos => {
            const originalAA = baseSequence[pos - 1];
            const pool = positionPools.get(pos) ?? getPoolForPosition(originalAA).filter(aa => aa !== originalAA);
            if (pool.length === 0) return;
            const targetAA = pool[Math.floor(Math.random() * pool.length)];
            const mappedPos = mapPosition(pos);
            if (mappedPos > 0 && mappedPos <= variantSeq.length) {
                variantSeq[mappedPos - 1] = targetAA;
                currentMutations.push({
                    position: pos,
                    from: originalAA,
                    to: targetAA,
                    type: 'substitution'
                });
            }
        });

        // Sort mutations by position
        currentMutations.sort((a, b) => a.position - b.position);

        variants.push({
            name: `var_${i + 1}_${currentMutations.map(formatMutationLabel).join('_')}`,
            sequence: variantSeq.join(''),
            mutations: currentMutations
        });
    }

    return variants;
}
