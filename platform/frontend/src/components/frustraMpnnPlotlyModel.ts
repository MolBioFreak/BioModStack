import {
    CANONICAL_AMINO_ACIDS,
    type CmLandscapeResidue,
} from './conformationalMapping/conformationalMappingSemantics.js';

export interface FrustraMpnnPlotlyModel {
    residueLabels: string[];
    sequenceIndices: number[];
    heatmapScores: Array<Array<number | null>>;
    heatmapCustomData: Array<Array<[string, string, string, string]>>;
    nativeScores: Array<number | null>;
    nativeClasses: Array<string | null>;
    substitutionScores: Record<string, number[]>;
}

export function buildFrustraMpnnPlotlyModel(residues: CmLandscapeResidue[]): FrustraMpnnPlotlyModel {
    const residueLabels = residues.map((residue) => `${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code || ''}`);
    const sequenceIndices = residues.map((residue) => residue.sequence_index + 1);
    const substitutionScores = Object.fromEntries(CANONICAL_AMINO_ACIDS.map((aa) => [aa, [] as number[]]));
    const heatmapScores = CANONICAL_AMINO_ACIDS.map(() => [] as Array<number | null>);
    const heatmapCustomData = CANONICAL_AMINO_ACIDS.map(() => [] as Array<[string, string, string, string]>);
    const nativeScores: Array<number | null> = [];
    const nativeClasses: Array<string | null> = [];

    residues.forEach((residue) => {
        CANONICAL_AMINO_ACIDS.forEach((aa, mutationIndex) => {
            const slot = residue.slots.find((candidate) => candidate.mutation_aa === aa);
            if (!slot) throw new Error(`missing_exact_20_slot:${residue.key}:${aa}`);
            heatmapScores[mutationIndex].push(slot.score);
            heatmapCustomData[mutationIndex].push([
                residue.wt,
                slot.class ?? 'unavailable',
                slot.status,
                slot.reason ?? '',
            ]);
            if (slot.score != null && Number.isFinite(slot.score)) substitutionScores[aa].push(slot.score);
        });
        const native = residue.slots.find((slot) => slot.mutation_aa === residue.wt);
        if (!native) throw new Error(`native_slot_missing:${residue.key}:${residue.wt}`);
        nativeScores.push(native.score);
        nativeClasses.push(native.class);
    });

    return {
        residueLabels,
        sequenceIndices,
        heatmapScores,
        heatmapCustomData,
        nativeScores,
        nativeClasses,
        substitutionScores,
    };
}
