import { canonicalizePrimerPlacement } from './selectionActions.js';

export interface ExportablePrimer {
    name: string;
    sequence: string;
    sequenceType?: 'dna' | 'rna';
    start: number;
    end: number;
    strand: 1 | -1;
    binding_status?: 'needs_review';
    binding_warning?: string;
    tm?: number;
    gc_percent?: number;
    sites?: Array<{
        start: number;
        end: number;
        strand: 1 | -1;
    }>;
}

function escapeTsv(value: unknown): string {
    return String(value ?? '')
        .replace(/\t/g, ' ')
        .replace(/[\r\n]+/g, ' ');
}

export function canonicalizeExportablePrimer(
    primer: ExportablePrimer,
    sequenceLength?: number,
    circular = false,
): ExportablePrimer {
    return sequenceLength === undefined
        ? primer
        : {
            ...primer,
            ...canonicalizePrimerPlacement(primer, sequenceLength, circular),
        };
}

export function formatPrimerSites(
    primer: ExportablePrimer,
    sequenceLength?: number,
    circular = false,
): string {
    if (primer.binding_status === 'needs_review') return '';
    const canonical = canonicalizeExportablePrimer(primer, sequenceLength, circular);
    const sites = canonical.sites && canonical.sites.length > 0
        ? canonical.sites
        : [{ start: canonical.start, end: canonical.end, strand: canonical.strand }];
    return sites
        .map((site) => `${site.start}-${site.end}:${site.strand}`)
        .join(';');
}

export function buildPrimersTsv(
    primers: ExportablePrimer[],
    defaultSequenceType: 'dna' | 'rna',
    sequenceLength?: number,
    circular = false,
): string {
    const headers = [
        'Name',
        'Sequence',
        'Sequence Type',
        'Start (0-based)',
        'End (half-open)',
        'Ordered Sites (start-end:strand)',
        'Strand',
        'Tm',
        'GC%',
        'Binding status',
        'Binding warning',
    ];
    const rows = primers.map((primer) => {
        const canonical = canonicalizeExportablePrimer(primer, sequenceLength, circular);
        const unplaced = canonical.binding_status === 'needs_review';
        return [
            canonical.name,
            canonical.sequence,
            canonical.sequenceType || defaultSequenceType,
            unplaced ? '' : canonical.start,
            unplaced ? '' : canonical.end,
            formatPrimerSites(canonical),
            canonical.strand === 1 ? '+' : '-',
            unplaced ? '' : canonical.tm ?? '',
            canonical.gc_percent ?? '',
            unplaced ? 'needs review' : 'placed',
            canonical.binding_warning || '',
        ].map(escapeTsv).join('\t');
    });
    return [headers.join('\t'), ...rows].join('\n');
}


// ── GenBank writer inputs ────────────────────────────────────────────────────
// The bio-parsers writer takes ascending-listed locations, 0-based starts and
// (with inclusive1BasedEnd) half-open ends. Features are stored in biological part
// order, so a minus-strand compound is listed in reverse for the file.

type ExportSegment = { start: number; end: number };

export interface ExportableFeature {
    name: string;
    type: string;
    start: number;
    end: number;
    strand: number;
    color?: string;
    description?: string;
    notes?: Record<string, unknown>;
    qualifiers?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    segments?: ExportSegment[];
}

function qualifierStrings(value: unknown): string[] {
    if (value === null || value === undefined) return [];
    if (Array.isArray(value)) return value.flatMap(qualifierStrings);
    // The GenBank writer does not escape double quotes inside a qualifier value.
    if (typeof value === 'object') return [JSON.stringify(value).replace(/"/g, "'")];
    return [String(value).replace(/"/g, "'")];
}

/** Every qualifier as a string array; provenance and other objects are kept as JSON notes. */
export function genbankQualifiers(feature: Pick<ExportableFeature, 'notes' | 'qualifiers' | 'provenance' | 'description'>): Record<string, string[]> {
    const merged: Record<string, string[]> = {};
    for (const source of [feature.notes, feature.qualifiers]) {
        for (const [key, value] of Object.entries(source || {})) {
            if (key === 'label') continue;
            const strings = qualifierStrings(value);
            if (strings.length === 0) continue;
            merged[key] = [...new Set([...(merged[key] || []), ...strings])];
        }
    }
    if (feature.provenance && Object.keys(feature.provenance).length > 0) {
        merged.bms_provenance = qualifierStrings(feature.provenance);
    }
    return merged;
}

export function genbankFeature(feature: ExportableFeature) {
    const biological = feature.segments && feature.segments.length > 0
        ? feature.segments
        : [{ start: feature.start, end: feature.end }];
    const listed = (feature.strand === -1 ? [...biological].reverse() : biological)
        .map((segment) => ({ start: segment.start, end: segment.end }));
    return {
        name: feature.name,
        type: feature.type,
        strand: feature.strand === -1 ? -1 : 1,
        start: listed[0].start,
        end: listed[0].end,
        locations: listed,
        color: feature.color,
        notes: genbankQualifiers(feature),
    };
}

interface GenbankPrimer extends ExportablePrimer {
    notes?: Record<string, unknown>;
}

/** Placed primers become primer_bind features on their annealing sites; unplaced ones are not written. */
export function genbankPrimerFeatures(primers: GenbankPrimer[], sequenceLength: number, circular: boolean) {
    return primers.flatMap((primer) => {
        const canonical = canonicalizeExportablePrimer(primer, sequenceLength, circular);
        if (canonical.binding_status === 'needs_review') return [];
        const sites = (canonical.sites && canonical.sites.length > 0
            ? canonical.sites
            : [{ start: canonical.start, end: canonical.end, strand: canonical.strand }])
            .filter((site) => site.end > site.start);
        if (sites.length === 0) return [];
        const notes: Record<string, string[]> = { note: [`sequence: ${canonical.sequence}`] };
        for (const key of ['tail_sequence', 'annealing_sequence']) {
            const value = primer.notes?.[key];
            if (typeof value === 'string' && value) notes[key] = [value];
        }
        return [genbankFeature({
            name: canonical.name, type: 'primer_bind', start: sites[0].start, end: sites[0].end,
            strand: canonical.strand,
            segments: sites.map((site) => ({ start: site.start, end: site.end })),
            notes,
        })];
    });
}

// ── Assembly order exports (R09) ─────────────────────────────────────────────

function csvCell(value: unknown): string {
    return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

export function buildAssemblyPrimersCsv(rows: Array<{
    id: string; name: string; direction: string; full_sequence: string; annealing_sequence: string;
    tail_sequence: string; tm?: number | null; tm_model?: string | null; warnings?: string[];
}>): string {
    const header = ['id', 'name', 'direction', 'full_sequence', 'annealing_sequence', 'tail_sequence', 'tm', 'tm_model', 'warnings'];
    const lines = rows.map((row) => [
        row.id, row.name, row.direction, row.full_sequence, row.annealing_sequence, row.tail_sequence,
        row.tm ?? '', row.tm_model ?? '', (row.warnings || []).join('; '),
    ].map(csvCell).join(','));
    return `${[header.join(','), ...lines].join('\n')}\n`;
}

export function buildFragmentsFasta(rows: Array<{ id: string; name: string; sequence: string }>): string {
    return `${rows.map((row) => `>${row.name || row.id}|length=${row.sequence.length}\n${row.sequence.match(/.{1,80}/g)?.join('\n') ?? ''}`).join('\n')}\n`;
}
