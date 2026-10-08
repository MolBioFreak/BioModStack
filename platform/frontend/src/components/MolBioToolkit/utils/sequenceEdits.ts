import { remapFeatureAfterDeletion, remapFeatureAfterInsertion, transformFeatureForSelection, type FeatureRecord } from './features.js';
import { complementSequence, reverseComplementSequence } from './nucleotides.js';
import { canonicalizePrimerPlacement } from './selectionActions.js';

type RangeLike = {
    start: number;
    end: number;
};

export interface SequenceEditPrimer extends RangeLike {
    id: string;
    name: string;
    sequence: string;
    sequenceType?: 'dna' | 'rna';
    strand: 1 | -1;
    sites?: Array<RangeLike & {
        strand: 1 | -1;
        tm?: number;
        note?: string;
    }>;
}

export interface SequenceEditData {
    name?: string;
    sequence: string;
    circular?: boolean;
    sequenceType: 'dna' | 'rna' | 'protein';
    features: FeatureRecord[];
    primers?: SequenceEditPrimer[];
    translations?: unknown[];
    analysisTracks?: unknown[];
}

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

function withUpdatedSequenceData<T extends SequenceEditData>(
    sequenceData: T,
    sequence: string,
    features: FeatureRecord[],
    primers: SequenceEditPrimer[],
): T {
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
    } as T;
}

function transformPrimerSequence(
    primerSequence: string,
    operation: TransformOperation,
    sequenceType: SequenceEditData['sequenceType'],
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
    features: FeatureRecord[],
    start: number,
    end: number,
    operation: TransformOperation,
): FeatureRecord[] {
    return features.map((feature) => transformFeatureForSelection(feature, start, end, operation));
}

function primerSites(primer: SequenceEditPrimer, sequenceLength: number, circular: boolean) {
    const canonical = canonicalizePrimerPlacement(primer, sequenceLength, circular);
    return canonical.sites?.map((site) => ({ ...site })) || [];
}

function withPrimerSites<T extends SequenceEditPrimer>(
    primer: T,
    sites: ReturnType<typeof primerSites>,
): T | null {
    if (sites.length === 0) {
        return null;
    }
    return {
        ...primer,
        start: sites[0].start,
        end: sites[sites.length - 1].end,
        strand: sites[0].strand,
        sites,
    } as T;
}

function remapPrimersAfterInsertion<T extends SequenceEditPrimer>(
    primers: T[],
    position: number,
    insertedLength: number,
    sequenceLength: number,
    circular: boolean,
): T[] {
    return primers.flatMap((primer) => {
        const remapped = remapRangeEntitiesAfterInsertion(
            primerSites(primer, sequenceLength, circular),
            position,
            insertedLength,
        );
        const next = withPrimerSites(primer, remapped);
        return next ? [next] : [];
    });
}

function remapPrimersAfterDeletion<T extends SequenceEditPrimer>(
    primers: T[],
    start: number,
    end: number,
    sequenceLength: number,
    circular: boolean,
): T[] {
    return primers.flatMap((primer) => {
        const remapped = remapRangeEntitiesAfterDeletion(
            primerSites(primer, sequenceLength, circular),
            start,
            end,
        );
        const next = withPrimerSites(primer, remapped);
        return next ? [next] : [];
    });
}

function transformPrimersForSelection<T extends SequenceEditPrimer>(
    primers: T[],
    start: number,
    end: number,
    operation: TransformOperation,
    sequenceType: SequenceEditData['sequenceType'],
    sequenceLength: number,
    circular: boolean,
): T[] {
    return primers.map((primer) => {
        const sites = primerSites(primer, sequenceLength, circular);
        if (!sites.every((site) => site.start >= start && site.end <= end)) {
            return primer;
        }
        const nextSites = sites
            .map((site) => ({
                ...site,
                ...(operation === 'complement'
                    ? { start: site.start, end: site.end }
                    : {
                        start: start + (end - site.end),
                        end: start + (end - site.start),
                    }),
                strand: (site.strand === 1 ? -1 : 1) as 1 | -1,
            }));
        const transformed = withPrimerSites(primer, nextSites);
        if (!transformed) {
            return primer;
        }
        return {
            ...transformed,
            sequence: transformPrimerSequence(primer.sequence, operation, sequenceType),
        } as T;
    });
}

function transformSelectedSegment(
    selectedSequence: string,
    operation: TransformOperation,
    sequenceType: SequenceEditData['sequenceType'],
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

export function applyInsertEdit<T extends SequenceEditData>(
    sequenceData: T,
    position: number,
    insertedSequence: string,
): T {
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
        remapPrimersAfterInsertion(
            sequenceData.primers || [],
            insertAt,
            insertedLength,
            sequenceData.sequence.length,
            Boolean(sequenceData.circular),
        ),
    );
}

export function applyDeleteEdit<T extends SequenceEditData>(
    sequenceData: T,
    start: number,
    end: number,
): T {
    const range = normalizeRange(start, end, sequenceData.sequence.length);
    const nextSequence = sequenceData.sequence.slice(0, range.start) + sequenceData.sequence.slice(range.end);

    return withUpdatedSequenceData(
        sequenceData,
        nextSequence,
        sequenceData.features
            .map((feature) => remapFeatureAfterDeletion(feature, range.start, range.end))
            .filter((feature): feature is FeatureRecord => Boolean(feature)),
        remapPrimersAfterDeletion(
            sequenceData.primers || [],
            range.start,
            range.end,
            sequenceData.sequence.length,
            Boolean(sequenceData.circular),
        ),
    );
}

export function applyReplaceEdit<T extends SequenceEditData>(
    sequenceData: T,
    start: number,
    end: number,
    replacementSequence: string,
): T {
    const range = normalizeRange(start, end, sequenceData.sequence.length);
    const deleted = applyDeleteEdit(sequenceData, range.start, range.end);
    return applyInsertEdit(deleted, range.start, replacementSequence);
}

export function applyTransformEdit<T extends SequenceEditData>(
    sequenceData: T,
    start: number,
    end: number,
    operation: TransformOperation,
): T {
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
        transformPrimersForSelection(
            sequenceData.primers || [],
            range.start,
            range.end,
            operation,
            sequenceData.sequenceType,
            sequenceData.sequence.length,
            Boolean(sequenceData.circular),
        ),
    );
}
