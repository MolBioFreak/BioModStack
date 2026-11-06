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
    binding_status?: 'needs_review';
    binding_warning?: string;
    tm?: number;
    tm_algorithm?: string;
    tm_salt_correction?: string;
    tm_settings?: unknown;
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
    digestFragment?: unknown;
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
    if (sequenceData.digestFragment && sequence !== sequenceData.sequence) {
        throw new Error('This digest fragment has physical strand ends. Sequence edits require an explicit duplex/end-repair operation.');
    }
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

export function invalidatePrimerBinding<T extends SequenceEditPrimer>(primer: T, reason: string): T {
    // Keep the ordered oligo, but never let stale coordinates look like a binding site.
    return {
        ...primer,
        start: 0,
        end: 0,
        sites: [],
        binding_status: 'needs_review',
        binding_warning: reason,
        tm: undefined,
        tm_algorithm: undefined,
        tm_salt_correction: undefined,
        tm_settings: undefined,
    };
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
        if (primer.binding_status === 'needs_review' || insertedLength === 0) return [primer];
        const sites = primerSites(primer, sequenceLength, circular);
        const insideSite = sites.some(site => site.start < position && position < site.end);
        // Zero is inside an origin-spanning footprint, even though it is the
        // boundary of each of its two serialized segments.
        const insideOrigin = circular && primer.start > primer.end
            && (position === 0 || position === sequenceLength);
        if (insideSite || insideOrigin) {
            return [invalidatePrimerBinding(primer, 'Template insertion changed this primer binding site. Review or re-add the primer.')];
        }
        const remapped = remapRangeEntitiesAfterInsertion(
            sites,
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
        if (primer.binding_status === 'needs_review' || start === end) return [primer];
        const sites = primerSites(primer, sequenceLength, circular);
        if (sites.some(site => site.start < end && start < site.end)) {
            return [invalidatePrimerBinding(primer, 'Template deletion or replacement changed this primer binding site. Review or re-add the primer.')];
        }
        const remapped = remapRangeEntitiesAfterDeletion(
            sites,
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
    sequenceLength: number,
    circular: boolean,
): T[] {
    return primers.map((primer) => {
        if (primer.binding_status === 'needs_review') return primer;
        const sites = primerSites(primer, sequenceLength, circular);
        if (!sites.some(site => site.start < end && start < site.end)) return primer;
        if (!sites.every((site) => site.start >= start && site.end <= end)) {
            return invalidatePrimerBinding(primer, 'A template transformation changed part of this binding site. Review or re-add the primer.');
        }
        if (operation !== 'reverse_complement') {
            return invalidatePrimerBinding(primer, 'Template reversal or complementation requires a new binding check. The ordered primer sequence is unchanged.');
        }
        const nextSites = sites
            .slice().reverse()
            .map((site) => ({
                ...site,
                start: start + (end - site.end),
                end: start + (end - site.start),
                strand: (site.strand === 1 ? -1 : 1) as 1 | -1,
            }));
        const transformed = withPrimerSites(primer, nextSites);
        if (!transformed) {
            return primer;
        }
        return transformed;
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
    if (insertedLength === 0) return sequenceData;
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
    if (range.start === range.end) return sequenceData;
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
    if (sequenceData.sequence.slice(range.start, range.end) === replacementSequence) return sequenceData;
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
            sequenceData.sequence.length,
            Boolean(sequenceData.circular),
        ),
    );
}
