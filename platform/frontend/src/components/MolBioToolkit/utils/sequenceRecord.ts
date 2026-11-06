/**
 * Normalize stored nucleotide sequence records into viewer SequenceData.
 * Shared by the MolBio Toolkit and the read-only guest share viewer.
 */

import type { SequenceAnalysisTrack } from '../../../lib/api';
import { getFeatureColor, normalizeFeatureType } from '../featureCatalog';
import type { AnalysisTrack, Feature, NucleotideSequenceResponse, Primer, SequenceData } from '../types';
import { dedupeFeatures, featureBounds } from './features';
import { inferSequenceTypeFromSequence } from './nucleotides';
import { normalizeStoredPrimerPlacement } from './selectionActions';

export function normalizeFeatureList(features: Feature[]): Feature[] {
    return dedupeFeatures(features);
}

export function normalizeFeatureRecord(feature: Partial<Feature> & Record<string, UntypedApiValue>, fallbackId: string): Feature {
    const rawSegments = Array.isArray(feature.segments)
        ? feature.segments
        : Array.isArray(feature.locations)
            ? feature.locations
            : [];

    const segments = rawSegments
        .map((segment: UntypedApiValue) => ({
            start: Number(segment?.start ?? segment?.startIndex ?? segment?.rangeBegin ?? feature.start ?? 0),
            end: Number(segment?.end ?? segment?.endIndex ?? segment?.rangeEnd ?? feature.end ?? 0),
        }))
        .filter((segment: { start: number; end: number }) => Number.isFinite(segment.start) && Number.isFinite(segment.end) && segment.end > segment.start);

    const start = typeof feature.start === 'number'
        ? feature.start
        : (segments[0]?.start ?? 0);
    const end = typeof feature.end === 'number'
        ? feature.end
        : (segments[segments.length - 1]?.end ?? 0);

    const normalizedType = normalizeFeatureType(feature.type || 'misc_feature');
    const normalized: Feature = {
        id: feature.id || fallbackId,
        name: feature.name || feature.type || 'feature',
        type: normalizedType,
        start,
        end,
        strand: feature.strand === -1 ? -1 : 1,
        color: feature.color || getFeatureColor(normalizedType),
        description: feature.description,
        notes: feature.notes,
        qualifiers: feature.qualifiers || feature.notes,
        provenance: feature.provenance,
        segments: segments.length > 0 ? segments : undefined,
    };

    const bounds = featureBounds(normalized);
    return {
        ...normalized,
        start: bounds.start,
        end: bounds.end,
    };
}

/**
 * Feature from the browser parser (Teselagen) read with inclusive1BasedEnd: 0-based start,
 * half-open end, ascending location list, wrapped pairs as start > end. Converted to the storage convention:
 * half-open ends, ordered non-wrapping segments in biological part order.
 */
export function browserParsedFeature(raw: Record<string, UntypedApiValue>, fallbackId: string, sequenceLength: number): Feature {
    const locations: Array<{ start: number; end: number }> = Array.isArray(raw.locations) && raw.locations.length > 0
        ? raw.locations
        : [{ start: raw.start, end: raw.end }];
    let pieces = locations.flatMap((loc) => {
        const start = Number(loc.start);
        const end = Number(loc.end);
        return start >= end && sequenceLength > 0
            ? [{ start, end: sequenceLength }, { start: 0, end }]
            : [{ start, end }];
    });
    if (raw.strand === -1) pieces = pieces.reverse();
    return normalizeFeatureRecord({ ...raw, segments: pieces, locations: undefined, start: undefined, end: undefined }, fallbackId);
}

/** primer_bind features become primers (sites in top-strand order, sequence read from the template). */
export function primersFromPrimerBindFeatures(
    features: Feature[],
    sequence: string,
): { features: Feature[]; primers: Primer[] } {
    const primers: Primer[] = [];
    const rest: Feature[] = [];
    const complement: Record<string, string> = { A: 'T', C: 'G', G: 'C', T: 'A' };
    for (const feature of features) {
        if (feature.type !== 'primer_bind') { rest.push(feature); continue; }
        const biological = feature.segments && feature.segments.length > 0 ? feature.segments : [{ start: feature.start, end: feature.end }];
        const sites = (feature.strand === -1 ? [...biological].reverse() : biological)
            .map((segment) => ({ start: segment.start, end: segment.end, strand: feature.strand }));
        const top = sites.map((site) => sequence.slice(site.start, site.end)).join('').toUpperCase();
        const bases = feature.strand === -1
            ? top.split('').reverse().map((base) => complement[base] ?? base).join('')
            : top;
        primers.push({
            id: feature.id, name: feature.name, sequence: bases, sequenceType: 'dna',
            start: sites[0].start, end: sites[sites.length - 1].end, strand: feature.strand, sites,
            notes: feature.notes, provenance: feature.provenance,
        });
    }
    return { features: rest, primers };
}

export function normalizePrimerRecord(
    primer: Partial<Primer> & Record<string, UntypedApiValue>,
    fallbackId: string,
    sequenceLength: number,
    circular: boolean,
): Primer {
    const placement = normalizeStoredPrimerPlacement(primer, sequenceLength, circular);
    return {
        id: primer.id || fallbackId,
        name: primer.name || 'Primer',
        sequence: (primer.sequence || '').toUpperCase(),
        sequenceType: primer.sequenceType || primer.sequence_type || inferSequenceTypeFromSequence(primer.sequence || ''),
        start: placement.start,
        end: placement.end,
        strand: placement.strand,
        tm: placement.binding_status === 'needs_review' ? undefined : primer.tm,
        gc_percent: primer.gc_percent,
        tm_algorithm: placement.binding_status === 'needs_review' ? undefined : primer.tm_algorithm,
        tm_salt_correction: placement.binding_status === 'needs_review' ? undefined : primer.tm_salt_correction,
        tm_settings: placement.binding_status === 'needs_review' ? undefined : primer.tm_settings,
        notes: primer.notes,
        provenance: primer.provenance,
        sites: placement.sites,
        binding_status: placement.binding_status,
        binding_warning: placement.binding_warning,
    };
}

export function sequenceDataFromApiRecord(seq: NucleotideSequenceResponse): SequenceData {
    return {
        name: seq.name,
        description: seq.description ?? undefined,
        sequence: seq.sequence,
        circular: seq.is_circular,
        sequenceType: seq.sequence_type,
        moleculeStrandedness: seq.molecule_strandedness,
        moleculeOrientation: seq.molecule_orientation,
        moleculeLabel: seq.molecule_label,
        features: normalizeFeatureList((seq.features || []).map((feature: Feature, index: number) => normalizeFeatureRecord(
            feature as Feature & Record<string, UntypedApiValue>,
            feature.id || `loaded_feature_${index}`,
        ))),
        primers: (seq.primers || []).map((primer: Primer, index: number) => normalizePrimerRecord(
            primer as Primer & Record<string, UntypedApiValue>,
            primer.id || `loaded_primer_${index}`,
            seq.sequence.length,
            seq.is_circular,
        )),
        translations: [],
        analysisTracks: (seq.analysis_tracks || []).map(trackFromApi),
        organism: seq.organism ?? undefined,
        accession: seq.accession ?? undefined,
        sourceFile: seq.source_file ?? undefined,
        parentId: seq.parent_id ?? null,
        operation: seq.operation ?? null,
        operationParams: seq.operation_params ?? null,
        version: seq.version ?? null,
    };
}

export function trackFromApi(track: SequenceAnalysisTrack): AnalysisTrack {
    return {
        id: track.id,
        name: track.name,
        kind: track.kind,
        description: track.description ?? undefined,
        color: track.color ?? undefined,
        sourceFormat: track.source_format ?? undefined,
        sourceName: track.source_name ?? undefined,
        sourceUrl: track.source_url ?? undefined,
        normalization: track.normalization ?? undefined,
        values: track.values || [],
        minValue: track.min_value ?? undefined,
        maxValue: track.max_value ?? undefined,
        createdAt: track.created_at ?? undefined,
    };
}
