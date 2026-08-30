import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    alignmentTrackAutoLoadDisposition,
    buildAlignmentTrackConfig,
    buildFullSourceCoverageTrackConfig,
    buildLocalIgvConfig,
    resolveAlignmentViewerArtifacts,
    resolveBrowserAlignmentTrackSource,
    replaceAlignmentTrackTransactionally,
    loadMissingTracksById,
    locusMatchesAlignmentSlice,
} from '../src/lib/ngsAlignmentViewer.js';
import {
    buildAlignmentLocusSliceRequest,
    normalizeAlignmentLocusSlice,
    normalizeAlignmentPresentation,
} from '../src/lib/ngsAlignmentSession.js';

const files = [
    { path: 'fastq_qc/aligned.bam' },
    { path: 'fastq_qc/aligned.bam.bai' },
    { path: 'fastq_qc/reference.normalized.fasta' },
    { path: 'fastq_qc/reference.normalized.fasta.fai' },
    { path: 'fastq_qc/dimer_candidates.bam' },
    { path: 'fastq_qc/dimer_candidates.bam.bai' },
    { path: 'fastq_qc/dimer_reference.fasta' },
    { path: 'fastq_qc/dimer_reference.fasta.fai' },
];

test('primary alignment session cannot silently select dimer evidence', () => {
    const result = resolveAlignmentViewerArtifacts(files, 'primary');

    assert.equal(result.ready, true);
    assert.equal(result.bam?.path, 'fastq_qc/aligned.bam');
    assert.equal(result.bai?.path, 'fastq_qc/aligned.bam.bai');
    assert.equal(result.fasta?.path, 'fastq_qc/reference.normalized.fasta');
    assert.equal(result.fai?.path, 'fastq_qc/reference.normalized.fasta.fai');
});

test('large governed BAM avoids unsafe automatic browser allocation', () => {
    assert.deepEqual(alignmentTrackAutoLoadDisposition(818_274_983), {
        autoLoad: false,
        reason: 'Alignment is 780.4 MiB; browser track loading is disabled. Use Inspect reads instead.',
    });
    assert.deepEqual(alignmentTrackAutoLoadDisposition(65_536), { autoLoad: true, reason: null });
    for (const unsafe of [undefined, null, 0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
        const disposition = alignmentTrackAutoLoadDisposition(unsafe);
        assert.equal(disposition.autoLoad, false);
        assert.match(disposition.reason || '', /unknown|invalid/i);
    }
});

test('track sources keep browser presentation separate from full-source download authority', () => {
    const full = resolveBrowserAlignmentTrackSource({
        jobId: 'job-a',
        sessionId: 'session-a',
        alignmentUrl: '/source.bam',
        alignmentIndexUrl: '/source.bam.bai',
        alignmentSizeBytes: 65_536,
    });
    assert.equal(full?.kind, 'full');
    assert.equal(full?.name, 'Full alignment');
    assert.deepEqual(full?.fullSourceDownload, { url: '/source.bam', sizeBytes: 65_536 });

    const presentation = presentationFixture();
    const preview = resolveBrowserAlignmentTrackSource({
        jobId: 'job-a', sessionId: 'session-a', alignmentUrl: '/source.bam',
        alignmentIndexUrl: '/source.bam.bai', alignmentSizeBytes: 818_274_983,
        presentation,
    });
    assert.equal(preview?.kind, 'preview');
    assert.equal(preview?.name, 'Primary-read preview');
    assert.equal(preview?.bamUrl, `/api/jobs/job-a/alignment-sessions/session-a/presentation/${hash('6')}/bam`);
    assert.equal(preview?.capped, true);
    assert.deepEqual(preview?.fullSourceDownload, { url: '/source.bam', sizeBytes: 818_274_983 });

    assert.equal(resolveBrowserAlignmentTrackSource({
        jobId: 'job-a', sessionId: 'session-a', alignmentUrl: '/source.bam',
        alignmentIndexUrl: '/source.bam.bai', alignmentSizeBytes: null,
    }), null, 'presentation mode must not synthesize old preview URLs without a receipt');
});

const hash = (character: string) => character.repeat(64);
const artifact = (kind: string, url: string) => ({
    kind, url, sha256: hash('a'), size_bytes: 1024, mime_type: 'application/octet-stream', range_capable: true as const,
});

function presentationFixture() {
    const base = `/api/jobs/job-a/alignment-sessions/session-a/presentation/${hash('6')}`;
    return {
        schema: 'bms.ngs.alignment-presentation.v1' as const,
        job_id: 'job-a', session_id: 'session-a', mode: 'primary' as const, state: 'ready' as const,
        source: {
            package_manifest_sha256: hash('1'), alignment_sha256: hash('2'), alignment_size_bytes: 818_274_983,
            alignment_index_sha256: hash('3'), alignment_index_size_bytes: 2048,
            primary_read_count: 10_000, alignment_record_count: 10_500,
        },
        policy: {
            id: 'primary-read-presentation-v3', version: 3, target_reads: 2000,
            max_preview_bytes: 67_108_864, max_coverage_bins: 10_000, max_seconds: 120,
        },
        preview: {
            kind: 'primary_read_preview' as const, selected_read_count: 2000, selected_record_count: 2100,
            selected_read_set_sha256: hash('4'), forward_count: 1000, reverse_count: 1100,
            bam: artifact('alignment_preview', `${base}/bam`),
            index: artifact('alignment_preview_index', `${base}/bai`),
        },
        coverage: {
            kind: 'full_source_primary_coverage' as const, bin_width_bp: 10, primary_read_count: 10_000,
            artifact: { ...artifact('full_source_primary_coverage', `${base}/coverage`), mime_type: 'text/plain' },
        },
        manifest: { ...artifact('alignment_presentation_manifest', `${base}/manifest`), mime_type: 'application/json' },
    };
}

function locusFixture() {
    return {
        schema: 'bms.ngs.alignment-locus-slice.v1' as const,
        job_id: 'job-a', session_id: 'session-a', slice_id: hash('5'), state: 'ready' as const,
        contig: 'plasmid', start_1based: 101, end_1based: 220, overlapping_read_count: 7000,
        selected_read_count: 5000, selected_record_count: 5100, capped: true,
        policy: {
            id: 'bounded-full-source-locus-slice', version: 1, max_reads: 5000,
            max_records: 20_000, max_bytes: 67_108_864, max_span_bp: 1_000_000, max_seconds: 30,
        },
        bam: artifact('alignment_locus_slice', `/api/jobs/job-a/alignment-sessions/session-a/locus-slices/${hash('5')}/${hash('a')}/bam`),
        index: artifact('alignment_locus_slice_index', `/api/jobs/job-a/alignment-sessions/session-a/locus-slices/${hash('5')}/${hash('a')}/bai`),
        manifest: {
            ...artifact('alignment_locus_slice_manifest', `/api/jobs/job-a/alignment-sessions/session-a/locus-slices/${hash('5')}/${hash('a')}/manifest`),
            mime_type: 'application/json',
        },
    };
}

test('presentation and locus receipts are exact closed contracts', () => {
    const presentation = presentationFixture();
    const authority = {
        mode: 'primary' as const,
        packageManifestSha256: hash('1'),
        alignmentSha256: hash('2'), alignmentSizeBytes: 818_274_983,
        alignmentIndexSha256: hash('3'), alignmentIndexSizeBytes: 2048,
    };
    assert.equal(normalizeAlignmentPresentation(presentation, 'job-a', 'session-a', authority), presentation);
    assert.throws(() => normalizeAlignmentPresentation({ ...presentation, extra: true }, 'job-a', 'session-a', authority), /unknown|invalid/i);
    const malformedPresentation = structuredClone(presentation) as any;
    delete malformedPresentation.coverage.artifact.sha256;
    assert.throws(() => normalizeAlignmentPresentation(malformedPresentation, 'job-a', 'session-a', authority), /invalid|missing/i);
    assert.throws(() => normalizeAlignmentPresentation(presentation, 'job-a', 'session-a', {
        ...authority, alignmentSha256: hash('9'),
    }), /authority|invalid/i);
    const unsafePresentation = structuredClone(presentation) as any;
    unsafePresentation.preview.bam.url = '//attacker.invalid/preview.bam';
    assert.throws(() => normalizeAlignmentPresentation(unsafePresentation, 'job-a', 'session-a', authority), /url|invalid|unsafe/i);

    const locus = locusFixture();
    const requestedLocus = { contig: 'plasmid', start_1based: 101, end_1based: 220, max_reads: 5000 };
    assert.equal(normalizeAlignmentLocusSlice(locus, 'job-a', 'session-a', requestedLocus), locus);
    assert.throws(() => normalizeAlignmentLocusSlice({ ...locus, extra: true }, 'job-a', 'session-a', requestedLocus), /unknown|invalid/i);
    assert.throws(() => normalizeAlignmentLocusSlice({ ...locus, selected_read_count: 7001 }, 'job-a', 'session-a', requestedLocus), /invalid/i);
    assert.throws(() => normalizeAlignmentLocusSlice({ ...locus, start_1based: 102 }, 'job-a', 'session-a', requestedLocus), /locus|invalid/i);
    assert.equal(normalizeAlignmentLocusSlice({
        ...locus, overlapping_read_count: 5000, selected_read_count: 5000, capped: true,
    }, 'job-a', 'session-a', requestedLocus).capped, true, 'a byte or record cap can apply after every overlapping read ID is selected');
});

test('locus request construction is exact, bounded, and one-based', () => {
    assert.deepEqual(buildAlignmentLocusSliceRequest({ contig: 'plasmid', start: 101, end: 220 }), {
        contig: 'plasmid', start_1based: 101, end_1based: 220, max_reads: 5000,
    });
    assert.throws(() => buildAlignmentLocusSliceRequest({ contig: 'plasmid', start: 0, end: 220 }), /invalid/i);
    assert.throws(() => buildAlignmentLocusSliceRequest({ contig: 'plasmid', start: 1, end: 1_000_001 }), /invalid/i);
});

test('IGV config has authoritative sequence, honest reads, and full-source coverage tracks', () => {
    const presentation = presentationFixture();
    const preview = resolveBrowserAlignmentTrackSource({
        jobId: 'job-a', sessionId: 'session-a', alignmentUrl: '/source.bam', alignmentIndexUrl: '/source.bai',
        alignmentSizeBytes: presentation.source.alignment_size_bytes, presentation,
    })!;
    const config = buildLocalIgvConfig({
        referenceId: 'plasmid', referenceName: 'plasmid', fastaUrl: '/reference.fasta', faiUrl: '/reference.fasta.fai',
        initialLocus: 'plasmid:1-100', auxiliaryTracks: [],
    });
    assert.deepEqual((config.tracks as any[])[0], {
        id: 'ngs-reference-bases', name: 'Reference bases', type: 'sequence',
        fastaURL: '/reference.fasta', indexURL: '/reference.fasta.fai', order: -1000,
    });
    const previewTrack = buildAlignmentTrackConfig(preview, 420);
    assert.equal(previewTrack.name, 'Primary-read preview');
    assert.equal(previewTrack.showCoverage, false, 'preview depth must not appear as full-source coverage');
    assert.deepEqual(buildFullSourceCoverageTrackConfig(presentation), {
        id: 'ngs-full-source-primary-read-coverage', name: 'Full-source primary-read coverage', type: 'wig', format: 'bedgraph',
        url: `/api/jobs/job-a/alignment-sessions/session-a/presentation/${hash('6')}/coverage`,
        autoscale: true, graphType: 'bar', height: 72,
    });
});

test('alignment replacement is transactional and stale replacements are removed', async () => {
    const oldTrack = { id: 'old', type: 'alignment' };
    const newTrack = { id: 'new', type: 'alignment' };
    const removed: string[] = [];
    const browser = {
        findTracks: () => [oldTrack],
        loadTrack: async () => newTrack,
        removeTrack: (track: { id: string }) => removed.push(track.id),
    };
    assert.equal(await replaceAlignmentTrackTransactionally(browser, { type: 'alignment' }, () => true), newTrack);
    assert.deepEqual(removed, ['old']);

    removed.length = 0;
    const staleBrowser = { ...browser, findTracks: () => [oldTrack] };
    assert.equal(await replaceAlignmentTrackTransactionally(staleBrowser, { type: 'alignment' }, () => false), null);
    assert.deepEqual(removed, ['new'], 'the stale new track is removed and the old track remains');

    removed.length = 0;
    const failingBrowser = { ...browser, loadTrack: async () => { throw new Error('load failed'); } };
    await assert.rejects(replaceAlignmentTrackTransactionally(failingBrowser, { type: 'alignment' }, () => true), /load failed/);
    assert.deepEqual(removed, [], 'failed replacement leaves the old alignment mounted');
});

test('auxiliary tracks load once by stable track id', async () => {
    const loaded: string[] = [];
    const browser = {
        findTracks: (predicate: (track: { id: string }) => boolean) => [{ id: 'coverage' }].filter(predicate),
        loadTrack: async (track: { id: string }) => { loaded.push(track.id); return track; },
    };
    await loadMissingTracksById(browser, [{ id: 'coverage' }, { id: 'junctions' }], () => true);
    assert.deepEqual(loaded, ['junctions']);
});

test('preview disclosure stays persistent and generic aligned-read naming is absent', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    assert.doesNotMatch(source, /!igvReadsTrackLoaded[\s\S]{0,400}Primary-read preview/u);
    assert.doesNotMatch(source, /name:\s*['"]Aligned Reads['"]/u);
    assert.match(source, /Load full-source reads for this locus/u);
    assert.match(source, /igvPresentationGenerationRef/u);
    assert.match(source, /igvLocusSliceGenerationRef/u);
    assert.match(source, /igvTrackOperationActiveRef/u);
    assert.match(source, /igvCurrentLocusRef\.current/u);
});

test('dimer candidate session is opt-in and remains independently bound', () => {
    const result = resolveAlignmentViewerArtifacts(files, 'dimer_candidates');

    assert.equal(result.ready, true);
    assert.equal(result.bam?.path, 'fastq_qc/dimer_candidates.bam');
    assert.equal(result.bai?.path, 'fastq_qc/dimer_candidates.bam.bai');
    assert.equal(result.fasta?.path, 'fastq_qc/dimer_reference.fasta');
});

test('a mismatched BAM index is not accepted', () => {
    const result = resolveAlignmentViewerArtifacts([
        { path: 'fastq_qc/aligned.bam' },
        { path: 'fastq_qc/unrelated.bam.bai' },
        { path: 'fastq_qc/reference.fasta' },
    ]);

    assert.equal(result.ready, false);
    assert.equal(result.bai, null);
    assert.deepEqual(result.missing, ['alignment_index']);
});

test('missing alignment inputs produce an explicit non-ready contract', () => {
    const result = resolveAlignmentViewerArtifacts([{ path: 'fastq_qc/reference.fasta' }]);

    assert.equal(result.ready, false);
    assert.deepEqual(result.missing, ['alignment', 'alignment_index']);
});

test('late IGV creation is removed through the library when its generation is stale', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const bind = module.createGenerationBoundResource as (<T>(
        create: () => Promise<T>,
        remove: (resource: T) => void,
        isCurrent: () => boolean,
    ) => Promise<T | null>) | undefined;
    assert.equal(typeof bind, 'function');
    let resolveBrowser!: (browser: { id: string }) => void;
    const pending = new Promise<{ id: string }>((resolve) => { resolveBrowser = resolve; });
    const removed: string[] = [];
    let current = true;
    const resultPromise = bind!(() => pending, (browser) => removed.push(browser.id), () => current);
    current = false;
    resolveBrowser({ id: 'late-browser' });
    assert.equal(await resultPromise, null);
    assert.deepEqual(removed, ['late-browser']);
});

test('stale IGV generation cleanup cannot remove the current mount', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const createMount = module.createIgvGenerationMount as ((container: unknown) => Record<string, unknown>) | undefined;
    const removeMount = module.removeIgvGenerationMount as ((container: unknown, mount: unknown) => void) | undefined;
    assert.equal(typeof createMount, 'function');
    assert.equal(typeof removeMount, 'function');

    type FakeNode = { className: string; style: Record<string, string>; parentElement: FakeContainer | null };
    type FakeContainer = {
        ownerDocument: { createElement: () => FakeNode };
        children: FakeNode[];
        replaceChildren: (node: FakeNode) => void;
        removeChild: (node: FakeNode) => void;
    };
    const container: FakeContainer = {
        ownerDocument: {
            createElement: () => ({ className: '', style: {}, parentElement: null }),
        },
        children: [],
        replaceChildren(node) {
            for (const previous of this.children) previous.parentElement = null;
            this.children = [node];
            node.parentElement = this;
        },
        removeChild(node) {
            this.children = this.children.filter((candidate) => candidate !== node);
            node.parentElement = null;
        },
    };

    const stale = createMount!(container) as unknown as FakeNode;
    const current = createMount!(container) as unknown as FakeNode;
    removeMount!(container, stale);
    assert.deepEqual(container.children, [current]);
    removeMount!(container, current);
    assert.deepEqual(container.children, []);
});

test('IGV creation timeout invalidates its generation and removes an eventual late browser', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const createWithTimeout = module.createGenerationBoundResourceWithTimeout as (<T>(options: {
        create: () => Promise<T>;
        remove: (resource: T) => void;
        isCurrent: () => boolean;
        invalidate: () => void;
        timeoutMs: number;
        timeoutMessage: string;
    }) => Promise<T | null>) | undefined;
    assert.equal(typeof createWithTimeout, 'function');
    let resolveBrowser!: (browser: { id: string }) => void;
    const pending = new Promise<{ id: string }>((resolve) => { resolveBrowser = resolve; });
    const removed: string[] = [];
    let current = true;
    let invalidations = 0;
    const creation = createWithTimeout!({
        create: () => pending,
        remove: (browser) => removed.push(browser.id),
        isCurrent: () => current,
        invalidate: () => { current = false; invalidations += 1; },
        timeoutMs: 5,
        timeoutMessage: 'creation timed out',
    });

    await assert.rejects(creation, /creation timed out/);
    assert.equal(invalidations, 1);
    resolveBrowser({ id: 'eventual-browser' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(removed, ['eventual-browser']);
});

test('a delayed track completion cannot publish after its browser generation changes', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const awaitCurrent = module.awaitCurrentGeneration as (<T>(operation: Promise<T>, isCurrent: () => boolean) => Promise<T | null>) | undefined;
    assert.equal(typeof awaitCurrent, 'function');
    let resolveTrack!: (track: { id: string }) => void;
    const delayedTrack = new Promise<{ id: string }>((resolve) => { resolveTrack = resolve; });
    let current = true;
    const completion = awaitCurrent!(delayedTrack, () => current);

    current = false;
    resolveTrack({ id: 'old-session-track' });

    assert.equal(await completion, null);
});

test('optional tracks are built only from the selected session artifacts', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const tracksForSession = module.resolveSessionAuxiliaryTracks as ((artifacts: Record<string, { url: string }>) => Array<Record<string, unknown>>) | undefined;
    assert.equal(typeof tracksForSession, 'function');
    assert.deepEqual(tracksForSession!({}), []);
    const tracks = tracksForSession!({
        coverage_depth: { url: '/api/jobs/job-a/alignment-artifacts/coverage-depth' },
        gc_content: { url: '/api/jobs/job-a/alignment-artifacts/gc-content' },
        position_gradient: { url: '/api/jobs/job-a/alignment-artifacts/gradient' },
        gc_zscore: { url: '/api/jobs/job-a/alignment-artifacts/gc-zscore' },
        split_read_density: { url: '/api/jobs/job-a/alignment-artifacts/split' },
        soft_clip_density: { url: '/api/jobs/job-a/alignment-artifacts/soft-clip' },
        junction_hotspots: { url: '/api/jobs/job-a/alignment-artifacts/junctions' },
        coverage: { url: '/api/jobs/job-a/alignment-artifacts/generic-coverage-tsv' },
    });
    assert.deepEqual(tracks[0], {
        id: 'ngs-auxiliary-coverage_depth',
        name: 'Coverage Depth',
        type: 'wig',
        format: 'bedgraph',
        url: '/api/jobs/job-a/alignment-artifacts/coverage-depth',
        graphType: 'bar',
        autoscale: true,
        color: '#4ea6ff',
        height: 56,
    });
    assert.equal(tracks.length, 7);
    assert.deepEqual(tracks.map((track) => track.url), [
        '/api/jobs/job-a/alignment-artifacts/coverage-depth',
        '/api/jobs/job-a/alignment-artifacts/gradient',
        '/api/jobs/job-a/alignment-artifacts/gc-content',
        '/api/jobs/job-a/alignment-artifacts/gc-zscore',
        '/api/jobs/job-a/alignment-artifacts/split',
        '/api/jobs/job-a/alignment-artifacts/soft-clip',
        '/api/jobs/job-a/alignment-artifacts/junctions',
    ]);
    assert.equal(tracks.some((track) => track.url === '/api/jobs/job-a/alignment-artifacts/generic-coverage-tsv'), false);
});

test('optional IGV track failures cannot suppress a loaded primary alignment', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    const primaryReady = source.indexOf('setIgvReadsTrackLoaded(true);');
    const optionalLoop = source.indexOf('loadMissingTracksById(browser, auxiliaryTracks');
    const optionalFailure = source.indexOf('setIgvAuxTrackFailures', optionalLoop);

    assert.ok(primaryReady >= 0 && primaryReady < optionalLoop);
    assert.ok(optionalFailure > optionalLoop);
    assert.match(source.slice(optionalLoop, optionalFailure + 200), /trackConfig, error/u);
});

test('primary IGV readiness requires the governed FASTA index', () => {
    const toolkit = readFileSync(resolve(process.cwd(), 'src/components/NGSToolkit.tsx'), 'utf8');
    assert.match(toolkit, /!activeIgvFaiUrl\s*\? 'Reference FASTA index \(\.fai\) not found yet\.'/u);
    assert.match(toolkit, /Reference FASTA index \(\.fai, required\)/u);
    assert.doesNotMatch(toolkit, /Reference FASTA index \(\.fai, optional\)/u);
});

test('variant navigation is rejected when it is not bound to the selected session reference', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const boundLocus = module.resolveBoundSessionLocus as ((requested: string, selected: string, contig: string, start: number, end?: number) => string | null) | undefined;
    assert.equal(typeof boundLocus, 'function');
    assert.equal(boundLocus!('session-a', 'session-b', 'ref', 12), null);
    assert.equal(boundLocus!('session-a', 'session-a', 'ref', 12, 14), 'ref:12-14');
});

test('pending navigation retains session identity and is rejected after a session switch', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const pendingLocus = module.resolvePendingSessionLocus as ((
        pending: { sessionId: string; locus: string } | null,
        selectedSessionId: string,
    ) => string | null) | undefined;
    assert.equal(typeof pendingLocus, 'function');
    const pending = { sessionId: 'primary', locus: 'plasmid:20-20' };
    assert.equal(pendingLocus!(pending, 'primary'), 'plasmid:20-20');
    assert.equal(pendingLocus!(pending, 'dimer'), null);
});

test('IGV locus-change payload becomes a bounded one-based read-inspector locus', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const currentLocus = module.resolveIgvReadLocus as ((loci: unknown) => {
        contig: string;
        start: number;
        end: number;
    } | null) | undefined;
    assert.equal(typeof currentLocus, 'function');
    assert.deepEqual(currentLocus!([{ chr: 'plasmid', start: 9.2, end: 20.1 }]), {
        contig: 'plasmid',
        start: 10,
        end: 21,
    });
    assert.equal(currentLocus!([]), null);
    assert.equal(currentLocus!([{ chr: '', start: 0, end: 10 }]), null);
});

test('mounted locus source matches only its exact authoritative interval', () => {
    assert.equal(locusMatchesAlignmentSlice(
        { contig: 'plasmid', start: 101, end: 250 },
        { contig: 'plasmid', start_1based: 101, end_1based: 250 },
    ), true);
    assert.equal(locusMatchesAlignmentSlice(
        { contig: 'plasmid', start: 102, end: 250 },
        { contig: 'plasmid', start_1based: 101, end_1based: 250 },
    ), false);
    assert.equal(locusMatchesAlignmentSlice(
        { contig: 'other', start: 101, end: 250 },
        { contig: 'plasmid', start_1based: 101, end_1based: 250 },
    ), false);
});

test('IGV alignment popover resolves the clicked read without exposing UUID entry', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const resolveRead = module.resolveIgvClickedReadId as ((payload: unknown) => string | null) | undefined;
    assert.equal(typeof resolveRead, 'function');
    assert.equal(resolveRead!([
        { name: 'Read Name', value: '98be5d1a-6ff9-4d9a-85cb-e21fb9cf9ce9' },
        { name: 'Cigar', value: '200M' },
    ]), '98be5d1a-6ff9-4d9a-85cb-e21fb9cf9ce9');
    assert.equal(resolveRead!([{ name: 'Read Name', value: '<script>alert(1)</script>' }]), null);
    assert.equal(resolveRead!([{ name: 'Mapping Quality', value: '60' }]), null);
});

test('viewer-session publication rejects an older IGV click after a newer click begins', async () => {
    const sessionModule = await import('../src/lib/ngsAlignmentSession.js') as Record<string, unknown>;
    const viewerModule = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const createGuard = sessionModule.createLatestRequestGuard as (() => { begin(): number; isCurrent(token: number): boolean }) | undefined;
    const publishCurrent = viewerModule.publishCurrentIgvReadSelection as (<T>(isCurrent: () => boolean, create: () => Promise<T>, publish: (value: T) => void) => Promise<boolean>) | undefined;
    assert.equal(typeof createGuard, 'function');
    assert.equal(typeof publishCurrent, 'function');

    let resolveA!: (value: string) => void;
    let resolveB!: (value: string) => void;
    const createdA = new Promise<string>((resolve) => { resolveA = resolve; });
    const createdB = new Promise<string>((resolve) => { resolveB = resolve; });
    const guard = createGuard!();
    const published: string[] = [];
    const tokenA = guard.begin();
    const pendingA = publishCurrent!(() => guard.isCurrent(tokenA), () => createdA, (value) => published.push(value));
    const tokenB = guard.begin();
    const pendingB = publishCurrent!(() => guard.isCurrent(tokenB), () => createdB, (value) => published.push(value));

    resolveB('session-b');
    assert.equal(await pendingB, true);
    resolveA('session-a');
    assert.equal(await pendingA, false);
    assert.deepEqual(published, ['session-b']);
});

test('timed-out IGV generation owns terminal loading state but cannot clear a newer generation', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const ownsTerminal = module.ownsIgvLoadTerminalState as (
        loadToken: number,
        currentToken: number,
        timeoutInvalidationToken: number | null,
        cancelled: boolean,
    ) => boolean;

    assert.equal(ownsTerminal(4, 4, null, false), true);
    assert.equal(ownsTerminal(4, 5, 5, false), true);
    assert.equal(ownsTerminal(4, 6, 5, false), false);
    assert.equal(ownsTerminal(4, 5, 5, true), false);
});

test('upstream NGS route producers retain context and avoid generic viewers', () => {
    const queueSource = readFileSync(new URL('../src/components/dashboard/JobQueueTable.tsx', import.meta.url), 'utf8');
    const molBioSource = readFileSync(new URL('../src/components/MolBioToolkit/MolBioToolkitV2.tsx', import.meta.url), 'utf8');

    assert.match(queueSource, /isNgsJob\(job\)/);
    assert.match(queueSource, /ngsResultHref\(job\.id, location\.search\)/);
    assert.match(queueSource, /NGS Run Inspector/);
    assert.match(molBioSource, /ngsResultHref\(workup\.job_id, location\.search\)/);
    assert.doesNotMatch(molBioSource, /href=\{`\/jobs\/\$\{encodeURIComponent\(workup\.job_id\)\}`\}/);
});

test('alignment track completion cannot navigate away from a session-bound locus', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    const locusDetectionCalls = source.match(/detectInitialLocusFromFasta\(igvFastaUrl\)/g) || [];

    assert.equal(locusDetectionCalls.length, 1);
    assert.match(source, /Track loading must never navigate/);
    assert.doesNotMatch(source, /loadedAlignmentTrack[\s\S]{0,2500}browser\.search\(/);
});

test('local IGV config disables default genomes and web locus lookup', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const buildConfig = module.buildLocalIgvConfig as ((input: {
        referenceId: string;
        referenceName: string;
        fastaUrl: string;
        faiUrl: string;
        bamUrl: string;
        baiUrl: string;
        initialLocus: string;
        auxiliaryTracks: Array<Record<string, unknown>>;
    }) => Record<string, unknown>) | undefined;
    assert.equal(typeof buildConfig, 'function');

    const config = buildConfig!({
        referenceId: 'eGFP_plasmid',
        referenceName: 'eGFP plasmid',
        fastaUrl: '/api/jobs/job-a/alignment-artifacts/reference',
        faiUrl: '/api/jobs/job-a/alignment-artifacts/reference-index',
        bamUrl: '/api/jobs/job-a/alignment-artifacts/bam',
        baiUrl: '/api/jobs/job-a/alignment-artifacts/bai',
        initialLocus: 'eGFP_plasmid:1-5570',
        auxiliaryTracks: [],
    });

    assert.equal(config.loadDefaultGenomes, false);
    assert.equal(config.search, false);
    assert.equal(config.queryParametersSupported, false);
    assert.deepEqual(config.genomeList, []);
    assert.equal(JSON.stringify(config).includes('igv.org'), false);
    assert.equal(JSON.stringify(config).includes('cdn.jsdelivr.net'), false);

    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    assert.match(source, /buildLocalIgvConfig\(/u);
});

test('local IGV Range parser accepts only the exact backend-bound contig and bounds', async () => {
    const module = await import('../src/lib/ngsAlignmentViewer.js') as Record<string, unknown>;
    const parseRange = module.parseLocalIgvRange as ((value: string, contig: string, length: number) => string | null) | undefined;
    assert.equal(typeof parseRange, 'function');

    assert.equal(parseRange!('eGFP_plasmid:3400-3600', 'eGFP_plasmid', 5570), 'eGFP_plasmid:3400-3600');
    assert.equal(parseRange!(' eGFP_plasmid:1-5570 ', 'eGFP_plasmid', 5570), 'eGFP_plasmid:1-5570');
    assert.equal(parseRange!('EGFP_PLASMID:3400-3600', 'eGFP_plasmid', 5570), null);
    assert.equal(parseRange!('eGFP_plasmid:3600-3400', 'eGFP_plasmid', 5570), null);
    assert.equal(parseRange!('eGFP_plasmid:1-5571', 'eGFP_plasmid', 5570), null);
    assert.equal(parseRange!('TP53', 'eGFP_plasmid', 5570), null);
});

test('NGS viewer exposes a readable base-scale view and legible IGV chrome', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');

    assert.match(source, /Read bases/u);
    assert.match(source, /focusReadableIgvRange/u);
    assert.match(source, /selectedAlignmentSession\?\.reference\?\.length_bp/u);
    const rangeStart = source.indexOf('const navigateToLocalIgvRange');
    const rangeEnd = source.indexOf('const focusReadableIgvRange');
    assert.doesNotMatch(source.slice(rangeStart, rangeEnd), /ontFastqQcResultState/u);
    assert.match(source, /ngs-readable-igv/u);
    assert.match(source, /\.igv-ui-popover \*/u);
    assert.match(source, /\.igv-ui-popover,/u);
    assert.match(css, /\.ngs-readable-igv/u);
    assert.match(css, /font-size:\s*14px\s*!important/u);
});

test('NGS viewer opens compactly with explicit Range, fullscreen, and read-inspector controls', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    const openStart = source.indexOf('const openIgvModal');
    const closeStart = source.indexOf('const closeIgvModal');
    const openBody = source.slice(openStart, closeStart);

    assert.ok(openStart >= 0 && closeStart > openStart);
    assert.doesNotMatch(openBody, /requestDocumentFullscreen/u);
    assert.match(source, /w-\[min\(96vw,1180px\)\]/u);
    assert.match(source, /aria-label="Range"/u);
    assert.match(source, /parseLocalIgvRange\(/u);
    assert.match(source, /Enter fullscreen/u);
    assert.match(source, /igvInspectorOpen &&/u);
});

test('historical CDN-backed report is not exposed as an active browser viewer', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');

    assert.doesNotMatch(source, /Open compact IGV report/u);
    assert.doesNotMatch(source, /igvReportDownloadHref/u);
});

test('canonical FASTQ-QC renders the scientific report before collapsed technical details', () => {
    const source = readFileSync(new URL('../src/components/NGSToolkit.tsx', import.meta.url), 'utf8');
    const resultPanel = source.indexOf('<OntFastqQcResultPanel');
    const technicalDetails = source.indexOf('Technical job details');
    assert.ok(resultPanel >= 0);
    assert.ok(technicalDetails > resultPanel);
    assert.match(source, /<details open=\{!isCanonicalFastqQcRun\}/u);
    assert.equal((source.match(/<OntFastqQcResultPanel/g) || []).length, 1);
});
