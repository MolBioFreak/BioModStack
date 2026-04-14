import type { Feature, Primer, SequenceData } from '../types';
import { remapFeatureAfterDeletion, remapFeatureAfterInsertion, transformFeatureForSelection } from './features';
import { complementSequence, reverseComplementSequence } from './nucleotides';

type RangeLike = {
    start: number;
    end: number;
};

export type TransformOperation = 'reverse' | 'complement' | 'reverse_complement';

function clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
}

function normalizeRange(start: number, end: number, sequenceLength: number) {
    const safeStart = clamp(Math.min(start, end), 0, sequenceLength);
    const safeEnd = clamp(Math.max(start, end), safeStart, sequenceLength);
    return { start: safeStart, end: safeEnd };
}

function remapRangeEntitiesAfterDeletion<T extends RangeLike>(entities: T[], start: number, end: number): T[] {
    const deletedLength = end - start;
    if (deletedLength <= 0) return entities;

    return entities.flatMap((entity) => {
        if (entity.end <= start) {
            return [entity];
        }

        if (entity.start >= end) {
            return [{
                ...entity,
                start: entity.start - deletedLength,
                end: entity.end - deletedLength,
            }];
        }

        const nextStart = entity.start < start ? entity.start : start;
        const nextEnd = entity.end > end ? entity.end - deletedLength : start;
        if (nextEnd <= nextStart) {
            return [];
        }

        return [{
            ...entity,
            start: nextStart,
            end: nextEnd,
        }];
    });
}

function remapRangeEntitiesAfterInsertion<T extends RangeLike>(entities: T[], position: number, insertedLength: number): T[] {
    if (insertedLength <= 0) return entities;

    return entities.map((entity) => {
        if (entity.end <= position) {
            return entity;
        }

        if (entity.start >= position) {
            return {
                ...entity,
                start: entity.start + insertedLength,
                end: entity.end + insertedLength,
            };
        }

        return {
            ...entity,
            end: entity.end + insertedLength,
        };
    });
}

function withUpdatedSequenceData(
    sequenceData: SequenceData,
    sequence: string,
    features: Feature[],
    primers: Primer[],
): SequenceData {
    return {
        ...sequenceData,
        sequence,
        features,
        primers,
        // ORFs/translations are derived from the sequence and should be regenerated.
        translations: [],
        // Analysis/evidence tracks are position-aligned to the original sequence.
        // Clear them on sequence-altering edits instead of leaving stale coordinates.
        analysisTracks: [],
    };
}

function transformPrimerSequence(
    primerSequence: string,
    operation: TransformOperation,
    sequenceType: SequenceData['sequenceType'],
): string {
    if (sequenceType === 'protein') {
        return primerSequence;
    }

    if (operation === 'reverse') {
        return complementSequence(primerSequence, sequenceType);
    }
    if (operation === 'complement') {
        return primerSequence.toUpperCase().split('').reverse().join('');
    }
    return primerSequence.toUpperCase();
}

function transformFeaturesForSelection(
    features: Feature[],
    start: number,
    end: number,
    operation: TransformOperation,
): Feature[] {
    return features.map((feature) => transformFeatureForSelection(feature, start, end, operation));
}

function transformPrimersForSelection(
    primers: Primer[],
    start: number,
    end: number,
    operation: TransformOperation,
    sequenceType: SequenceData['sequenceType'],
): Primer[] {
    return primers.map((primer) => {
        const transformed = transformFeatureForSelection(primer as Feature, start, end, operation) as Primer;
        if (transformed === primer || primer.start < start || primer.end > end) {
            return transformed;
        }
        return {
            ...transformed,
            sequence: transformPrimerSequence(primer.sequence, operation, sequenceType),
        };
    });
}

function transformSelectedSegment(
    selectedSequence: string,
    operation: TransformOperation,
    sequenceType: SequenceData['sequenceType'],
): string {
    if (sequenceType === 'protein') {
        return selectedSequence;
    }

    if (operation === 'reverse') {
        return selectedSequence.split('').reverse().join('');
    }
    if (operation === 'complement') {
        return complementSequence(selectedSequence, sequenceType);
    }
    return reverseComplementSequence(selectedSequence, sequenceType);
}

export function applyInsertEdit(sequenceData: SequenceData, position: number, insertedSequence: string): SequenceData {
    const insertAt = clamp(position, 0, sequenceData.sequence.length);
    const insertedLength = insertedSequence.length;
    const nextSequence =
        sequenceData.sequence.slice(0, insertAt) +
        insertedSequence +
        sequenceData.sequence.slice(insertAt);

    return withUpdatedSequenceData(
        sequenceData,
        nextSequence,
        sequenceData.features.map((feature) => remapFeatureAfterInsertion(feature, insertAt, insertedLength)),
        remapRangeEntitiesAfterInsertion(sequenceData.primers || [], insertAt, insertedLength),
    );
}

export function applyDeleteEdit(sequenceData: SequenceData, start: number, end: number): SequenceData {
    const range = normalizeRange(start, end, sequenceData.sequence.length);
    const nextSequence = sequenceData.sequence.slice(0, range.start) + sequenceData.sequence.slice(range.end);

    return withUpdatedSequenceData(
        sequenceData,
        nextSequence,
        sequenceData.features
            .map((feature) => remapFeatureAfterDeletion(feature, range.start, range.end))
            .filter((feature): feature is Feature => Boolean(feature)),
        remapRangeEntitiesAfterDeletion(sequenceData.primers || [], range.start, range.end),
    );
}

export function applyReplaceEdit(
    sequenceData: SequenceData,
    start: number,
    end: number,
    replacementSequence: string,
): SequenceData {
    const range = normalizeRange(start, end, sequenceData.sequence.length);
    const deleted = applyDeleteEdit(sequenceData, range.start, range.end);
    return applyInsertEdit(deleted, range.start, replacementSequence);
}

export function applyTransformEdit(
    sequenceData: SequenceData,
    start: number,
    end: number,
    operation: TransformOperation,
): SequenceData {
    const range = normalizeRange(start, end, sequenceData.sequence.length);
    if (range.end <= range.start) {
        return sequenceData;
    }

    const selectedSequence = sequenceData.sequence.slice(range.start, range.end);
    const transformedSegment = transformSelectedSegment(selectedSequence, operation, sequenceData.sequenceType);
    const nextSequence =
        sequenceData.sequence.slice(0, range.start) +
        transformedSegment +
        sequenceData.sequence.slice(range.end);

    return withUpdatedSequenceData(
        sequenceData,
        nextSequence,
        transformFeaturesForSelection(sequenceData.features, range.start, range.end, operation),
        transformPrimersForSelection(sequenceData.primers || [], range.start, range.end, operation, sequenceData.sequenceType),
    );
}
