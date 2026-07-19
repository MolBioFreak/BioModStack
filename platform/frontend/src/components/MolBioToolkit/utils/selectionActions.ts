import {
    reverseComplementSequence,
} from './nucleotides.js';

export interface PersistentSelection {
    start: number;
    end: number;
    clockwise?: boolean;
    type?: string;
    name?: string;
    annotationId?: string;
}

export interface SelectionRange {
    start: number;
    end: number;
}

export interface SelectionPlacement {
    start: number;
    end: number;
    wrapsOrigin: boolean;
}

export interface SelectionSnapshot {
    selection: PersistentSelection;
    ranges: SelectionRange[];
    placement: SelectionPlacement;
    sequence: string;
    length: number;
    coordinateLabel: string;
    coordinateKey: string;
}

function clampCoordinate(value: number, sequenceLength: number): number | null {
    if (!Number.isFinite(value) || sequenceLength < 0) {
        return null;
    }
    return Math.max(0, Math.min(Math.trunc(value), sequenceLength));
}

function normalizeSelection(
    selection: PersistentSelection,
    sequenceLength: number,
): PersistentSelection | null {
    const start = clampCoordinate(selection.start, sequenceLength);
    const end = clampCoordinate(selection.end, sequenceLength);
    if (start === null || end === null) {
        return null;
    }
    return {
        ...selection,
        start,
        end,
    };
}

export function hasRangeSelection(selection: PersistentSelection | null | undefined): boolean {
    return Boolean(selection && selection.start !== selection.end);
}

/**
 * SeqViz emits zero-width cursor selections at the beginning/end of pointer
 * gestures. Keep the last completed range until a new non-empty range arrives
 * or the caller explicitly clears selection state.
 */
export function resolvePersistentSelection<T extends PersistentSelection>(
    current: T | null,
    incoming: T,
    sequenceLength: number,
): T | null {
    const normalizedIncoming = normalizeSelection(incoming, sequenceLength) as T | null;
    if (!normalizedIncoming) {
        return current;
    }

    if (
        normalizedIncoming.start === normalizedIncoming.end
        && hasRangeSelection(current)
    ) {
        return current;
    }

    return normalizedIncoming;
}

/**
 * Convert SeqViz's directed display-space selection into one canonical
 * source-space range. Circular selections are represented clockwise; a
 * counter-clockwise drag therefore swaps its endpoints before any reverse-
 * display coordinate transform. Right-button emissions are ignored so a
 * context-menu gesture cannot replace the last committed drag.
 */
export function mapSeqVizSelectionToSource<T extends PersistentSelection>(
    selection: T,
    sequenceLength: number,
    circular: boolean,
    reverseCoordinates: boolean,
    pointerButton: number | null,
): T | null {
    if (pointerButton === 2) {
        return null;
    }

    const normalized = normalizeSelection(selection, sequenceLength) as T | null;
    if (!normalized) {
        return null;
    }

    let start = normalized.start;
    let end = normalized.end;
    if (circular) {
        if (normalized.clockwise === false) {
            [start, end] = [end, start];
        }
        if (reverseCoordinates) {
            [start, end] = [sequenceLength - end, sequenceLength - start];
        }
        return {
            ...normalized,
            start,
            end,
            clockwise: true,
        };
    }

    const lower = Math.min(start, end);
    const upper = Math.max(start, end);
    return {
        ...normalized,
        start: reverseCoordinates ? sequenceLength - upper : lower,
        end: reverseCoordinates ? sequenceLength - lower : upper,
    };
}

export function sequenceForPlotDisplay(
    sequence: string,
    sourceSequenceType: 'dna' | 'rna',
    reverseCoordinates = false,
): string {
    return reverseCoordinates
        ? reverseComplementSequence(sequence, sourceSequenceType)
        : sequence;
}

export function selectionForPlotDisplay<T extends PersistentSelection>(
    selection: T | null | undefined,
    sequenceLength: number,
    circular: boolean,
    reverseCoordinates = false,
): T | null {
    if (!selection) return null;
    return mapSeqVizSelectionToSource(
        selection,
        sequenceLength,
        circular,
        reverseCoordinates,
        null,
    );
}

export function selectionFromPlotRange(
    xRange: readonly number[] | null | undefined,
    sequenceLength: number,
    reverseCoordinates = false,
): PersistentSelection | null {
    if (!xRange || xRange.length < 2) return null;
    const start = Math.max(0, Math.floor(Math.min(xRange[0], xRange[1])));
    const end = Math.min(sequenceLength, Math.ceil(Math.max(xRange[0], xRange[1])));
    if (end <= start) return null;
    return mapSeqVizSelectionToSource(
        { start, end, clockwise: true, type: 'TRACK' },
        sequenceLength,
        false,
        reverseCoordinates,
        null,
    );
}

export function getSelectionRanges(
    selection: PersistentSelection | null | undefined,
    sequenceLength: number,
    circular: boolean,
): SelectionRange[] {
    if (!selection || sequenceLength <= 0) {
        return [];
    }

    const normalized = normalizeSelection(selection, sequenceLength);
    if (!normalized || normalized.start === normalized.end) {
        return [];
    }

    if (!circular || normalized.start < normalized.end) {
        return [{
            start: Math.min(normalized.start, normalized.end),
            end: Math.max(normalized.start, normalized.end),
        }];
    }

    return [
        { start: normalized.start, end: sequenceLength },
        { start: 0, end: normalized.end },
    ].filter((range) => range.end > range.start);
}

export function formatSelectionLabel(
    ranges: SelectionRange[],
    circular: boolean,
): string {
    if (ranges.length === 0) {
        return 'No selection';
    }
    if (ranges.length === 1) {
        return `${ranges[0].start + 1} - ${ranges[0].end}${circular ? ' (circular)' : ''}`;
    }
    return ranges
        .map((range) => `${range.start + 1} - ${range.end}`)
        .join(' + ');
}

export function createSelectionSnapshot(
    selection: PersistentSelection | null | undefined,
    sequence: string,
    circular: boolean,
): SelectionSnapshot | null {
    const ranges = getSelectionRanges(selection, sequence.length, circular);
    if (!selection || ranges.length === 0) {
        return null;
    }

    const normalizedSelection = normalizeSelection(selection, sequence.length);
    if (!normalizedSelection) {
        return null;
    }

    const selectedSequence = ranges
        .map((range) => sequence.slice(range.start, range.end))
        .join('');
    if (!selectedSequence) {
        return null;
    }

    const copiedSelection = { ...normalizedSelection };
    const copiedRanges = ranges.map((range) => ({ ...range }));
    const wrapsOrigin = circular && copiedRanges.length > 1;

    return {
        selection: copiedSelection,
        ranges: copiedRanges,
        placement: wrapsOrigin
            ? {
                start: copiedSelection.start,
                end: copiedSelection.end,
                wrapsOrigin: true,
            }
            : {
                start: copiedRanges[0].start,
                end: copiedRanges[copiedRanges.length - 1].end,
                wrapsOrigin: false,
            },
        sequence: selectedSequence,
        length: copiedRanges.reduce(
            (total, range) => total + (range.end - range.start),
            0,
        ),
        coordinateLabel: formatSelectionLabel(copiedRanges, circular),
        coordinateKey: copiedRanges
            .map((range) => `${range.start + 1}_${range.end}`)
            .join('_wrap_'),
    };
}

export type SelectionPrimerDirection = 'forward' | 'reverse';

export interface PreparedSelectionPrimer {
    sequence: string;
    sequenceType: 'dna' | 'rna';
    strand: 1 | -1;
}

export function buildPrimerTmRequest<T extends object>(
    prepared: PreparedSelectionPrimer,
    settings: T,
) {
    return {
        primers: [{
            sequence: prepared.sequence,
            sequence_type: prepared.sequenceType,
        }],
        settings,
    };
}

export interface NormalizedStoredPrimerPlacement {
    start: number;
    end: number;
    strand: 1 | -1;
    sites?: Array<{
        start: number;
        end: number;
        strand: 1 | -1;
        tm?: number;
        note?: string;
    }>;
}

export function normalizeStoredPrimerPlacement(
    primer: Record<string, unknown>,
): NormalizedStoredPrimerPlacement {
    const rawSites = Array.isArray(primer.sites) ? primer.sites : [];
    const sites = rawSites.flatMap((rawSite) => {
        if (!rawSite || typeof rawSite !== 'object') return [];
        const site = rawSite as Record<string, unknown>;
        const rawStart = Number(
            site.start ?? site.bindingSiteStart ?? primer.start ?? 0,
        );
        const rawEnd = Number(
            site.end ?? site.bindingSiteEnd ?? primer.end ?? 0,
        );
        const start = site.bindingSiteStart != null && site.start == null
            ? Math.max(0, rawStart - 1)
            : rawStart;
        const strand = site.strand === -1 || String(site.strand || '').toLowerCase() === 'bottom'
            ? -1
            : 1;
        if (!Number.isFinite(start) || !Number.isFinite(rawEnd) || rawEnd <= start) {
            return [];
        }
        const tm = Number.isFinite(site.tm)
            ? Number(site.tm)
            : Number.isFinite(site.meltingTemperature)
                ? Number(site.meltingTemperature)
                : undefined;
        const note = typeof site.note === 'string' ? site.note : undefined;
        return [{
            start,
            end: rawEnd,
            strand: strand as 1 | -1,
            ...(tm !== undefined ? { tm } : {}),
            ...(note !== undefined ? { note } : {}),
        }];
    });

    const firstSite = sites[0] || null;
    const storedStart = Number(primer.start);
    const storedEnd = Number(primer.end);
    const hasStoredStart = primer.start != null && Number.isFinite(storedStart);
    const hasStoredEnd = primer.end != null && Number.isFinite(storedEnd);
    const storedStrand = primer.strand === 1 || primer.strand === -1
        ? primer.strand
        : null;
    return {
        start: hasStoredStart ? storedStart : (firstSite?.start ?? 0),
        end: hasStoredEnd ? storedEnd : (firstSite?.end ?? 0),
        strand: storedStrand ?? firstSite?.strand ?? 1,
        sites: sites.length > 0 ? sites : undefined,
    };
}

interface PrimerPlacementInput {
    start: number;
    end: number;
    strand: 1 | -1;
    tm?: number;
    sites?: Array<{
        start: number;
        end: number;
        strand: 1 | -1;
        tm?: number;
    }>;
}

export function getPrimerRenderableSites(primer: PrimerPlacementInput) {
    if (primer.sites && primer.sites.length > 0) {
        return primer.sites.map((site) => ({ ...site }));
    }
    return [{
        start: primer.start,
        end: primer.end,
        strand: primer.strand,
        tm: primer.tm,
    }];
}

export function getPrimerHighlightRegions(
    primer: PrimerPlacementInput,
    color: string,
    label: string,
) {
    return getPrimerRenderableSites(primer).map((site) => ({
        start: site.start,
        end: site.end,
        color,
        label,
    }));
}

export function prepareSelectionPrimer(
    snapshot: SelectionSnapshot,
    direction: SelectionPrimerDirection,
    sourceSequenceType: 'dna' | 'rna' | 'protein',
): PreparedSelectionPrimer {
    const strand: 1 | -1 = direction === 'reverse' ? -1 : 1;
    const sequence = strand === 1
        ? snapshot.sequence
        : reverseComplementSequence(
            snapshot.sequence,
            sourceSequenceType === 'rna' ? 'rna' : 'dna',
        );
    return {
        sequence,
        sequenceType: sourceSequenceType === 'rna' ? 'rna' : 'dna',
        strand,
    };
}

interface BuildSelectionPrimerInput<TTmSettings> {
    id: string;
    name: string;
    notes?: string;
    snapshot: SelectionSnapshot;
    prepared: PreparedSelectionPrimer;
    tm?: number;
    gcPercent?: number;
    tmAlgorithm?: string;
    tmSaltCorrection?: string;
    tmSettings?: TTmSettings;
}

export function buildSelectionPrimer<TTmSettings>({
    id,
    name,
    notes,
    snapshot,
    prepared,
    tm,
    gcPercent,
    tmAlgorithm,
    tmSaltCorrection,
    tmSettings,
}: BuildSelectionPrimerInput<TTmSettings>) {
    return {
        id,
        name,
        sequence: prepared.sequence,
        sequenceType: prepared.sequenceType,
        start: snapshot.placement.start,
        end: snapshot.placement.end,
        strand: prepared.strand,
        sites: snapshot.ranges.map((range) => ({
            start: range.start,
            end: range.end,
            strand: prepared.strand,
            tm,
        })),
        tm,
        gc_percent: gcPercent,
        tm_algorithm: tmAlgorithm,
        tm_salt_correction: tmSaltCorrection,
        tm_settings: tmSettings,
        notes: {
            source: 'selection_dialog',
            ...(notes ? { note: notes } : {}),
        },
        provenance: {
            workflow: 'selection_dialog',
            wraps_origin: snapshot.placement.wrapsOrigin,
            selected_ranges: snapshot.ranges,
        },
    };
}
