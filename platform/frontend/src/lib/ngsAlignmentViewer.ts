import type { AlignmentLocusSlice, AlignmentPresentation } from './ngsAlignmentSession.js';

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
            id: `ngs-auxiliary-${role}`,
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
            id: 'ngs-auxiliary-junction-hotspots',
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

const MAX_BROWSER_ALIGNMENT_BYTES = 536_870_912;

export function alignmentTrackAutoLoadDisposition(sizeBytes: number | null | undefined): {
    autoLoad: boolean;
    reason: string | null;
} {
    if (typeof sizeBytes !== 'number' || !Number.isFinite(sizeBytes) || sizeBytes <= 0) {
        return {
            autoLoad: false,
            reason: 'Alignment size is unknown or invalid; full-source browser loading is disabled. A bounded presentation is required.',
        };
    }
    if (sizeBytes <= MAX_BROWSER_ALIGNMENT_BYTES) {
        return { autoLoad: true, reason: null };
    }
    return {
        autoLoad: false,
        reason: `Alignment is ${(sizeBytes / 1_048_576).toFixed(1)} MiB; browser track loading is disabled. Use Inspect reads instead.`,
    };
}

export interface BrowserAlignmentTrackInput {
    jobId: string;
    sessionId: string;
    alignmentUrl: string;
    alignmentIndexUrl: string;
    alignmentSizeBytes: number | null | undefined;
    presentation?: AlignmentPresentation | null;
    locusSlice?: AlignmentLocusSlice | null;
}

export interface BrowserAlignmentTrackSource {
    kind: 'full' | 'preview' | 'locus';
    name: 'Full alignment' | 'Primary-read preview' | 'Bounded full-source locus slice';
    bamUrl: string;
    baiUrl: string;
    byteSize: number;
    selectedReadCount: number | null;
    availableReadCount: number | null;
    policyVersion: number | null;
    capped: boolean;
    fullSourceDownload: { url: string; sizeBytes: number | null };
}

export function resolveBrowserAlignmentTrackSource(input: BrowserAlignmentTrackInput): BrowserAlignmentTrackSource | null {
    const fullSourceDownload = {
        url: input.alignmentUrl,
        sizeBytes: typeof input.alignmentSizeBytes === 'number' && Number.isFinite(input.alignmentSizeBytes)
            && input.alignmentSizeBytes > 0
            ? input.alignmentSizeBytes : null,
    };
    if (input.locusSlice) {
        return {
            kind: 'locus', name: 'Bounded full-source locus slice', bamUrl: input.locusSlice.bam.url,
            baiUrl: input.locusSlice.index.url, byteSize: input.locusSlice.bam.size_bytes,
            selectedReadCount: input.locusSlice.selected_read_count,
            availableReadCount: input.locusSlice.overlapping_read_count,
            policyVersion: input.locusSlice.policy.version, capped: input.locusSlice.capped, fullSourceDownload,
        };
    }
    if (alignmentTrackAutoLoadDisposition(input.alignmentSizeBytes).autoLoad) {
        return {
            kind: 'full', name: 'Full alignment', bamUrl: input.alignmentUrl, baiUrl: input.alignmentIndexUrl,
            byteSize: input.alignmentSizeBytes as number, selectedReadCount: null, availableReadCount: null,
            policyVersion: null, capped: false, fullSourceDownload,
        };
    }
    if (!input.presentation || input.presentation.job_id !== input.jobId || input.presentation.session_id !== input.sessionId) return null;
    return {
        kind: 'preview', name: 'Primary-read preview', bamUrl: input.presentation.preview.bam.url,
        baiUrl: input.presentation.preview.index.url, byteSize: input.presentation.preview.bam.size_bytes,
        selectedReadCount: input.presentation.preview.selected_read_count,
        availableReadCount: input.presentation.source.primary_read_count,
        policyVersion: input.presentation.policy.version,
        capped: input.presentation.preview.selected_read_count < input.presentation.source.primary_read_count,
        fullSourceDownload,
    };
}

export function buildAlignmentTrackConfig(
    source: BrowserAlignmentTrackSource,
    height: number,
    options: { displayMode?: string; colorBy?: string; groupBy?: string } = {},
): Record<string, unknown> {
    return {
        name: source.name, type: 'alignment', format: 'bam', url: source.bamUrl, indexURL: source.baiUrl,
        showSoftClips: true, showCoverage: source.kind === 'full', showMismatches: true, showAllBases: true,
        showInsertionText: true, autoHeight: false, height, displayMode: options.displayMode || 'EXPANDED',
        visibilityWindow: -1, samplingWindowSize: 40, samplingDepth: 10000, maxRows: 500,
        alignmentRowHeight: 9, squishedRowHeight: 4,
        ...(options.colorBy && options.colorBy !== 'none' ? { colorBy: options.colorBy } : {}),
        ...(options.groupBy && options.groupBy !== 'none' ? { groupBy: options.groupBy } : {}),
    };
}

export function buildFullSourceCoverageTrackConfig(presentation: AlignmentPresentation): Record<string, unknown> {
    return {
        id: 'ngs-full-source-primary-read-coverage',
        name: 'Full-source primary-read coverage', type: 'wig', format: 'bedgraph',
        url: presentation.coverage.artifact.url, autoscale: true, graphType: 'bar', height: 72,
    };
}

export function buildLocalIgvConfig(input: LocalIgvConfigInput): Record<string, unknown> {
    const sequenceTrack = {
        id: 'ngs-reference-bases',
        name: 'Reference bases',
        type: 'sequence',
        fastaURL: input.fastaUrl,
        ...(input.faiUrl ? { indexURL: input.faiUrl } : {}),
        order: -1000,
    };
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
            sequenceTrack,
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

interface MutableIgvTrackBrowser {
    findTracks: (predicate: (track: Record<string, unknown>) => boolean) => Array<Record<string, unknown>>;
    loadTrack: (config: Record<string, unknown>) => Promise<Record<string, unknown>>;
    removeTrack: (track: Record<string, unknown>) => void;
}

export async function replaceAlignmentTrackTransactionally(
    browser: MutableIgvTrackBrowser,
    config: Record<string, unknown>,
    isCurrent: () => boolean,
): Promise<Record<string, unknown> | null> {
    const previousTracks = browser.findTracks((track) => track.type === 'alignment') || [];
    const loadedTrack = await browser.loadTrack(config);
    if (!loadedTrack) throw new Error('IGV did not return the loaded alignment track.');
    if (!isCurrent()) {
        browser.removeTrack(loadedTrack);
        return null;
    }
    for (const track of previousTracks) {
        if (track !== loadedTrack) browser.removeTrack(track);
    }
    return loadedTrack;
}

export async function loadMissingTracksById(
    browser: MutableIgvTrackBrowser,
    configs: Array<Record<string, unknown>>,
    isCurrent: () => boolean,
    onError?: (config: Record<string, unknown>, reason: unknown) => void,
): Promise<void> {
    for (const config of configs) {
        const id = config.id;
        if (typeof id !== 'string' || !id) throw new Error('IGV auxiliary track id is required.');
        if ((browser.findTracks((track) => track.id === id) || []).length > 0) continue;
        try {
            const loadedTrack = await browser.loadTrack(config);
            if (!isCurrent()) {
                if (loadedTrack) browser.removeTrack(loadedTrack);
                return;
            }
        } catch (reason) {
            if (!isCurrent()) return;
            if (!onError) throw reason;
            onError(config, reason);
        }
    }
}
