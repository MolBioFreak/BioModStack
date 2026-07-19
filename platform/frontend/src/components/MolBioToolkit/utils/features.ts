export interface FeatureRecord {
    id: string;
    name: string;
    type: string;
    start: number;
    end: number;
    strand: 1 | -1;
    color?: string;
    description?: string;
    notes?: Record<string, unknown>;
    qualifiers?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    segments?: Array<{ start: number; end: number }>;
}

type Feature = FeatureRecord;

type Segment = { start: number; end: number };
type TransformOperation = 'reverse' | 'complement' | 'reverse_complement';

export function featureSegments(feature: Feature): Array<{ start: number; end: number }> {
    if (feature.segments && feature.segments.length > 0) {
        return [...feature.segments]
            .map((segment) => ({ start: segment.start, end: segment.end }));
    }
    return [{ start: feature.start, end: feature.end }];
}

export function featureBounds(feature: Feature): { start: number; end: number } {
    const segments = featureSegments(feature);
    return {
        start: Math.min(...segments.map((segment) => segment.start)),
        end: Math.max(...segments.map((segment) => segment.end)),
    };
}

export function featureLength(feature: Feature): number {
    return featureSegments(feature).reduce((sum, segment) => sum + (segment.end - segment.start), 0);
}

function mergedCoverageSegments(feature: Feature): Segment[] {
    const sorted = [...featureSegments(feature)].sort(
        (left, right) => left.start - right.start || left.end - right.end,
    );
    const merged: Segment[] = [];
    for (const segment of sorted) {
        const previous = merged[merged.length - 1];
        if (!previous || segment.start > previous.end) {
            merged.push({ ...segment });
        } else {
            previous.end = Math.max(previous.end, segment.end);
        }
    }
    return merged;
}

export function featureOverlapLength(left: Feature, right: Feature): number {
    const leftSegments = mergedCoverageSegments(left);
    const rightSegments = mergedCoverageSegments(right);
    let leftIndex = 0;
    let rightIndex = 0;
    let overlap = 0;
    while (leftIndex < leftSegments.length && rightIndex < rightSegments.length) {
        const leftSegment = leftSegments[leftIndex];
        const rightSegment = rightSegments[rightIndex];
        overlap += Math.max(
            0,
            Math.min(leftSegment.end, rightSegment.end) - Math.max(leftSegment.start, rightSegment.start),
        );
        if (leftSegment.end <= rightSegment.end) {
            leftIndex += 1;
        } else {
            rightIndex += 1;
        }
    }
    return overlap;
}

export function featureHighlightRegions(feature: Feature, color: string) {
    return featureSegments(feature).map((segment) => ({
        start: segment.start,
        end: segment.end,
        color,
        label: feature.name,
    }));
}

export function featureCoordinateLabel(feature: Feature): string {
    return featureSegments(feature)
        .map((segment) => `${segment.start + 1}–${segment.end}`)
        .join(' + ');
}

function normalizeSegments(segments: Segment[]): Segment[] {
    return [...segments]
        .filter((segment) => Number.isFinite(segment.start) && Number.isFinite(segment.end) && segment.end > segment.start)
        .map((segment) => ({ start: segment.start, end: segment.end }));
}

function withFeatureSegments(feature: Feature, segments: Segment[]): Feature | null {
    const normalized = normalizeSegments(segments);
    if (normalized.length === 0) {
        return null;
    }

    const bounds = {
        start: Math.min(...normalized.map((segment) => segment.start)),
        end: Math.max(...normalized.map((segment) => segment.end)),
    };
    const preserveSegments = Boolean(feature.segments && feature.segments.length > 0) || normalized.length > 1;

    return {
        ...feature,
        start: bounds.start,
        end: bounds.end,
        segments: preserveSegments ? normalized : undefined,
    };
}

function flipStrand(strand: 1 | -1): 1 | -1 {
    return strand === 1 ? -1 : 1;
}

function segmentsContainedWithin(feature: Feature, start: number, end: number): boolean {
    return featureSegments(feature).every((segment) => segment.start >= start && segment.end <= end);
}

export function remapFeatureAfterInsertion(feature: Feature, position: number, insertedLength: number): Feature {
    if (insertedLength <= 0) {
        return feature;
    }

    const nextSegments = featureSegments(feature).map((segment) => {
        if (segment.end <= position) {
            return segment;
        }

        if (segment.start >= position) {
            return {
                start: segment.start + insertedLength,
                end: segment.end + insertedLength,
            };
        }

        return {
            start: segment.start,
            end: segment.end + insertedLength,
        };
    });

    return withFeatureSegments(feature, nextSegments) || feature;
}

export function remapFeatureAfterDeletion(feature: Feature, start: number, end: number): Feature | null {
    const deletedLength = end - start;
    if (deletedLength <= 0) {
        return feature;
    }

    const nextSegments = featureSegments(feature).flatMap((segment) => {
        if (segment.end <= start) {
            return [segment];
        }

        if (segment.start >= end) {
            return [{
                start: segment.start - deletedLength,
                end: segment.end - deletedLength,
            }];
        }

        const nextStart = segment.start < start ? segment.start : start;
        const nextEnd = segment.end > end ? segment.end - deletedLength : start;
        return nextEnd > nextStart ? [{ start: nextStart, end: nextEnd }] : [];
    });

    return withFeatureSegments(feature, nextSegments);
}

export function transformFeatureForSelection(
    feature: Feature,
    start: number,
    end: number,
    operation: TransformOperation,
): Feature {
    if (!segmentsContainedWithin(feature, start, end)) {
        return feature;
    }

    const currentSegments = featureSegments(feature);
    const nextSegments = operation === 'complement'
        ? currentSegments
        : currentSegments.map((segment) => ({
            start: start + (end - segment.end),
            end: start + (end - segment.start),
        }));

    const nextFeature = withFeatureSegments(feature, nextSegments) || feature;
    return {
        ...nextFeature,
        strand: flipStrand(feature.strand),
    };
}

function normalizeLabel(value: string | undefined): string {
    return (value || '').trim().replace(/\s+/g, ' ').toLowerCase();
}

function normalizeNoteValue(value: unknown): string[] {
    if (value == null) {
        return [];
    }
    if (Array.isArray(value)) {
        return value
            .flatMap((item) => normalizeNoteValue(item))
            .filter((item) => item.length > 0);
    }
    const normalized = String(value).trim();
    return normalized ? [normalized] : [];
}

function mergeNoteValues(existingValue: unknown, incomingValue: unknown): unknown {
    const merged = Array.from(new Set([
        ...normalizeNoteValue(existingValue),
        ...normalizeNoteValue(incomingValue),
    ]));

    if (merged.length === 0) {
        return undefined;
    }
    if (merged.length === 1) {
        return merged[0];
    }
    return merged;
}

function mergeFeatureNotes(
    existingNotes?: Record<string, unknown>,
    incomingNotes?: Record<string, unknown>,
): Record<string, unknown> | undefined {
    const merged: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(existingNotes || {})) {
        const nextValue = mergeNoteValues(undefined, value);
        if (nextValue !== undefined) {
            merged[key] = nextValue;
        }
    }
    for (const [key, value] of Object.entries(incomingNotes || {})) {
        const nextValue = mergeNoteValues(merged[key], value);
        if (nextValue !== undefined) {
            merged[key] = nextValue;
        }
    }
    return Object.keys(merged).length > 0 ? merged : undefined;
}

function preferLongerText(existing?: string, incoming?: string): string | undefined {
    const safeExisting = (existing || '').trim();
    const safeIncoming = (incoming || '').trim();
    if (!safeExisting) {
        return safeIncoming || undefined;
    }
    if (!safeIncoming) {
        return safeExisting;
    }
    return safeIncoming.length > safeExisting.length ? safeIncoming : safeExisting;
}

function featureIdentityKey(feature: Feature): string {
    return [
        normalizeLabel(feature.name),
        normalizeLabel(feature.type),
        featureSegments(feature).map((segment) => `${segment.start}-${segment.end}`).join(','),
        feature.strand,
    ].join('|');
}

function mergeFeatures(existing: Feature, incoming: Feature): Feature {
    const bounds = featureBounds(existing);
    return {
        ...existing,
        id: existing.id || incoming.id,
        name: existing.name || incoming.name,
        type: existing.type || incoming.type,
        start: bounds.start,
        end: bounds.end,
        strand: existing.strand,
        color: existing.color || incoming.color,
        description: preferLongerText(existing.description, incoming.description),
        notes: mergeFeatureNotes(existing.notes, incoming.notes),
        qualifiers: mergeFeatureNotes(existing.qualifiers, incoming.qualifiers),
        provenance: mergeFeatureNotes(existing.provenance, incoming.provenance),
        segments: featureSegments(existing),
    };
}

export function dedupeFeatures(features: Feature[]): Feature[] {
    const mergedByKey = new Map<string, Feature>();

    for (const feature of features) {
        const key = featureIdentityKey(feature);
        const existing = mergedByKey.get(key);
        if (!existing) {
            mergedByKey.set(key, {
                ...feature,
                name: feature.name?.trim() || 'feature',
                type: feature.type?.trim() || 'misc_feature',
                ...featureBounds(feature),
                segments: featureSegments(feature),
            });
            continue;
        }
        mergedByKey.set(key, mergeFeatures(existing, feature));
    }

    return Array.from(mergedByKey.values());
}
