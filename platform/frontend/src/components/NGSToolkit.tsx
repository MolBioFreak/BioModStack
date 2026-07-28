import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data, Layout, PlotMouseEvent } from 'plotly.js';
import type { IGV as IgvLibrary } from 'igv';
import { fetchJobLogs, fetchJobStages, fetchJobs, type Job, type JobLogs } from '../lib/api';
import {
    awaitCurrentGeneration,
    createGenerationBoundResourceWithTimeout,
    ownsIgvLoadTerminalState,
    removeIgvBrowser,
    resolveAlignmentViewerArtifacts,
    resolveBoundSessionLocus,
    resolveIgvReadLocus,
    resolvePendingSessionLocus,
    resolveSessionAuxiliaryTracks,
    type AlignmentReadLocus,
    type PendingSessionNavigation,
} from '../lib/ngsAlignmentViewer';
import { fetchAlignmentSessions, type AlignmentSession } from '../lib/ngsAlignmentSession';
import { normalizeNanoporeCloneState } from '../lib/nanoporeCloneState';
import { jobPollingInterval } from '../lib/queryPolling';
import { NanoporeTemplate } from './NanoporeTemplate';
import { OntInstrumentPanel } from './ngs/OntInstrumentPanel';
import { RawReadInspector } from './ngs/RawReadInspector';
import { BarcodeUnitsPanel } from './ngs/BarcodeUnitsPanel';
import { SequenceQcManifestPanel } from './ngs/SequenceQcManifestPanel';
import { useSequenceQcManifest } from './ngs/useSequenceQcManifest';
import { useThemeColors, useThemePlotlyLayout } from './useThemeColors';

type ToolkitView = 'launch' | 'instrument' | 'runs';
type LogTab = 'parsed' | 'command' | 'stderr' | 'nextflow';
type StageOutputsMap = Record<string, string[]>;

const NANOPORE_DOC_LINKS = [
    { label: 'Dorado docs', href: 'https://dorado-docs.readthedocs.io/en/latest/' },
    { label: 'Dorado GitHub', href: 'https://github.com/nanoporetech/dorado' },
    { label: 'modkit GitHub', href: 'https://github.com/nanoporetech/modkit' },
    { label: 'wf-clone GitHub', href: 'https://github.com/epi2me-labs/wf-clone-validation' },
    { label: 'minimap2 GitHub', href: 'https://github.com/lh3/minimap2' },
    { label: 'IGV.js docs', href: 'https://igv.org/doc/igvjs/' },
    { label: 'Nextflow docs', href: 'https://www.nextflow.io/docs/latest/index.html' },
] as const;

interface IgvArtifacts {
    bamPath: string | null;
    bamUrl: string | null;
    baiPath: string | null;
    baiUrl: string | null;
    fastaPath: string | null;
    fastaUrl: string | null;
    faiPath: string | null;
    faiUrl: string | null;
    coverageDepthPath: string | null;
    coverageDepthUrl: string | null;
    positionGradientPath: string | null;
    positionGradientUrl: string | null;
    gcContentPath: string | null;
    gcContentUrl: string | null;
    gcZscorePath: string | null;
    gcZscoreUrl: string | null;
    splitDensityPath: string | null;
    splitDensityUrl: string | null;
    softclipDensityPath: string | null;
    softclipDensityUrl: string | null;
    junctionHotspotsPath: string | null;
    junctionHotspotsUrl: string | null;
    reportPath: string | null;
    reportUrl: string | null;
    trackConfigPath: string | null;
    trackConfigUrl: string | null;
    missingReason: string | null;
}

interface MethylationArtifacts {
    summaryPath: string | null;
    summaryUrl: string | null;
    bedPath: string | null;
    bedUrl: string | null;
    missingReason: string | null;
}

interface IgvAlignmentSource {
    label: string;
    bamPath: string;
    bamUrl: string | null;
    baiPath: string | null;
    baiUrl: string | null;
}

interface IgvReferenceSource {
    label: string;
    fastaPath: string;
    fastaUrl: string | null;
    faiPath: string | null;
    faiUrl: string | null;
}

interface MultimerArtifacts {
    summaryPath: string | null;
    summaryUrl: string | null;
    lengthsPath: string | null;
    lengthsUrl: string | null;
    candidatesPath: string | null;
    candidatesUrl: string | null;
    logPath: string | null;
    logUrl: string | null;
    dimerFastqPath: string | null;
    dimerFastqUrl: string | null;
    dimerFastaPath: string | null;
    dimerFastaUrl: string | null;
    dimerLengthsPath: string | null;
    dimerLengthsUrl: string | null;
    dimerSummaryPath: string | null;
    dimerSummaryUrl: string | null;
    dimerConsensusPath: string | null;
    dimerConsensusUrl: string | null;
    dominantDimerConsensusPath: string | null;
    dominantDimerConsensusUrl: string | null;
    dominantDimerConsensusMetadataPath: string | null;
    dominantDimerConsensusMetadataUrl: string | null;
    dimerJunctionPath: string | null;
    dimerJunctionUrl: string | null;
    dimerJunctionEventsPath: string | null;
    dimerJunctionEventsUrl: string | null;
    dimerJunctionClustersPath: string | null;
    dimerJunctionClustersUrl: string | null;
    dimerJunctionHotspotsPath: string | null;
    dimerJunctionHotspotsUrl: string | null;
    dimerJunctionRotatedPath: string | null;
    dimerJunctionRotatedUrl: string | null;
    dimerJunctionRotationSummaryPath: string | null;
    dimerJunctionRotationSummaryUrl: string | null;
    dimerBreakpointScreenPath: string | null;
    dimerBreakpointScreenUrl: string | null;
    dimerBreakpointSequencesPath: string | null;
    dimerBreakpointSequencesUrl: string | null;
    dimerSecondaryAnomaliesPath: string | null;
    dimerSecondaryAnomaliesUrl: string | null;
    dimerSecondarySummaryPath: string | null;
    dimerSecondarySummaryUrl: string | null;
    dimerReadsPath: string | null;
    dimerReadsUrl: string | null;
    dimerReadLedgerPath: string | null;
    dimerReadLedgerUrl: string | null;
    dimerBreakpointReadsPath: string | null;
    dimerBreakpointReadsUrl: string | null;
    dimerRotatedRemapSummaryPath: string | null;
    dimerRotatedRemapSummaryUrl: string | null;
    dimerRotatedRemapBreakpointsPath: string | null;
    dimerRotatedRemapBreakpointsUrl: string | null;
    dimerBamPath: string | null;
    dimerBamUrl: string | null;
    dimerBaiPath: string | null;
    dimerBaiUrl: string | null;
    dimerReferencePath: string | null;
    dimerReferenceUrl: string | null;
    dimerReferenceIndexPath: string | null;
    dimerReferenceIndexUrl: string | null;
    missingReason: string | null;
}

interface SummaryTable {
    header: string[];
    rows: string[][];
}

interface MultimerCandidateRow {
    readIndex: number | null;
    readLength: number | null;
    classification: string;
}

interface DimerJunctionProfileRow {
    positionModRef: number;
    readCount: number;
    spanningReads: number;
}

interface DimerReadJunctionRow {
    readId: string;
    start: number | null;
    end: number | null;
    positionModRef: number | null;
    crossesJunction: boolean | null;
    method: string | null;
    orientation: string | null;
    missingLeftBp: number | null;
    missingRightBp: number | null;
    source: 'legacy' | 'events';
}

interface DimerJunctionClusterRow {
    clusterId: string | null;
    positionModRef: number;
    supportReads: number;
    crossingReads: number;
    supportPercent: number | null;
    method: string | null;
    orientation: string | null;
    eventCount: number | null;
    inBoundaryWindow: boolean | null;
    source: 'clusters' | 'hotspots' | 'events' | 'profile';
}

interface DimerBreakpointScreenRow {
    positionModRef: number;
    totalSupportReads: number;
    seamSupportReads: number;
    splitSupportReads: number;
    supportPctAll: number | null;
    splitPctOfPosition: number | null;
    splitPctOfAllSplit: number | null;
    inBoundaryWindow: boolean | null;
    boundaryStartReads: number | null;
    boundaryStartFraction: number | null;
    seamFraction: number | null;
    splitToSeamRatio: number | null;
    artifactFlag: boolean | null;
    confidence: string | null;
    junctionWindowLabel: string | null;
    junctionWindowSeq: string | null;
}

interface MultimerReportData {
    summary: SummaryTable | null;
    metrics: Record<string, number>;
    readLengths: number[];
    candidates: MultimerCandidateRow[];
    dimerSummary: SummaryTable | null;
    dimerJunctionRows: DimerJunctionProfileRow[];
    dimerJunctionClusters: DimerJunctionClusterRow[];
    dimerBreakpointScreenRows: DimerBreakpointScreenRow[];
    dimerReadJunctions: DimerReadJunctionRow[];
    dimerConsensusPreview: string | null;
    dominantDimerConsensusPreview: string | null;
    dominantDimerConsensusMetadata: SummaryTable | null;
    referenceName: string | null;
    referenceLength: number | null;
    referenceSequence: string | null;
}

interface MethylationLocus {
    chrom: string;
    start: number;
    end: number;
    code: string;
    strand: string;
    percentModified: number | null;
    coverage: number | null;
}

interface MethylationPoint {
    chrom: string;
    position: number;
    code: string;
    percentModified: number | null;
    coverage: number | null;
}

interface MethylationSeries {
    code: string;
    label: string;
    points: MethylationPoint[];
}

interface MotifSiteCall {
    chrom: string;
    position: number;
    motif: 'Dam' | 'Dcm';
    context: string;
    strand: '+' | '-';
    pairKey: string;
    percentModified: number | null;
    coverage: number | null;
}

interface MethylationReportData {
    summary: SummaryTable | null;
    topLoci: MethylationLocus[];
    series: MethylationSeries[];
    damSites: MotifSiteCall[];
    dcmSites: MotifSiteCall[];
    referenceName: string | null;
    referenceLength: number | null;
    referenceSequence: string | null;
}

interface SelectedMotifPoint extends MotifSiteCall {
    sequenceContext: string | null;
    contextStart: number | null;
    contextEnd: number | null;
}

interface SequenceRow {
    start: number;
    end: number;
    bases: string;
}

interface SequenceBaseHighlight {
    motif: 'Dam' | 'Dcm';
    strand: '+' | '-';
    percentModified: number;
    color: string;
}

interface HighlightedSequenceSegment {
    text: string;
    position: number | null;
    highlight: SequenceBaseHighlight | null;
    isSelected: boolean;
}

const STAGE_LABELS: Record<string, string> = {
    dorado_basecall: 'Dorado Basecall',
    doradobasecall: 'Dorado Basecall',
    dorado_align: 'Dorado Align',
    doradoalign: 'Dorado Align',
    bam_prepare: 'BAM Prepare',
    preparebamforanalysis: 'BAM Prepare',
    modkit: 'modkit',
    modkitpileup: 'modkit Pileup',
    modkitsummary: 'modkit Summary',
    fastq_qc: 'FASTQ Plasmid QC',
    fastqplasmidqc: 'FASTQ Plasmid QC',
    multimer_qc: 'Multimer QC',
    dimer_analysis: 'Dimer Analysis',
    dimeranalysis: 'Dimer Analysis',
    fastqdimeranalysis: 'Dimer Analysis',
    fastq_align: 'FASTQ Align',
    fastqalign: 'FASTQ Align',
    fastqmultimerqc: 'Multimer QC',
    runclonevalidation: 'wf-clone-validation',
    'wf-clone-validation': 'wf-clone-validation',
    wf_clone_validation: 'wf-clone-validation',
};

const STATUS_OPTIONS = ['all', 'queued', 'running', 'completed', 'failed', 'cancelled'];
const ALLOWED_ROOT_PREFIXES = ['bms_results/', 'inputs/', 'benchmarkdata/', 'lib/', 'rcsb/', 'downloads/', 'data/'];
const IGV_REQUIRED_VERSION = '3.7.3';
const IGV_INIT_TIMEOUT_MS = 20000;
const IGV_INITIAL_LOCUS_WINDOW_BP = 800;
const IGV_INITIAL_FULL_LOCUS_MAX_BP = 100000;
const IGV_READS_TRACK_MIN_HEIGHT_PX = 260;
const IGV_READS_TRACK_MIN_WITHOUT_AUX_PX = 520;
const IGV_READS_TRACK_BOTTOM_GUTTER_PX = 8;
const IGV_AUX_TRACK_RESERVED_GUTTER_PX = 24;
const IGV_AUX_TRACK_MAX_RESERVED_FRACTION = 0.42;
const METHYLATION_BED_MAX_BYTES = 8 * 1024 * 1024;
const METHYLATION_SUMMARY_MAX_BYTES = 2 * 1024 * 1024;
const REFERENCE_FASTA_MAX_BYTES = 2 * 1024 * 1024;
const MULTIMER_SUMMARY_MAX_BYTES = 512 * 1024;
const MULTIMER_LENGTHS_MAX_BYTES = 8 * 1024 * 1024;
const MULTIMER_CANDIDATES_MAX_BYTES = 2 * 1024 * 1024;
const DIMER_CONSENSUS_MAX_BYTES = 512 * 1024;
const DIMER_CONSENSUS_PREVIEW_CHARS = 12000;
const MAX_PLOT_POINTS_PER_CODE = 5000;
const REFERENCE_SEQUENCE_LINE_WIDTH = 120;
const DEFAULT_MOTIF_MIN_COVERAGE = 20;
const ALIGNMENT_STAGE_ALIASES = ['dorado_align', 'doradoalign', 'bam_prepare', 'bamprepare', 'preparebamforanalysis', 'fastq_align', 'fastqalign'];
const REFERENCE_STAGE_ALIASES = [...ALIGNMENT_STAGE_ALIASES, 'reference_prepare', 'referenceprepareforigv'];
const MODKIT_STAGE_ALIASES = ['modkit', 'modkitpileup', 'modkitsummary'];
const MULTIMER_STAGE_ALIASES = ['fastq_qc', 'fastqqc', 'fastqplasmidqc', 'multimer_qc', 'multimerqc', 'fastqmultimerqc'];
const DIMER_STAGE_ALIASES = ['dimer_analysis', 'dimeranalysis', 'fastqdimeranalysis'];
const METHYLATION_STRAND_FILTERS = ['both', '+', '-'] as const;
const MOTIF_CONCORDANCE_DELTA_PERCENT = 5;
const RELEVANT_METHYLATION_CODES = new Set(['a', 'm']);
const IGV_ALIGNMENT_DISPLAY_OPTIONS = [
    { value: 'EXPANDED', label: 'Expanded' },
    { value: 'SQUISHED', label: 'Squished' },
    { value: 'FULL', label: 'Full' },
];
const IGV_ALIGNMENT_COLOR_OPTIONS = [
    { value: 'none', label: 'Color: None' },
    { value: 'strand', label: 'Color: Strand' },
    { value: 'firstOfPairStrand', label: 'Color: First Pair Strand' },
    { value: 'pairOrientation', label: 'Color: Pair Orientation' },
    { value: 'tlen', label: 'Color: Template Length' },
    { value: 'unexpectedPair', label: 'Color: Unexpected Pair' },
    { value: 'basemod', label: 'Color: Base Mods' },
    { value: 'basemod2', label: 'Color: Base Mods (Alt)' },
];
const IGV_ALIGNMENT_GROUP_OPTIONS = [
    { value: 'none', label: 'Group: None' },
    { value: 'strand', label: 'Group: Strand' },
    { value: 'firstOfPairStrand', label: 'Group: First Pair Strand' },
    { value: 'pairOrientation', label: 'Group: Pair Orientation' },
    { value: 'mateChr', label: 'Group: Mate Chromosome' },
    { value: 'chimeric', label: 'Group: Chimeric' },
    { value: 'supplementary', label: 'Group: Supplementary' },
    { value: 'readOrder', label: 'Group: Read Order' },
];
let igvLibraryPromise: Promise<{ igv: IgvLibrary; version: string }> | null = null;

function isAllowedRelativePath(path: string): boolean {
    return ALLOWED_ROOT_PREFIXES.some((prefix) => path.startsWith(prefix));
}

function toDownloadHref(path: string, cacheKey?: string): string | null {
    if (!isAllowedRelativePath(path)) return null;
    const encoded = path.split('/').map((part) => encodeURIComponent(part)).join('/');
    if (cacheKey) {
        return `/api/files/download/${encoded}?v=${encodeURIComponent(cacheKey)}`;
    }
    return `/api/files/download/${encoded}`;
}

function toStreamHref(path: string, cacheKey?: string): string | null {
    if (!isAllowedRelativePath(path)) return null;
    const encoded = path.split('/').map((part) => encodeURIComponent(part)).join('/');
    if (cacheKey) {
        return `/api/files/stream/${encoded}?v=${encodeURIComponent(cacheKey)}`;
    }
    return `/api/files/stream/${encoded}`;
}

function normalizeAllowedRelativePath(path: string | null | undefined): string | null {
    if (!path) return null;
    const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '');
    if (!normalized) return null;

    if (isAllowedRelativePath(`${normalized}/`)) {
        return normalized;
    }

    const lowered = normalized.toLowerCase();
    for (const rootPrefix of ALLOWED_ROOT_PREFIXES) {
        const root = rootPrefix.replace(/\/+$/, '');
        const rootLower = root.toLowerCase();
        const token = `/${rootLower}/`;
        const idx = lowered.lastIndexOf(token);
        if (idx >= 0) {
            const rel = normalized.slice(idx + 1);
            if (isAllowedRelativePath(`${rel}/`)) {
                return rel;
            }
        }
        if (lowered.endsWith(`/${rootLower}`) && isAllowedRelativePath(`${root}/`)) {
            return root;
        }
    }

    return null;
}

function resolveRunPrefix(outputDir: string | null | undefined, paths: string[]): string | null {
    const normalized = normalizeAllowedRelativePath(outputDir);
    if (normalized) return normalized;
    if (!outputDir) return null;

    const leaf = outputDir
        .replace(/\\/g, '/')
        .replace(/\/+$/, '')
        .split('/')
        .filter(Boolean)
        .pop();
    if (!leaf) return null;

    for (const path of dedupePaths(paths)) {
        const segments = path.replace(/\\/g, '/').split('/').filter(Boolean);
        const idx = segments.lastIndexOf(leaf);
        if (idx <= 0) continue;
        const prefix = segments.slice(0, idx + 1).join('/');
        if (isAllowedRelativePath(`${prefix}/`)) {
            return prefix;
        }
    }
    return null;
}

function dedupePaths(paths: string[]): string[] {
    return [...new Set(paths.filter((value): value is string => typeof value === 'string' && value.length > 0))];
}

function filterPathsByRunPrefix(paths: string[], runPrefix: string | null): string[] {
    const deduped = dedupePaths(paths);
    if (!runPrefix) return deduped;
    const prefix = runPrefix.replace(/\/+$/, '');
    return deduped.filter((path) => path === prefix || path.startsWith(`${prefix}/`));
}

function findFirstMatchingPath(paths: string[], patterns: RegExp[]): string | null {
    for (const p of paths) {
        const normalized = p.toLowerCase();
        if (patterns.some((re) => re.test(normalized))) {
            return p;
        }
    }
    return null;
}

function normalizeStageKey(stage: string): string {
    return stage.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function hasMeaningfulValue(value: unknown): boolean {
    if (value == null) return false;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        return normalized !== '' && !['null', 'none', 'undefined', 'n/a', 'na'].includes(normalized);
    }
    return Boolean(value);
}

function resolveFastqQcEnabled(params: Record<string, unknown>, hasFastq: boolean): boolean {
    if (!hasFastq) return false;
    if (typeof params.run_fastq_qc === 'boolean') return params.run_fastq_qc;
    if (typeof params.run_multimer_qc === 'boolean') return params.run_multimer_qc;
    return false;
}

function collectPathsForStageAliases(stageOutputs: StageOutputsMap, aliases: string[]): string[] {
    const aliasSet = new Set(aliases.map(normalizeStageKey));
    return Object.entries(stageOutputs || {})
        .flatMap(([stage, outputs]) => {
            if (!aliasSet.has(normalizeStageKey(stage))) return [];
            if (!Array.isArray(outputs)) return [];
            return outputs.filter((value): value is string => typeof value === 'string' && value.length > 0);
        });
}

function collectStageOutputPaths(stageOutputs: StageOutputsMap): string[] {
    const values = Object.values(stageOutputs || {});
    return values.flat().filter((value): value is string => typeof value === 'string' && value.length > 0);
}

function resolveBamIndexArtifactPath(bamPath: string | null, paths: string[]): string | null {
    if (bamPath) {
        const exactCandidates = [`${bamPath}.bai`, `${bamPath}.csi`];
        const exact = exactCandidates.find((candidate) => paths.includes(candidate));
        if (exact) return exact;

        const bamBase = bamPath.split('/').pop();
        if (bamBase) {
            const paired = paths.find((path) => path.endsWith(`${bamBase}.bai`) || path.endsWith(`${bamBase}.csi`));
            if (paired) return paired;
        }
    }
    return findFirstMatchingPath(paths, [/\.bam\.(bai|csi)$/i, /\.(bai|csi)$/i]);
}

function resolveFastaIndexArtifactPath(fastaPath: string | null, paths: string[]): string | null {
    if (!fastaPath) return null;
    const exact = `${fastaPath}.fai`;
    if (paths.includes(exact)) return exact;

    const fastaBase = fastaPath.split('/').pop();
    if (fastaBase) {
        const paired = paths.find((path) => path.endsWith(`${fastaBase}.fai`));
        if (paired) return paired;
    }

    return findFirstMatchingPath(paths, [/\.fai$/i]);
}

function formatIgvSourceLabel(path: string): string {
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    if (parts.length <= 2) return parts.join('/');
    return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

function resolveIgvArtifacts(job: Job | null, stageOutputs: StageOutputsMap): IgvArtifacts {
    if (!job) {
        return {
            bamPath: null,
            bamUrl: null,
            baiPath: null,
            baiUrl: null,
            fastaPath: null,
            fastaUrl: null,
            faiPath: null,
            faiUrl: null,
            coverageDepthPath: null,
            coverageDepthUrl: null,
            positionGradientPath: null,
            positionGradientUrl: null,
            gcContentPath: null,
            gcContentUrl: null,
            gcZscorePath: null,
            gcZscoreUrl: null,
            splitDensityPath: null,
            splitDensityUrl: null,
            softclipDensityPath: null,
            softclipDensityUrl: null,
            junctionHotspotsPath: null,
            junctionHotspotsUrl: null,
            reportPath: null,
            reportUrl: null,
            trackConfigPath: null,
            trackConfigUrl: null,
            missingReason: 'Select a run to inspect IGV artifacts.',
        };
    }

    const paths = collectStageOutputPaths(stageOutputs);
    const alignmentPaths = collectPathsForStageAliases(stageOutputs, ALIGNMENT_STAGE_ALIASES);
    const referencePaths = collectPathsForStageAliases(stageOutputs, REFERENCE_STAGE_ALIASES);
    const dimerPaths = collectPathsForStageAliases(stageOutputs, DIMER_STAGE_ALIASES);
    const params = (job.params || {}) as Record<string, unknown>;
    const runPrefix = resolveRunPrefix(job.output_dir, paths);
    const scopedPaths = filterPathsByRunPrefix(paths, runPrefix);
    const scopedAlignmentPaths = filterPathsByRunPrefix(alignmentPaths, runPrefix);
    const scopedReferencePaths = filterPathsByRunPrefix(referencePaths, runPrefix);
    const scopedDimerPaths = filterPathsByRunPrefix(dimerPaths, runPrefix);
    const dimerBase = runPrefix
        ? (scopedDimerPaths.length > 0 ? scopedDimerPaths : scopedPaths)
        : (dimerPaths.length > 0 ? dimerPaths : paths);
    const alignmentBase = runPrefix
        ? (scopedAlignmentPaths.length > 0 ? scopedAlignmentPaths : dimerBase)
        : (alignmentPaths.length > 0 ? alignmentPaths : dimerBase);
    const referenceBase = runPrefix
        ? (scopedReferencePaths.length > 0 ? scopedReferencePaths : dimerBase)
        : (referencePaths.length > 0 ? referencePaths : dimerBase);
    const alignmentCandidates = dedupePaths(alignmentBase);
    const referenceCandidates = dedupePaths(referenceBase);
    const analysisCandidates = dedupePaths(runPrefix ? scopedPaths : paths);

    const viewerInputs = dedupePaths([
        ...alignmentCandidates,
        ...referenceCandidates,
        ...analysisCandidates,
    ]).map((path) => ({ path }));
    const primaryViewer = resolveAlignmentViewerArtifacts(viewerInputs, 'primary');
    const bamPath = primaryViewer.bam?.path || null;
    const baiPath = primaryViewer.bai?.path || null;
    const fastaPath = primaryViewer.fasta?.path || null;
    const faiPath = primaryViewer.fai?.path || null;

    const fallbackReference = typeof params.reference_fasta === 'string' ? params.reference_fasta : null;
    const fallbackReferenceIndex = fallbackReference ? `${fallbackReference}.fai` : null;

    const cacheKey = job.id;
    const bamUrl = bamPath ? toStreamHref(bamPath, cacheKey) : null;
    const baiUrl = baiPath ? toStreamHref(baiPath, cacheKey) : null;
    const fastaUrl = fastaPath ? toStreamHref(fastaPath, cacheKey) : (fallbackReference ? toStreamHref(fallbackReference, cacheKey) : null);
    const faiUrl = faiPath ? toStreamHref(faiPath, cacheKey) : (fallbackReferenceIndex ? toStreamHref(fallbackReferenceIndex, cacheKey) : null);
    const coverageDepthPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_coverage_depth\.bedgraph$/i, /(^|\/)igv_coverage_depth\.bedgraph$/i]);
    const positionGradientPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_position_gradient\.bedgraph$/i, /(^|\/)igv_position_gradient\.bedgraph$/i]);
    const gcContentPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_gc_content\.bedgraph$/i, /(^|\/)igv_gc_content\.bedgraph$/i]);
    const gcZscorePath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_gc_zscore\.bedgraph$/i, /(^|\/)igv_gc_zscore\.bedgraph$/i]);
    const splitDensityPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_split_read_density\.bedgraph$/i, /(^|\/)igv_split_read_density\.bedgraph$/i]);
    const softclipDensityPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_softclip_density\.bedgraph$/i, /(^|\/)igv_softclip_density\.bedgraph$/i]);
    const junctionHotspotsPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_junction_hotspots\.bed$/i, /(^|\/)igv_junction_hotspots\.bed$/i]);
    const reportPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_report\.html$/i, /(^|\/)igv_report\.html$/i]);
    const trackConfigPath = findFirstMatchingPath(analysisCandidates, [/\/fastq_qc\/igv_track_config\.json$/i, /(^|\/)igv_track_config\.json$/i]);
    const coverageDepthUrl = coverageDepthPath ? toStreamHref(coverageDepthPath, cacheKey) : null;
    const positionGradientUrl = positionGradientPath ? toStreamHref(positionGradientPath, cacheKey) : null;
    const gcContentUrl = gcContentPath ? toStreamHref(gcContentPath, cacheKey) : null;
    const gcZscoreUrl = gcZscorePath ? toStreamHref(gcZscorePath, cacheKey) : null;
    const splitDensityUrl = splitDensityPath ? toStreamHref(splitDensityPath, cacheKey) : null;
    const softclipDensityUrl = softclipDensityPath ? toStreamHref(softclipDensityPath, cacheKey) : null;
    const junctionHotspotsUrl = junctionHotspotsPath ? toStreamHref(junctionHotspotsPath, cacheKey) : null;
    const reportUrl = reportPath ? toDownloadHref(reportPath, cacheKey) : null;
    const trackConfigUrl = trackConfigPath ? toDownloadHref(trackConfigPath, cacheKey) : null;

    let missingReason: string | null = null;
    if (!bamUrl) {
        missingReason = 'Aligned BAM artifact not found yet.';
    } else if (!baiUrl) {
        missingReason = 'BAM index (.bai/.csi) not found yet.';
    } else if (!fastaUrl) {
        missingReason = 'Reference FASTA not found. Run with reference_fasta to enable IGV.';
    } else if (!faiUrl) {
        missingReason = 'Reference FASTA index (.fai) not found.';
    }

    return {
        bamPath,
        bamUrl,
        baiPath,
        baiUrl,
        fastaPath: fastaPath || fallbackReference,
        fastaUrl,
        faiPath: faiPath || fallbackReferenceIndex,
        faiUrl,
        coverageDepthPath,
        coverageDepthUrl,
        positionGradientPath,
        positionGradientUrl,
        gcContentPath,
        gcContentUrl,
        gcZscorePath,
        gcZscoreUrl,
        splitDensityPath,
        splitDensityUrl,
        softclipDensityPath,
        softclipDensityUrl,
        junctionHotspotsPath,
        junctionHotspotsUrl,
        reportPath,
        reportUrl,
        trackConfigPath,
        trackConfigUrl,
        missingReason,
    };
}

function resolveMethylationArtifacts(job: Job | null, stageOutputs: StageOutputsMap): MethylationArtifacts {
    if (!job) {
        return {
            summaryPath: null,
            summaryUrl: null,
            bedPath: null,
            bedUrl: null,
            missingReason: 'Select a run to inspect methylation outputs.',
        };
    }

    const params = (job.params || {}) as Record<string, unknown>;
    const runModkit = params.run_modkit !== false;
    const paths = collectStageOutputPaths(stageOutputs);
    const modkitPaths = collectPathsForStageAliases(stageOutputs, MODKIT_STAGE_ALIASES);
    const runPrefix = resolveRunPrefix(job.output_dir, paths);
    const scopedPaths = filterPathsByRunPrefix(paths, runPrefix);
    const scopedModkitPaths = filterPathsByRunPrefix(modkitPaths, runPrefix);
    const candidateBase = runPrefix
        ? (scopedModkitPaths.length > 0 ? scopedModkitPaths : scopedPaths)
        : (modkitPaths.length > 0 ? modkitPaths : paths);
    const candidates = dedupePaths(candidateBase);

    const summaryPath = findFirstMatchingPath(candidates, [/\/methylation\/modkit_summary\.tsv$/i, /(^|\/)modkit_summary\.tsv$/i]);
    const bedPath = findFirstMatchingPath(candidates, [/\/methylation\/methylation\.bed$/i, /\/methylation\/.*\.bed$/i]);
    const cacheKey = job.id;
    const summaryUrl = summaryPath ? toStreamHref(summaryPath, cacheKey) : null;
    const bedUrl = bedPath ? toStreamHref(bedPath, cacheKey) : null;

    let missingReason: string | null = null;
    if (!runModkit) {
        missingReason = 'modkit was disabled for this run (run_modkit=false).';
    } else if (!summaryUrl && !bedUrl) {
        missingReason = 'No modkit methylation outputs found yet.';
    }

    return {
        summaryPath,
        summaryUrl,
        bedPath,
        bedUrl,
        missingReason,
    };
}

function resolveMultimerArtifacts(job: Job | null, stageOutputs: StageOutputsMap): MultimerArtifacts {
    if (!job) {
        return {
            summaryPath: null,
            summaryUrl: null,
            lengthsPath: null,
            lengthsUrl: null,
            candidatesPath: null,
            candidatesUrl: null,
            logPath: null,
            logUrl: null,
            dimerFastqPath: null,
            dimerFastqUrl: null,
            dimerFastaPath: null,
            dimerFastaUrl: null,
            dimerLengthsPath: null,
            dimerLengthsUrl: null,
            dimerSummaryPath: null,
            dimerSummaryUrl: null,
            dimerConsensusPath: null,
            dimerConsensusUrl: null,
            dominantDimerConsensusPath: null,
            dominantDimerConsensusUrl: null,
            dominantDimerConsensusMetadataPath: null,
            dominantDimerConsensusMetadataUrl: null,
            dimerJunctionPath: null,
            dimerJunctionUrl: null,
            dimerJunctionEventsPath: null,
            dimerJunctionEventsUrl: null,
            dimerJunctionClustersPath: null,
            dimerJunctionClustersUrl: null,
            dimerJunctionHotspotsPath: null,
            dimerJunctionHotspotsUrl: null,
            dimerJunctionRotatedPath: null,
            dimerJunctionRotatedUrl: null,
            dimerJunctionRotationSummaryPath: null,
            dimerJunctionRotationSummaryUrl: null,
            dimerBreakpointScreenPath: null,
            dimerBreakpointScreenUrl: null,
            dimerBreakpointSequencesPath: null,
            dimerBreakpointSequencesUrl: null,
            dimerSecondaryAnomaliesPath: null,
            dimerSecondaryAnomaliesUrl: null,
            dimerSecondarySummaryPath: null,
            dimerSecondarySummaryUrl: null,
            dimerReadsPath: null,
            dimerReadsUrl: null,
            dimerReadLedgerPath: null,
            dimerReadLedgerUrl: null,
            dimerBreakpointReadsPath: null,
            dimerBreakpointReadsUrl: null,
            dimerRotatedRemapSummaryPath: null,
            dimerRotatedRemapSummaryUrl: null,
            dimerRotatedRemapBreakpointsPath: null,
            dimerRotatedRemapBreakpointsUrl: null,
            dimerBamPath: null,
            dimerBamUrl: null,
            dimerBaiPath: null,
            dimerBaiUrl: null,
            dimerReferencePath: null,
            dimerReferenceUrl: null,
            dimerReferenceIndexPath: null,
            dimerReferenceIndexUrl: null,
            missingReason: 'Select a run to inspect multimer QC outputs.',
        };
    }

    const params = (job.params || {}) as Record<string, unknown>;
    const hasFastq = hasMeaningfulValue(params.fastq_path);
    const runFastqQc = resolveFastqQcEnabled(params, hasFastq);
    const paths = collectStageOutputPaths(stageOutputs);
    const multimerPaths = collectPathsForStageAliases(stageOutputs, MULTIMER_STAGE_ALIASES);
    const dimerPaths = collectPathsForStageAliases(stageOutputs, DIMER_STAGE_ALIASES);
    const runPrefix = resolveRunPrefix(job.output_dir, paths);
    const scopedPaths = filterPathsByRunPrefix(paths, runPrefix);
    const scopedMultimerPaths = filterPathsByRunPrefix(multimerPaths, runPrefix);
    const scopedDimerPaths = filterPathsByRunPrefix(dimerPaths, runPrefix);
    const scopedStagePaths = dedupePaths([...scopedMultimerPaths, ...scopedDimerPaths]);
    const stagePaths = dedupePaths([...multimerPaths, ...dimerPaths]);
    const candidateBase = runPrefix
        ? (scopedStagePaths.length > 0 ? scopedStagePaths : scopedPaths)
        : (stagePaths.length > 0 ? stagePaths : paths);
    const candidates = dedupePaths(candidateBase);

    const summaryPath = findFirstMatchingPath(candidates, [/\/fastq_qc\/fastq_qc_summary\.tsv$/i, /(^|\/)fastq_qc_summary\.tsv$/i, /\/multimer_qc\/multimer_summary\.tsv$/i, /(^|\/)multimer_summary\.tsv$/i]);
    const lengthsPath = findFirstMatchingPath(candidates, [/\/fastq_qc\/read_lengths\.tsv$/i, /(^|\/)read_lengths\.tsv$/i]);
    const candidatesPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/multimer_candidates\.tsv$/i, /(^|\/)multimer_candidates\.tsv$/i]);
    const logPath = findFirstMatchingPath(candidates, [/\/fastq_qc\/fastq_qc\.log$/i, /(^|\/)fastq_qc\.log$/i, /\/multimer_qc\/multimer_qc\.log$/i, /(^|\/)multimer_qc\.log$/i]);
    const dimerFastqPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_candidates\.fastq$/i, /(^|\/)dimer_candidates\.fastq$/i]);
    const dimerFastaPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_candidates\.fasta$/i, /(^|\/)dimer_candidates\.fasta$/i]);
    const dimerLengthsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_read_lengths\.tsv$/i, /(^|\/)dimer_read_lengths\.tsv$/i]);
    const dimerSummaryPath = findFirstMatchingPath(candidates, [/\/fastq_qc\/fastq_alignment_stats\.tsv$/i, /(^|\/)fastq_alignment_stats\.tsv$/i, /\/multimer_qc\/dimer_analysis_summary\.tsv$/i, /(^|\/)dimer_analysis_summary\.tsv$/i]);
    const dimerConsensusPath = findFirstMatchingPath(candidates, [/\/fastq_qc\/fastq_consensus\.fasta$/i, /(^|\/)fastq_consensus\.fasta$/i, /\/multimer_qc\/dimer_consensus\.fasta$/i, /(^|\/)dimer_consensus\.fasta$/i]);
    const dominantDimerConsensusPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dominant_dimer_consensus\.fasta$/i, /(^|\/)dominant_dimer_consensus\.fasta$/i]);
    const dominantDimerConsensusMetadataPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dominant_dimer_consensus_metadata\.tsv$/i, /(^|\/)dominant_dimer_consensus_metadata\.tsv$/i]);
    const dimerCanonicalEvidencePath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_evidence_by_position\.tsv$/i, /(^|\/)dimer_evidence_by_position\.tsv$/i]);
    const dimerCanonicalReadEventsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_read_events\.tsv$/i, /(^|\/)dimer_read_events\.tsv$/i]);
    const dimerJunctionPathLegacy = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_profile\.tsv$/i, /(^|\/)dimer_junction_profile\.tsv$/i]);
    const dimerJunctionEventsPathLegacy = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_events\.tsv$/i, /(^|\/)dimer_junction_events\.tsv$/i]);
    const dimerJunctionClustersPathLegacy = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_clusters\.tsv$/i, /(^|\/)dimer_junction_clusters\.tsv$/i]);
    const dimerJunctionHotspotsPathLegacy = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_hotspots\.tsv$/i, /(^|\/)dimer_junction_hotspots\.tsv$/i]);
    const dimerJunctionPath = dimerJunctionPathLegacy || dimerCanonicalEvidencePath;
    const dimerJunctionEventsPath = dimerJunctionEventsPathLegacy || dimerCanonicalReadEventsPath;
    const dimerJunctionClustersPath = dimerJunctionClustersPathLegacy || dimerCanonicalEvidencePath;
    const dimerJunctionHotspotsPath = dimerJunctionHotspotsPathLegacy || dimerCanonicalEvidencePath;
    const dimerJunctionRotatedPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_rotated_profile\.tsv$/i, /(^|\/)dimer_junction_rotated_profile\.tsv$/i]);
    const dimerJunctionRotationSummaryPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_rotation_summary\.tsv$/i, /(^|\/)dimer_junction_rotation_summary\.tsv$/i]);
    const dimerBreakpointScreenPathLegacy = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_breakpoint_screen\.tsv$/i, /(^|\/)dimer_breakpoint_screen\.tsv$/i]);
    const dimerBreakpointScreenPath = dimerBreakpointScreenPathLegacy || dimerCanonicalEvidencePath;
    const dimerBreakpointSequencesPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_breakpoint_sequences\.tsv$/i, /(^|\/)dimer_breakpoint_sequences\.tsv$/i]);
    const dimerSecondaryAnomaliesPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_secondary_anomalies\.tsv$/i, /(^|\/)dimer_secondary_anomalies\.tsv$/i]);
    const dimerSecondarySummaryPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_secondary_summary\.tsv$/i, /(^|\/)dimer_secondary_summary\.tsv$/i]);
    const dimerReadsPathLegacy = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_read_junctions\.tsv$/i, /(^|\/)dimer_read_junctions\.tsv$/i]);
    const dimerReadsPath = dimerReadsPathLegacy || dimerCanonicalReadEventsPath;
    const dimerReadLedgerPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_read_ledger\.tsv$/i, /(^|\/)dimer_read_ledger\.tsv$/i]);
    const dimerBreakpointReadsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_breakpoint_reads\.tsv$/i, /(^|\/)dimer_breakpoint_reads\.tsv$/i]);
    const dimerRotatedRemapSummaryPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_rotated_remap_summary\.tsv$/i, /(^|\/)dimer_rotated_remap_summary\.tsv$/i]);
    const dimerRotatedRemapBreakpointsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_rotated_remap_breakpoints\.tsv$/i, /(^|\/)dimer_rotated_remap_breakpoints\.tsv$/i]);
    const dimerBamPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_candidates\.aligned\.bam$/i, /(^|\/)dimer_candidates\.aligned\.bam$/i]);
    const dimerBaiPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_candidates\.aligned\.bam\.(bai|csi)$/i, /(^|\/)dimer_candidates\.aligned\.bam\.(bai|csi)$/i]);
    const dimerReferencePath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_reference\.fasta$/i, /(^|\/)dimer_reference\.fasta$/i]);
    const dimerReferenceIndexPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_reference\.fasta\.fai$/i, /(^|\/)dimer_reference\.fasta\.fai$/i]);

    const cacheKey = job.id;
    const summaryUrl = summaryPath ? toStreamHref(summaryPath, cacheKey) : null;
    const lengthsUrl = lengthsPath ? toStreamHref(lengthsPath, cacheKey) : null;
    const candidatesUrl = candidatesPath ? toStreamHref(candidatesPath, cacheKey) : null;
    const logUrl = logPath ? toStreamHref(logPath, cacheKey) : null;
    const dimerFastqUrl = dimerFastqPath ? toStreamHref(dimerFastqPath, cacheKey) : null;
    const dimerFastaUrl = dimerFastaPath ? toStreamHref(dimerFastaPath, cacheKey) : null;
    const dimerLengthsUrl = dimerLengthsPath ? toStreamHref(dimerLengthsPath, cacheKey) : null;
    const dimerSummaryUrl = dimerSummaryPath ? toStreamHref(dimerSummaryPath, cacheKey) : null;
    const dimerConsensusUrl = dimerConsensusPath ? toStreamHref(dimerConsensusPath, cacheKey) : null;
    const dominantDimerConsensusUrl = dominantDimerConsensusPath ? toStreamHref(dominantDimerConsensusPath, cacheKey) : null;
    const dominantDimerConsensusMetadataUrl = dominantDimerConsensusMetadataPath ? toStreamHref(dominantDimerConsensusMetadataPath, cacheKey) : null;
    const dimerJunctionUrl = dimerJunctionPath ? toStreamHref(dimerJunctionPath, cacheKey) : null;
    const dimerJunctionEventsUrl = dimerJunctionEventsPath ? toStreamHref(dimerJunctionEventsPath, cacheKey) : null;
    const dimerJunctionClustersUrl = dimerJunctionClustersPath ? toStreamHref(dimerJunctionClustersPath, cacheKey) : null;
    const dimerJunctionHotspotsUrl = dimerJunctionHotspotsPath ? toStreamHref(dimerJunctionHotspotsPath, cacheKey) : null;
    const dimerJunctionRotatedUrl = dimerJunctionRotatedPath ? toStreamHref(dimerJunctionRotatedPath, cacheKey) : null;
    const dimerJunctionRotationSummaryUrl = dimerJunctionRotationSummaryPath ? toStreamHref(dimerJunctionRotationSummaryPath, cacheKey) : null;
    const dimerBreakpointScreenUrl = dimerBreakpointScreenPath ? toStreamHref(dimerBreakpointScreenPath, cacheKey) : null;
    const dimerBreakpointSequencesUrl = dimerBreakpointSequencesPath ? toStreamHref(dimerBreakpointSequencesPath, cacheKey) : null;
    const dimerSecondaryAnomaliesUrl = dimerSecondaryAnomaliesPath ? toStreamHref(dimerSecondaryAnomaliesPath, cacheKey) : null;
    const dimerSecondarySummaryUrl = dimerSecondarySummaryPath ? toStreamHref(dimerSecondarySummaryPath, cacheKey) : null;
    const dimerReadsUrl = dimerReadsPath ? toStreamHref(dimerReadsPath, cacheKey) : null;
    const dimerReadLedgerUrl = dimerReadLedgerPath ? toStreamHref(dimerReadLedgerPath, cacheKey) : null;
    const dimerBreakpointReadsUrl = dimerBreakpointReadsPath ? toStreamHref(dimerBreakpointReadsPath, cacheKey) : null;
    const dimerRotatedRemapSummaryUrl = dimerRotatedRemapSummaryPath ? toStreamHref(dimerRotatedRemapSummaryPath, cacheKey) : null;
    const dimerRotatedRemapBreakpointsUrl = dimerRotatedRemapBreakpointsPath ? toStreamHref(dimerRotatedRemapBreakpointsPath, cacheKey) : null;
    const dimerBamUrl = dimerBamPath ? toStreamHref(dimerBamPath, cacheKey) : null;
    const dimerBaiUrl = dimerBaiPath ? toStreamHref(dimerBaiPath, cacheKey) : null;
    const dimerReferenceUrl = dimerReferencePath ? toStreamHref(dimerReferencePath, cacheKey) : null;
    const dimerReferenceIndexUrl = dimerReferenceIndexPath ? toStreamHref(dimerReferenceIndexPath, cacheKey) : null;

    let missingReason: string | null = null;
    if (!hasFastq) {
        missingReason = 'Run does not include FASTQ input.';
    } else if (!runFastqQc) {
        missingReason = 'FASTQ plasmid QC is disabled for this run.';
    } else if (
        !summaryUrl
        && !lengthsUrl
        && !candidatesUrl
        && !dimerSummaryUrl
        && !dimerJunctionUrl
        && !dimerJunctionEventsUrl
        && !dimerJunctionClustersUrl
        && !dimerJunctionHotspotsUrl
        && !dimerJunctionRotatedUrl
        && !dimerJunctionRotationSummaryUrl
        && !dimerBreakpointScreenUrl
        && !dimerBreakpointSequencesUrl
        && !dimerSecondaryAnomaliesUrl
        && !dimerSecondarySummaryUrl
        && !dimerConsensusUrl
        && !dimerReadsUrl
        && !dimerReadLedgerUrl
        && !dimerBreakpointReadsUrl
        && !dimerRotatedRemapSummaryUrl
        && !dimerRotatedRemapBreakpointsUrl
    ) {
        missingReason = 'No multimer QC outputs found yet.';
    }

    return {
        summaryPath,
        summaryUrl,
        lengthsPath,
        lengthsUrl,
        candidatesPath,
        candidatesUrl,
        logPath,
        logUrl,
        dimerFastqPath,
        dimerFastqUrl,
        dimerFastaPath,
        dimerFastaUrl,
        dimerLengthsPath,
        dimerLengthsUrl,
        dimerSummaryPath,
        dimerSummaryUrl,
        dimerConsensusPath,
        dimerConsensusUrl,
        dominantDimerConsensusPath,
        dominantDimerConsensusUrl,
        dominantDimerConsensusMetadataPath,
        dominantDimerConsensusMetadataUrl,
        dimerJunctionPath,
        dimerJunctionUrl,
        dimerJunctionEventsPath,
        dimerJunctionEventsUrl,
        dimerJunctionClustersPath,
        dimerJunctionClustersUrl,
        dimerJunctionHotspotsPath,
        dimerJunctionHotspotsUrl,
        dimerJunctionRotatedPath,
        dimerJunctionRotatedUrl,
        dimerJunctionRotationSummaryPath,
        dimerJunctionRotationSummaryUrl,
        dimerBreakpointScreenPath,
        dimerBreakpointScreenUrl,
        dimerBreakpointSequencesPath,
        dimerBreakpointSequencesUrl,
        dimerSecondaryAnomaliesPath,
        dimerSecondaryAnomaliesUrl,
        dimerSecondarySummaryPath,
        dimerSecondarySummaryUrl,
        dimerReadsPath,
        dimerReadsUrl,
        dimerReadLedgerPath,
        dimerReadLedgerUrl,
        dimerBreakpointReadsPath,
        dimerBreakpointReadsUrl,
        dimerRotatedRemapSummaryPath,
        dimerRotatedRemapSummaryUrl,
        dimerRotatedRemapBreakpointsPath,
        dimerRotatedRemapBreakpointsUrl,
        dimerBamPath,
        dimerBamUrl,
        dimerBaiPath,
        dimerBaiUrl,
        dimerReferencePath,
        dimerReferenceUrl,
        dimerReferenceIndexPath,
        dimerReferenceIndexUrl,
        missingReason,
    };
}

async function fetchTextRange(url: string, maxBytes: number): Promise<string> {
    const response = await fetch(url, {
        headers: {
            Range: `bytes=0-${Math.max(maxBytes - 1, 0)}`,
        },
    });
    if (!response.ok) {
        throw new Error(`Failed to fetch ${url} (${response.status})`);
    }
    return response.text();
}

function isFetchNotFoundError(error: unknown): boolean {
    const msg = error instanceof Error ? error.message : String(error);
    return /\b404\b/.test(msg);
}

function parseSummaryTable(text: string): SummaryTable | null {
    const lines = text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0 && !line.startsWith('#'));

    if (lines.length === 0) return null;
    const delimiter = lines[0].includes('\t') ? '\t' : (lines[0].includes(',') ? ',' : null);

    // modkit summary commonly emits key-value pairs. Render them as a 2-column table.
    if (!delimiter) {
        const kvRows = lines
            .map((line) => {
                const parts = line.split(/\s+/).filter(Boolean);
                if (parts.length < 2) return null;
                return [parts[0], parts.slice(1).join(' ')];
            })
            .filter((row): row is string[] => row !== null);
        if (kvRows.length > 0) {
            const table = {
                header: ['metric', 'value'],
                rows: kvRows.slice(0, 100),
            };
            return removeDeprecatedSummaryFields(table);
        }
    }

    const splitLine = (line: string): string[] => {
        if (delimiter === '\t' || delimiter === ',') return line.split(delimiter).map((v) => v.trim());
        return line.split(/\s+/).map((v) => v.trim());
    };

    const header = splitLine(lines[0]);
    const rows = lines.slice(1, 101).map(splitLine);
    const table = {
        header,
        rows,
    };
    return removeDeprecatedSummaryFields(table);
}

function parseNumericMetricsFromSummaryTable(table: SummaryTable | null): Record<string, number> {
    if (!table || table.header.length === 0) return {};
    const firstCol = table.header[0]?.trim().toLowerCase();
    if (firstCol !== 'metric') return {};

    const metrics: Record<string, number> = {};
    for (const row of table.rows) {
        const key = String(row[0] ?? '').trim().toLowerCase();
        const valueRaw = String(row[1] ?? '').trim();
        if (!key) continue;
        const numeric = Number.parseFloat(valueRaw);
        if (Number.isFinite(numeric)) {
            metrics[key] = numeric;
        }
    }
    return metrics;
}

function parseReadLengths(text: string, maxRows = 250000): number[] {
    const values: number[] = [];
    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const value = Number.parseInt(line, 10);
        if (!Number.isFinite(value) || value <= 0) continue;
        values.push(value);
        if (values.length >= maxRows) break;
    }
    return values;
}

function parseMultimerCandidates(text: string, maxRows = 1000): MultimerCandidateRow[] {
    const rows: MultimerCandidateRow[] = [];
    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const cols = line.split('\t');
        if (cols.length < 2) continue;

        const readIndex = Number.parseInt(cols[0] ?? '', 10);
        const readLength = Number.parseInt(cols[1] ?? '', 10);
        const classification = String(cols[2] ?? '').trim();

        // Skip probable header rows.
        if (!Number.isFinite(readIndex) && !Number.isFinite(readLength) && classification) continue;

        rows.push({
            readIndex: Number.isFinite(readIndex) ? readIndex : null,
            readLength: Number.isFinite(readLength) ? readLength : null,
            classification: classification || 'candidate',
        });

        if (rows.length >= maxRows) break;
    }
    return rows;
}

function removeDeprecatedSummaryFields(table: SummaryTable): SummaryTable {
    const normalizedHeader = table.header.map((col) => col.trim().toLowerCase());
    const deprecatedColumnIndexes = normalizedHeader
        .map((col, idx) => (col.includes('deprecated') ? idx : -1))
        .filter((idx) => idx >= 0);

    if (deprecatedColumnIndexes.length > 0) {
        const keepIndexes = table.header
            .map((_, idx) => idx)
            .filter((idx) => !deprecatedColumnIndexes.includes(idx));
        return {
            header: keepIndexes.map((idx) => table.header[idx]),
            rows: table.rows.map((row) => keepIndexes.map((idx) => row[idx] ?? '')),
        };
    }

    const metricIndex = normalizedHeader.indexOf('metric');
    if (metricIndex >= 0) {
        return {
            header: table.header,
            rows: table.rows.filter((row) => !String(row[metricIndex] ?? '').toLowerCase().includes('deprecated')),
        };
    }

    return table;
}

function parseBedTopLoci(text: string, maxRows = 12): MethylationLocus[] {
    const loci: MethylationLocus[] = [];
    const percentScale = inferPercentScaleFromBedText(text);
    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const cols = line.split('\t');
        if (cols.length < 3) continue;

        const start0 = Number.parseInt(cols[1] ?? '', 10);
        const end = Number.parseInt(cols[2] ?? '', 10);
        if (!Number.isFinite(start0) || !Number.isFinite(end)) continue;
        const code = normalizeModCode(cols[3] || '');
        if (!RELEVANT_METHYLATION_CODES.has(code)) continue;

        const coverageValue = Number.parseFloat(cols[9] ?? '');
        const coverage = Number.isFinite(coverageValue) ? coverageValue : null;

        let percentValue = Number.parseFloat(cols[10] ?? '');
        if (!Number.isFinite(percentValue)) {
            percentValue = Number.parseFloat(cols[4] ?? '');
        }
        if (Number.isFinite(percentValue)) {
            percentValue *= percentScale;
        }
        const percentModified = Number.isFinite(percentValue) ? percentValue : null;

        loci.push({
            chrom: cols[0],
            start: start0 + 1,
            end,
            code,
            strand: cols[5] || '.',
            percentModified,
            coverage,
        });
    }

    const withPercent = loci
        .filter((locus) => locus.percentModified != null)
        .sort((a, b) => (b.percentModified ?? -1) - (a.percentModified ?? -1));

    if (withPercent.length > 0) return withPercent.slice(0, maxRows);
    return loci.slice(0, maxRows);
}

interface ParsedBedRecord {
    chrom: string;
    position: number; // 1-based
    code: string;
    strand: string;
    percentModified: number | null;
    coverage: number | null;
    context: string;
}

interface FastaRecord {
    id: string;
    sequence: string;
}

function normalizeModCode(raw: string): string {
    const firstToken = raw.trim().toLowerCase().split(',')[0];
    if (!firstToken) return '';
    if (firstToken === '6ma' || firstToken === 'm6a') return 'a';
    if (firstToken === '5mc' || firstToken === 'm5c') return 'm';
    if (firstToken === '5hmc') return 'h';
    return firstToken;
}

function parseModkitBedRecords(text: string): ParsedBedRecord[] {
    const rows: ParsedBedRecord[] = [];
    const percentScale = inferPercentScaleFromBedText(text);
    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const cols = line.split('\t');
        if (cols.length < 11) continue;

        const start0 = Number.parseInt(cols[1] ?? '', 10);
        if (!Number.isFinite(start0)) continue;

        const coverageValue = Number.parseFloat(cols[9] ?? '');
        const coverage = Number.isFinite(coverageValue) ? coverageValue : null;

        let percentValue = Number.parseFloat(cols[10] ?? '');
        if (Number.isFinite(percentValue)) {
            percentValue *= percentScale;
        }
        const percentModified = Number.isFinite(percentValue) ? percentValue : null;

        rows.push({
            chrom: cols[0] ?? 'unknown',
            position: start0 + 1,
            code: normalizeModCode(cols[3] || ''),
            strand: cols[5] || '.',
            percentModified,
            coverage,
            context: cols[3] || '',
        });
    }
    return rows;
}

function inferPercentScaleFromBedText(text: string): number {
    void text;
    // modkit pileup bedMethyl uses column 11 as percent modified (0..100).
    // Keep scale fixed at 1 to avoid 100x inflation when controls have only sub-1% calls.
    return 1;
}

function aggregateMethylationSeries(records: ParsedBedRecord[]): MethylationSeries[] {
    const byCode = new Map<string, Map<string, { chrom: string; position: number; weighted: number; weight: number; coverage: number }>>();

    for (const record of records) {
        const code = record.code || 'unknown';
        if (!byCode.has(code)) {
            byCode.set(code, new Map());
        }
        const codeMap = byCode.get(code)!;
        const key = `${record.chrom}:${record.position}`;
        if (!codeMap.has(key)) {
            codeMap.set(key, {
                chrom: record.chrom,
                position: record.position,
                weighted: 0,
                weight: 0,
                coverage: 0,
            });
        }
        const acc = codeMap.get(key)!;
        const weight = record.coverage && record.coverage > 0 ? record.coverage : 1;
        if (record.percentModified != null) {
            acc.weighted += record.percentModified * weight;
            acc.weight += weight;
        }
        if (record.coverage != null && record.coverage > acc.coverage) {
            acc.coverage = record.coverage;
        }
    }

    const labelByCode: Record<string, string> = {
        m: '5mC',
        h: '5hmC',
        a: '6mA',
        '6ma': '6mA',
        '5mc': '5mC',
    };

    const allSeries: MethylationSeries[] = [];
    for (const [code, pointsMap] of byCode.entries()) {
        const points: MethylationPoint[] = Array.from(pointsMap.values())
            .map((v) => ({
                chrom: v.chrom,
                position: v.position,
                code,
                percentModified: v.weight > 0 ? v.weighted / v.weight : null,
                coverage: v.coverage > 0 ? v.coverage : null,
            }))
            .sort((a, b) => a.position - b.position);

        if (points.length > MAX_PLOT_POINTS_PER_CODE) {
            const stride = Math.ceil(points.length / MAX_PLOT_POINTS_PER_CODE);
            const downsampled = points.filter((_, idx) => idx % stride === 0);
            allSeries.push({
                code,
                label: labelByCode[code] || `Code ${code}`,
                points: downsampled,
            });
            continue;
        }

        allSeries.push({
            code,
            label: labelByCode[code] || `Code ${code}`,
            points,
        });
    }

    return allSeries.sort((a, b) => a.label.localeCompare(b.label));
}

function parseFastaRecords(text: string): FastaRecord[] {
    const records: FastaRecord[] = [];
    let currentId: string | null = null;
    let chunks: string[] = [];

    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line) continue;
        if (line.startsWith('>')) {
            if (currentId && chunks.length > 0) {
                records.push({ id: currentId, sequence: chunks.join('').toUpperCase() });
            }
            currentId = line.slice(1).trim().split(/\s+/)[0] || null;
            chunks = [];
        } else if (currentId) {
            chunks.push(line);
        }
    }

    if (currentId && chunks.length > 0) {
        records.push({ id: currentId, sequence: chunks.join('').toUpperCase() });
    }

    return records;
}

function findMotifStarts(sequence: string, motifRegex: RegExp): Array<{ start: number; context: string }> {
    const hits: Array<{ start: number; context: string }> = [];
    const regex = new RegExp(motifRegex.source, motifRegex.flags.includes('g') ? motifRegex.flags : `${motifRegex.flags}g`);
    let match: RegExpExecArray | null;
    while ((match = regex.exec(sequence)) !== null) {
        const captured = match[1] || match[0];
        if (!captured) {
            regex.lastIndex += 1;
            continue;
        }
        hits.push({ start: match.index, context: captured });
        if (match.index === regex.lastIndex) {
            regex.lastIndex += 1;
        }
    }
    return hits;
}

function buildPositionCodeLookup(records: ParsedBedRecord[]): Map<string, { weighted: number; weight: number; coverage: number }> {
    const lookup = new Map<string, { weighted: number; weight: number; coverage: number }>();
    for (const record of records) {
        const key = `${record.chrom}:${record.position}:${record.code}`;
        if (!lookup.has(key)) {
            lookup.set(key, { weighted: 0, weight: 0, coverage: 0 });
        }
        const entry = lookup.get(key)!;
        const weight = record.coverage && record.coverage > 0 ? record.coverage : 1;
        if (record.percentModified != null) {
            entry.weighted += record.percentModified * weight;
            entry.weight += weight;
        }
        if (record.coverage != null && record.coverage > entry.coverage) {
            entry.coverage = record.coverage;
        }
    }
    return lookup;
}

function lookupCodePercent(
    lookup: Map<string, { weighted: number; weight: number; coverage: number }>,
    chrom: string,
    position: number,
    code: string
): { percent: number | null; coverage: number | null } {
    const key = `${chrom}:${position}:${code}`;
    const entry = lookup.get(key);
    if (!entry) return { percent: null, coverage: null };
    const percent = entry.weight > 0 ? entry.weighted / entry.weight : null;
    const coverage = entry.coverage > 0 ? entry.coverage : null;
    return { percent, coverage };
}

function pickDominantCytosineCall(
    mCall: { percent: number | null; coverage: number | null },
    hCall: { percent: number | null; coverage: number | null }
): { percent: number | null; coverage: number | null } {
    const mPercent = mCall.percent;
    const hPercent = hCall.percent;
    const hasM = mPercent != null;
    const hasH = hPercent != null;

    if (!hasM && !hasH) {
        return { percent: null, coverage: null };
    }

    const coverage = Math.max(mCall.coverage || 0, hCall.coverage || 0) || null;
    if (!hasH || (hasM && (mPercent as number) >= (hPercent as number))) {
        return { percent: mPercent, coverage };
    }
    return { percent: hPercent, coverage };
}

function buildDamDcmCalls(
    records: ParsedBedRecord[],
    fastaText: string
): {
    damSites: MotifSiteCall[];
    dcmSites: MotifSiteCall[];
    referenceName: string | null;
    referenceLength: number | null;
    referenceSequence: string | null;
} {
    const fastaRecords = parseFastaRecords(fastaText);
    if (fastaRecords.length === 0) {
        return { damSites: [], dcmSites: [], referenceName: null, referenceLength: null, referenceSequence: null };
    }

    const chromCounts = new Map<string, number>();
    for (const record of records) {
        chromCounts.set(record.chrom, (chromCounts.get(record.chrom) || 0) + 1);
    }
    const mostFrequentChrom = Array.from(chromCounts.entries()).sort((a, b) => b[1] - a[1])[0]?.[0];
    const selectedRef = fastaRecords.find((rec) => rec.id === mostFrequentChrom) || fastaRecords[0];
    const chrom = selectedRef.id;
    const lookup = buildPositionCodeLookup(records);

    const damStarts = findMotifStarts(selectedRef.sequence, /(?=(GATC))/gi);
    const dcmStarts = findMotifStarts(selectedRef.sequence, /(?=(CC[AT]GG))/gi);

    const damSites: MotifSiteCall[] = [];
    for (const hit of damStarts) {
        // GATC plus-strand adenine at start+2 (1-based), opposite-strand adenine maps to start+3.
        const plusPos = hit.start + 2;
        const minusPos = hit.start + 3;
        const pairKey = `${chrom}:Dam:${hit.start + 1}`;
        const plus = lookupCodePercent(lookup, chrom, plusPos, 'a');
        const minus = lookupCodePercent(lookup, chrom, minusPos, 'a');
        damSites.push({
            chrom,
            motif: 'Dam',
            position: plusPos,
            context: hit.context,
            strand: '+',
            pairKey,
            percentModified: plus.percent,
            coverage: plus.coverage,
        });
        damSites.push({
            chrom,
            motif: 'Dam',
            position: minusPos,
            context: hit.context,
            strand: '-',
            pairKey,
            percentModified: minus.percent,
            coverage: minus.coverage,
        });
    }

    const dcmSites: MotifSiteCall[] = [];
    for (const hit of dcmStarts) {
        // CCWGG second cytosine at start+2 (1-based), opposite-strand second cytosine maps to start+4.
        const plusPos = hit.start + 2;
        const minusPos = hit.start + 4;
        const pairKey = `${chrom}:Dcm:${hit.start + 1}`;

        const plusM = lookupCodePercent(lookup, chrom, plusPos, 'm');
        const plusH = lookupCodePercent(lookup, chrom, plusPos, 'h');
        const plus = pickDominantCytosineCall(plusM, plusH);

        const minusM = lookupCodePercent(lookup, chrom, minusPos, 'm');
        const minusH = lookupCodePercent(lookup, chrom, minusPos, 'h');
        const minus = pickDominantCytosineCall(minusM, minusH);

        dcmSites.push({
            chrom,
            motif: 'Dcm',
            position: plusPos,
            context: hit.context,
            strand: '+',
            pairKey,
            percentModified: plus.percent,
            coverage: plus.coverage,
        });
        dcmSites.push({
            chrom,
            motif: 'Dcm',
            position: minusPos,
            context: hit.context,
            strand: '-',
            pairKey,
            percentModified: minus.percent,
            coverage: minus.coverage,
        });
    }

    return {
        damSites,
        dcmSites,
        referenceName: selectedRef.id,
        referenceLength: selectedRef.sequence.length,
        referenceSequence: selectedRef.sequence,
    };
}

function formatSequenceContext(sequence: string, position: number, flank = 12): { text: string; start: number; end: number } {
    const clampedPos = Math.max(1, Math.min(sequence.length, position));
    const start = Math.max(1, clampedPos - flank);
    const end = Math.min(sequence.length, clampedPos + flank);
    const prefix = sequence.slice(start - 1, clampedPos - 1);
    const base = sequence.charAt(clampedPos - 1) || 'N';
    const suffix = sequence.slice(clampedPos, end);
    return {
        text: `${prefix}[${base}]${suffix}`,
        start,
        end,
    };
}

function buildSequenceRows(sequence: string, lineWidth = REFERENCE_SEQUENCE_LINE_WIDTH): SequenceRow[] {
    if (!sequence) return [];
    const rows: SequenceRow[] = [];
    for (let start = 1; start <= sequence.length; start += lineWidth) {
        const end = Math.min(sequence.length, start + lineWidth - 1);
        rows.push({
            start,
            end,
            bases: sequence.slice(start - 1, end),
        });
    }
    return rows;
}

function clampNumber(value: number, min: number, max: number): number {
    return Math.min(max, Math.max(min, value));
}

function toAlphaColor(color: string, alpha: number): string {
    const clampedAlpha = clampNumber(alpha, 0, 1);
    const normalized = color.trim();
    const hex = normalized.startsWith('#') ? normalized.slice(1) : normalized;
    if (/^[0-9a-fA-F]{3}$/.test(hex)) {
        const r = parseInt(hex[0] + hex[0], 16);
        const g = parseInt(hex[1] + hex[1], 16);
        const b = parseInt(hex[2] + hex[2], 16);
        return `rgba(${r}, ${g}, ${b}, ${clampedAlpha.toFixed(3)})`;
    }
    if (/^[0-9a-fA-F]{6}$/.test(hex)) {
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${clampedAlpha.toFixed(3)})`;
    }
    return normalized;
}

function percentToHighlightAlpha(percentModified: number): number {
    const normalized = clampNumber(percentModified, 0, 100) / 100;
    return 0.12 + (0.72 * normalized);
}

function buildHighlightedSequenceSegments(
    row: SequenceRow,
    highlightsByPosition: Map<number, SequenceBaseHighlight>,
    selectedPosition: number | null
): HighlightedSequenceSegment[] {
    const segments: HighlightedSequenceSegment[] = [];
    let plainBuffer = '';
    let plainStartPos = row.start;

    const flushPlain = () => {
        if (!plainBuffer) return;
        segments.push({
            text: plainBuffer,
            position: plainStartPos,
            highlight: null,
            isSelected: false,
        });
        plainBuffer = '';
    };

    for (let offset = 0; offset < row.bases.length; offset += 1) {
        const position = row.start + offset;
        const base = row.bases.charAt(offset);
        const highlight = highlightsByPosition.get(position) || null;
        const isSelected = selectedPosition != null && selectedPosition === position;

        if (!highlight && !isSelected) {
            if (plainBuffer.length === 0) {
                plainStartPos = position;
            }
            plainBuffer += base;
            continue;
        }

        flushPlain();
        segments.push({
            text: base,
            position,
            highlight,
            isSelected,
        });
    }

    flushPlain();
    return segments;
}

function parseSemver(version: string): [number, number, number] | null {
    const match = version.match(/^(\d+)\.(\d+)\.(\d+)/);
    if (!match) return null;
    return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function isVersionAtLeast(actual: string, required: string): boolean {
    const actualParts = parseSemver(actual);
    const requiredParts = parseSemver(required);
    if (!actualParts || !requiredParts) return false;
    for (let i = 0; i < 3; i += 1) {
        if (actualParts[i] > requiredParts[i]) return true;
        if (actualParts[i] < requiredParts[i]) return false;
    }
    return true;
}

async function loadIgvLibrary(): Promise<{ igv: IgvLibrary; version: string }> {
    if (!igvLibraryPromise) {
        igvLibraryPromise = import('igv')
            .then((mod) => {
                const igv = mod.default;
                if (!igv || typeof igv.createBrowser !== 'function') {
                    throw new Error('IGV package loaded, but createBrowser is unavailable.');
                }
                const version = typeof igv.version === 'function' ? igv.version() : 'unknown';
                if (version !== 'unknown' && !isVersionAtLeast(version, IGV_REQUIRED_VERSION)) {
                    throw new Error(`Loaded IGV.js ${version}, but ${IGV_REQUIRED_VERSION}+ is required.`);
                }
                return { igv, version };
            })
            .catch((error) => {
                igvLibraryPromise = null;
                throw error instanceof Error ? error : new Error(String(error));
            });
    }
    return igvLibraryPromise;
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
    let timeoutHandle: number | null = null;
    try {
        return await Promise.race([
            promise,
            new Promise<T>((_, reject) => {
                timeoutHandle = window.setTimeout(() => reject(new Error(message)), timeoutMs);
            }),
        ]);
    } finally {
        if (timeoutHandle != null) {
            window.clearTimeout(timeoutHandle);
        }
    }
}

async function detectInitialLocusFromFasta(fastaUrl: string | null): Promise<string | undefined> {
    if (!fastaUrl) return undefined;
    try {
        const response = await fetch(fastaUrl, {
            headers: { Range: 'bytes=0-2097151' },
        });
        if (!response.ok) return undefined;
        const text = await response.text();
        const lines = text.split(/\r?\n/);
        let chrom = '';
        let seqLen = 0;
        for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line) continue;
            if (line.startsWith('>')) {
                if (chrom) break;
                chrom = line.replace(/^>\s*/, '').split(/\s+/)[0] || '';
                continue;
            }
            if (!chrom) continue;
            seqLen += line.replace(/\s+/g, '').length;
            if (seqLen > IGV_INITIAL_FULL_LOCUS_MAX_BP) break;
        }
        if (!chrom) return undefined;
        if (seqLen > 0 && seqLen <= IGV_INITIAL_FULL_LOCUS_MAX_BP) {
            return `${chrom}:1-${seqLen}`;
        }
        return `${chrom}:1-${IGV_INITIAL_LOCUS_WINDOW_BP}`;
    } catch {
        return undefined;
    }
}

function resolveIgvReadsTrackHeight(container: HTMLDivElement | null, auxiliaryTrackHeightPx = 0): number {
    const height = container?.clientHeight ?? 0;
    if (height <= 0) return IGV_READS_TRACK_MIN_WITHOUT_AUX_PX;
    if (auxiliaryTrackHeightPx <= 0) {
        return Math.max(IGV_READS_TRACK_MIN_WITHOUT_AUX_PX, Math.floor(height - IGV_READS_TRACK_BOTTOM_GUTTER_PX));
    }
    const reservedCap = Math.floor(height * IGV_AUX_TRACK_MAX_RESERVED_FRACTION);
    const reserved = Math.min(
        reservedCap,
        Math.max(0, Math.round(auxiliaryTrackHeightPx + IGV_AUX_TRACK_RESERVED_GUTTER_PX))
    );
    // Keep only a tiny gutter so alignment rows can consume nearly all available vertical space.
    return Math.max(IGV_READS_TRACK_MIN_HEIGHT_PX, Math.floor(height - reserved - IGV_READS_TRACK_BOTTOM_GUTTER_PX));
}

function findIgvAlignmentTrack(browser: UntypedApiValue): UntypedApiValue | null {
    if (!browser || typeof browser.findTracks !== 'function') return null;
    const tracks = browser.findTracks((track: UntypedApiValue) => {
        if (!track) return false;
        const type = String(track.type || track.config?.type || '').toLowerCase();
        return type === 'alignment' || Boolean(track.alignmentTrack);
    });
    if (Array.isArray(tracks) && tracks.length > 0) return tracks[0];
    const byName = browser.findTracks('name', 'Aligned Reads');
    if (Array.isArray(byName) && byName.length > 0) return byName[0];
    return null;
}

function applyIgvAlignmentOptionsToTrack(
    track: UntypedApiValue,
    options: { displayMode: string; colorBy: string; groupBy: string }
): void {
    if (!track) return;
    const bamTrack = track.alignmentTrack ? track : null;
    const alignmentTrack = bamTrack?.alignmentTrack || track.alignmentTrack || track;
    if (!alignmentTrack) return;

    const displayMode = options.displayMode.toUpperCase();
    // IGV alignment tracks use explicit "none" here; undefined falls back to default
    // (often pair-based coloring), which makes UI controls appear ineffective.
    const nextColorBy = options.colorBy || 'none';
    const nextGroupBy = options.groupBy === 'none' ? undefined : options.groupBy;
    const colorChanged = alignmentTrack.colorBy !== nextColorBy;
    const groupChanged = alignmentTrack.groupBy !== nextGroupBy;
    const trackView = alignmentTrack.trackView || bamTrack?.trackView || track.trackView;
    const targets = [alignmentTrack, bamTrack, track].filter((candidate, idx, arr) => (
        candidate && arr.indexOf(candidate) === idx
    ));

    if (typeof alignmentTrack.setDisplayMode === 'function') {
        alignmentTrack.setDisplayMode(displayMode);
    } else if (typeof track.setDisplayMode === 'function') {
        track.setDisplayMode(displayMode);
    } else {
        alignmentTrack.displayMode = displayMode;
        if (alignmentTrack.config) {
            alignmentTrack.config.displayMode = displayMode;
        }
        trackView?.checkContentHeight?.();
    }

    for (const target of targets) {
        target.colorBy = nextColorBy;
        if (target.config) {
            target.config.colorBy = nextColorBy;
        }
        target.groupBy = nextGroupBy;
        if (target.config) {
            target.config.groupBy = nextGroupBy;
        }
    }

    if (groupChanged) {
        const repackTarget = targets.find((candidate) => typeof candidate?.repackAlignments === 'function');
        if (repackTarget) {
            repackTarget.repackAlignments();
            return;
        }
    }

    if (groupChanged && typeof alignmentTrack.getCachedAlignmentContainers === 'function') {
        const containers = alignmentTrack.getCachedAlignmentContainers();
        const packTarget = targets.find((candidate) => typeof candidate?.setDisplayMode === 'function') || alignmentTrack;
        if (Array.isArray(containers)) {
            for (const container of containers) {
                if (container && typeof container.pack === 'function') {
                    container.pack(packTarget);
                }
            }
        }
        trackView?.checkContentHeight?.();
        return;
    }

    if (colorChanged || groupChanged) {
        trackView?.checkContentHeight?.();
    }
    trackView?.repaintViews?.();
}

function resolveIgvAuxiliaryTrackHeight(browser: UntypedApiValue): number {
    if (!browser || typeof browser.findTracks !== 'function') return 0;
    const auxTracks = browser.findTracks((track: UntypedApiValue) => {
        if (!track) return false;
        const type = String(track.type || track.config?.type || '').toLowerCase();
        return type !== 'alignment' && type !== 'ruler' && type !== 'ideogram';
    });
    if (!Array.isArray(auxTracks) || auxTracks.length === 0) return 0;
    return auxTracks.reduce((sum: number, track: UntypedApiValue) => {
        const height = Number(track?.height ?? track?.config?.height);
        if (!Number.isFinite(height)) return sum + 42;
        return sum + Math.max(20, Math.round(height));
    }, 0);
}

function resizeIgvAlignmentTrackToContainer(browser: UntypedApiValue, container: HTMLDivElement | null): void {
    const track = findIgvAlignmentTrack(browser);
    if (!track) return;
    const alignmentTrack = track.alignmentTrack || track;
    const trackView = alignmentTrack?.trackView || track?.trackView;
    if (!alignmentTrack || !trackView) return;

    const auxHeight = resolveIgvAuxiliaryTrackHeight(browser);
    const nextHeight = resolveIgvReadsTrackHeight(container, auxHeight);
    const currentHeight = Number(alignmentTrack.height ?? track.height ?? alignmentTrack.config?.height ?? 0);
    if (Number.isFinite(currentHeight) && Math.abs(currentHeight - nextHeight) < 2) return;

    alignmentTrack.height = nextHeight;
    if (alignmentTrack.config) {
        alignmentTrack.config.height = nextHeight;
    }
    if (track !== alignmentTrack) {
        track.height = nextHeight;
        if (track.config) {
            track.config.height = nextHeight;
        }
    }
    trackView.checkContentHeight?.();
    trackView.repaintViews?.();
}

async function requestDocumentFullscreen(): Promise<void> {
    if (!document.fullscreenEnabled || document.fullscreenElement) return;
    await document.documentElement.requestFullscreen();
}

async function exitDocumentFullscreen(): Promise<void> {
    if (!document.fullscreenElement) return;
    await document.exitFullscreen();
}

function resolveThemeColor(cssVarName: string, fallback: string): string {
    const value = getComputedStyle(document.documentElement).getPropertyValue(cssVarName).trim();
    return value || fallback;
}

function ensureIgvThemeStyles(container: HTMLDivElement | null): void {
    const shadowRoot = container?.shadowRoot;
    if (!shadowRoot) return;
    if (shadowRoot.querySelector('style[data-bms-igv-theme="true"]')) return;

    const style = document.createElement('style');
    style.setAttribute('data-bms-igv-theme', 'true');
    style.textContent = `
      .igv-container {
        background-color: var(--bg-primary) !important;
        color: var(--text-primary) !important;
      }
      .igv-navbar,
      .igv-navbar-left-container,
      .igv-navbar-right-container,
      .igv-navbar-header,
      .igv-trackgear-container,
      .igv-current-genome,
      .igv-locus-size-group,
      .igv-search-container,
      .igv-ruler-div,
      .igv-ruler-shim,
      .igv-ruler-label,
      .igv-whole-genome-container,
      .igv-track-label,
      .igv-track-label-name,
      .igv-ruler-tooltip,
      .igv-cytoband-tooltip,
      .igv-viewport-message {
        color: var(--text-primary) !important;
      }
      .igv-ruler-div svg text,
      .igv-ruler-div text {
        fill: var(--text-primary) !important;
      }
      .igv-ruler-div,
      .igv-ruler-shim,
      .igv-ruler-shim * {
        color: var(--text-primary) !important;
      }
      .igv-menu-popup *,
      .igv-ui-dropdown *,
      .igv-track-menu-category {
        color: var(--text-primary) !important;
      }
      .igv-navbar,
      .igv-menu-popup,
      .igv-ui-dropdown,
      .igv-ui-dropdown > div,
      .igv-ui-dropdown > div > div,
      .igv-menu-popup > div:not(:first-child) > div,
      .igv-track-label {
        background-color: var(--bg-secondary) !important;
        border-color: var(--border-primary) !important;
      }
      .igv-search-input,
      .igv-current-genome,
      .igv-locus-size-group {
        color: var(--text-primary) !important;
        background-color: var(--bg-primary) !important;
        border-color: var(--border-primary) !important;
      }
      .igv-search-input::placeholder {
        color: var(--text-secondary) !important;
        opacity: 1;
      }
      .igv-navbar-button,
      .igv-navbar-text-button,
      .igv-navbar-icon-button {
        color: var(--text-primary) !important;
        border-color: var(--border-primary) !important;
        background-color: var(--bg-tertiary) !important;
      }
      .igv-navbar-button:hover,
      .igv-navbar-text-button:hover,
      .igv-navbar-icon-button:hover {
        background-color: var(--card-hover) !important;
      }
      .igv-ruler-tooltip > div,
      .igv-cytoband-tooltip > div {
        color: var(--text-primary) !important;
        background-color: color-mix(in srgb, var(--bg-secondary) 96%, transparent) !important;
        border: 1px solid var(--border-primary) !important;
      }
      .igv-navbar-text-button-svg-inactive rect,
      #igv-save-svg-group rect,
      #igv-save-png-group rect {
        fill: var(--bg-tertiary) !important;
        stroke: var(--border-secondary) !important;
      }
      .igv-navbar-text-button-svg-inactive text,
      #igv-save-svg-group text,
      #igv-save-png-group text {
        fill: var(--text-primary) !important;
      }
      .igv-navbar-text-button-svg-hover rect,
      #igv-save-svg-group:hover rect,
      #igv-save-png-group:hover rect {
        fill: var(--card-hover) !important;
        stroke: var(--border-secondary) !important;
      }
      .igv-navbar-text-button-svg-hover text,
      #igv-save-svg-group:hover text,
      #igv-save-png-group:hover text {
        fill: var(--text-primary) !important;
      }
      .igv-viewport-message {
        background-color: color-mix(in srgb, var(--bg-secondary) 90%, transparent) !important;
      }
    `;
    shadowRoot.appendChild(style);
}

function patchIgvRulerContrast(browser: UntypedApiValue): void {
    if (!browser || typeof browser.findTracks !== 'function') return;
    const tracks = browser.findTracks((track: UntypedApiValue) => {
        if (!track) return false;
        const type = String(track.type || track.config?.type || '').toLowerCase();
        const idOrName = String(track.id || track.name || '').toLowerCase();
        return type === 'ruler' || idOrName.includes('ruler');
    });
    if (!Array.isArray(tracks)) return;

    for (const track of tracks) {
        if (!track || track.__bmsRulerContrastPatched) continue;
        if (typeof track.doDraw !== 'function') continue;
        if (typeof track.height === 'number' && track.height < 56) {
            track.height = 56;
        }

        const originalDoDraw = track.doDraw.bind(track);
        track.doDraw = (args: UntypedApiValue) => {
            const context = args?.context;
            if (!context || typeof context.save !== 'function' || typeof context.restore !== 'function') {
                return originalDoDraw(args);
            }

            const textColor = resolveThemeColor('--text-primary', '#e5e7eb');
            const lineColor = resolveThemeColor('--border-secondary', textColor);
            const fontFamily = getComputedStyle(document.body).fontFamily || 'sans-serif';
            const originalFillText = typeof context.fillText === 'function'
                ? context.fillText.bind(context)
                : null;
            const originalStrokeText = typeof context.strokeText === 'function'
                ? context.strokeText.bind(context)
                : null;

            context.save();
            context.fillStyle = textColor;
            context.strokeStyle = lineColor;
            context.lineWidth = 1.1;
            context.font = `600 13px ${fontFamily}`;
            context.shadowColor = 'rgba(0, 0, 0, 0.5)';
            context.shadowBlur = 1;
            if (originalFillText) {
                context.fillText = (...drawArgs: UntypedApiValue[]) => {
                    context.fillStyle = textColor;
                    context.font = `600 13px ${fontFamily}`;
                    return originalFillText(...drawArgs);
                };
            }
            if (originalStrokeText) {
                context.strokeText = (...drawArgs: UntypedApiValue[]) => {
                    context.strokeStyle = 'rgba(0, 0, 0, 0.55)';
                    context.lineWidth = 1.2;
                    return originalStrokeText(...drawArgs);
                };
            }
            try {
                return originalDoDraw(args);
            } finally {
                if (originalFillText) {
                    context.fillText = originalFillText;
                }
                if (originalStrokeText) {
                    context.strokeText = originalStrokeText;
                }
                context.restore();
            }
        };
        track.__bmsRulerContrastPatched = true;
    }
}

function stageDisplayName(stage: string | null | undefined): string {
    if (!stage) return '—';
    const key = stage.toLowerCase();
    return STAGE_LABELS[key] || stage;
}

function ontWorkflowDisplayName(workflowId: unknown, fallbackMode: string): string {
    const labels: Record<string, string> = {
        ont_basecall_dna: 'ONT DNA Basecalling',
        ont_basecall_rna: 'ONT RNA Basecalling',
        ont_plasmid_qc: 'ONT Plasmid QC',
        ont_construct_screening: 'ONT Construct Screening',
        ont_methylation_analysis: 'ONT Methylation Analysis',
        ont_fastq_qc: 'ONT FASTQ QC',
        wf_clone_validation: 'wf-clone-validation',
    };
    return typeof workflowId === 'string' && labels[workflowId] ? labels[workflowId] : fallbackMode.replace(/_/g, ' ');
}

function formatParamValue(value: unknown): string {
    if (value === undefined || value === null || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

export function NGSToolkit() {
    const navigate = useNavigate();
    const [view, setView] = useState<ToolkitView>('launch');
    const [initialValues, setInitialValues] = useState<Record<string, unknown> | undefined>(undefined);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
    const [logsModalOpen, setLogsModalOpen] = useState(false);
    const [logsLoading, setLogsLoading] = useState(false);
    const [logsData, setLogsData] = useState<JobLogs | null>(null);
    const [activeLogTab, setActiveLogTab] = useState<LogTab>('parsed');
    const [igvModalOpen, setIgvModalOpen] = useState(false);
    const [igvIsFullscreen, setIgvIsFullscreen] = useState(false);
    const [igvLoading, setIgvLoading] = useState(false);
    const [igvError, setIgvError] = useState<string | null>(null);
    const [igvVersion, setIgvVersion] = useState<string | null>(null);
    const [igvAutoLoadAttempted, setIgvAutoLoadAttempted] = useState(false);
    const [igvAlignmentDisplayMode, setIgvAlignmentDisplayMode] = useState<string>('EXPANDED');
    const [igvAlignmentColorBy, setIgvAlignmentColorBy] = useState<string>('strand');
    const [igvAlignmentGroupBy, setIgvAlignmentGroupBy] = useState<string>('none');
    const [igvSelectedBamPath, setIgvSelectedBamPath] = useState<string>('');
    const [igvSelectedReferencePath, setIgvSelectedReferencePath] = useState<string>('');
    const [selectedAlignmentSessionId, setSelectedAlignmentSessionId] = useState<string>('');
    const [multimerLoading, setMultimerLoading] = useState(false);
    const [multimerError, setMultimerError] = useState<string | null>(null);
    const [multimerReport, setMultimerReport] = useState<MultimerReportData | null>(null);
    const [methylationLoading, setMethylationLoading] = useState(false);
    const [methylationError, setMethylationError] = useState<string | null>(null);
    const [methylationReport, setMethylationReport] = useState<MethylationReportData | null>(null);
    const [showRawTopLoci, setShowRawTopLoci] = useState(false);
    const [selectedMotifPoint, setSelectedMotifPoint] = useState<SelectedMotifPoint | null>(null);
    const [strandFilter, setStrandFilter] = useState<(typeof METHYLATION_STRAND_FILTERS)[number]>('both');
    const [motifMinCoverage, setMotifMinCoverage] = useState<number>(DEFAULT_MOTIF_MIN_COVERAGE);
    const [requireStrandConcordance, setRequireStrandConcordance] = useState(true);
    const igvContainerRef = useRef<HTMLDivElement | null>(null);
    const igvLoadTokenRef = useRef(0);
    const igvBrowserRef = useRef<UntypedApiValue | null>(null);
    const igvLibraryRef = useRef<UntypedApiValue | null>(null);
    const pendingIgvLocusRef = useRef<PendingSessionNavigation | null>(null);
    const selectedAlignmentSessionIdRef = useRef('');
    const igvLoadedSourceKeyRef = useRef('');
    const [igvCurrentLocus, setIgvCurrentLocus] = useState<AlignmentReadLocus | null>(null);
    const [igvReadsTrackLoaded, setIgvReadsTrackLoaded] = useState(false);
    const [igvReadsTrackLoading, setIgvReadsTrackLoading] = useState(false);
    const themeColors = useThemeColors();
    const basePlotlyLayout = useThemePlotlyLayout();

    const openIgvModal = useCallback(async () => {
        setIgvModalOpen(true);
        try {
            await requestDocumentFullscreen();
        } catch {
            // Fullscreen requests can be denied by browser policy; modal still opens.
        }
    }, []);

    const closeIgvModal = useCallback(async () => {
        setIgvModalOpen(false);
        setIgvError(null);
        igvLoadedSourceKeyRef.current = '';
        try {
            await exitDocumentFullscreen();
        } catch {
            // no-op
        }
    }, []);

    useEffect(() => {
        const handleFullscreenChange = () => {
            setIgvIsFullscreen(Boolean(document.fullscreenElement));
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        handleFullscreenChange();
        return () => {
            document.removeEventListener('fullscreenchange', handleFullscreenChange);
        };
    }, []);

    useEffect(() => {
        const raw = localStorage.getItem('clonedJobData');
        if (!raw) return;
        try {
            const parsed = JSON.parse(raw);
            if (parsed?.model_id === 'nanopore') {
                const clonedJob = {
                    name: parsed.name,
                    params: parsed.params || {},
                } as Job;
                setInitialValues(normalizeNanoporeCloneState(clonedJob));
                setView('launch');
            }
        } catch (e) {
            console.error('Failed to parse cloned nanopore data:', e);
        } finally {
            localStorage.removeItem('clonedJobData');
        }
    }, []);

    const { data: jobsData, isLoading } = useQuery({
        queryKey: ['jobs', 'ngs'],
        queryFn: () => fetchJobs({ include_children: true, model_id: 'nanopore', limit: 100, summary: true }),
        refetchInterval: (query) => jobPollingInterval(5000, query),
    });

    const nanoporeJobs = useMemo(() => {
        const jobs = jobsData?.data.jobs || [];
        return jobs.filter((j) =>
            j.model_id === 'nanopore' ||
            j.mode === 'methylation_analysis' ||
            j.mode === 'nanopore_methylation'
        );
    }, [jobsData]);

    const filteredJobs = useMemo(() => {
        return nanoporeJobs.filter((job) => {
            const q = search.trim().toLowerCase();
            const matchesSearch = !q ||
                job.name.toLowerCase().includes(q) ||
                job.id.toLowerCase().includes(q);
            const matchesStatus = statusFilter === 'all' || job.status === statusFilter;
            return matchesSearch && matchesStatus;
        });
    }, [nanoporeJobs, search, statusFilter]);

    const selectedJob = useMemo(() => {
        if (!selectedJobId) return null;
        return nanoporeJobs.find((job) => job.id === selectedJobId) || null;
    }, [selectedJobId, nanoporeJobs]);

    useEffect(() => {
        if (!selectedJobId && filteredJobs.length > 0) {
            setSelectedJobId(filteredJobs[0].id);
            return;
        }
        if (selectedJobId && !filteredJobs.some((job) => job.id === selectedJobId)) {
            setSelectedJobId(filteredJobs.length > 0 ? filteredJobs[0].id : null);
        }
    }, [filteredJobs, selectedJobId]);

    useEffect(() => {
        setShowRawTopLoci(false);
    }, [selectedJob?.id]);

    const { data: stagesData, isLoading: stagesLoading } = useQuery({
        queryKey: ['job-stages', selectedJobId],
        queryFn: () => fetchJobStages(selectedJobId as string),
        enabled: !!selectedJobId,
        refetchInterval: (query) => selectedJob?.status === 'running'
            ? jobPollingInterval(4000, query)
            : false,
    });
    const {
        data: alignmentSessions = [],
        error: alignmentSessionsError,
    } = useQuery<AlignmentSession[]>({
        queryKey: ['ngs-alignment-sessions', selectedJobId],
        queryFn: () => fetchAlignmentSessions(selectedJobId as string),
        enabled: !!selectedJobId && selectedJob?.status === 'completed',
        retry: false,
        staleTime: 30_000,
    });

    const stats = useMemo(() => {
        return {
            total: nanoporeJobs.length,
            running: nanoporeJobs.filter((j) => j.status === 'running').length,
            queued: nanoporeJobs.filter((j) => j.status === 'queued').length,
            failed: nanoporeJobs.filter((j) => j.status === 'failed').length,
            completed: nanoporeJobs.filter((j) => j.status === 'completed').length,
        };
    }, [nanoporeJobs]);

    const stagePayload = stagesData?.data && stagesData.data.job_id === selectedJobId
        ? stagesData.data
        : null;
    const allStages = stagePayload?.all_stages || selectedJob?.all_stages || [];
    const completedStageKeySet = new Set(
        (stagePayload?.completed_stages || selectedJob?.completed_stages || []).map((stage) => normalizeStageKey(stage))
    );
    const currentStage = stagePayload?.current_stage || selectedJob?.current_stage || null;
    const currentStageKey = currentStage ? normalizeStageKey(currentStage) : '';
    const forceCompleteByJobStatus = selectedJob?.status === 'completed';
    const stageOutputs = useMemo(() => (stagePayload?.stage_outputs || {}) as StageOutputsMap, [stagePayload?.stage_outputs]);
    const selectedJobParams = (selectedJob?.params || {}) as Record<string, unknown>;
    const selectedReferenceFastaPath = typeof selectedJobParams.reference_fasta === 'string'
        ? selectedJobParams.reference_fasta
        : null;
    const hasFastqInput = hasMeaningfulValue(selectedJobParams.fastq_path);
    const hasBamInput = hasMeaningfulValue(selectedJobParams.bam_path);
    const hasPod5Input = hasMeaningfulValue(selectedJobParams.pod5_dir);
    const isFastqOnlyRun = hasFastqInput && !hasBamInput && !hasPod5Input;
    const sequenceQcManifestState = useSequenceQcManifest(selectedJob?.id, selectedJob?.status);
    const shouldShowMethylationInspector = !isFastqOnlyRun;
    const shouldShowMultimerInspector = hasFastqInput;
    const igvArtifacts = useMemo(
        () => resolveIgvArtifacts(selectedJob, stageOutputs),
        [selectedJob, stageOutputs]
    );
    const multimerArtifacts = useMemo(
        () => resolveMultimerArtifacts(selectedJob, stageOutputs),
        [selectedJob, stageOutputs]
    );
    const methylationArtifacts = useMemo(
        () => resolveMethylationArtifacts(selectedJob, stageOutputs),
        [selectedJob, stageOutputs]
    );
    const igvSourcePaths = useMemo(() => {
        const allPaths = dedupePaths(collectStageOutputPaths(stageOutputs));
        const runPrefix = resolveRunPrefix(selectedJob?.output_dir, allPaths);
        return runPrefix ? dedupePaths(filterPathsByRunPrefix(allPaths, runPrefix)) : allPaths;
    }, [selectedJob?.output_dir, stageOutputs]);
    const igvAlignmentSources = useMemo<IgvAlignmentSource[]>(() => {
        const sourcePaths = dedupePaths([
            ...igvSourcePaths,
            ...(igvArtifacts.bamPath ? [igvArtifacts.bamPath] : []),
        ]);
        const bamPaths = sourcePaths.filter((path) => (
            /\.bam$/i.test(path)
            && !/\.bam\.(bai|csi)$/i.test(path)
            && !/\/calls\.bam$/i.test(path)
        ));
        const cacheKey = selectedJob?.id || undefined;
        return bamPaths.map((bamPath) => {
            const baiPath = resolveBamIndexArtifactPath(bamPath, sourcePaths);
            return {
                label: formatIgvSourceLabel(bamPath),
                bamPath,
                bamUrl: toStreamHref(bamPath, cacheKey),
                baiPath,
                baiUrl: baiPath ? toStreamHref(baiPath, cacheKey) : null,
            };
        }).filter((source) => Boolean(source.bamUrl));
    }, [igvSourcePaths, igvArtifacts.bamPath, selectedJob?.id]);
    const igvReferenceSources = useMemo<IgvReferenceSource[]>(() => {
        const sourcePaths = dedupePaths([
            ...igvSourcePaths,
            ...(igvArtifacts.fastaPath ? [igvArtifacts.fastaPath] : []),
            ...(selectedReferenceFastaPath ? [selectedReferenceFastaPath] : []),
        ]);
        const fastaPaths = sourcePaths.filter((path) => (
            /\.(fasta|fa)$/i.test(path)
            && !/\.fai$/i.test(path)
        ));
        const cacheKey = selectedJob?.id || undefined;
        return fastaPaths.map((fastaPath) => {
            const faiPath = resolveFastaIndexArtifactPath(fastaPath, sourcePaths);
            return {
                label: formatIgvSourceLabel(fastaPath),
                fastaPath,
                fastaUrl: toStreamHref(fastaPath, cacheKey),
                faiPath,
                faiUrl: faiPath ? toStreamHref(faiPath, cacheKey) : null,
            };
        }).filter((source) => Boolean(source.fastaUrl));
    }, [igvSourcePaths, igvArtifacts.fastaPath, selectedReferenceFastaPath, selectedJob?.id]);
    const selectedAlignmentSession = useMemo(
        () => alignmentSessions.find((session) => session.session_id === selectedAlignmentSessionId)
            || alignmentSessions.find((session) => session.mode === 'primary')
            || alignmentSessions[0]
            || null,
        [alignmentSessions, selectedAlignmentSessionId]
    );
    useEffect(() => {
        const preferred = alignmentSessions.find((session) => session.mode === 'primary') || alignmentSessions[0] || null;
        if (!alignmentSessions.some((session) => session.session_id === selectedAlignmentSessionId)) {
            setSelectedAlignmentSessionId(preferred?.session_id || '');
        }
    }, [alignmentSessions, selectedAlignmentSessionId]);
    useEffect(() => {
        const selectedSessionId = selectedAlignmentSession?.session_id || '';
        selectedAlignmentSessionIdRef.current = selectedSessionId;
        if (pendingIgvLocusRef.current?.sessionId !== selectedSessionId) {
            pendingIgvLocusRef.current = null;
        }
        setIgvCurrentLocus(null);
    }, [selectedAlignmentSession?.session_id]);
    const activeIgvBamPath = selectedAlignmentSession ? `${selectedAlignmentSession.mode}:alignment` : null;
    const activeIgvBamUrl = selectedAlignmentSession?.artifacts.alignment?.url || null;
    const activeIgvBaiPath = selectedAlignmentSession ? `${selectedAlignmentSession.mode}:alignment-index` : null;
    const activeIgvBaiUrl = selectedAlignmentSession?.artifacts.alignment_index?.url || null;
    const activeIgvFastaPath = selectedAlignmentSession ? `${selectedAlignmentSession.mode}:reference` : null;
    const activeIgvFastaUrl = selectedAlignmentSession?.artifacts.reference?.url || null;
    const activeIgvFaiPath = selectedAlignmentSession?.artifacts.reference_index
        ? `${selectedAlignmentSession.mode}:reference-index`
        : null;
    const activeIgvFaiUrl = selectedAlignmentSession?.artifacts.reference_index?.url || null;
    const activeIgvSourceKey = selectedAlignmentSession?.session_id || '';
    const navigateToVerifiedLocus = useCallback((
        position_1based: number,
        end_1based: number | undefined,
        source: string,
    ) => {
        if (!selectedAlignmentSession?.ready || !selectedAlignmentSession.reference_contig) {
            setIgvError(`Cannot navigate ${source}: the selected alignment session has no authoritative reference contig.`);
            return;
        }
        const locus = resolveBoundSessionLocus(
            selectedAlignmentSession.session_id,
            selectedAlignmentSession.session_id,
            selectedAlignmentSession.reference_contig,
            position_1based,
            end_1based ?? position_1based,
        );
        if (!locus) {
            setIgvError(`Cannot navigate ${source}: invalid or unbound locus.`);
            return;
        }
        const navigation: PendingSessionNavigation = {
            sessionId: selectedAlignmentSession.session_id,
            locus,
        };
        pendingIgvLocusRef.current = navigation;
        const browser = igvBrowserRef.current;
        if (igvModalOpen && browser && typeof browser.search === 'function') {
            const loadToken = igvLoadTokenRef.current;
            const navigationIsCurrent = () => (
                igvLoadTokenRef.current === loadToken
                && igvBrowserRef.current === browser
                && selectedAlignmentSessionIdRef.current === navigation.sessionId
                && pendingIgvLocusRef.current === navigation
            );
            void (async () => {
                try {
                    const completion = await awaitCurrentGeneration(
                        Promise.resolve(browser.search(navigation.locus)),
                        navigationIsCurrent,
                    );
                    if (completion === null || !navigationIsCurrent()) return;
                    pendingIgvLocusRef.current = null;
                } catch (error: unknown) {
                    if (!navigationIsCurrent()) return;
                    setIgvError(`Failed to navigate selected IGV session: ${error instanceof Error ? error.message : String(error)}`);
                }
            })();
            return;
        }
        void openIgvModal();
    }, [igvModalOpen, openIgvModal, selectedAlignmentSession]);
    const selectedReferenceFastaUrl = activeIgvFastaUrl;
    const igvMissingReason = alignmentSessionsError
        ? 'Authoritative alignment session is unavailable.'
        : !selectedAlignmentSession
            ? 'No job-scoped alignment session was published.'
            : !selectedAlignmentSession.ready
                ? selectedAlignmentSession.unavailable_reason || 'Alignment session failed validation.'
                : !activeIgvBamUrl
                    ? 'Aligned BAM artifact not found yet.'
                    : !activeIgvBaiUrl
                        ? 'BAM index (.bai/.csi) not found yet.'
                        : !activeIgvFastaUrl
                            ? 'Reference FASTA not found yet.'
                            : null;
    const igvReady = selectedAlignmentSession?.ready === true && !igvMissingReason;
    const igvReadinessChecks = useMemo(
        () => [
            {
                label: 'Aligned BAM',
                ok: Boolean(activeIgvBamUrl),
                path: activeIgvBamPath,
            },
            {
                label: 'BAM index (.bai/.csi)',
                ok: Boolean(activeIgvBaiUrl),
                path: activeIgvBaiPath,
            },
            {
                label: 'Reference FASTA',
                ok: Boolean(activeIgvFastaUrl),
                path: activeIgvFastaPath,
            },
            {
                label: 'Reference FASTA index (.fai, optional)',
                ok: Boolean(activeIgvFaiUrl),
                path: activeIgvFaiPath,
            },
        ],
        [
            activeIgvBamPath,
            activeIgvBamUrl,
            activeIgvBaiPath,
            activeIgvBaiUrl,
            activeIgvFastaPath,
            activeIgvFastaUrl,
            activeIgvFaiPath,
            activeIgvFaiUrl,
        ]
    );
    const igvAuxReadinessChecks = useMemo(() => {
        const coverage = selectedAlignmentSession?.artifacts.coverage_depth;
        return [{
            label: 'Session-bound coverage depth track',
            ok: Boolean(coverage),
            path: coverage?.manifest || null,
        }];
    }, [selectedAlignmentSession]);
    const missingIgvAuxTracks = useMemo(
        () => igvAuxReadinessChecks.filter((check) => !check.ok),
        [igvAuxReadinessChecks]
    );
    useEffect(() => {
        if (igvAlignmentSources.length === 0) {
            if (igvSelectedBamPath !== '') {
                setIgvSelectedBamPath('');
            }
            return;
        }
        if (!igvAlignmentSources.some((source) => source.bamPath === igvSelectedBamPath)) {
            const preferred = igvArtifacts.bamPath && igvAlignmentSources.some((source) => source.bamPath === igvArtifacts.bamPath)
                ? igvArtifacts.bamPath
                : igvAlignmentSources[0].bamPath;
            setIgvSelectedBamPath(preferred);
        }
    }, [igvAlignmentSources, igvArtifacts.bamPath, igvSelectedBamPath]);
    useEffect(() => {
        if (igvReferenceSources.length === 0) {
            if (igvSelectedReferencePath !== '') {
                setIgvSelectedReferencePath('');
            }
            return;
        }
        if (!igvReferenceSources.some((source) => source.fastaPath === igvSelectedReferencePath)) {
            const preferred = activeIgvFastaPath && igvReferenceSources.some((source) => source.fastaPath === activeIgvFastaPath)
                ? activeIgvFastaPath
                : igvReferenceSources[0].fastaPath;
            setIgvSelectedReferencePath(preferred);
        }
    }, [igvReferenceSources, activeIgvFastaPath, igvSelectedReferencePath]);
    const igvReportDownloadHref = selectedAlignmentSession?.artifacts.report?.url || null;
    const igvTrackConfigDownloadHref = selectedAlignmentSession?.artifacts.track_config?.url || null;
    const methylationSummaryDownloadHref = methylationArtifacts.summaryPath
        ? toDownloadHref(methylationArtifacts.summaryPath, selectedJob?.id || undefined)
        : null;
    const methylationBedDownloadHref = methylationArtifacts.bedPath
        ? toDownloadHref(methylationArtifacts.bedPath, selectedJob?.id || undefined)
        : null;
    const multimerSummaryDownloadHref = multimerArtifacts.summaryPath
        ? toDownloadHref(multimerArtifacts.summaryPath, selectedJob?.id || undefined)
        : null;
    const multimerLengthsDownloadHref = multimerArtifacts.lengthsPath
        ? toDownloadHref(multimerArtifacts.lengthsPath, selectedJob?.id || undefined)
        : null;
    const multimerCandidatesDownloadHref = multimerArtifacts.candidatesPath
        ? toDownloadHref(multimerArtifacts.candidatesPath, selectedJob?.id || undefined)
        : null;
    const fastqAlignmentSummaryDownloadHref = multimerArtifacts.dimerSummaryPath
        ? toDownloadHref(multimerArtifacts.dimerSummaryPath, selectedJob?.id || undefined)
        : null;
    const fastqConsensusDownloadHref = (multimerArtifacts.dominantDimerConsensusPath || multimerArtifacts.dimerConsensusPath)
        ? toDownloadHref(
            multimerArtifacts.dominantDimerConsensusPath || multimerArtifacts.dimerConsensusPath || '',
            selectedJob?.id || undefined
        )
        : null;
    const fastqQcLogDownloadHref = multimerArtifacts.logPath
        ? toDownloadHref(multimerArtifacts.logPath, selectedJob?.id || undefined)
        : null;
    const motifAllSites = useMemo(
        () => {
            const all = [
                ...(methylationReport?.damSites || []),
                ...(methylationReport?.dcmSites || []),
            ];
            return all.sort((a, b) => {
                if (a.motif !== b.motif) return a.motif.localeCompare(b.motif);
                if (a.position !== b.position) return a.position - b.position;
                return a.strand.localeCompare(b.strand);
            });
        },
        [methylationReport]
    );
    const motifSitesPassingCoverage = useMemo(
        () => motifAllSites.filter(
            (site) => site.percentModified != null
                && (motifMinCoverage <= 0 || (site.coverage != null && site.coverage >= motifMinCoverage))
        ),
        [motifAllSites, motifMinCoverage]
    );
    const concordantPairKeys = useMemo(() => {
        if (!requireStrandConcordance) return null;
        const pairMap = new Map<string, { plus: number | null; minus: number | null }>();
        for (const site of motifSitesPassingCoverage) {
            if (!pairMap.has(site.pairKey)) {
                pairMap.set(site.pairKey, { plus: null, minus: null });
            }
            const row = pairMap.get(site.pairKey)!;
            if (site.strand === '+') row.plus = site.percentModified;
            if (site.strand === '-') row.minus = site.percentModified;
        }
        const allowed = new Set<string>();
        for (const [pairKey, row] of pairMap.entries()) {
            if (row.plus == null || row.minus == null) continue;
            if (Math.abs(row.plus - row.minus) <= MOTIF_CONCORDANCE_DELTA_PERCENT) {
                allowed.add(pairKey);
            }
        }
        return allowed;
    }, [motifSitesPassingCoverage, requireStrandConcordance]);
    const passesConcordance = useCallback(
        (site: MotifSiteCall) => !requireStrandConcordance || Boolean(concordantPairKeys?.has(site.pairKey)),
        [requireStrandConcordance, concordantPairKeys]
    );
    const filteredDamSites = useMemo(
        () => (methylationReport?.damSites || [])
            .filter((site) => site.percentModified != null)
            .filter((site) => motifMinCoverage <= 0 || (site.coverage != null && site.coverage >= motifMinCoverage))
            .filter(passesConcordance)
            .filter((site) => strandFilter === 'both' || site.strand === strandFilter)
            .sort((a, b) => a.position - b.position),
        [methylationReport, strandFilter, motifMinCoverage, passesConcordance]
    );
    const filteredDamAllSites = useMemo(
        () => (methylationReport?.damSites || [])
            .filter(passesConcordance)
            .filter((site) => strandFilter === 'both' || site.strand === strandFilter),
        [methylationReport, strandFilter, passesConcordance]
    );
    const filteredDcmSites = useMemo(
        () => (methylationReport?.dcmSites || [])
            .filter((site) => site.percentModified != null)
            .filter((site) => motifMinCoverage <= 0 || (site.coverage != null && site.coverage >= motifMinCoverage))
            .filter(passesConcordance)
            .filter((site) => strandFilter === 'both' || site.strand === strandFilter)
            .sort((a, b) => a.position - b.position),
        [methylationReport, strandFilter, motifMinCoverage, passesConcordance]
    );
    const filteredDcmAllSites = useMemo(
        () => (methylationReport?.dcmSites || [])
            .filter(passesConcordance)
            .filter((site) => strandFilter === 'both' || site.strand === strandFilter),
        [methylationReport, strandFilter, passesConcordance]
    );
    const methylationPlotData = useMemo<Data[]>(() => {
        const traces: Data[] = [];
        const referenceSequence = methylationReport?.referenceSequence || null;
        const barColors = {
            damPlus: themeColors.accentPrimary,
            damMinus: themeColors.link,
            dcmPlus: themeColors.success,
            dcmMinus: themeColors.error,
        };
        const sequenceSnippetForPosition = (position: number): string => {
            if (!referenceSequence || position <= 0) return 'n/a';
            const snippet = formatSequenceContext(referenceSequence, position, 12);
            return `${snippet.start}-${snippet.end} ${snippet.text}`;
        };

        const addBarTrace = (label: string, sites: MotifSiteCall[], color: string, motif: 'Dam' | 'Dcm', strand: '+' | '-') => {
            if (sites.length === 0) return;
            traces.push({
                type: 'bar',
                name: `${label} ${strand}`,
                x: sites.map((site) => site.position),
                y: sites.map((site) => site.percentModified),
                marker: {
                    color,
                    line: {
                        color: themeColors.textPrimary,
                        width: 1,
                    },
                    pattern: strand === '-'
                        ? {
                            shape: '/',
                            fgcolor: themeColors.textPrimary,
                            solidity: 0.2,
                        }
                        : undefined,
                },
                opacity: 1,
                customdata: sites.map((site) => [
                    site.strand,
                    site.context,
                    site.coverage ?? 0,
                    site.chrom,
                    motif,
                    sequenceSnippetForPosition(site.position),
                    site.pairKey,
                ]),
                hovertemplate: [
                    `<b>${label}</b>`,
                    'Position: %{x}',
                    'Strand: %{customdata[0]}',
                    'Context: %{customdata[1]}',
                    'Observed: %{y:.2f}%',
                    'Coverage: %{customdata[2]:.0f}',
                    'Sequence: %{customdata[5]}',
                    '<extra></extra>',
                ].join('<br>'),
            });
        };

        const damPlus = filteredDamSites.filter((site) => site.strand === '+');
        const damMinus = filteredDamSites.filter((site) => site.strand === '-');
        const dcmPlus = filteredDcmSites.filter((site) => site.strand === '+');
        const dcmMinus = filteredDcmSites.filter((site) => site.strand === '-');

        addBarTrace('Dam (GATC)', damPlus, barColors.damPlus, 'Dam', '+');
        addBarTrace('Dam (GATC)', damMinus, barColors.damMinus, 'Dam', '-');
        addBarTrace('Dcm (CCWGG)', dcmPlus, barColors.dcmPlus, 'Dcm', '+');
        addBarTrace('Dcm (CCWGG)', dcmMinus, barColors.dcmMinus, 'Dcm', '-');

        return traces;
    }, [
        filteredDamSites,
        filteredDcmSites,
        methylationReport?.referenceSequence,
        themeColors.accentPrimary,
        themeColors.link,
        themeColors.success,
        themeColors.error,
        themeColors.textPrimary,
    ]);
    const methylationPlotLayout = useMemo<Partial<Layout>>(() => ({
        ...basePlotlyLayout,
        margin: { l: 44, r: 16, t: 26, b: 48 },
        barmode: 'group',
        bargap: 0.22,
        showlegend: true,
        legend: {
            orientation: 'h',
            x: 0,
            xanchor: 'left',
            y: 1.02,
            yanchor: 'bottom',
            font: { color: themeColors.textSecondary, size: 11 },
        },
        xaxis: {
            title: { text: 'Reference Position (bp)', font: { color: themeColors.textSecondary } },
            tickfont: { color: themeColors.textSecondary },
            gridcolor: `${themeColors.borderPrimary}66`,
            zeroline: false,
            showline: true,
            linecolor: themeColors.borderPrimary,
            ticks: 'outside',
            tickcolor: themeColors.borderPrimary,
        },
        yaxis: {
            title: { text: 'Percent Modified', font: { color: themeColors.textSecondary } },
            tickfont: { color: themeColors.textSecondary },
            range: [0, 100],
            gridcolor: `${themeColors.borderPrimary}66`,
            zeroline: false,
            showline: true,
            linecolor: themeColors.borderPrimary,
            ticksuffix: '%',
        },
        hovermode: 'closest',
        hoverlabel: {
            bgcolor: themeColors.bgSecondary,
            bordercolor: themeColors.borderPrimary,
            font: { color: themeColors.textPrimary, size: 11 },
        },
    }), [basePlotlyLayout, themeColors]);
    const methylationPlotConfig = useMemo(
        () => ({ responsive: true, displaylogo: false, scrollZoom: true }),
        []
    );
    const filteredMotifAllSites = useMemo(
        () => [...filteredDamAllSites, ...filteredDcmAllSites].sort((a, b) => a.position - b.position),
        [filteredDamAllSites, filteredDcmAllSites]
    );
    const filteredMotifCalledSites = useMemo(
        () => [...filteredDamSites, ...filteredDcmSites].sort((a, b) => a.position - b.position),
        [filteredDamSites, filteredDcmSites]
    );
    const filteredMotifHighSites = useMemo(
        () => filteredMotifCalledSites.filter((site) => (site.percentModified ?? 0) > 5),
        [filteredMotifCalledSites]
    );
    const referenceSequenceRows = useMemo(
        () => buildSequenceRows(methylationReport?.referenceSequence || '', REFERENCE_SEQUENCE_LINE_WIDTH),
        [methylationReport?.referenceSequence]
    );
    const referenceSiteHighlightsByPosition = useMemo(() => {
        const highlights = new Map<number, SequenceBaseHighlight>();
        const addSite = (site: MotifSiteCall, color: string) => {
            if (site.percentModified == null) return;
            const percentModified = clampNumber(site.percentModified, 0, 100);
            const candidate: SequenceBaseHighlight = {
                motif: site.motif,
                strand: site.strand,
                percentModified,
                color,
            };
            const existing = highlights.get(site.position);
            if (!existing || percentModified > existing.percentModified) {
                highlights.set(site.position, candidate);
            }
        };

        for (const site of filteredDamSites) {
            addSite(site, site.strand === '+' ? themeColors.accentPrimary : themeColors.link);
        }
        for (const site of filteredDcmSites) {
            addSite(site, site.strand === '+' ? themeColors.success : themeColors.error);
        }

        return highlights;
    }, [
        filteredDamSites,
        filteredDcmSites,
        themeColors.accentPrimary,
        themeColors.link,
        themeColors.success,
        themeColors.error,
    ]);
    const multimerMetrics = useMemo(() => multimerReport?.metrics || {}, [multimerReport?.metrics]);
    const expectedPlasmidSize = Number.isFinite(multimerMetrics.expected_plasmid_size)
        ? multimerMetrics.expected_plasmid_size
        : Number.parseFloat(String(selectedJobParams.expected_plasmid_size ?? ''));
    const multimerSummaryLookup = useMemo(() => {
        const table = multimerReport?.summary;
        if (!table || table.header.length === 0) return new Map<string, string>();
        const metricIdx = table.header.findIndex((h) => h.trim().toLowerCase() === 'metric');
        const valueIdx = table.header.findIndex((h) => h.trim().toLowerCase() === 'value');
        if (metricIdx < 0 || valueIdx < 0) return new Map<string, string>();
        const lookup = new Map<string, string>();
        for (const row of table.rows) {
            const key = String(row[metricIdx] ?? '').trim().toLowerCase();
            if (!key) continue;
            lookup.set(key, String(row[valueIdx] ?? '').trim());
        }
        return lookup;
    }, [multimerReport?.summary]);
    const readMultimerMetric = useCallback((keys: string[]): number => {
        for (const key of keys) {
            const fromMetrics = multimerMetrics[key];
            if (Number.isFinite(fromMetrics)) return Number(fromMetrics);
            const raw = multimerSummaryLookup.get(key.toLowerCase());
            if (raw == null) continue;
            const fromSummary = Number.parseFloat(raw);
            if (Number.isFinite(fromSummary)) return fromSummary;
        }
        return 0;
    }, [multimerMetrics, multimerSummaryLookup]);
    const multimerClassCounts = useMemo(() => ({
        monomer: Math.max(0, Math.round(readMultimerMetric(['monomer_like_reads', 'monomer_reads']))),
        dimer: Math.max(0, Math.round(readMultimerMetric(['dimer_candidate_reads', 'dimer_reads']))),
        trimer: Math.max(0, Math.round(readMultimerMetric(['trimer_reads']))),
        highOrder: Math.max(0, Math.round(readMultimerMetric(['multimer_candidate_reads', 'tetramer_plus_reads']))),
    }), [readMultimerMetric]);
    const totalClassifiedReads = useMemo(
        () => multimerClassCounts.monomer + multimerClassCounts.dimer + multimerClassCounts.trimer + multimerClassCounts.highOrder,
        [multimerClassCounts]
    );
    const alignmentSummaryLookup = useMemo(() => {
        const table = multimerReport?.dimerSummary;
        if (!table || table.header.length === 0) return new Map<string, string>();
        const metricIdx = table.header.findIndex((h) => h.trim().toLowerCase() === 'metric');
        const valueIdx = table.header.findIndex((h) => h.trim().toLowerCase() === 'value');
        if (metricIdx < 0 || valueIdx < 0) return new Map<string, string>();
        const lookup = new Map<string, string>();
        for (const row of table.rows) {
            const key = String(row[metricIdx] ?? '').trim().toLowerCase();
            if (!key) continue;
            lookup.set(key, String(row[valueIdx] ?? '').trim());
        }
        return lookup;
    }, [multimerReport?.dimerSummary]);
    const readAlignmentMetric = useCallback((keys: string[]): number | null => {
        for (const key of keys) {
            const raw = alignmentSummaryLookup.get(key.toLowerCase());
            if (raw == null) continue;
            const parsed = Number.parseFloat(raw);
            if (Number.isFinite(parsed)) return parsed;
        }
        return null;
    }, [alignmentSummaryLookup]);
    const alignedReadCount = useMemo(() => {
        const preferred = readAlignmentMetric(['aligned_reads', 'aligned_dimer_reads', 'mapped_reads']);
        if (preferred != null) return Math.max(0, Math.round(preferred));
        const fallback = readMultimerMetric(['aligned_reads']);
        return fallback > 0 ? Math.max(0, Math.round(fallback)) : null;
    }, [readAlignmentMetric, readMultimerMetric]);
    const consensusStatus = alignmentSummaryLookup.get('consensus_status') || null;
    const consensusPreview = multimerReport?.dominantDimerConsensusPreview
        || multimerReport?.dimerConsensusPreview
        || null;
    const topMultimerCandidates = useMemo(
        () => (multimerReport?.candidates || []).slice(0, 40),
        [multimerReport?.candidates]
    );
    const hasFastqQcDetails = useMemo(() => Boolean(
        (multimerReport?.readLengths?.length || 0) > 0
        || (multimerReport?.candidates?.length || 0) > 0
        || totalClassifiedReads > 0
        || multimerReport?.summary
        || multimerReport?.dimerSummary
        || consensusPreview
    ), [
        multimerReport?.readLengths,
        multimerReport?.candidates,
        totalClassifiedReads,
        multimerReport?.summary,
        multimerReport?.dimerSummary,
        consensusPreview,
    ]);
    const multimerClassLegendItems = useMemo(
        () => ([
            { label: 'Monomer-like', value: multimerClassCounts.monomer, color: themeColors.success },
            { label: 'Dimer candidate', value: multimerClassCounts.dimer, color: themeColors.warning },
            { label: 'Trimer candidate', value: multimerClassCounts.trimer, color: themeColors.error },
            { label: 'Higher-order', value: multimerClassCounts.highOrder, color: themeColors.accentPrimary },
        ]).filter((row) => Number.isFinite(row.value) && row.value > 0),
        [
            multimerClassCounts.monomer,
            multimerClassCounts.dimer,
            multimerClassCounts.trimer,
            multimerClassCounts.highOrder,
            themeColors.success,
            themeColors.warning,
            themeColors.error,
            themeColors.accentPrimary,
        ]
    );
    const multimerHistogramPlotData = useMemo<Data[]>(() => {
        const lengths = multimerReport?.readLengths || [];
        if (lengths.length === 0) return [];
        const readMetric = (keys: string[]): number | null => {
            for (const key of keys) {
                const value = multimerMetrics[key];
                if (Number.isFinite(value)) return Number(value);
            }
            return null;
        };
        const expected = Number.isFinite(expectedPlasmidSize) && expectedPlasmidSize > 0 ? expectedPlasmidSize : null;
        const dimerCutoff = readMetric(['dimer_cutoff']) ?? (expected ? expected * 1.5 : null);
        const trimerCutoff = readMetric(['trimer_cutoff', 'multimer_cutoff']) ?? (expected ? expected * 2.5 : null);
        const tetramerCutoff = readMetric(['tetramer_cutoff']) ?? (expected ? expected * 3.5 : null);
        const minLen = Math.min(...lengths);
        const maxLen = Math.max(...lengths);
        const span = Math.max(1, maxLen - minLen);
        const binCount = Math.min(80, Math.max(20, Math.round(Math.sqrt(lengths.length))));
        const binWidth = Math.max(1, Math.ceil(span / binCount));
        const counts = new Array(binCount).fill(0);
        for (const len of lengths) {
            const idx = Math.max(0, Math.min(binCount - 1, Math.floor((len - minLen) / binWidth)));
            counts[idx] += 1;
        }
        const centers = counts.map((_, idx) => minLen + (idx * binWidth) + (binWidth / 2));
        const classifyLength = (value: number): { label: string; color: string } => {
            if (Number.isFinite(dimerCutoff as number) && value < (dimerCutoff as number)) {
                return { label: 'Monomer-like', color: themeColors.success };
            }
            if (Number.isFinite(trimerCutoff as number) && value < (trimerCutoff as number)) {
                return { label: 'Dimer candidate', color: themeColors.warning };
            }
            if (Number.isFinite(tetramerCutoff as number) && value < (tetramerCutoff as number)) {
                return { label: 'Trimer candidate', color: themeColors.error };
            }
            if (Number.isFinite(tetramerCutoff as number)) {
                return { label: 'Higher-order', color: themeColors.accentPrimary };
            }
            return { label: 'Unclassified', color: themeColors.link };
        };
        const classes = centers.map((center) => classifyLength(center));

        return [{
            type: 'bar',
            x: centers,
            y: counts,
            marker: {
                color: classes.map((entry) => entry.color),
                line: { color: themeColors.textPrimary, width: 0.6 },
            },
            customdata: classes.map((entry) => [entry.label]),
            hovertemplate: 'Length bin center: %{x:.0f} bp<br>Reads: %{y}<br>Class: %{customdata[0]}<extra></extra>',
        }];
    }, [
        multimerReport?.readLengths,
        multimerMetrics,
        expectedPlasmidSize,
        themeColors.success,
        themeColors.warning,
        themeColors.error,
        themeColors.accentPrimary,
        themeColors.link,
        themeColors.textPrimary,
    ]);
    const multimerHistogramLayout = useMemo<Partial<Layout>>(() => {
        const shapes: NonNullable<Layout['shapes']> = [];
        const readMetric = (keys: string[]): number | null => {
            for (const key of keys) {
                const value = multimerMetrics[key];
                if (Number.isFinite(value)) return Number(value);
            }
            return null;
        };
        const expected = Number.isFinite(expectedPlasmidSize) && expectedPlasmidSize > 0 ? expectedPlasmidSize : null;
        const dimerCutoff = readMetric(['dimer_cutoff']) ?? (expected ? expected * 1.5 : null);
        const trimerCutoff = readMetric(['trimer_cutoff', 'multimer_cutoff']) ?? (expected ? expected * 2.5 : null);
        const tetramerCutoff = readMetric(['tetramer_cutoff']) ?? (expected ? expected * 3.5 : null);

        const addCutoff = (x: number | null, color: string, dash: 'dot' | 'dash' | 'solid', label: string) => {
            if (!Number.isFinite(x as number)) return;
            shapes.push({
                type: 'line',
                x0: x as number,
                x1: x as number,
                y0: 0,
                y1: 1,
                yref: 'paper',
                line: { color, width: 1.2, dash },
                name: label,
            });
        };
        addCutoff(dimerCutoff, themeColors.warning, 'dot', 'dimer cutoff');
        addCutoff(trimerCutoff, themeColors.error, 'dash', 'trimer cutoff');
        addCutoff(tetramerCutoff, themeColors.accentPrimary, 'solid', 'tetramer cutoff');

        return {
            ...basePlotlyLayout,
            margin: { l: 44, r: 12, t: 20, b: 48 },
            showlegend: false,
            shapes,
            xaxis: {
                title: { text: 'Read Length (bp)', font: { color: themeColors.textSecondary } },
                tickfont: { color: themeColors.textSecondary },
                gridcolor: `${themeColors.borderPrimary}55`,
            },
            yaxis: {
                title: { text: 'Read Count', font: { color: themeColors.textSecondary } },
                tickfont: { color: themeColors.textSecondary },
                gridcolor: `${themeColors.borderPrimary}66`,
                rangemode: 'tozero',
            },
        };
    }, [basePlotlyLayout, multimerMetrics, expectedPlasmidSize, themeColors]);
    const multimerPlotConfig = useMemo(
        () => ({ responsive: true, displaylogo: false, scrollZoom: true }),
        []
    );
    const selectedSequencePosition = selectedMotifPoint?.position ?? null;
    const handleMethylationPointClick = useCallback((event: Readonly<PlotMouseEvent>) => {
        const first = event.points?.[0];
        if (!first || !methylationReport) return;

        const xRaw = typeof first.x === 'number' ? first.x : Number(first.x);
        const yRaw = typeof first.y === 'number' ? first.y : Number(first.y);
        if (!Number.isFinite(xRaw) || !Number.isFinite(yRaw)) return;

        const custom = Array.isArray(first.customdata) ? first.customdata : [];
        const strand = custom[0] === '-' ? '-' : '+';
        const context = typeof custom[1] === 'string' ? custom[1] : '';
        const coverageVal = Number(custom[2]);
        const coverage = Number.isFinite(coverageVal) ? coverageVal : null;
        const chrom = typeof custom[3] === 'string' ? custom[3] : (methylationReport.referenceName || 'reference');
        const motif: 'Dam' | 'Dcm' = custom[4] === 'Dcm' ? 'Dcm' : 'Dam';
        const position = Math.round(xRaw);
        const percentModified = yRaw;
        const pairKey = typeof custom[6] === 'string' ? custom[6] : `${chrom}:${motif}:${position}`;

        let sequenceContext: string | null = null;
        let contextStart: number | null = null;
        let contextEnd: number | null = null;
        if (methylationReport.referenceSequence && position > 0) {
            const snippet = formatSequenceContext(methylationReport.referenceSequence, position, 12);
            sequenceContext = snippet.text;
            contextStart = snippet.start;
            contextEnd = snippet.end;
        }

        setSelectedMotifPoint({
            motif,
            chrom,
            position,
            strand,
            context,
            percentModified,
            coverage,
            pairKey,
            sequenceContext,
            contextStart,
            contextEnd,
        });
    }, [methylationReport]);
    useEffect(() => {
        setSelectedMotifPoint(null);
    }, [selectedJob?.id, strandFilter, motifMinCoverage, requireStrandConcordance]);

    useEffect(() => {
        let cancelled = false;

        const loadMultimerReport = async () => {
            if (!selectedJob) {
                setMultimerLoading(false);
                setMultimerError('Select a run to inspect multimer QC outputs.');
                setMultimerReport(null);
                return;
            }

            if (!shouldShowMultimerInspector) {
                setMultimerLoading(false);
                setMultimerError('FASTQ input is required for multimer QC.');
                setMultimerReport(null);
                return;
            }

            const hasAnyMultimerOutput = Boolean(
                multimerArtifacts.summaryUrl
                || multimerArtifacts.lengthsUrl
                || multimerArtifacts.candidatesUrl
                || multimerArtifacts.dimerSummaryUrl
                || multimerArtifacts.dimerConsensusUrl
                || multimerArtifacts.dominantDimerConsensusUrl
            );
            if (!hasAnyMultimerOutput) {
                setMultimerLoading(false);
                setMultimerError(multimerArtifacts.missingReason);
                setMultimerReport(null);
                return;
            }

            setMultimerLoading(true);
            setMultimerError(null);
            try {
                let summary: SummaryTable | null = null;
                let metrics: Record<string, number> = {};
                let readLengths: number[] = [];
                let candidates: MultimerCandidateRow[] = [];
                let dimerSummary: SummaryTable | null = null;
                let dimerConsensusPreview: string | null = null;
                let dominantDimerConsensusPreview: string | null = null;
                let referenceName: string | null = null;
                let referenceLength: number | null = null;
                let referenceSequence: string | null = null;
                const warnings: string[] = [];

                if (multimerArtifacts.summaryUrl) {
                    try {
                        const summaryText = await fetchTextRange(multimerArtifacts.summaryUrl, MULTIMER_SUMMARY_MAX_BYTES);
                        summary = parseSummaryTable(summaryText);
                        metrics = parseNumericMetricsFromSummaryTable(summary);
                    } catch (err) {
                        warnings.push(`Summary unavailable (${err instanceof Error ? err.message : String(err)})`);
                    }
                }

                if (multimerArtifacts.lengthsUrl) {
                    try {
                        const lengthsText = await fetchTextRange(multimerArtifacts.lengthsUrl, MULTIMER_LENGTHS_MAX_BYTES);
                        readLengths = parseReadLengths(lengthsText);
                    } catch (err) {
                        warnings.push(`Read-length table unavailable (${err instanceof Error ? err.message : String(err)})`);
                    }
                }

                if (multimerArtifacts.candidatesUrl) {
                    try {
                        const candidatesText = await fetchTextRange(multimerArtifacts.candidatesUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        candidates = parseMultimerCandidates(candidatesText, 500);
                    } catch (err) {
                        warnings.push(`Candidate table unavailable (${err instanceof Error ? err.message : String(err)})`);
                    }
                }

                if (multimerArtifacts.dimerSummaryUrl) {
                    try {
                        const dimerSummaryText = await fetchTextRange(multimerArtifacts.dimerSummaryUrl, MULTIMER_SUMMARY_MAX_BYTES);
                        dimerSummary = parseSummaryTable(dimerSummaryText);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer summary unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (multimerArtifacts.dimerConsensusUrl) {
                    try {
                        const consensusText = await fetchTextRange(multimerArtifacts.dimerConsensusUrl, DIMER_CONSENSUS_MAX_BYTES);
                        const trimmed = consensusText.trim();
                        if (trimmed.length > 0) {
                            dimerConsensusPreview = trimmed.length > DIMER_CONSENSUS_PREVIEW_CHARS
                                ? `${trimmed.slice(0, DIMER_CONSENSUS_PREVIEW_CHARS)}\n... (truncated preview)`
                                : trimmed;
                        }
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer consensus unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }
                if (multimerArtifacts.dominantDimerConsensusUrl) {
                    try {
                        const dominantConsensusText = await fetchTextRange(multimerArtifacts.dominantDimerConsensusUrl, DIMER_CONSENSUS_MAX_BYTES);
                        const trimmed = dominantConsensusText.trim();
                        if (trimmed.length > 0) {
                            dominantDimerConsensusPreview = trimmed.length > DIMER_CONSENSUS_PREVIEW_CHARS
                                ? `${trimmed.slice(0, DIMER_CONSENSUS_PREVIEW_CHARS)}\n... (truncated preview)`
                                : trimmed;
                        }
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dominant dimer consensus unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (selectedReferenceFastaUrl) {
                    try {
                        const fastaText = await fetchTextRange(selectedReferenceFastaUrl, REFERENCE_FASTA_MAX_BYTES);
                        const fastaRecords = parseFastaRecords(fastaText);
                        if (fastaRecords.length > 0) {
                            referenceName = fastaRecords[0].id;
                            referenceSequence = fastaRecords[0].sequence;
                            referenceLength = fastaRecords[0].sequence.length;
                        }
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Reference context unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (cancelled) return;

                setMultimerReport({
                    summary,
                    metrics,
                    readLengths,
                    candidates,
                    dimerSummary,
                    dimerJunctionRows: [],
                    dimerJunctionClusters: [],
                    dimerBreakpointScreenRows: [],
                    dimerReadJunctions: [],
                    dimerConsensusPreview,
                    dominantDimerConsensusPreview,
                    dominantDimerConsensusMetadata: null,
                    referenceName,
                    referenceLength,
                    referenceSequence,
                });

                if (warnings.length > 0) {
                    setMultimerError(warnings.join(' • '));
                } else {
                    setMultimerError(null);
                }
            } finally {
                if (!cancelled) {
                    setMultimerLoading(false);
                }
            }
        };

        loadMultimerReport();
        return () => {
            cancelled = true;
        };
    }, [selectedJob?.id, shouldShowMultimerInspector, multimerArtifacts.summaryUrl, multimerArtifacts.lengthsUrl, multimerArtifacts.candidatesUrl, multimerArtifacts.dimerSummaryUrl, multimerArtifacts.dimerConsensusUrl, multimerArtifacts.dominantDimerConsensusUrl, selectedReferenceFastaUrl, multimerArtifacts.missingReason, selectedJob]);

    useEffect(() => {
        let cancelled = false;

        const loadMethylationReport = async () => {
            if (!selectedJob) {
                setMethylationLoading(false);
                setMethylationError('Select a run to inspect methylation outputs.');
                setMethylationReport(null);
                return;
            }

            if (!shouldShowMethylationInspector) {
                setMethylationLoading(false);
                setMethylationError('Methylation report is not applicable for FASTQ-only multimer QC runs.');
                setMethylationReport(null);
                return;
            }

            if (!methylationArtifacts.summaryUrl && !methylationArtifacts.bedUrl) {
                setMethylationLoading(false);
                setMethylationError(methylationArtifacts.missingReason);
                setMethylationReport(null);
                return;
            }

            setMethylationLoading(true);
            setMethylationError(null);

            try {
                let summary: SummaryTable | null = null;
                let topLoci: MethylationLocus[] = [];
                let series: MethylationSeries[] = [];
                let damSites: MotifSiteCall[] = [];
                let dcmSites: MotifSiteCall[] = [];
                let referenceName: string | null = null;
                let referenceLength: number | null = null;
                let referenceSequence: string | null = null;
                const warnings: string[] = [];
                let bedRecords: ParsedBedRecord[] = [];

                if (methylationArtifacts.summaryUrl) {
                    try {
                        const summaryText = await fetchTextRange(methylationArtifacts.summaryUrl, METHYLATION_SUMMARY_MAX_BYTES);
                        summary = parseSummaryTable(summaryText);
                    } catch (err) {
                        warnings.push(`Summary unavailable (${err instanceof Error ? err.message : String(err)})`);
                    }
                }

                if (methylationArtifacts.bedUrl) {
                    try {
                        const bedText = await fetchTextRange(methylationArtifacts.bedUrl, METHYLATION_BED_MAX_BYTES);
                        topLoci = parseBedTopLoci(bedText, 12);
                        bedRecords = parseModkitBedRecords(bedText);
                        series = aggregateMethylationSeries(bedRecords);
                    } catch (err) {
                        warnings.push(`BED preview unavailable (${err instanceof Error ? err.message : String(err)})`);
                    }
                }

                if (bedRecords.length > 0) {
                    if (activeIgvFastaUrl) {
                        try {
                            const fastaText = await fetchTextRange(activeIgvFastaUrl, REFERENCE_FASTA_MAX_BYTES);
                            const motifCalls = buildDamDcmCalls(bedRecords, fastaText);
                            damSites = motifCalls.damSites;
                            dcmSites = motifCalls.dcmSites;
                            referenceName = motifCalls.referenceName;
                            referenceLength = motifCalls.referenceLength;
                            referenceSequence = motifCalls.referenceSequence;
                        } catch (err) {
                            warnings.push(`Dam/Dcm detection skipped (${err instanceof Error ? err.message : String(err)})`);
                        }
                    } else {
                        warnings.push('Dam/Dcm detection skipped (reference FASTA unavailable).');
                    }
                }

                if (cancelled) return;

                setMethylationReport({
                    summary,
                    topLoci,
                    series,
                    damSites,
                    dcmSites,
                    referenceName,
                    referenceLength,
                    referenceSequence,
                });
                setSelectedMotifPoint(null);

                if (warnings.length > 0 && !summary && topLoci.length === 0) {
                    setMethylationError(warnings.join(' • '));
                } else if (warnings.length > 0) {
                    setMethylationError(warnings.join(' • '));
                } else {
                    setMethylationError(null);
                }
            } finally {
                if (!cancelled) {
                    setMethylationLoading(false);
                }
            }
        };

        loadMethylationReport();

        return () => {
            cancelled = true;
        };
    }, [selectedJob?.id, shouldShowMethylationInspector, methylationArtifacts.summaryUrl, methylationArtifacts.bedUrl, methylationArtifacts.missingReason, activeIgvFastaUrl, selectedJob]);

    useEffect(() => {
        if (!igvModalOpen) return;
        if (!igvReady) {
            setIgvLoading(false);
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            setIgvAutoLoadAttempted(false);
            igvLoadedSourceKeyRef.current = '';
            setIgvError(igvMissingReason);
            return;
        }
        if (!igvContainerRef.current) {
            setIgvLoading(false);
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            setIgvAutoLoadAttempted(false);
            igvLoadedSourceKeyRef.current = '';
            return;
        }

        let cancelled = false;
        let igvBrowser: UntypedApiValue = null;
        let removeLocusListener: (() => void) | null = null;
        let creationTimedOut = false;
        let timeoutInvalidationToken: number | null = null;
        const loadToken = ++igvLoadTokenRef.current;
        const isCurrentLoad = () => igvLoadTokenRef.current === loadToken;
        const ownsTerminalState = () => ownsIgvLoadTerminalState(
            loadToken,
            igvLoadTokenRef.current,
            creationTimedOut ? timeoutInvalidationToken : null,
            cancelled,
        );

        const initIgv = async () => {
            setIgvLoading(true);
            setIgvError(null);
            setIgvVersion(null);
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            setIgvAutoLoadAttempted(false);
            igvLoadedSourceKeyRef.current = '';
            if (igvContainerRef.current) {
                igvContainerRef.current.innerHTML = '';
            }

            try {
                const { igv, version } = await withTimeout(
                    loadIgvLibrary(),
                    IGV_INIT_TIMEOUT_MS,
                    `IGV initialization timed out after ${Math.round(IGV_INIT_TIMEOUT_MS / 1000)}s while loading IGV library`
                );
                if (cancelled || !igvContainerRef.current) return;
                if (isCurrentLoad() && !cancelled) {
                    setIgvVersion(version);
                }
                const igvAny = igv as UntypedApiValue;
                if (typeof igvAny.setDefaults === 'function') {
                    igvAny.setDefaults({
                        showControls: true,
                        showNavigation: true,
                        showRuler: true,
                        showCenterGuideButton: true,
                        showCenterGuide: true,
                        showTrackLabelButton: true,
                        showTrackLabels: true,
                        showCursorTrackingGuideButton: true,
                        showCursorTrackingGuide: true,
                        showSVGButton: true,
                        showSampleNames: true,
                    });
                }
                const initialLocus = await withTimeout(
                    detectInitialLocusFromFasta(activeIgvFastaUrl),
                    Math.max(5000, Math.floor(IGV_INIT_TIMEOUT_MS / 2)),
                    `IGV initialization timed out after ${Math.round(IGV_INIT_TIMEOUT_MS / 1000)}s while preparing reference`
                );
                if (cancelled || !igvContainerRef.current) return;

                const requestedLocus = resolvePendingSessionLocus(
                    pendingIgvLocusRef.current,
                    selectedAlignmentSessionIdRef.current,
                ) || initialLocus;
                igvBrowser = await createGenerationBoundResourceWithTimeout({
                    create: () => igvAny.createBrowser(igvContainerRef.current, {
                        ...(requestedLocus ? { locus: requestedLocus } : {}),
                        reference: {
                            fastaURL: activeIgvFastaUrl,
                            ...(activeIgvFaiUrl
                                ? {
                                    indexURL: activeIgvFaiUrl,
                                    indexed: true,
                                }
                                : {
                                    indexed: false,
                                }),
                        },
                        tracks: [],
                    }),
                    remove: (staleBrowser: unknown) => removeIgvBrowser(igvAny, staleBrowser),
                    isCurrent: () => isCurrentLoad() && !cancelled,
                    invalidate: () => {
                        creationTimedOut = true;
                        if (isCurrentLoad()) {
                            igvLoadTokenRef.current += 1;
                            timeoutInvalidationToken = igvLoadTokenRef.current;
                        }
                    },
                    timeoutMs: IGV_INIT_TIMEOUT_MS,
                    timeoutMessage: `IGV initialization timed out after ${Math.round(IGV_INIT_TIMEOUT_MS / 1000)}s`,
                });
                if (!igvBrowser) return;
                igvLibraryRef.current = igvAny;
                ensureIgvThemeStyles(igvContainerRef.current);
                patchIgvRulerContrast(igvBrowser);
                igvBrowserRef.current = igvBrowser;
                if (typeof igvBrowser.on === 'function') {
                    const locusHandler = (loci: unknown) => {
                        if (!isCurrentLoad() || cancelled || igvBrowserRef.current !== igvBrowser) return;
                        setIgvCurrentLocus(resolveIgvReadLocus(loci));
                    };
                    igvBrowser.on('locuschange', locusHandler);
                    removeLocusListener = () => {
                        if (typeof igvBrowser?.off === 'function') igvBrowser.off('locuschange', locusHandler);
                    };
                }

                if (isCurrentLoad() && !cancelled) {
                    setIgvLoading(false);
                }

                if (!cancelled && requestedLocus && igvBrowser && typeof igvBrowser.search === 'function') {
                    void awaitCurrentGeneration(
                        Promise.resolve(igvBrowser.search(requestedLocus)),
                        () => isCurrentLoad() && !cancelled && igvBrowserRef.current === igvBrowser,
                    ).then((completion) => {
                        if (completion !== null && isCurrentLoad()) pendingIgvLocusRef.current = null;
                    }).catch(() => { /* keep viewer open if locus search fails */ });
                }
            } catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                if (ownsTerminalState()) {
                    const needsLibraryHint = /igv|module|import|createBrowser|version/i.test(msg);
                    const suffix = needsLibraryHint
                        ? ` Ensure \`igv@${IGV_REQUIRED_VERSION}\` is installed and the frontend bundle is rebuilt.`
                        : '';
                    setIgvError(`Failed to initialize IGV viewer: ${msg}.${suffix}`);
                    setIgvVersion(null);
                }
                try {
                    if (igvBrowser) removeIgvBrowser(igvLibraryRef.current, igvBrowser);
                } catch {
                    // no-op
                }
                igvBrowserRef.current = null;
                igvLibraryRef.current = null;
            } finally {
                if (ownsTerminalState()) {
                    setIgvLoading(false);
                }
            }
        };

        const igvContainer = igvContainerRef.current;

        initIgv();

        return () => {
            cancelled = true;
            if (isCurrentLoad()) igvLoadTokenRef.current += 1;
            removeLocusListener?.();
            try {
                if (igvBrowser) removeIgvBrowser(igvLibraryRef.current, igvBrowser);
            } catch {
                // no-op
            }
            igvBrowserRef.current = null;
            igvLibraryRef.current = null;
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            setIgvAutoLoadAttempted(false);
            if (igvContainer) {
                igvContainer.innerHTML = '';
            }
        };
    }, [
        igvModalOpen,
        igvReady,
        activeIgvFastaUrl,
        activeIgvFaiUrl,
        igvMissingReason,
        selectedAlignmentSession?.session_id,
    ]);

    const handleLoadIgvReadsTrack = useCallback(async () => {
        if (igvReadsTrackLoading) return;
        const browser = igvBrowserRef.current;
        if (!browser || typeof browser.loadTrack !== 'function') {
            setIgvError('IGV browser is not ready yet.');
            return;
        }
        if (!activeIgvBamUrl || !activeIgvBaiUrl) {
            setIgvError('Aligned BAM and index are required to load reads track.');
            return;
        }
        setIgvReadsTrackLoading(true);
        setIgvError(null);
        const loadToken = igvLoadTokenRef.current;
        const sessionId = selectedAlignmentSession?.session_id || '';
        const isCurrentTrackLoad = () => (
            igvLoadTokenRef.current === loadToken
            && igvBrowserRef.current === browser
            && selectedAlignmentSessionIdRef.current === sessionId
        );
        try {
            if (typeof browser.findTracks === 'function' && typeof browser.removeTrack === 'function') {
                const existingTracks = browser.findTracks((track: UntypedApiValue) => track && track.type !== 'ruler');
                if (Array.isArray(existingTracks)) {
                    for (const track of existingTracks) {
                        try {
                            browser.removeTrack(track);
                        } catch {
                            // keep loading remaining tracks
                        }
                    }
                }
            }

            const auxiliaryTracks = resolveSessionAuxiliaryTracks(selectedAlignmentSession?.artifacts || {});

            const auxiliaryTrackHeightPx = auxiliaryTracks.reduce((sum, track) => (
                sum + (typeof track.height === 'number' ? track.height : 0)
            ), 0);
            const readsTrackHeight = resolveIgvReadsTrackHeight(igvContainerRef.current, auxiliaryTrackHeightPx);
            const alignmentTrack: Record<string, unknown> = {
                name: 'Aligned Reads',
                type: 'alignment',
                format: 'bam',
                url: activeIgvBamUrl,
                indexURL: activeIgvBaiUrl,
                showSoftClips: true,
                showCoverage: true,
                showMismatches: true,
                showAllBases: false,
                showInsertionText: true,
                autoHeight: false,
                height: readsTrackHeight,
                displayMode: igvAlignmentDisplayMode,
                // FASTQ/dimer runs are typically small enough to render across full plasmids.
                // A tiny visibilityWindow can make tracks appear "empty" until deep zoom.
                visibilityWindow: -1,
                samplingWindowSize: 40,
                samplingDepth: 10000,
                maxRows: 500,
                alignmentRowHeight: 9,
                squishedRowHeight: 4,
            };
            if (igvAlignmentColorBy !== 'none') {
                alignmentTrack.colorBy = igvAlignmentColorBy;
            }
            if (igvAlignmentGroupBy !== 'none') {
                alignmentTrack.groupBy = igvAlignmentGroupBy;
            }
            const loadedAlignmentTrack = await awaitCurrentGeneration(
                Promise.resolve(browser.loadTrack(alignmentTrack)),
                isCurrentTrackLoad,
            );
            if (loadedAlignmentTrack === null || !isCurrentTrackLoad()) return;
            applyIgvAlignmentOptionsToTrack(loadedAlignmentTrack, {
                displayMode: igvAlignmentDisplayMode,
                colorBy: igvAlignmentColorBy,
                groupBy: igvAlignmentGroupBy,
            });

            for (const trackConfig of auxiliaryTracks) {
                const loadedTrack = await awaitCurrentGeneration(
                    Promise.resolve(browser.loadTrack(trackConfig)),
                    isCurrentTrackLoad,
                );
                if (loadedTrack === null || !isCurrentTrackLoad()) return;
            }
            if (!isCurrentTrackLoad()) return;
            patchIgvRulerContrast(browser);
            resizeIgvAlignmentTrackToContainer(browser, igvContainerRef.current);

            // Track loading must never navigate. Browser creation already applied either
            // the session-bound requested locus or the FASTA-derived initial locus.
            resizeIgvAlignmentTrackToContainer(browser, igvContainerRef.current);
            igvLoadedSourceKeyRef.current = activeIgvSourceKey;
            setIgvReadsTrackLoaded(true);
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            if (isCurrentTrackLoad()) setIgvError(`Failed to load IGV tracks: ${msg}`);
        } finally {
            if (isCurrentTrackLoad()) setIgvReadsTrackLoading(false);
        }
    }, [
        igvReadsTrackLoading,
        activeIgvBamUrl,
        activeIgvBaiUrl,
        activeIgvSourceKey,
        selectedAlignmentSession,
        igvAlignmentDisplayMode,
        igvAlignmentColorBy,
        igvAlignmentGroupBy,
    ]);

    useEffect(() => {
        if (!igvModalOpen || !igvReadsTrackLoaded) return;
        if (igvReadsTrackLoading) return;
        const browser = igvBrowserRef.current;
        const alignmentTrack = findIgvAlignmentTrack(browser);
        if (!alignmentTrack) return;
        applyIgvAlignmentOptionsToTrack(alignmentTrack, {
            displayMode: igvAlignmentDisplayMode,
            colorBy: igvAlignmentColorBy,
            groupBy: igvAlignmentGroupBy,
        });
        resizeIgvAlignmentTrackToContainer(browser, igvContainerRef.current);
        patchIgvRulerContrast(browser);
    }, [
        igvModalOpen,
        igvReadsTrackLoaded,
        igvReadsTrackLoading,
        igvAlignmentDisplayMode,
        igvAlignmentColorBy,
        igvAlignmentGroupBy,
    ]);

    useEffect(() => {
        if (!igvModalOpen || !igvReadsTrackLoaded) return;
        const browser = igvBrowserRef.current;
        if (!browser) return;

        let frameHandle = 0;
        const applyResize = () => {
            if (frameHandle) {
                window.cancelAnimationFrame(frameHandle);
            }
            frameHandle = window.requestAnimationFrame(() => {
                resizeIgvAlignmentTrackToContainer(browser, igvContainerRef.current);
                patchIgvRulerContrast(browser);
            });
        };

        applyResize();
        window.addEventListener('resize', applyResize);
        return () => {
            window.removeEventListener('resize', applyResize);
            if (frameHandle) {
                window.cancelAnimationFrame(frameHandle);
            }
        };
    }, [igvModalOpen, igvReadsTrackLoaded, igvIsFullscreen]);

    useEffect(() => {
        if (!igvModalOpen) return;
        if (!igvReady || igvLoading) return;
        if (igvReadsTrackLoading || igvReadsTrackLoaded) return;
        if (igvAutoLoadAttempted) return;
        if (!igvBrowserRef.current) return;
        setIgvAutoLoadAttempted(true);
        void handleLoadIgvReadsTrack();
    }, [
        igvModalOpen,
        igvReady,
        igvLoading,
        igvReadsTrackLoading,
        igvReadsTrackLoaded,
        igvAutoLoadAttempted,
        handleLoadIgvReadsTrack,
    ]);

    useEffect(() => {
        if (!igvModalOpen) return;
        if (!igvReady) return;
        if (!igvReadsTrackLoaded || igvReadsTrackLoading) return;
        if (!activeIgvSourceKey) return;
        if (igvLoadedSourceKeyRef.current === activeIgvSourceKey) return;
        void handleLoadIgvReadsTrack();
    }, [
        igvModalOpen,
        igvReady,
        igvReadsTrackLoaded,
        igvReadsTrackLoading,
        activeIgvSourceKey,
        handleLoadIgvReadsTrack,
    ]);

    const handleViewLogs = async (jobId: string) => {
        setLogsModalOpen(true);
        setLogsLoading(true);
        setLogsData(null);
        setActiveLogTab('parsed');
        try {
            const response = await fetchJobLogs(jobId);
            setLogsData(response.data);
        } catch (error) {
            console.error('Failed to fetch logs:', error);
            setLogsData(null);
        } finally {
            setLogsLoading(false);
        }
    };

    return (
        <div className="min-h-screen bg-[var(--bg-primary)] p-6 space-y-6 text-[var(--text-primary)]">
            <header className="space-y-2">
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-3xl font-bold text-[var(--text-primary)]">NGS Toolkit</h1>
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => navigate('/designer')}
                            className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-secondary)] transition-colors"
                            title="Open Molecular Biology Toolkit"
                        >
                            Mol Bio Toolkit
                        </button>
                        <button
                            onClick={() => setView('launch')}
                            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${view === 'launch'
                                ? 'text-white'
                                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
                                }`}
                            style={view === 'launch' ? { backgroundColor: 'var(--accent-secondary)' } : undefined}
                        >
                            Analyze existing data
                        </button>
                        <button
                            onClick={() => setView('instrument')}
                            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${view === 'instrument'
                                ? 'text-white'
                                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
                                }`}
                            style={view === 'instrument' ? { backgroundColor: 'var(--accent-secondary)' } : undefined}
                        >
                            Start instrument run
                        </button>
                        <button
                            onClick={() => setView('runs')}
                            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${view === 'runs'
                                ? 'text-white'
                                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
                                }`}
                            style={view === 'runs' ? { backgroundColor: 'var(--accent-secondary)' } : undefined}
                        >
                            Runs
                        </button>
                    </div>
                </div>
                <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs uppercase tracking-wide text-[var(--text-secondary)] mr-1">Documentation</span>
                        {NANOPORE_DOC_LINKS.map((link) => (
                            <a
                                key={link.href}
                                href={link.href}
                                target="_blank"
                                rel="noreferrer"
                                className="px-2.5 py-1 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-xs text-[var(--text-primary)] hover:border-[var(--accent-secondary)] hover:text-[var(--accent-secondary)] transition-colors"
                            >
                                {link.label}
                            </a>
                        ))}
                    </div>
                </div>
            </header>

            {view === 'launch' ? (
                <NanoporeTemplate
                    onBack={() => setView('runs')}
                    initialValues={initialValues}
                />
            ) : view === 'instrument' ? (
                <OntInstrumentPanel onAnalyzeExistingData={() => setView('launch')} />
            ) : (
                <section className="space-y-4">
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                        <div className="bg-[var(--bg-secondary)] rounded-lg p-3 border border-[var(--border-primary)]">
                            <p className="text-xs text-[var(--text-secondary)]">Total</p>
                            <p className="text-xl font-semibold text-[var(--text-primary)]">{stats.total}</p>
                        </div>
                        <div className="bg-[var(--bg-secondary)] rounded-lg p-3 border border-[var(--border-primary)]">
                            <p className="text-xs text-[var(--text-secondary)]">Running</p>
                            <p className="text-xl font-semibold text-emerald-400">{stats.running}</p>
                        </div>
                        <div className="bg-[var(--bg-secondary)] rounded-lg p-3 border border-[var(--border-primary)]">
                            <p className="text-xs text-[var(--text-secondary)]">Queued</p>
                            <p className="text-xl font-semibold text-blue-400">{stats.queued}</p>
                        </div>
                        <div className="bg-[var(--bg-secondary)] rounded-lg p-3 border border-[var(--border-primary)]">
                            <p className="text-xs text-[var(--text-secondary)]">Completed</p>
                            <p className="text-xl font-semibold text-cyan-400">{stats.completed}</p>
                        </div>
                        <div className="bg-[var(--bg-secondary)] rounded-lg p-3 border border-[var(--border-primary)]">
                            <p className="text-xs text-[var(--text-secondary)]">Failed</p>
                            <p className="text-xl font-semibold text-rose-400">{stats.failed}</p>
                        </div>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-4 gap-3">
                        <div className="lg:col-span-2">
                            <input
                                type="text"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                placeholder="Search jobs..."
                                className="w-full bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none"
                                style={{ borderColor: 'var(--border-primary)' }}
                            />
                        </div>
                        <div>
                            <select
                                value={statusFilter}
                                onChange={(e) => setStatusFilter(e.target.value)}
                                className="w-full bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none"
                            >
                                {STATUS_OPTIONS.map((status) => (
                                    <option key={status} value={status}>
                                        {status === 'all' ? 'All statuses' : status}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div className="flex justify-end">
                            <button
                                onClick={() => {
                                    setSearch('');
                                    setStatusFilter('all');
                                }}
                                className="px-3 py-2 text-sm rounded border border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)] transition-colors"
                            >
                                Reset Filters
                            </button>
                        </div>
                    </div>

                    <div className="bg-[var(--bg-secondary)] rounded-lg border border-[var(--border-primary)] overflow-hidden">
                        <div className="px-4 py-3 border-b border-[var(--border-primary)] flex items-center justify-between">
                            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Nanopore Jobs</h2>
                            <button
                                onClick={() => setView('launch')}
                                className="px-3 py-1.5 text-xs rounded text-white transition-colors"
                                style={{ backgroundColor: 'var(--accent-secondary)' }}
                            >
                                New Nanopore Run
                            </button>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">
                                    <tr>
                                        <th className="text-left px-4 py-2">Job</th>
                                        <th className="text-left px-4 py-2">Workflow</th>
                                        <th className="text-left px-4 py-2">Status</th>
                                        <th className="text-left px-4 py-2">Stage</th>
                                        <th className="text-left px-4 py-2">Created</th>
                                        <th className="text-left px-4 py-2">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {isLoading ? (
                                        <tr>
                                            <td colSpan={6} className="px-4 py-6 text-center text-[var(--text-secondary)]">Loading nanopore jobs...</td>
                                        </tr>
                                    ) : filteredJobs.length === 0 ? (
                                        <tr>
                                            <td colSpan={6} className="px-4 py-6 text-center text-[var(--text-secondary)]">No nanopore jobs found for current filters.</td>
                                        </tr>
                                    ) : (
                                        filteredJobs.map((job) => (
                                            <tr
                                                key={job.id}
                                                className={`border-t border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)] ${selectedJobId === job.id ? 'bg-[var(--bg-tertiary)]' : ''}`}
                                            >
                                                <td className="px-4 py-2 text-[var(--text-primary)]">{job.name}</td>
                                                <td className="px-4 py-2 text-[var(--text-secondary)]">{ontWorkflowDisplayName(job.params?.ont_workflow_id, job.mode)}</td>
                                                <td className="px-4 py-2">
                                                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${job.status === 'completed'
                                                        ? 'bg-emerald-500/20 text-emerald-400'
                                                        : job.status === 'running'
                                                            ? 'bg-blue-500/20 text-blue-400'
                                                            : job.status === 'queued'
                                                                ? 'bg-cyan-500/20 text-cyan-400'
                                                                : job.status === 'failed'
                                                                    ? 'bg-rose-500/20 text-rose-400'
                                                                    : 'bg-slate-500/20 text-slate-300'
                                                        }`}>
                                                        {job.status}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-2 text-[var(--text-secondary)]">{stageDisplayName(job.current_stage)}</td>
                                                <td className="px-4 py-2 text-[var(--text-secondary)]">
                                                    {new Date(job.created_at).toLocaleString()}
                                                </td>
                                                <td className="px-4 py-2">
                                                    <div className="flex gap-2">
                                                        <button
                                                            onClick={() => setSelectedJobId(job.id)}
                                                            className="px-2 py-1 text-xs rounded bg-[var(--bg-tertiary)] hover:bg-[var(--bg-secondary)] text-[var(--text-primary)]"
                                                        >
                                                            Inspect
                                                        </button>
                                                        <button
                                                            onClick={() => handleViewLogs(job.id)}
                                                            className="px-2 py-1 text-xs rounded bg-[var(--bg-tertiary)] hover:bg-[var(--bg-secondary)] text-[var(--text-primary)]"
                                                        >
                                                            Logs
                                                        </button>
                                                        <button
                                                            onClick={() => navigate(`/jobs/${job.id}`)}
                                                            className="px-2 py-1 text-xs rounded bg-[var(--bg-tertiary)] hover:bg-[var(--bg-secondary)] text-[var(--text-primary)]"
                                                        >
                                                            Open
                                                        </button>
                                                        <button
                                                            onClick={() => {
                                                                setInitialValues(normalizeNanoporeCloneState(job));
                                                                setView('launch');
                                                            }}
                                                            className="px-2 py-1 text-xs rounded text-white"
                                                            style={{ backgroundColor: 'var(--accent-secondary)' }}
                                                        >
                                                            Reuse Params
                                                        </button>
                                                    </div>
                                                </td>
                                            </tr>
                                        ))
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div className="bg-[var(--bg-secondary)] rounded-lg border border-[var(--border-primary)] p-4 space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Run Inspector</h3>
                            {selectedJob && (
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => void openIgvModal()}
                                        title={igvMissingReason || 'Open IGV genome viewer'}
                                        className="px-3 py-1.5 text-xs rounded border transition-colors text-[var(--text-primary)] border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)]"
                                    >
                                        Open IGV
                                    </button>
                                    <span className="text-xs text-[var(--text-secondary)]">{selectedJob.id}</span>
                                </div>
                            )}
                        </div>

                        {!selectedJob ? (
                            <p className="text-sm text-[var(--text-secondary)]">Select a run to inspect.</p>
                        ) : (
                            <>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                                    <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                        <div className="text-xs text-[var(--text-secondary)] mb-1">Job</div>
                                        <div className="text-[var(--text-primary)] font-medium">{selectedJob.name}</div>
                                    </div>
                                    <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                        <div className="text-xs text-[var(--text-secondary)] mb-1">Status</div>
                                        <div className="text-[var(--text-primary)]">{selectedJob.status}</div>
                                    </div>
                                    <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                        <div className="text-xs text-[var(--text-secondary)] mb-1">Output Directory</div>
                                        <div className="text-[var(--text-secondary)] font-mono text-xs break-all">{selectedJob.output_dir || '—'}</div>
                                    </div>
                                    <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                        <div className="text-xs text-[var(--text-secondary)] mb-1">Created</div>
                                        <div className="text-[var(--text-primary)]">{new Date(selectedJob.created_at).toLocaleString()}</div>
                                    </div>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                                    {[
                                        ['Input source', selectedJob.params?.pod5_dir ? 'pod5' : selectedJob.params?.bam_path ? 'bam' : selectedJob.params?.fastq_path ? 'fastq' : '—'],
                                        ['Pinned GPU (queue)', selectedJob.pinned_gpu],
                                        ['Pinned GPUs', selectedJob.params?.pinned_gpus],
                                        ['Lock GPUs', selectedJob.params?.lock_gpus],
                                        ['POD5 directory', selectedJob.params?.pod5_dir],
                                        ['BAM path', selectedJob.params?.bam_path],
                                        ['BAM force realign', selectedJob.params?.bam_force_realign],
                                        ['BAM min MAPQ', selectedJob.params?.bam_min_mapq],
                                        ['FASTQ path', selectedJob.params?.fastq_path],
                                        ['Reference FASTA', selectedJob.params?.reference_fasta],
                                        ['Dorado model', selectedJob.params?.dorado_model],
                                        ['Modified bases', selectedJob.params?.modified_bases],
                                        ['Min qscore (POD5 basecalling)', selectedJob.params?.min_qscore],
                                        ['Trim adapters', selectedJob.params?.trim_adapters],
                                        ['Run modkit', selectedJob.params?.run_modkit],
                                        ['Run FASTQ QC', selectedJob.params?.run_fastq_qc],
                                        ['Run multimer QC (legacy)', selectedJob.params?.run_multimer_qc],
                                        ['Expected plasmid size', selectedJob.params?.expected_plasmid_size],
                                        ['Min FASTQ read length', selectedJob.params?.min_fastq_read_length],
                                        ['FASTQ minimap2 preset', selectedJob.params?.fastq_minimap2_preset],
                                        ['FASTQ keep secondary', selectedJob.params?.fastq_minimap2_allow_secondary],
                                        ['IGV track window (bp)', selectedJob.params?.igv_track_window_bp],
                                        ['IGV report max sites', selectedJob.params?.igv_report_max_sites],
                                        ['IGV report flanking (bp)', selectedJob.params?.igv_report_flanking_bp],
                                        ['Run assembly', selectedJob.params?.run_assembly],
                                        ['Assembly tool', selectedJob.params?.wf_clone_assembly_tool],
                                        ['Approx size (bp)', selectedJob.params?.wf_clone_approx_size],
                                        ['Assembly coverage', selectedJob.params?.wf_clone_assm_coverage],
                                        ['Assembly trim length', selectedJob.params?.wf_clone_trim_length],
                                        ['Assembly min quality', selectedJob.params?.wf_clone_min_quality],
                                        ['wf-clone sample', selectedJob.params?.wf_clone_sample],
                                        ['Large construct mode', selectedJob.params?.wf_clone_large_construct],
                                    ].map(([label, value]) => (
                                        <div key={label} className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                            <div className="text-xs text-[var(--text-secondary)] mb-1">{label}</div>
                                            <div className="text-[var(--text-primary)] text-sm break-all">{formatParamValue(value)}</div>
                                        </div>
                                    ))}
                                </div>

                                <SequenceQcManifestPanel
                                    status={sequenceQcManifestState.status}
                                    manifest={sequenceQcManifestState.manifest}
                                    message={sequenceQcManifestState.message}
                                    onNavigateLocus={navigateToVerifiedLocus}
                                />

                                <BarcodeUnitsPanel
                                    jobId={selectedJob.id}
                                    enabled={selectedJob.status === 'completed' && selectedJob.model_id === 'nanopore' && selectedJob.mode === 'basecall_dna' && Boolean(selectedJob.params?.barcode_kit)}
                                    defaultReference={typeof selectedJob.params?.reference_fasta === 'string' ? selectedJob.params.reference_fasta : ''}
                                />

                                <div className="space-y-2">
                                    <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">IGV Readiness</h4>
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                        {igvReadinessChecks.map((check) => (
                                            <div
                                                key={check.label}
                                                className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2"
                                            >
                                                <div className="flex items-center justify-between gap-2">
                                                    <div className="text-sm text-[var(--text-primary)]">{check.label}</div>
                                                    <div className={`text-xs px-2 py-0.5 rounded ${check.ok
                                                        ? 'bg-emerald-500/20 text-emerald-400'
                                                        : 'bg-rose-500/20 text-rose-400'
                                                        }`}>
                                                        {check.ok ? 'ready' : 'missing'}
                                                    </div>
                                                </div>
                                                <div className="mt-1 text-xs text-[var(--text-secondary)] font-mono break-all">
                                                    {check.path || 'No resolved path'}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="pt-1">
                                        <div className="text-[11px] text-[var(--text-secondary)] mb-1">Optional analysis tracks</div>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                            {igvAuxReadinessChecks.map((check) => (
                                                <div
                                                    key={check.label}
                                                    className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2"
                                                >
                                                    <div className="flex items-center justify-between gap-2">
                                                        <div className="text-xs text-[var(--text-primary)]">{check.label}</div>
                                                        <div className={`text-[10px] px-2 py-0.5 rounded ${check.ok
                                                            ? 'bg-emerald-500/20 text-emerald-400'
                                                            : 'bg-slate-500/20 text-slate-300'
                                                            }`}>
                                                            {check.ok ? 'found' : 'missing'}
                                                        </div>
                                                    </div>
                                                    <div className="mt-1 text-[10px] text-[var(--text-secondary)] font-mono break-all">
                                                        {check.path || 'No resolved path'}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                    {!igvReady && (
                                        <p className="text-xs text-[var(--text-secondary)]">
                                            IGV unavailable: {igvMissingReason}
                                        </p>
                                    )}
                                    {(igvReportDownloadHref || igvTrackConfigDownloadHref) && (
                                        <div className="flex flex-wrap items-center gap-2 pt-1">
                                            {igvReportDownloadHref && (
                                                <a
                                                    href={igvReportDownloadHref}
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    className="px-2 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                >
                                                    Open IGV report
                                                </a>
                                            )}
                                            {igvTrackConfigDownloadHref && (
                                                <a
                                                    href={igvTrackConfigDownloadHref}
                                                    className="px-2 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                >
                                                    Download track config
                                                </a>
                                            )}
                                        </div>
                                    )}
                                </div>
                                {shouldShowMultimerInspector && (
                                    <div className="space-y-2">
                                        <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">FASTQ QC</h4>
                                        {multimerLoading ? (
                                            <p className="text-sm text-[var(--text-secondary)]">Loading FASTQ QC outputs...</p>
                                        ) : multimerReport === null ? (
                                            <p className="text-sm text-[var(--text-secondary)]">
                                                {multimerError || multimerArtifacts.missingReason || 'No FASTQ QC outputs available for this run.'}
                                            </p>
                                        ) : (
                                            <div className="space-y-3">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    {multimerSummaryDownloadHref && (
                                                        <a
                                                            href={multimerSummaryDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download QC summary
                                                        </a>
                                                    )}
                                                    {multimerLengthsDownloadHref && (
                                                        <a
                                                            href={multimerLengthsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download read lengths
                                                        </a>
                                                    )}
                                                    {multimerCandidatesDownloadHref && (
                                                        <a
                                                            href={multimerCandidatesDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download multimer candidates
                                                        </a>
                                                    )}
                                                    {fastqAlignmentSummaryDownloadHref && (
                                                        <a
                                                            href={fastqAlignmentSummaryDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download alignment stats
                                                        </a>
                                                    )}
                                                    {fastqConsensusDownloadHref && (
                                                        <a
                                                            href={fastqConsensusDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download consensus
                                                        </a>
                                                    )}
                                                    {fastqQcLogDownloadHref && (
                                                        <a
                                                            href={fastqQcLogDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download QC log
                                                        </a>
                                                    )}
                                                </div>
                                                {multimerError && (
                                                    <p className="text-xs text-amber-300">{multimerError}</p>
                                                )}

                                                {!hasFastqQcDetails ? (
                                                    <p className="text-sm text-[var(--text-secondary)]">No parsed FASTQ summary yet.</p>
                                                ) : (
                                                    <>
                                                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Monomer-like</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{multimerClassCounts.monomer}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Dimer candidates</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{multimerClassCounts.dimer}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Trimer candidates</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{multimerClassCounts.trimer}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Higher-order</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{multimerClassCounts.highOrder}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Classified total</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{totalClassifiedReads}</div>
                                                            </div>
                                                        </div>

                                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Aligned reads</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{alignedReadCount ?? '—'}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Read lengths rows</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{multimerReport.readLengths.length}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Expected plasmid size</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{Number.isFinite(expectedPlasmidSize) ? `${Math.round(expectedPlasmidSize)} bp` : '—'}</div>
                                                            </div>
                                                            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                                                <div className="text-xs text-[var(--text-secondary)]">Consensus status</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{consensusStatus || 'n/a'}</div>
                                                            </div>
                                                        </div>

                                                        {multimerClassLegendItems.length > 0 && (
                                                            <div className="flex flex-wrap gap-3 text-[11px] text-[var(--text-secondary)]">
                                                                {multimerClassLegendItems.map((item) => (
                                                                    <span key={item.label} className="inline-flex items-center gap-1.5">
                                                                        <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: item.color }} />
                                                                        <span>{item.label}</span>
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        )}

                                                        {multimerHistogramPlotData.length > 0 ? (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Read length distribution</div>
                                                                <Plot
                                                                    data={multimerHistogramPlotData}
                                                                    layout={multimerHistogramLayout}
                                                                    config={multimerPlotConfig}
                                                                    className="w-full h-[260px]"
                                                                    style={{ width: '100%', height: '260px' }}
                                                                    useResizeHandler
                                                                />
                                                            </div>
                                                        ) : (
                                                            <p className="text-xs text-[var(--text-secondary)]">No read-length table.</p>
                                                        )}

                                                        {topMultimerCandidates.length > 0 && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Multimer candidates</div>
                                                                <div className="overflow-x-auto">
                                                                    <table className="w-full text-xs">
                                                                        <thead>
                                                                            <tr className="border-b border-[var(--border-primary)]">
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Read #</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Length (bp)</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Classification</th>
                                                                            </tr>
                                                                        </thead>
                                                                        <tbody>
                                                                            {topMultimerCandidates.map((row, idx) => (
                                                                                <tr key={`multimer-candidate-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.readIndex ?? '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.readLength ?? '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)]">{row.classification || '—'}</td>
                                                                                </tr>
                                                                            ))}
                                                                        </tbody>
                                                                    </table>
                                                                </div>
                                                            </div>
                                                        )}

                                                        {consensusPreview && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Consensus preview</div>
                                                                <pre className="text-[11px] leading-relaxed text-[var(--text-primary)] font-mono whitespace-pre-wrap break-words max-h-[280px] overflow-auto">
                                                                    {consensusPreview}
                                                                </pre>
                                                            </div>
                                                        )}
                                                    </>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )}

                                {shouldShowMethylationInspector && (
                                <div className="space-y-2">
                                    <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Methylation Report</h4>
                                    {methylationLoading ? (
                                        <p className="text-sm text-[var(--text-secondary)]">Loading modkit outputs...</p>
                                    ) : methylationReport === null ? (
                                        <p className="text-sm text-[var(--text-secondary)]">
                                            {methylationError || methylationArtifacts.missingReason || 'No methylation outputs available for this run.'}
                                        </p>
                                    ) : (
                                        <div className="space-y-3">
                                            <div className="flex flex-wrap items-center gap-2">
                                                {methylationSummaryDownloadHref && (
                                                    <a
                                                        href={methylationSummaryDownloadHref}
                                                        className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                    >
                                                        Download modkit summary
                                                    </a>
                                                )}
                                                {methylationBedDownloadHref && (
                                                    <a
                                                        href={methylationBedDownloadHref}
                                                        className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                    >
                                                        Download methylation BED
                                                    </a>
                                                )}
                                            </div>
                                            {methylationError && (
                                                <p className="text-xs text-amber-300">{methylationError}</p>
                                            )}

                                            <div className="text-xs text-[var(--text-secondary)]">
                                                Dam/Dcm motif view; Dcm uses <span className="font-mono">max(m, h)</span>.
                                            </div>
                                            <div className="flex flex-wrap items-center gap-2 text-xs">
                                                <span className="text-[var(--text-secondary)]">Strand view:</span>
                                                {METHYLATION_STRAND_FILTERS.map((value) => (
                                                    <button
                                                        key={`strand-filter-${value}`}
                                                        type="button"
                                                        onClick={() => setStrandFilter(value)}
                                                        className={`px-2 py-1 rounded border transition-colors ${strandFilter === value
                                                            ? 'text-[var(--text-primary)]'
                                                            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                            }`}
                                                        style={{
                                                            borderColor: 'var(--border-primary)',
                                                            backgroundColor: strandFilter === value ? 'var(--bg-secondary)' : 'transparent',
                                                        }}
                                                    >
                                                        {value === 'both' ? 'both' : value === '+' ? 'plus (+)' : 'minus (-)'}
                                                    </button>
                                                ))}
                                            </div>
                                            <div className="flex flex-wrap items-center gap-2 text-xs">
                                                <span className="text-[var(--text-secondary)]">Min coverage:</span>
                                                {[0, 10, 20, 50].map((value) => (
                                                    <button
                                                        key={`coverage-filter-${value}`}
                                                        type="button"
                                                        onClick={() => setMotifMinCoverage(value)}
                                                        className={`px-2 py-1 rounded border transition-colors ${motifMinCoverage === value
                                                            ? 'text-[var(--text-primary)]'
                                                            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                            }`}
                                                        style={{
                                                            borderColor: 'var(--border-primary)',
                                                            backgroundColor: motifMinCoverage === value ? 'var(--bg-secondary)' : 'transparent',
                                                        }}
                                                    >
                                                        {value === 0 ? 'all' : `${value}x`}
                                                    </button>
                                                ))}
                                            </div>
                                            <div className="flex flex-wrap items-center gap-2 text-xs">
                                                <span className="text-[var(--text-secondary)]">Strand concordance:</span>
                                                <button
                                                    type="button"
                                                    onClick={() => setRequireStrandConcordance(true)}
                                                    className={`px-2 py-1 rounded border transition-colors ${requireStrandConcordance
                                                        ? 'text-[var(--text-primary)]'
                                                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                        }`}
                                                    style={{
                                                        borderColor: 'var(--border-primary)',
                                                        backgroundColor: requireStrandConcordance ? 'var(--bg-secondary)' : 'transparent',
                                                    }}
                                                >
                                                    required
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setRequireStrandConcordance(false)}
                                                    className={`px-2 py-1 rounded border transition-colors ${!requireStrandConcordance
                                                        ? 'text-[var(--text-primary)]'
                                                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                        }`}
                                                    style={{
                                                        borderColor: 'var(--border-primary)',
                                                        backgroundColor: !requireStrandConcordance ? 'var(--bg-secondary)' : 'transparent',
                                                    }}
                                                >
                                                    off
                                                </button>
                                            </div>
                                            <div className="text-xs text-[var(--text-secondary)]">
                                                Filtered motif sites with calls: <span className="font-mono">{filteredMotifCalledSites.length}</span> | {' '}
                                                &gt;5% sites: <span className="font-mono">{filteredMotifHighSites.length}</span>.
                                            </div>
                                            <div className="text-xs text-[var(--text-secondary)]">
                                                Negative controls: expect ≤5% at adequate depth.
                                            </div>

                                            {methylationPlotData.length > 0 ? (
                                                <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-3">
                                                    <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                        <div className="text-xs text-[var(--text-secondary)] mb-2">Dam/Dcm motif methylation</div>
                                                        <Plot
                                                            data={methylationPlotData}
                                                            layout={methylationPlotLayout}
                                                            config={methylationPlotConfig}
                                                            style={{ width: '100%', height: '340px' }}
                                                            useResizeHandler
                                                            onClick={handleMethylationPointClick}
                                                        />
                                                        {selectedMotifPoint && (
                                                            <div className="mt-3 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-xs space-y-1">
                                                                <div className="text-[var(--text-secondary)]">Selected point</div>
                                                                <div className="text-[var(--text-primary)]">
                                                                    <span className="font-medium">{selectedMotifPoint.motif}</span>{' '}
                                                                    <span className="font-mono">{`${selectedMotifPoint.chrom}:${selectedMotifPoint.position}`}</span>{' '}
                                                                    strand {selectedMotifPoint.strand} | {selectedMotifPoint.percentModified != null ? `${selectedMotifPoint.percentModified.toFixed(2)}%` : '—'} | coverage {selectedMotifPoint.coverage != null ? selectedMotifPoint.coverage.toFixed(0) : '—'}
                                                                </div>
                                                                <div className="text-[var(--text-primary)]">
                                                                    Motif context: <span className="font-mono">{selectedMotifPoint.context}</span>
                                                                </div>
                                                                <div className="text-[var(--text-primary)]">
                                                                    Sequence window
                                                                    {selectedMotifPoint.contextStart != null && selectedMotifPoint.contextEnd != null && (
                                                                        <> <span className="font-mono">({selectedMotifPoint.contextStart}-{selectedMotifPoint.contextEnd})</span></>
                                                                    )}
                                                                    :{' '}
                                                                    <span className="font-mono">
                                                                        {selectedMotifPoint.sequenceContext || 'Reference sequence not available for this point.'}
                                                                    </span>
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>

                                                    <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3 space-y-2">
                                                        <div className="text-xs text-[var(--text-secondary)]">
                                                            Reference sequence (5' to 3')
                                                            {methylationReport.referenceName && (
                                                                <> <span className="font-mono text-[var(--text-primary)]">{methylationReport.referenceName}</span></>
                                                            )}
                                                            {methylationReport.referenceLength != null && (
                                                                <> <span className="font-mono">({methylationReport.referenceLength} bp)</span></>
                                                            )}
                                                        </div>
                                                        <div className="text-[11px] text-[var(--text-secondary)] space-y-1">
                                                            <div>Shading = % modified; +/- hues are separate.</div>
                                                            <div className="flex flex-wrap items-center gap-3">
                                                                <span className="inline-flex items-center gap-1">
                                                                    <span className="h-2.5 w-8 rounded border border-[var(--border-primary)]" style={{
                                                                        background: `linear-gradient(90deg, ${toAlphaColor(themeColors.accentPrimary, 0.12)} 0%, ${toAlphaColor(themeColors.accentPrimary, 0.84)} 100%)`,
                                                                    }} />
                                                                    <span className="font-mono">Dam</span>
                                                                </span>
                                                                <span className="inline-flex items-center gap-1">
                                                                    <span className="h-2.5 w-8 rounded border border-[var(--border-primary)]" style={{
                                                                        background: `linear-gradient(90deg, ${toAlphaColor(themeColors.success, 0.12)} 0%, ${toAlphaColor(themeColors.success, 0.84)} 100%)`,
                                                                    }} />
                                                                    <span className="font-mono">Dcm</span>
                                                                </span>
                                                            </div>
                                                        </div>
                                                        {referenceSequenceRows.length > 0 ? (
                                                            <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] max-h-[340px] overflow-auto">
                                                                <div className="min-w-[560px] divide-y divide-[var(--border-primary)]/40">
                                                                    {referenceSequenceRows.map((row) => {
                                                                        const segments = buildHighlightedSequenceSegments(
                                                                            row,
                                                                            referenceSiteHighlightsByPosition,
                                                                            selectedSequencePosition
                                                                        );
                                                                        return (
                                                                            <div key={`reference-seq-row-${row.start}`} className="grid grid-cols-[120px_1fr] items-start gap-2 px-3 py-1.5">
                                                                                <div className="text-[11px] text-[var(--text-secondary)] font-mono whitespace-nowrap">
                                                                                    {`${row.start}-${row.end}`}
                                                                                </div>
                                                                                <div className="text-[12px] text-[var(--text-primary)] font-mono whitespace-pre tracking-[0.02em] break-all">
                                                                                    {segments.map((segment, idx) => {
                                                                                        if (!segment.highlight && !segment.isSelected) {
                                                                                            return <span key={`seq-seg-${row.start}-${idx}`}>{segment.text}</span>;
                                                                                        }
                                                                                        const highlight = segment.highlight;
                                                                                        const highlightColor = highlight?.color || themeColors.accentPrimary;
                                                                                        const alpha = highlight ? percentToHighlightAlpha(highlight.percentModified) : 0.2;
                                                                                        const titleBits = [];
                                                                                        if (segment.position != null) titleBits.push(`Position: ${segment.position}`);
                                                                                        if (highlight) {
                                                                                            titleBits.push(`${highlight.motif} ${highlight.strand}`);
                                                                                            titleBits.push(`% Modified: ${highlight.percentModified.toFixed(2)}%`);
                                                                                        }
                                                                                        if (segment.isSelected) {
                                                                                            titleBits.push('Selected from chart');
                                                                                        }
                                                                                        return (
                                                                                            <span
                                                                                                key={`seq-seg-${row.start}-${idx}`}
                                                                                                title={titleBits.join(' | ')}
                                                                                                style={{
                                                                                                    backgroundColor: highlight ? toAlphaColor(highlightColor, alpha) : 'transparent',
                                                                                                    borderBottom: highlight ? `1px solid ${highlightColor}` : 'none',
                                                                                                    outline: segment.isSelected ? `1px solid ${themeColors.accentPrimary}` : 'none',
                                                                                                    outlineOffset: '0px',
                                                                                                    borderRadius: '2px',
                                                                                                    padding: '0 1px',
                                                                                                }}
                                                                                            >
                                                                                                {segment.text}
                                                                                            </span>
                                                                                        );
                                                                                    })}
                                                                                </div>
                                                                            </div>
                                                                        );
                                                                    })}
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <p className="text-xs text-[var(--text-secondary)]">Reference sequence unavailable.</p>
                                                        )}
                                                        {selectedSequencePosition != null && (
                                                            <p className="text-xs text-[var(--text-secondary)]">
                                                                Highlighted site from selected bar: <span className="font-mono">{selectedSequencePosition}</span>
                                                            </p>
                                                        )}
                                                    </div>
                                                </div>
                                            ) : (
                                                <p className="text-xs text-[var(--text-secondary)]">No per-site methylation points.</p>
                                            )}

                                            <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3 space-y-2">
                                                <div className="text-xs text-[var(--text-secondary)]">
                                                    {methylationReport.referenceName
                                                        ? `Reference: ${methylationReport.referenceName}${methylationReport.referenceLength ? ` (${methylationReport.referenceLength} bp)` : ''}`
                                                        : 'Reference unavailable for Dam/Dcm motif detection.'}
                                                </div>
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2">
                                                        <div className="text-[var(--text-secondary)]">Dam motifs (GATC)</div>
                                                        <div className="text-[var(--text-primary)] font-medium">
                                                            {filteredDamAllSites.length} visible, {filteredDamSites.length} with 6mA calls
                                                        </div>
                                                    </div>
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2">
                                                        <div className="text-[var(--text-secondary)]">Dcm motifs (CCWGG)</div>
                                                        <div className="text-[var(--text-primary)] font-medium">
                                                            {filteredDcmAllSites.length} visible, {filteredDcmSites.length} with 5mC calls
                                                        </div>
                                                    </div>
                                                </div>
                                                {filteredMotifAllSites.length > 0 && (
                                                    <div className="overflow-x-auto">
                                                        <table className="w-full text-xs">
                                                            <thead>
                                                                <tr className="border-b border-[var(--border-primary)]">
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Motif</th>
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Site</th>
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Strand</th>
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Context</th>
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Call</th>
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">% Modified</th>
                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Coverage</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody>
                                                                {filteredMotifAllSites.map((site, idx) => (
                                                                    <tr key={`motif-site-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{site.motif}</td>
                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{`${site.chrom}:${site.position}`}</td>
                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{site.strand}</td>
                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{site.context}</td>
                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                            {site.percentModified != null ? 'called' : 'no-call'}
                                                                        </td>
                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                            {site.percentModified != null ? `${site.percentModified.toFixed(2)}%` : '—'}
                                                                        </td>
                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                            {site.coverage != null ? site.coverage.toFixed(0) : '—'}
                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                )}
                                            </div>

                                            {methylationReport.summary ? (
                                                <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                    <div className="text-xs text-[var(--text-secondary)] mb-2">modkit summary (first 20 rows)</div>
                                                    <div className="overflow-x-auto">
                                                        <table className="w-full text-xs">
                                                            <thead>
                                                                <tr className="border-b border-[var(--border-primary)]">
                                                                    {methylationReport.summary.header.map((column) => (
                                                                        <th key={column} className="text-left font-medium text-[var(--text-secondary)] px-2 py-1 whitespace-nowrap">
                                                                            {column}
                                                                        </th>
                                                                    ))}
                                                                </tr>
                                                            </thead>
                                                            <tbody>
                                                                {methylationReport.summary.rows.slice(0, 20).map((row, idx) => (
                                                                    <tr key={`summary-row-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                        {methylationReport.summary?.header.map((_, colIdx) => (
                                                                            <td key={`summary-cell-${idx}-${colIdx}`} className="px-2 py-1 text-[var(--text-primary)] whitespace-nowrap">
                                                                                {row[colIdx] ?? '—'}
                                                                            </td>
                                                                        ))}
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            ) : (
                                                <p className="text-xs text-[var(--text-secondary)]">No parseable modkit summary table detected.</p>
                                            )}

                                            <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                <div className="flex items-center justify-between gap-3 mb-2">
                                                    <div className="text-xs text-[var(--text-secondary)]">
                                                        Raw modkit loci
                                                    </div>
                                                    <button
                                                        type="button"
                                                        onClick={() => setShowRawTopLoci((prev) => !prev)}
                                                        className="text-xs px-2 py-1 rounded border border-[var(--border-primary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--text-primary)]/40 transition-colors"
                                                    >
                                                        {showRawTopLoci ? 'Hide' : 'Show'}
                                                    </button>
                                                </div>

                                                {showRawTopLoci ? (
                                                    methylationReport.topLoci.length > 0 ? (
                                                        <div className="overflow-x-auto">
                                                            <table className="w-full text-xs">
                                                                <thead>
                                                                    <tr className="border-b border-[var(--border-primary)]">
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Locus</th>
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Code</th>
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Strand</th>
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">% Modified</th>
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Coverage</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {methylationReport.topLoci.map((locus, idx) => (
                                                                        <tr key={`locus-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                            <td className="px-2 py-1 text-[var(--text-primary)] font-mono whitespace-nowrap">
                                                                                {`${locus.chrom}:${locus.start}-${locus.end}`}
                                                                            </td>
                                                                            <td className="px-2 py-1 text-[var(--text-primary)] font-mono">
                                                                                {locus.code}
                                                                            </td>
                                                                            <td className="px-2 py-1 text-[var(--text-primary)]">{locus.strand}</td>
                                                                            <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                                {locus.percentModified != null ? `${locus.percentModified.toFixed(2)}%` : '—'}
                                                                            </td>
                                                                            <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                                {locus.coverage != null ? locus.coverage.toFixed(0) : '—'}
                                                                            </td>
                                                                        </tr>
                                                                    ))}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                    ) : (
                                                        <p className="text-xs text-[var(--text-secondary)]">No methylation BED rows available to preview.</p>
                                                    )
                                                ) : null}
                                            </div>
                                        </div>
                                    )}
                                </div>
                                )}

                                <div className="space-y-2">
                                    <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Stage Progress</h4>
                                    {stagesLoading ? (
                                        <p className="text-sm text-[var(--text-secondary)]">Loading stage state...</p>
                                    ) : allStages.length === 0 ? (
                                        <p className="text-sm text-[var(--text-secondary)]">No explicit stage plan available yet for this run.</p>
                                    ) : (
                                        <div className="space-y-2">
                                            {allStages.map((stage) => {
                                                const stageKey = normalizeStageKey(stage);
                                                const isComplete = forceCompleteByJobStatus || completedStageKeySet.has(stageKey);
                                                const isCurrent = !isComplete && currentStageKey !== '' && currentStageKey === stageKey;
                                                return (
                                                    <div key={stage} className="flex items-center justify-between bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2">
                                                        <div className="text-sm text-[var(--text-primary)]">{stageDisplayName(stage)}</div>
                                                        <div className={`text-xs px-2 py-0.5 rounded ${isComplete
                                                            ? 'bg-emerald-500/20 text-emerald-400'
                                                            : isCurrent
                                                                ? 'bg-blue-500/20 text-blue-400'
                                                                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)]'
                                                            }`}>
                                                            {isComplete ? 'completed' : isCurrent ? 'running' : 'pending'}
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>

                                <div className="space-y-2">
                                    <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Stage Artifacts</h4>
                                    {Object.keys(stageOutputs).length === 0 ? (
                                        <p className="text-sm text-[var(--text-secondary)]">No stage outputs recorded yet.</p>
                                    ) : (
                                        <div className="space-y-3">
                                            {Object.entries(stageOutputs).map(([stage, outputs]) => (
                                                <div key={stage} className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                    <div className="text-sm text-[var(--text-primary)] mb-2">{stageDisplayName(stage)}</div>
                                                    {outputs.length === 0 ? (
                                                        <div className="text-xs text-[var(--text-secondary)]">No files listed.</div>
                                                    ) : (
                                                        <ul className="space-y-1">
                                                            {outputs.map((output) => {
                                                                const href = toDownloadHref(output, selectedJob?.id || undefined);
                                                                const name = output.split('/').pop() || output;
                                                                return (
                                                                    <li key={`${stage}:${output}`} className="text-xs">
                                                                        {href ? (
                                                                            <a
                                                                                href={href}
                                                                                className="text-sky-300 hover:text-sky-200 underline break-all"
                                                                            >
                                                                                {name}
                                                                            </a>
                                                                        ) : (
                                                                            <span className="text-[var(--text-secondary)] break-all">{output}</span>
                                                                        )}
                                                                    </li>
                                                                );
                                                            })}
                                                        </ul>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </>
                        )}
                    </div>
                </section>
            )}

            {igvModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-0 bg-black/80 backdrop-blur-sm">
                    <div className={`bg-[var(--bg-secondary)] border border-[var(--border-primary)] shadow-2xl w-screen h-screen max-w-none max-h-none flex flex-col ${igvIsFullscreen ? 'rounded-none border-0' : 'rounded-2xl'}`}>
                        <div className="flex items-center gap-2 px-2 py-1 border-b border-[var(--border-primary)]">
                            <div className="min-w-0 flex-1 flex items-center gap-2 text-[11px] text-[var(--text-secondary)]">
                                <span className="text-xs font-semibold text-[var(--text-primary)]">IGV</span>
                                {selectedJob && (
                                    <span className="truncate max-w-[40vw]" title={selectedJob.name}>
                                        {selectedJob.name}
                                    </span>
                                )}
                                <span>IGV.js {igvVersion || `loading (>= ${IGV_REQUIRED_VERSION})`}</span>
                                <span>{igvIsFullscreen ? 'FS on' : 'FS off'}</span>
                            </div>
                            <div className="flex items-center gap-1">
                                <select
                                    value={selectedAlignmentSession?.session_id || ''}
                                    onChange={(event) => setSelectedAlignmentSessionId(event.target.value)}
                                    disabled={igvLoading || igvReadsTrackLoading || alignmentSessions.length === 0}
                                    title="Authoritative job-scoped alignment session"
                                    className="max-w-[250px] bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-1.5 py-0.5 text-[11px] text-[var(--text-primary)]"
                                >
                                    {alignmentSessions.length === 0 && (
                                        <option value="">No validated sessions</option>
                                    )}
                                    {alignmentSessions.map((session) => (
                                        <option key={session.session_id} value={session.session_id}>
                                            {session.mode === 'primary' ? 'Primary alignment' : 'Dimer candidates'} · {session.ready ? 'ready' : 'unavailable'}
                                        </option>
                                    ))}
                                </select>
                                <select
                                    value={igvAlignmentDisplayMode}
                                    onChange={(event) => setIgvAlignmentDisplayMode(event.target.value)}
                                    disabled={igvLoading || igvReadsTrackLoading}
                                    className="bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-1.5 py-0.5 text-[11px] text-[var(--text-primary)]"
                                >
                                    {IGV_ALIGNMENT_DISPLAY_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>
                                            {option.label}
                                        </option>
                                    ))}
                                </select>
                                <select
                                    value={igvAlignmentColorBy}
                                    onChange={(event) => setIgvAlignmentColorBy(event.target.value)}
                                    disabled={igvLoading || igvReadsTrackLoading}
                                    className="bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-1.5 py-0.5 text-[11px] text-[var(--text-primary)]"
                                >
                                    {IGV_ALIGNMENT_COLOR_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>
                                            {option.label}
                                        </option>
                                    ))}
                                </select>
                                <select
                                    value={igvAlignmentGroupBy}
                                    onChange={(event) => setIgvAlignmentGroupBy(event.target.value)}
                                    disabled={igvLoading || igvReadsTrackLoading}
                                    className="bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-1.5 py-0.5 text-[11px] text-[var(--text-primary)]"
                                >
                                    {IGV_ALIGNMENT_GROUP_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>
                                            {option.label}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <button
                                type="button"
                                onClick={() => void handleLoadIgvReadsTrack()}
                                disabled={igvLoading || igvReadsTrackLoading || !activeIgvBamUrl || !activeIgvBaiUrl}
                                className="px-2 py-0.5 text-[11px] rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {igvReadsTrackLoading ? 'Loading tracks...' : igvReadsTrackLoaded ? 'Reload tracks' : 'Load tracks'}
                            </button>
                            <button
                                onClick={() => void closeIgvModal()}
                                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-lg leading-none px-1.5 py-0.5 transition-colors"
                            >
                                ×
                            </button>
                        </div>

                        <div className="flex-1 overflow-hidden min-h-0">
                            <div className="relative w-full h-full">
                                <div
                                    ref={igvContainerRef}
                                    className="absolute inset-0 bg-[var(--bg-primary)]"
                                />
                                {igvLoading && (
                                    <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-primary)]/65 text-[var(--text-secondary)] text-xs">
                                        Loading IGV viewer...
                                    </div>
                                )}
                                {!igvLoading && igvError && (
                                    <div className="absolute top-2 left-2 right-2 rounded border border-red-500/40 bg-red-500/10 text-red-300 text-xs px-2 py-1.5">
                                        {igvError}
                                    </div>
                                )}
                                {!igvLoading && !igvError && !igvReadsTrackLoaded && (
                                    <div className="absolute bottom-2 left-2 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)]/85 text-[var(--text-secondary)] text-xs px-2 py-1.5">
                                        Reference loaded; tracks autoload or use Load tracks.
                                    </div>
                                )}
                                {!igvLoading && !igvError && igvReadsTrackLoaded && missingIgvAuxTracks.length > 0 && (
                                    <div className="absolute bottom-2 left-2 max-w-[42vw] rounded border border-amber-400/35 bg-amber-500/10 text-amber-200 text-[11px] px-2 py-1.5">
                                        Missing optional tracks: {missingIgvAuxTracks.map((check) => check.label).join(', ')}
                                    </div>
                                )}
                                {selectedJob && selectedAlignmentSession?.ready && (
                                    <RawReadInspector
                                        jobId={selectedJob.id}
                                        sessionId={selectedAlignmentSession.session_id}
                                        currentLocus={igvCurrentLocus}
                                    />
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {logsModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
                    <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl shadow-2xl w-full max-w-5xl max-h-[80vh] flex flex-col">
                        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-primary)]">
                            <div>
                                <h2 className="text-xl font-semibold text-[var(--text-primary)]">Nanopore Run Logs</h2>
                                {logsData && (
                                    <p className="text-sm text-[var(--text-secondary)] mt-1">
                                        {logsData.job_name} • Exit code: {logsData.exit_code ?? 'N/A'}
                                    </p>
                                )}
                            </div>
                            <button
                                onClick={() => {
                                    setLogsModalOpen(false);
                                    setLogsData(null);
                                }}
                                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-2xl font-light transition-colors"
                            >
                                ×
                            </button>
                        </div>

                        <div className="flex border-b border-[var(--border-primary)] px-4">
                            {[
                                { id: 'parsed' as const, label: 'Parsed Error' },
                                { id: 'command' as const, label: 'Command Log' },
                                { id: 'stderr' as const, label: 'stderr' },
                                { id: 'nextflow' as const, label: 'Nextflow Log' },
                            ].map((tab) => (
                                <button
                                    key={tab.id}
                                    onClick={() => setActiveLogTab(tab.id)}
                                    className={`px-4 py-3 text-sm font-medium transition-colors ${activeLogTab === tab.id
                                        ? 'border-b-2 -mb-px'
                                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                        }`}
                                    style={activeLogTab === tab.id ? { color: 'var(--accent-secondary)', borderColor: 'var(--accent-secondary)' } : undefined}
                                >
                                    {tab.label}
                                </button>
                            ))}
                        </div>

                        <div className="flex-1 overflow-auto p-4 min-h-[300px]">
                            {logsLoading ? (
                                <div className="flex items-center justify-center h-full text-[var(--text-secondary)]">
                                    Loading logs...
                                </div>
                            ) : !logsData ? (
                                <div className="flex items-center justify-center h-full text-[var(--text-secondary)]">
                                    Failed to load logs
                                </div>
                            ) : (
                                <pre className="text-sm text-[var(--text-secondary)] font-mono whitespace-pre-wrap break-words">
                                    {activeLogTab === 'parsed' && (
                                        logsData.parsed_error || <span className="text-[var(--text-secondary)] italic">No specific error extracted</span>
                                    )}
                                    {activeLogTab === 'command' && (
                                        logsData.command_log || <span className="text-[var(--text-secondary)] italic">No command log available</span>
                                    )}
                                    {activeLogTab === 'stderr' && (
                                        logsData.command_err || <span className="text-[var(--text-secondary)] italic">No stderr output</span>
                                    )}
                                    {activeLogTab === 'nextflow' && (
                                        logsData.nextflow_log || <span className="text-[var(--text-secondary)] italic">No Nextflow log available</span>
                                    )}
                                </pre>
                            )}
                        </div>

                        <div className="flex justify-end px-6 py-4 border-t border-[var(--border-primary)]">
                            <button
                                onClick={() => {
                                    setLogsModalOpen(false);
                                    setLogsData(null);
                                }}
                                className="px-4 py-2 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-primary)] text-[var(--text-primary)] rounded-lg transition-colors"
                            >
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default NGSToolkit;
