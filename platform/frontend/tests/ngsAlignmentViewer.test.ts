import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import { alignmentTrackAutoLoadDisposition, resolveAlignmentViewerArtifacts } from '../src/lib/ngsAlignmentViewer.js';

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
    const optionalLoop = source.indexOf('for (const trackConfig of auxiliaryTracks)');
    const optionalFailure = source.indexOf('setIgvAuxTrackFailures', optionalLoop);

    assert.ok(primaryReady >= 0 && primaryReady < optionalLoop);
    assert.ok(optionalFailure > optionalLoop);
    assert.match(source.slice(optionalLoop, optionalFailure + 200), /catch/);
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
