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
    bestAlternativeDeltas: Array<number | null>;
    worstAlternativeDeltas: Array<number | null>;
    medianAlternativeScores: Array<number | null>;
    highAlternativeFractions: Array<number | null>;
    minimalAlternativeFractions: Array<number | null>;
    substitutionClassFractions: Record<string, { high: number; neutral: number; minimal: number; missing: number }>;
}

const median = (values: number[]): number | null => {
    if (values.length === 0) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
};

export function buildFrustraMpnnPlotlyModel(residues: CmLandscapeResidue[]): FrustraMpnnPlotlyModel {
    const residueLabels = residues.map((residue) => `${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code || ''}`);
    const sequenceIndices = residues.map((residue) => residue.sequence_index + 1);
    const substitutionScores = Object.fromEntries(CANONICAL_AMINO_ACIDS.map((aa) => [aa, [] as number[]]));
    const heatmapScores = CANONICAL_AMINO_ACIDS.map(() => [] as Array<number | null>);
    const heatmapCustomData = CANONICAL_AMINO_ACIDS.map(() => [] as Array<[string, string, string, string]>);
    const nativeScores: Array<number | null> = [];
    const nativeClasses: Array<string | null> = [];
    const bestAlternativeDeltas: Array<number | null> = [];
    const worstAlternativeDeltas: Array<number | null> = [];
    const medianAlternativeScores: Array<number | null> = [];
    const highAlternativeFractions: Array<number | null> = [];
    const minimalAlternativeFractions: Array<number | null> = [];
    const substitutionClassCounts = Object.fromEntries(CANONICAL_AMINO_ACIDS.map((aa) => [aa, { high: 0, neutral: 0, minimal: 0, missing: 0 }]));

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
            const classCounts = substitutionClassCounts[aa];
            if (slot.status !== 'ok' || !slot.class || !(slot.class in classCounts)) classCounts.missing += 1;
            else classCounts[slot.class as 'high' | 'neutral' | 'minimal'] += 1;
        });
        const native = residue.slots.find((slot) => slot.mutation_aa === residue.wt);
        if (!native) throw new Error(`native_slot_missing:${residue.key}:${residue.wt}`);
        nativeScores.push(native.score);
        nativeClasses.push(native.class);
        const alternatives = residue.slots.filter((slot) => slot.mutation_aa !== residue.wt && slot.status === 'ok' && slot.score != null && Number.isFinite(slot.score));
        const alternativeScores = alternatives.map((slot) => slot.score as number);
        const deltas = native.score == null ? [] : alternativeScores.map((score) => score - native.score!);
        bestAlternativeDeltas.push(deltas.length > 0 ? Math.max(...deltas) : null);
        worstAlternativeDeltas.push(deltas.length > 0 ? Math.min(...deltas) : null);
        medianAlternativeScores.push(median(alternativeScores));
        highAlternativeFractions.push(alternatives.length > 0 ? alternatives.filter((slot) => slot.class === 'high').length / alternatives.length : null);
        minimalAlternativeFractions.push(alternatives.length > 0 ? alternatives.filter((slot) => slot.class === 'minimal').length / alternatives.length : null);
    });

    const substitutionClassFractions = Object.fromEntries(CANONICAL_AMINO_ACIDS.map((aa) => {
        const counts = substitutionClassCounts[aa];
        const total = counts.high + counts.neutral + counts.minimal + counts.missing;
        return [aa, {
            high: total ? counts.high / total : 0,
            neutral: total ? counts.neutral / total : 0,
            minimal: total ? counts.minimal / total : 0,
            missing: total ? counts.missing / total : 0,
        }];
    }));

    return {
        residueLabels,
        sequenceIndices,
        heatmapScores,
        heatmapCustomData,
        nativeScores,
        nativeClasses,
        substitutionScores,
        bestAlternativeDeltas,
        worstAlternativeDeltas,
        medianAlternativeScores,
        highAlternativeFractions,
        minimalAlternativeFractions,
        substitutionClassFractions,
    };
}
