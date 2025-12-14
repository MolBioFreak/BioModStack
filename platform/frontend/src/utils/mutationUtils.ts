
export interface Mutation {
    position: number;
    from: string;
    to: string;
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

const AMINO_ACIDS = ['A', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'V', 'W', 'Y'];

const CONSERVATIVE_GROUPS = [
    ['A', 'V', 'L', 'I', 'M'], // Aliphatic
    ['F', 'Y', 'W'],           // Aromatic
    ['S', 'T'],                // Hydroxyl
    ['K', 'R', 'H'],           // Basic
    ['D', 'E', 'N', 'Q'],      // Acidic/Amide
    ['C', 'G', 'P']            // Special
];

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

// Generate variants based on regions and strategy
export function generateLibrary(
    baseSequence: string,
    regions: MutationRegion[],
    strategy: SubstitutionStrategy,
    numVariants: number,
    mutationsPerVariant: [number, number],
    customAA: string[] = []
): VariantSequence[] {
    const variants: VariantSequence[] = [];
    const activeRegions = regions.filter(r => r.enabled);
    
    // Get all valid positions from enabled regions
    let validPositions: number[] = [];
    activeRegions.forEach(r => {
        // 1-based to 0-based for internal logic, but keep 1-based for mutations
        for (let i = r.start; i <= r.end; i++) {
            if (i > 0 && i <= baseSequence.length) {
                validPositions.push(i);
            }
        }
    });

    if (validPositions.length === 0) return [];

    for (let i = 0; i < numVariants; i++) {
        const numMutations = Math.floor(Math.random() * (mutationsPerVariant[1] - mutationsPerVariant[0] + 1)) + mutationsPerVariant[0];
        const selectedPositions = new Set<number>();
        
        // Select unique positions
        while (selectedPositions.size < numMutations) {
            const pos = validPositions[Math.floor(Math.random() * validPositions.length)];
            selectedPositions.add(pos);
        }

        const currentMutations: Mutation[] = [];
        let variantSeq = baseSequence.split('');

        selectedPositions.forEach(pos => {
            const originalAA = baseSequence[pos - 1];
            let targetAA = originalAA;
            
            // Basic random strategy for now - can enhance with conservative logic later
            let pool = strategy === 'custom' ? customAA : AMINO_ACIDS;
            if (strategy === 'conservative') {
                const group = CONSERVATIVE_GROUPS.find(g => g.includes(originalAA));
                pool = group || AMINO_ACIDS;
            } else if (strategy === 'nonconservative') {
                 const group = CONSERVATIVE_GROUPS.find(g => g.includes(originalAA));
                 pool = AMINO_ACIDS.filter(aa => !group?.includes(aa));
            }

            // Pick different AA
            do {
                targetAA = pool[Math.floor(Math.random() * pool.length)];
            } while (targetAA === originalAA && pool.length > 1);

            variantSeq[pos - 1] = targetAA;
            currentMutations.push({
                position: pos,
                from: originalAA,
                to: targetAA
            });
        });

        // Sort mutations by position
        currentMutations.sort((a, b) => a.position - b.position);

        variants.push({
            name: `var_${i + 1}_${currentMutations.map(m => `${m.from}${m.position}${m.to}`).join('_')}`,
            sequence: variantSeq.join(''),
            mutations: currentMutations
        });
    }

    return variants;
}
