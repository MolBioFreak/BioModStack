import type { Feature } from '../types';

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
        feature.start,
        feature.end,
        feature.strand,
    ].join('|');
}

function mergeFeatures(existing: Feature, incoming: Feature): Feature {
    return {
        ...existing,
        id: existing.id || incoming.id,
        name: existing.name || incoming.name,
        type: existing.type || incoming.type,
        start: existing.start,
        end: existing.end,
        strand: existing.strand,
        color: existing.color || incoming.color,
        description: preferLongerText(existing.description, incoming.description),
        notes: mergeFeatureNotes(existing.notes, incoming.notes),
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
            });
            continue;
        }
        mergedByKey.set(key, mergeFeatures(existing, feature));
    }

    return Array.from(mergedByKey.values());
}
