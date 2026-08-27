export type AlignmentViewerMode = 'primary' | 'dimer_candidates';

export interface AlignmentViewerFile {
    path: string;
    name?: string;
}

export interface AlignmentViewerArtifacts {
    mode: AlignmentViewerMode;
    bam: AlignmentViewerFile | null;
    bai: AlignmentViewerFile | null;
    fasta: AlignmentViewerFile | null;
    fai: AlignmentViewerFile | null;
    ready: boolean;
    missing: Array<'alignment' | 'alignment_index' | 'reference'>;
}

const pathOf = (file: AlignmentViewerFile): string => file.path || file.name || '';
const isDimer = (path: string): boolean => /(?:^|[/_.-])(dimer|multimer|concatemer)(?:[/_.-]|$)/i.test(path);

function choose(paths: AlignmentViewerFile[], patterns: RegExp[]): AlignmentViewerFile | null {
    for (const pattern of patterns) {
        const match = paths.find((file) => pattern.test(pathOf(file)));
        if (match) return match;
    }
    return paths[0] || null;
}

function findIndex(bam: AlignmentViewerFile | null, files: AlignmentViewerFile[]): AlignmentViewerFile | null {
    if (!bam) return null;
    const path = pathOf(bam);
    const candidates = [`${path}.bai`, path.replace(/\.bam$/i, '.bai'), `${path}.csi`, path.replace(/\.bam$/i, '.csi')];
    return files.find((file) => candidates.includes(pathOf(file))) || null;
}

function findFastaIndex(fasta: AlignmentViewerFile | null, files: AlignmentViewerFile[]): AlignmentViewerFile | null {
    if (!fasta) return null;
    const path = pathOf(fasta);
    return files.find((file) => pathOf(file) === `${path}.fai`) || null;
}

export function resolveAlignmentViewerArtifacts(
    files: AlignmentViewerFile[],
    mode: AlignmentViewerMode = 'primary',
): AlignmentViewerArtifacts {
    const modeFiles = files.filter((file) => mode === 'dimer_candidates' ? isDimer(pathOf(file)) : !isDimer(pathOf(file)));
    const bamFiles = modeFiles.filter((file) => /\.bam$/i.test(pathOf(file)) && !/\.bam\.(?:bai|csi)$/i.test(pathOf(file)));
    const fastaFiles = modeFiles.filter((file) => /\.(?:fa|fasta)$/i.test(pathOf(file)) && !/\.fai$/i.test(pathOf(file)));
    const bam = choose(bamFiles, mode === 'primary'
        ? [/fastq_qc\/aligned\.bam$/i, /\/aligned\.bam$/i, /alignment/i]
        : [/dimer_candidates\.bam$/i, /dimer.*\.bam$/i]);
    const fasta = choose(fastaFiles, mode === 'primary'
        ? [/reference\.normalized\.fasta$/i, /reference_qc\.fasta$/i, /\/reference\.(?:fa|fasta)$/i]
        : [/dimer_reference\.(?:fa|fasta)$/i, /dimer.*\.(?:fa|fasta)$/i]);
    const bai = findIndex(bam, modeFiles);
    const fai = findFastaIndex(fasta, modeFiles);
    const missing: AlignmentViewerArtifacts['missing'] = [];
    if (!bam) missing.push('alignment');
    if (!bai) missing.push('alignment_index');
    if (!fasta) missing.push('reference');
    return { mode, bam, bai, fasta, fai, ready: missing.length === 0, missing };
}

export async function createGenerationBoundResource<T>(
    create: () => Promise<T>,
    remove: (resource: T) => void,
    isCurrent: () => boolean,
): Promise<T | null> {
    const resource = await create();
    if (!isCurrent()) {
        remove(resource);
        return null;
    }
    return resource;
}

export function ownsIgvLoadTerminalState(
    loadToken: number,
    currentToken: number,
    timeoutInvalidationToken: number | null,
    cancelled: boolean,
): boolean {
    if (cancelled) return false;
    return currentToken === loadToken
        || (timeoutInvalidationToken !== null && currentToken === timeoutInvalidationToken);
}


export async function createGenerationBoundResourceWithTimeout<T>(options: {
    create: () => Promise<T>;
    remove: (resource: T) => void;
    isCurrent: () => boolean;
    invalidate: () => void;
    timeoutMs: number;
    timeoutMessage: string;
}): Promise<T | null> {
    const resource = createGenerationBoundResource(options.create, options.remove, options.isCurrent);
    let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
        timeoutHandle = setTimeout(() => {
            options.invalidate();
            reject(new Error(options.timeoutMessage));
        }, options.timeoutMs);
    });
    try {
        return await Promise.race([resource, timeout]);
    } finally {
        if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
    }
}

export async function awaitCurrentGeneration<T>(
    operation: Promise<T>,
    isCurrent: () => boolean,
): Promise<T | null> {
    const value = await operation;
    return isCurrent() ? value : null;
}

export function removeIgvBrowser(
    library: { removeBrowser?: (browser: unknown) => void } | null,
    browser: unknown,
): void {
    if (!browser) return;
    if (!library || typeof library.removeBrowser !== 'function') {
        throw new Error('IGV library removeBrowser is unavailable.');
    }
    library.removeBrowser(browser);
}

export function resolveSessionAuxiliaryTracks(
    artifacts: Record<string, { url: string } | undefined>,
): Array<Record<string, unknown>> {
    const bedgraphTracks = [
        ['coverage_depth', 'Coverage Depth', 'bar', '#4ea6ff'],
        ['position_gradient', 'Position Gradient', 'heatmap', '#8b9cff'],
        ['gc_content', 'GC Content', 'line', '#55c58a'],
        ['gc_zscore', 'GC Z-score', 'line', '#b884f7'],
        ['split_read_density', 'Split-read Density', 'bar', '#ffad5c'],
        ['soft_clip_density', 'Soft-clip Density', 'bar', '#ff6b7a'],
    ] as const;
    const tracks: Array<Record<string, unknown>> = [];
    for (const [role, name, graphType, color] of bedgraphTracks) {
        const artifact = artifacts[role];
        if (!artifact) continue;
        tracks.push({
            name,
            type: 'wig',
            format: 'bedgraph',
            url: artifact.url,
            graphType,
            autoscale: true,
            color,
            height: 56,
        });
    }
    const junctions = artifacts.junction_hotspots;
    if (junctions) {
        tracks.push({
            name: 'Junction Hotspots',
            type: 'annotation',
            format: 'bed',
            url: junctions.url,
            displayMode: 'EXPANDED',
            color: '#ffbe6f',
            height: 56,
        });
    }
    return tracks;
}

export interface LocalIgvConfigInput {
    referenceId: string;
    referenceName: string;
    fastaUrl: string;
    faiUrl?: string | null;
    bamUrl?: string | null;
    baiUrl?: string | null;
    initialLocus?: string | null;
    auxiliaryTracks: Array<Record<string, unknown>>;
}

export function buildLocalIgvConfig(input: LocalIgvConfigInput): Record<string, unknown> {
    const alignmentTracks = input.bamUrl && input.baiUrl
        ? [{
            name: 'Aligned Reads',
            type: 'alignment',
            format: 'bam',
            url: input.bamUrl,
            indexURL: input.baiUrl,
            height: 420,
            displayMode: 'EXPANDED',
            colorBy: 'strand',
        }]
        : [];
    return {
        loadDefaultGenomes: false,
        genomeList: [],
        search: false,
        queryParametersSupported: false,
        reference: {
            id: input.referenceId,
            name: input.referenceName,
            fastaURL: input.fastaUrl,
            ...(input.faiUrl
                ? { indexURL: input.faiUrl, indexed: true }
                : { indexed: false }),
        },
        ...(input.initialLocus ? { locus: input.initialLocus } : {}),
        tracks: [
            ...alignmentTracks,
            ...input.auxiliaryTracks,
        ],
    };
}

export function parseLocalIgvRange(
    value: string,
    referenceContig: string,
    referenceLength: number,
): string | null {
    if (!referenceContig || !Number.isInteger(referenceLength) || referenceLength < 1) return null;
    const match = /^([^:\s]+):(\d+)-(\d+)$/.exec(value.trim());
    if (!match || match[1] !== referenceContig) return null;
    const start = Number(match[2]);
    const end = Number(match[3]);
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start || end > referenceLength) {
        return null;
    }
    return `${referenceContig}:${start}-${end}`;
}

export function resolveBoundSessionLocus(
    requestedSessionId: string,
    selectedSessionId: string,
    contig: string,
    start: number,
    end = start,
): string | null {
    if (requestedSessionId !== selectedSessionId || !contig || start < 1 || end < start) return null;
    return `${contig}:${start}-${end}`;
}

export interface PendingSessionNavigation {
    sessionId: string;
    locus: string;
}

export function resolvePendingSessionLocus(
    pending: PendingSessionNavigation | null,
    selectedSessionId: string,
): string | null {
    return pending?.sessionId === selectedSessionId ? pending.locus : null;
}

export interface AlignmentReadLocus {
    contig: string;
    start: number;
    end: number;
}

export function resolveIgvReadLocus(loci: unknown): AlignmentReadLocus | null {
    if (!Array.isArray(loci) || loci.length === 0) return null;
    const first = loci[0] as { chr?: unknown; start?: unknown; end?: unknown };
    if (
        typeof first.chr !== 'string'
        || !first.chr.trim()
        || typeof first.start !== 'number'
        || !Number.isFinite(first.start)
        || typeof first.end !== 'number'
        || !Number.isFinite(first.end)
    ) {
        return null;
    }
    const start = Math.max(1, Math.floor(first.start) + 1);
    const end = Math.max(start, Math.ceil(first.end));
    return { contig: first.chr.trim(), start, end };
}
