import { canonicalizePrimerPlacement } from './selectionActions.js';

export interface ExportablePrimer {
    name: string;
    sequence: string;
    sequenceType?: 'dna' | 'rna';
    start: number;
    end: number;
    strand: 1 | -1;
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
    ];
    const rows = primers.map((primer) => {
        const canonical = canonicalizeExportablePrimer(primer, sequenceLength, circular);
        return [
            canonical.name,
            canonical.sequence,
            canonical.sequenceType || defaultSequenceType,
            canonical.start,
            canonical.end,
            formatPrimerSites(canonical),
            canonical.strand === 1 ? '+' : '-',
            canonical.tm ?? '',
            canonical.gc_percent ?? '',
        ].map(escapeTsv).join('\t');
    });
    return [headers.join('\t'), ...rows].join('\n');
}
