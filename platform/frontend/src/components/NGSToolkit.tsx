import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data, Layout, PlotMouseEvent } from 'plotly.js';
import { fetchJobLogs, fetchJobStages, fetchJobs, type Job, type JobLogs } from '../lib/api';
import { NanoporeTemplate } from './NanoporeTemplate';
import { useThemeColors, useThemePlotlyLayout } from './useThemeColors';

type ToolkitView = 'launch' | 'runs';
type LogTab = 'parsed' | 'command' | 'stderr' | 'nextflow';
type StageOutputsMap = Record<string, string[]>;

interface IgvArtifacts {
    bamPath: string | null;
    bamUrl: string | null;
    baiPath: string | null;
    baiUrl: string | null;
    fastaPath: string | null;
    fastaUrl: string | null;
    faiPath: string | null;
    faiUrl: string | null;
    missingReason: string | null;
}

interface MethylationArtifacts {
    summaryPath: string | null;
    summaryUrl: string | null;
    bedPath: string | null;
    bedUrl: string | null;
    missingReason: string | null;
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

interface DimerSequenceHighlight {
    readCount: number;
    supportPercent: number | null;
    backgroundColor: string;
}

interface DimerHighlightedSequenceSegment {
    text: string;
    position: number | null;
    highlight: DimerSequenceHighlight | null;
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
const IGV_SCRIPT_BASE_ID = 'bms-igv-script';
const IGV_SCRIPT_URLS = [
    '/api/files/igv-script',
];
const IGV_SCRIPT_TIMEOUT_MS = 15000;
const IGV_INIT_TIMEOUT_MS = 20000;
const IGV_INITIAL_LOCUS_WINDOW_BP = 800;
const IGV_INITIAL_FULL_LOCUS_MAX_BP = 100000;
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
const DEFAULT_INCLUDE_BOUNDARY_JUNCTIONS = false;
const MIN_CONFIDENT_SPLIT_SUPPORT_READS = 3;
const ALIGNMENT_STAGE_ALIASES = ['dorado_align', 'doradoalign', 'bam_prepare', 'bamprepare', 'preparebamforanalysis', 'fastq_align', 'fastqalign'];
const REFERENCE_STAGE_ALIASES = [...ALIGNMENT_STAGE_ALIASES, 'reference_prepare', 'referenceprepareforigv'];
const MODKIT_STAGE_ALIASES = ['modkit', 'modkitpileup', 'modkitsummary'];
const MULTIMER_STAGE_ALIASES = ['multimer_qc', 'multimerqc', 'fastqmultimerqc'];
const DIMER_STAGE_ALIASES = ['dimer_analysis', 'dimeranalysis', 'fastqdimeranalysis'];
const METHYLATION_STRAND_FILTERS = ['both', '+', '-'] as const;
const MOTIF_CONCORDANCE_DELTA_PERCENT = 20;
const RELEVANT_METHYLATION_CODES = new Set(['a', 'm']);

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

function resolveBamArtifactPath(paths: string[]): string | null {
    const dimerAligned = findFirstMatchingPath(paths, [/\/dimer_candidates\.aligned\.bam$/, /(^|\/)dimer_candidates\.aligned\.bam$/]);
    if (dimerAligned) return dimerAligned;

    const aligned = findFirstMatchingPath(paths, [/\/aligned\.bam$/, /(^|\/)aligned\.bam$/]);
    if (aligned) return aligned;

    const nonBasecallBam = paths.find((path) => (
        /\.bam$/i.test(path)
        && !/\.bam\.(bai|csi)$/i.test(path)
        && !/\/calls\.bam$/i.test(path)
    ));
    if (nonBasecallBam) return nonBasecallBam;

    return paths.find((path) => /\.bam$/i.test(path) && !/\.bam\.(bai|csi)$/i.test(path)) || null;
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

    const hasFastq = hasMeaningfulValue(params.fastq_path);
    const runMultimerQc = hasFastq && params.run_multimer_qc !== false;
    const dimerBamPreferred = findFirstMatchingPath(
        dimerBase,
        [/\/multimer_qc\/dimer_candidates\.aligned\.bam$/i, /(^|\/)dimer_candidates\.aligned\.bam$/i]
    );
    const dimerBaiPreferred = resolveBamIndexArtifactPath(dimerBamPreferred, dimerBase);
    const dimerFastaPreferred = findFirstMatchingPath(
        dimerBase,
        [/\/multimer_qc\/dimer_reference\.fasta$/i, /(^|\/)dimer_reference\.fasta$/i]
    );
    const dimerFaiPreferred = findFirstMatchingPath(
        dimerBase,
        [/\/multimer_qc\/dimer_reference\.fasta\.fai$/i, /(^|\/)dimer_reference\.fasta\.fai$/i]
    );

    const bamPath = (runMultimerQc && dimerBamPreferred)
        ? dimerBamPreferred
        : resolveBamArtifactPath(alignmentCandidates);
    const baiPath = (runMultimerQc && dimerBamPreferred)
        ? dimerBaiPreferred
        : resolveBamIndexArtifactPath(bamPath, alignmentCandidates);
    const fastaPath = findFirstMatchingPath(
        (runMultimerQc && dimerFastaPreferred) ? dimerBase : referenceCandidates,
        [/\/dimer_reference\.fasta$/, /\/reference\.fasta$/, /\/reference\.fa$/, /\.fasta$/, /\.fa$/]
    );
    const faiPath = findFirstMatchingPath(
        (runMultimerQc && dimerFastaPreferred) ? dimerBase : referenceCandidates,
        [/\/dimer_reference\.fasta\.fai$/, /\/reference\.fasta\.fai$/, /\/reference\.fa\.fai$/, /\.fai$/]
    );

    const fallbackReference = typeof params.reference_fasta === 'string' ? params.reference_fasta : null;
    const fallbackReferenceIndex = fallbackReference ? `${fallbackReference}.fai` : null;

    const cacheKey = job.id;
    const bamUrl = bamPath ? toStreamHref(bamPath, cacheKey) : null;
    const baiUrl = baiPath ? toStreamHref(baiPath, cacheKey) : null;
    const fastaUrl = fastaPath ? toStreamHref(fastaPath, cacheKey) : (fallbackReference ? toStreamHref(fallbackReference, cacheKey) : null);
    const faiUrl = faiPath ? toStreamHref(faiPath, cacheKey) : (fallbackReferenceIndex ? toStreamHref(fallbackReferenceIndex, cacheKey) : null);

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
        fastaPath: (runMultimerQc ? (dimerFastaPreferred || fastaPath) : fastaPath) || fallbackReference,
        fastaUrl,
        faiPath: (runMultimerQc ? (dimerFaiPreferred || faiPath) : faiPath) || fallbackReferenceIndex,
        faiUrl,
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
    const runMultimerQc = hasFastq && params.run_multimer_qc !== false;
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

    const summaryPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/multimer_summary\.tsv$/i, /(^|\/)multimer_summary\.tsv$/i]);
    const lengthsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/read_lengths\.tsv$/i, /(^|\/)read_lengths\.tsv$/i]);
    const candidatesPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/multimer_candidates\.tsv$/i, /(^|\/)multimer_candidates\.tsv$/i]);
    const logPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/multimer_qc\.log$/i, /(^|\/)multimer_qc\.log$/i]);
    const dimerFastqPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_candidates\.fastq$/i, /(^|\/)dimer_candidates\.fastq$/i]);
    const dimerFastaPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_candidates\.fasta$/i, /(^|\/)dimer_candidates\.fasta$/i]);
    const dimerLengthsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_read_lengths\.tsv$/i, /(^|\/)dimer_read_lengths\.tsv$/i]);
    const dimerSummaryPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_analysis_summary\.tsv$/i, /(^|\/)dimer_analysis_summary\.tsv$/i]);
    const dimerConsensusPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_consensus\.fasta$/i, /(^|\/)dimer_consensus\.fasta$/i]);
    const dominantDimerConsensusPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dominant_dimer_consensus\.fasta$/i, /(^|\/)dominant_dimer_consensus\.fasta$/i]);
    const dominantDimerConsensusMetadataPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dominant_dimer_consensus_metadata\.tsv$/i, /(^|\/)dominant_dimer_consensus_metadata\.tsv$/i]);
    const dimerJunctionPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_profile\.tsv$/i, /(^|\/)dimer_junction_profile\.tsv$/i]);
    const dimerJunctionEventsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_events\.tsv$/i, /(^|\/)dimer_junction_events\.tsv$/i]);
    const dimerJunctionClustersPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_clusters\.tsv$/i, /(^|\/)dimer_junction_clusters\.tsv$/i]);
    const dimerJunctionHotspotsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_hotspots\.tsv$/i, /(^|\/)dimer_junction_hotspots\.tsv$/i]);
    const dimerJunctionRotatedPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_rotated_profile\.tsv$/i, /(^|\/)dimer_junction_rotated_profile\.tsv$/i]);
    const dimerJunctionRotationSummaryPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_junction_rotation_summary\.tsv$/i, /(^|\/)dimer_junction_rotation_summary\.tsv$/i]);
    const dimerBreakpointScreenPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_breakpoint_screen\.tsv$/i, /(^|\/)dimer_breakpoint_screen\.tsv$/i]);
    const dimerReadsPath = findFirstMatchingPath(candidates, [/\/multimer_qc\/dimer_read_junctions\.tsv$/i, /(^|\/)dimer_read_junctions\.tsv$/i]);
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
    } else if (!runMultimerQc) {
        missingReason = 'FASTQ multimer QC is disabled for this run.';
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

function normalizeTsvHeaderKey(value: string): string {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function parseTsvRowsWithHeader(text: string): { header: string[]; rows: string[][] } {
    const lines = text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0 && !line.startsWith('#'));

    if (lines.length === 0) {
        return { header: [], rows: [] };
    }

    const firstCols = lines[0].split('\t').map((col) => col.trim());
    const firstNormalized = firstCols.map((col) => normalizeTsvHeaderKey(col));
    const looksLikeHeader = firstNormalized.some((col) => /[a-z]/.test(col));

    if (looksLikeHeader) {
        return {
            header: firstCols,
            rows: lines.slice(1).map((line) => line.split('\t').map((col) => col.trim())),
        };
    }

    return {
        header: firstCols.map((_, idx) => `col_${idx}`),
        rows: lines.map((line) => line.split('\t').map((col) => col.trim())),
    };
}

function findTsvColumnIndex(normalizedHeader: string[], aliases: string[]): number {
    const normalizedAliases = aliases.map((alias) => normalizeTsvHeaderKey(alias));
    for (const alias of normalizedAliases) {
        const exact = normalizedHeader.indexOf(alias);
        if (exact >= 0) return exact;
    }

    for (const alias of normalizedAliases) {
        const partial = normalizedHeader.findIndex((key) => key.includes(alias) || alias.includes(key));
        if (partial >= 0) return partial;
    }

    return -1;
}

function parseOptionalNumber(value: string | null | undefined): number | null {
    if (value == null) return null;
    const raw = String(value).trim();
    if (!raw) return null;
    const normalized = raw.replace(/,/g, '');
    const isPercent = normalized.endsWith('%');
    const numeric = Number.parseFloat(isPercent ? normalized.slice(0, -1) : normalized);
    if (!Number.isFinite(numeric)) return null;
    return numeric;
}

function parseOptionalInteger(value: string | null | undefined): number | null {
    const numeric = parseOptionalNumber(value);
    if (numeric == null) return null;
    return Math.round(numeric);
}

function parseOptionalBoolean(value: string | null | undefined): boolean | null {
    if (value == null) return null;
    const normalized = String(value).trim().toLowerCase();
    if (!normalized) return null;
    if (['1', 'true', 't', 'yes', 'y', 'crossing', 'crosses', 'spanning', 'spans'].includes(normalized)) return true;
    if (['0', 'false', 'f', 'no', 'n', 'non_crossing', 'noncrossing', 'not_crossing'].includes(normalized)) return false;
    const numeric = Number.parseFloat(normalized);
    if (Number.isFinite(numeric)) return numeric > 0;
    return null;
}

function parseOptionalText(value: string | null | undefined): string | null {
    if (value == null) return null;
    const trimmed = String(value).trim();
    return trimmed.length > 0 ? trimmed : null;
}

function normalizeMaybePercent(value: number | null): number | null {
    if (value == null) return null;
    if (value >= 0 && value <= 1) return value * 100;
    return value;
}

function modeNonEmpty(values: Array<string | null | undefined>): string | null {
    const counts = new Map<string, number>();
    for (const value of values) {
        const key = parseOptionalText(value);
        if (!key) continue;
        counts.set(key, (counts.get(key) || 0) + 1);
    }
    const top = Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0];
    return top?.[0] || null;
}

function parseDimerJunctionProfile(
    text: string,
    maxRows = 5000
): DimerJunctionProfileRow[] {
    const parsed = parseTsvRowsWithHeader(text);
    const header = parsed.header.map((col) => normalizeTsvHeaderKey(col));
    const positionIdx = findTsvColumnIndex(header, ['position_mod_ref', 'position', 'junction_position', 'junction_pos']);
    const readCountIdx = findTsvColumnIndex(header, ['read_count', 'support_reads', 'dimer_reads', 'count']);
    const spanningIdx = findTsvColumnIndex(header, ['junction_spanning_reads', 'spanning_reads', 'crossing_reads', 'crossing_count']);

    const rows: DimerJunctionProfileRow[] = [];
    for (const cols of parsed.rows) {
        const position = parseOptionalInteger(cols[positionIdx >= 0 ? positionIdx : 0]);
        const readCount = parseOptionalInteger(cols[readCountIdx >= 0 ? readCountIdx : 1]);
        const spanningReads = parseOptionalInteger(cols[spanningIdx >= 0 ? spanningIdx : 2]);
        if (!Number.isFinite(position as number) || !Number.isFinite(readCount as number)) continue;

        rows.push({
            positionModRef: position as number,
            readCount: readCount as number,
            spanningReads: Number.isFinite(spanningReads as number) ? (spanningReads as number) : 0,
        });
        if (rows.length >= maxRows) break;
    }

    return rows.sort((a, b) => a.positionModRef - b.positionModRef);
}

function parseDimerReadJunctions(
    text: string,
    maxRows = 1000
): DimerReadJunctionRow[] {
    const parsed = parseTsvRowsWithHeader(text);
    const header = parsed.header.map((col) => normalizeTsvHeaderKey(col));
    const readIdIdx = findTsvColumnIndex(header, ['read_id', 'read', 'qname', 'query_name']);
    const startIdx = findTsvColumnIndex(header, ['start', 'read_start', 'alignment_start', 'ref_start']);
    const endIdx = findTsvColumnIndex(header, ['end', 'read_end', 'alignment_end', 'ref_end']);
    const posIdx = findTsvColumnIndex(header, ['position_mod_ref', 'pos_mod', 'junction_position', 'junction_pos', 'position']);
    const crossesIdx = findTsvColumnIndex(header, ['crosses_junction', 'crosses', 'crossing', 'spans_junction', 'junction_crossing']);

    const rows: DimerReadJunctionRow[] = [];
    for (const cols of parsed.rows) {
        const start = parseOptionalInteger(cols[startIdx >= 0 ? startIdx : 1]);
        const end = parseOptionalInteger(cols[endIdx >= 0 ? endIdx : 2]);
        const positionModRef = parseOptionalInteger(cols[posIdx >= 0 ? posIdx : 3]);
        const readId = parseOptionalText(cols[readIdIdx >= 0 ? readIdIdx : 0]) || '';
        if (start == null && end == null && positionModRef == null && !readId) continue;

        rows.push({
            readId,
            start,
            end,
            positionModRef,
            crossesJunction: parseOptionalBoolean(cols[crossesIdx >= 0 ? crossesIdx : 4]),
            method: null,
            orientation: null,
            missingLeftBp: null,
            missingRightBp: null,
            source: 'legacy',
        });
        if (rows.length >= maxRows) break;
    }
    return rows;
}

function parseDimerJunctionEvents(
    text: string,
    maxRows = 1500
): DimerReadJunctionRow[] {
    const parsed = parseTsvRowsWithHeader(text);
    const header = parsed.header.map((col) => normalizeTsvHeaderKey(col));
    const readIdIdx = findTsvColumnIndex(header, ['read_id', 'read', 'qname', 'query_name']);
    const startIdx = findTsvColumnIndex(header, ['start', 'read_start', 'alignment_start', 'ref_start']);
    const endIdx = findTsvColumnIndex(header, ['end', 'read_end', 'alignment_end', 'ref_end']);
    const posIdx = findTsvColumnIndex(header, ['position_mod_ref', 'junction_position', 'junction_pos', 'position', 'hotspot_position']);
    const crossesIdx = findTsvColumnIndex(header, ['crosses_junction', 'crossing', 'spans_junction', 'junction_crossing']);
    const methodIdx = findTsvColumnIndex(header, ['method', 'junction_method', 'detection_method', 'call_method']);
    const orientationIdx = findTsvColumnIndex(header, ['orientation', 'strand', 'junction_orientation', 'read_orientation']);
    const missingLeftIdx = findTsvColumnIndex(header, ['missing_left_bp', 'missing_5p_bp', 'missing_5p', 'left_missing_bp', 'left_gap_bp']);
    const missingRightIdx = findTsvColumnIndex(header, ['missing_right_bp', 'missing_3p_bp', 'missing_3p', 'right_missing_bp', 'right_gap_bp']);

    const rows: DimerReadJunctionRow[] = [];
    for (const cols of parsed.rows) {
        const readId = parseOptionalText(cols[readIdIdx >= 0 ? readIdIdx : 0]) || '';
        const start = parseOptionalInteger(cols[startIdx]);
        const end = parseOptionalInteger(cols[endIdx]);
        const positionModRef = parseOptionalInteger(cols[posIdx]);
        if (!readId && start == null && end == null && positionModRef == null) continue;

        rows.push({
            readId,
            start,
            end,
            positionModRef,
            crossesJunction: parseOptionalBoolean(cols[crossesIdx]),
            method: parseOptionalText(cols[methodIdx]),
            orientation: parseOptionalText(cols[orientationIdx]),
            missingLeftBp: parseOptionalInteger(cols[missingLeftIdx]),
            missingRightBp: parseOptionalInteger(cols[missingRightIdx]),
            source: 'events',
        });
        if (rows.length >= maxRows) break;
    }
    return rows;
}

function parseDimerJunctionClusters(
    text: string,
    maxRows = 5000,
    source: DimerJunctionClusterRow['source'] = 'clusters'
): DimerJunctionClusterRow[] {
    const parsed = parseTsvRowsWithHeader(text);
    const header = parsed.header.map((col) => normalizeTsvHeaderKey(col));
    const clusterIdIdx = findTsvColumnIndex(header, ['cluster_id', 'cluster', 'id']);
    const posIdx = findTsvColumnIndex(header, ['position_mod_ref', 'junction_position', 'junction_pos', 'position', 'hotspot_position']);
    const supportIdx = findTsvColumnIndex(header, ['support_reads', 'read_count', 'cluster_reads', 'total_reads', 'support_count']);
    const crossingIdx = findTsvColumnIndex(header, ['crossing_reads', 'junction_spanning_reads', 'spanning_reads', 'crossing_count']);
    const supportPctIdx = findTsvColumnIndex(header, ['support_percent', 'support_pct', 'support_fraction', 'crossing_percent', 'crossing_pct', 'crossing_fraction']);
    const methodIdx = findTsvColumnIndex(header, ['method', 'junction_method', 'detection_method', 'call_method']);
    const orientationIdx = findTsvColumnIndex(header, ['orientation', 'strand', 'junction_orientation']);
    const eventCountIdx = findTsvColumnIndex(header, ['event_count', 'events', 'member_count', 'n_events']);
    const boundaryIdx = findTsvColumnIndex(header, ['in_boundary_window', 'boundary_window', 'is_boundary', 'in_boundary']);

    const rows: DimerJunctionClusterRow[] = [];
    for (const cols of parsed.rows) {
        const positionModRef = parseOptionalInteger(cols[posIdx >= 0 ? posIdx : 0]);
        if (!Number.isFinite(positionModRef as number) || (positionModRef as number) <= 0) continue;
        const supportReads = parseOptionalInteger(cols[supportIdx >= 0 ? supportIdx : 1]);
        const crossingRaw = crossingIdx >= 0
            ? parseOptionalInteger(cols[crossingIdx])
            : null;
        const eventCount = parseOptionalInteger(cols[eventCountIdx]);
        let supportPercent = normalizeMaybePercent(parseOptionalNumber(cols[supportPctIdx]));
        const safeSupportReads = Math.max(0, supportReads ?? 0);
        // Hotspot tables only have support count + support pct (no explicit crossing count).
        // Treat support_reads as crossing support for hotspot-derived rows.
        const safeCrossingReads = Math.max(0, source === 'hotspots' ? safeSupportReads : (crossingRaw ?? 0));
        if (supportPercent == null && safeSupportReads > 0 && safeCrossingReads > 0) {
            supportPercent = (safeCrossingReads / safeSupportReads) * 100;
        }

        rows.push({
            clusterId: parseOptionalText(cols[clusterIdIdx]),
            positionModRef: positionModRef as number,
            supportReads: safeSupportReads,
            crossingReads: safeCrossingReads,
            supportPercent,
            method: parseOptionalText(cols[methodIdx]),
            orientation: parseOptionalText(cols[orientationIdx]),
            eventCount,
            inBoundaryWindow: parseOptionalBoolean(cols[boundaryIdx]),
            source,
        });
        if (rows.length >= maxRows) break;
    }

    return rows.sort((a, b) => a.positionModRef - b.positionModRef);
}

function parseDimerBreakpointScreen(
    text: string,
    maxRows = 5000
): DimerBreakpointScreenRow[] {
    const parsed = parseTsvRowsWithHeader(text);
    const header = parsed.header.map((col) => normalizeTsvHeaderKey(col));
    const posIdx = findTsvColumnIndex(header, ['position_mod_ref', 'position', 'junction_position', 'junction_pos']);
    const totalIdx = findTsvColumnIndex(header, ['total_support_reads', 'support_reads', 'total_reads', 'crossing_reads']);
    const seamIdx = findTsvColumnIndex(header, ['seam_support_reads', 'seam_reads']);
    const splitIdx = findTsvColumnIndex(header, ['split_support_reads', 'split_reads']);
    const supportPctIdx = findTsvColumnIndex(header, ['support_pct_all', 'support_percent_all', 'support_pct']);
    const splitPctPosIdx = findTsvColumnIndex(header, ['split_pct_of_position', 'split_fraction_of_position']);
    const splitPctAllIdx = findTsvColumnIndex(header, ['split_pct_of_all_split', 'split_fraction_of_all_split']);
    const boundaryIdx = findTsvColumnIndex(header, ['in_boundary_window', 'boundary_window', 'is_boundary', 'in_boundary']);
    const boundaryStartReadsIdx = findTsvColumnIndex(header, ['boundary_start_reads', 'start_boundary_reads']);
    const boundaryStartFractionIdx = findTsvColumnIndex(header, ['boundary_start_fraction', 'start_boundary_fraction']);
    const seamFractionIdx = findTsvColumnIndex(header, ['seam_fraction', 'seam_support_fraction']);
    const splitToSeamRatioIdx = findTsvColumnIndex(header, ['split_to_seam_ratio', 'split_seam_ratio']);
    const artifactFlagIdx = findTsvColumnIndex(header, ['artifact_flag', 'artifact_likely']);
    const confidenceIdx = findTsvColumnIndex(header, ['confidence', 'confidence_tier', 'screen_confidence']);

    const rows: DimerBreakpointScreenRow[] = [];
    for (const cols of parsed.rows) {
        const positionModRef = parseOptionalInteger(cols[posIdx >= 0 ? posIdx : 0]);
        if (!Number.isFinite(positionModRef as number) || (positionModRef as number) <= 0) continue;
        rows.push({
            positionModRef: positionModRef as number,
            totalSupportReads: Math.max(0, parseOptionalInteger(cols[totalIdx >= 0 ? totalIdx : 1]) ?? 0),
            seamSupportReads: Math.max(0, parseOptionalInteger(cols[seamIdx >= 0 ? seamIdx : 2]) ?? 0),
            splitSupportReads: Math.max(0, parseOptionalInteger(cols[splitIdx >= 0 ? splitIdx : 3]) ?? 0),
            supportPctAll: normalizeMaybePercent(parseOptionalNumber(cols[supportPctIdx])),
            splitPctOfPosition: normalizeMaybePercent(parseOptionalNumber(cols[splitPctPosIdx])),
            splitPctOfAllSplit: normalizeMaybePercent(parseOptionalNumber(cols[splitPctAllIdx])),
            inBoundaryWindow: parseOptionalBoolean(cols[boundaryIdx]),
            boundaryStartReads: parseOptionalInteger(cols[boundaryStartReadsIdx]),
            boundaryStartFraction: normalizeMaybePercent(parseOptionalNumber(cols[boundaryStartFractionIdx])),
            seamFraction: normalizeMaybePercent(parseOptionalNumber(cols[seamFractionIdx])),
            splitToSeamRatio: parseOptionalNumber(cols[splitToSeamRatioIdx]),
            artifactFlag: parseOptionalBoolean(cols[artifactFlagIdx]),
            confidence: parseOptionalText(cols[confidenceIdx]),
        });
        if (rows.length >= maxRows) break;
    }
    return rows;
}

function clustersFromLegacyProfile(rows: DimerJunctionProfileRow[]): DimerJunctionClusterRow[] {
    return rows.map((row) => ({
        clusterId: null,
        positionModRef: row.positionModRef,
        supportReads: row.readCount,
        crossingReads: row.spanningReads,
        supportPercent: null,
        method: null,
        orientation: null,
        eventCount: null,
        inBoundaryWindow: null,
        source: 'profile',
    }));
}

function clustersFromEvents(rows: DimerReadJunctionRow[]): DimerJunctionClusterRow[] {
    const grouped = new Map<number, {
        supportReads: number;
        crossingReads: number;
        eventCount: number;
        methods: Array<string | null>;
        orientations: Array<string | null>;
    }>();

    for (const row of rows) {
        if (row.positionModRef == null) continue;
        if (!grouped.has(row.positionModRef)) {
            grouped.set(row.positionModRef, {
                supportReads: 0,
                crossingReads: 0,
                eventCount: 0,
                methods: [],
                orientations: [],
            });
        }
        const acc = grouped.get(row.positionModRef)!;
        acc.supportReads += 1;
        acc.eventCount += 1;
        if (row.crossesJunction === true) acc.crossingReads += 1;
        acc.methods.push(row.method);
        acc.orientations.push(row.orientation);
    }

    return Array.from(grouped.entries())
        .map(([positionModRef, value]) => ({
            clusterId: null,
            positionModRef,
            supportReads: value.supportReads,
            crossingReads: value.crossingReads,
            supportPercent: value.supportReads > 0 ? (value.crossingReads / value.supportReads) * 100 : null,
            method: modeNonEmpty(value.methods),
            orientation: modeNonEmpty(value.orientations),
            eventCount: value.eventCount,
            inBoundaryWindow: null,
            source: 'events' as const,
        }))
        .sort((a, b) => a.positionModRef - b.positionModRef);
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
    // modkit bedMethyl outputs percent values (0..100), where low values commonly appear as decimals (e.g. 0.65%).
    // Some tools emit fractions (0..1). Detect scale once per file.
    let inspected = 0;
    let maxObserved = Number.NEGATIVE_INFINITY;
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const cols = line.split('\t');
        if (cols.length < 11) continue;
        const value = Number.parseFloat(cols[10] ?? '');
        if (!Number.isFinite(value)) continue;
        if (value > maxObserved) maxObserved = value;
        inspected += 1;
        if (inspected >= 2000 || maxObserved > 1.0) break;
    }
    if (!Number.isFinite(maxObserved)) return 1;
    return maxObserved > 1.0 ? 1 : 100;
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
        const plusPct = plusM.percent;
        const plusCov = plusM.coverage;

        const minusM = lookupCodePercent(lookup, chrom, minusPos, 'm');
        const minusPct = minusM.percent;
        const minusCov = minusM.coverage;

        dcmSites.push({
            chrom,
            motif: 'Dcm',
            position: plusPos,
            context: hit.context,
            strand: '+',
            pairKey,
            percentModified: plusPct,
            coverage: plusCov,
        });
        dcmSites.push({
            chrom,
            motif: 'Dcm',
            position: minusPos,
            context: hit.context,
            strand: '-',
            pairKey,
            percentModified: minusPct,
            coverage: minusCov,
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

function formatCircularJunctionWindow(
    sequence: string,
    rightPosition: number,
    flank = 50
): { text: string; label: string } | null {
    const len = sequence.length;
    if (!len) return null;

    const normalize = (pos: number): number => {
        const v = ((pos - 1) % len + len) % len;
        return v + 1;
    };
    const sliceCircular = (startPos: number, count: number): string => {
        let out = '';
        for (let i = 0; i < count; i++) {
            const pos = normalize(startPos + i);
            out += sequence.charAt(pos - 1) || 'N';
        }
        return out;
    };

    const rightStart = normalize(rightPosition);
    const leftEnd = normalize(rightStart - 1);
    const leftStart = normalize(rightStart - flank);
    const rightEnd = normalize(rightStart + flank - 1);

    const upstream = sliceCircular(rightStart - flank, flank);
    const downstream = sliceCircular(rightStart, flank);
    return {
        text: `${upstream}[|]${downstream}`,
        label: `${leftStart}-${leftEnd}|${rightStart}-${rightEnd}`,
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

function readCountToHighlightAlpha(readCount: number, maxReadCount: number): number {
    if (!Number.isFinite(readCount) || readCount <= 0) return 0;
    if (!Number.isFinite(maxReadCount) || maxReadCount <= 0) return 0.2;
    const normalized = clampNumber(readCount / maxReadCount, 0, 1);
    return 0.15 + (0.75 * Math.sqrt(normalized));
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

function buildDimerHighlightedSequenceSegments(
    row: SequenceRow,
    highlightsByPosition: Map<number, DimerSequenceHighlight>
): DimerHighlightedSequenceSegment[] {
    const segments: DimerHighlightedSequenceSegment[] = [];
    let plainBuffer = '';
    let plainStartPos = row.start;

    const flushPlain = () => {
        if (!plainBuffer) return;
        segments.push({
            text: plainBuffer,
            position: plainStartPos,
            highlight: null,
        });
        plainBuffer = '';
    };

    for (let offset = 0; offset < row.bases.length; offset += 1) {
        const position = row.start + offset;
        const base = row.bases.charAt(offset);
        const highlight = highlightsByPosition.get(position) || null;
        if (!highlight) {
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
        });
    }

    flushPlain();
    return segments;
}

async function waitForIgvScript(script: HTMLScriptElement, source: string): Promise<void> {
    if ((window as any).igv) return;
    if (script.dataset.loaded === 'true') return;

    await new Promise<void>((resolve, reject) => {
        let finished = false;
        const timeout = window.setTimeout(() => {
            if (finished) return;
            finished = true;
            reject(new Error(`Timed out loading IGV script from ${source}`));
        }, IGV_SCRIPT_TIMEOUT_MS);

        const cleanup = () => {
            window.clearTimeout(timeout);
            script.removeEventListener('load', onLoad);
            script.removeEventListener('error', onError);
        };

        const onLoad = () => {
            if (finished) return;
            finished = true;
            cleanup();
            script.dataset.loaded = 'true';
            resolve();
        };
        const onError = () => {
            if (finished) return;
            finished = true;
            cleanup();
            reject(new Error(`Failed to load IGV script from ${source}`));
        };

        script.addEventListener('load', onLoad);
        script.addEventListener('error', onError);
    });
}

async function loadIgvLibrary(): Promise<any> {
    if ((window as any).igv) return (window as any).igv;

    let lastError: Error | null = null;
    for (let i = 0; i < IGV_SCRIPT_URLS.length; i++) {
        const source = IGV_SCRIPT_URLS[i];
        const scriptId = `${IGV_SCRIPT_BASE_ID}-${i}`;
        let script = document.getElementById(scriptId) as HTMLScriptElement | null;

        if (script && script.dataset.failed === 'true') {
            script.remove();
            script = null;
        }

        if (!script) {
            script = document.createElement('script');
            script.id = scriptId;
            script.src = source;
            script.async = true;
            script.dataset.loaded = 'false';
            script.dataset.failed = 'false';
            document.head.appendChild(script);
        }

        try {
            await waitForIgvScript(script, source);
            if ((window as any).igv) {
                return (window as any).igv;
            }
            lastError = new Error(`IGV script loaded from ${source} but window.igv is unavailable`);
        } catch (err) {
            script.dataset.failed = 'true';
            lastError = err instanceof Error ? err : new Error(String(err));
        }
    }

    const sourceList = IGV_SCRIPT_URLS.join(', ');
    throw lastError || new Error(`Failed to load IGV script from configured sources: ${sourceList}`);
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

function stageDisplayName(stage: string | null | undefined): string {
    if (!stage) return '—';
    const key = stage.toLowerCase();
    return STAGE_LABELS[key] || stage;
}

function formatParamValue(value: unknown): string {
    if (value === undefined || value === null || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function normalizeInitialValues(job: Job | null): Record<string, unknown> | undefined {
    if (!job) return undefined;
    const p = job.params || {};
    const pinnedGpus = (Array.isArray(p.pinned_gpus) ? p.pinned_gpus : (job.pinned_gpu != null ? [job.pinned_gpu] : []))
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value >= 0);
    const inputSource = p.fastq_path ? 'fastq' : (p.bam_path ? 'bam' : 'pod5');
    return {
        jobName: job.name,
        pinnedGpus,
        lockGpus: p.lock_gpus === true,
        inputSource,
        pod5Dir: p.pod5_dir || '',
        bamPath: p.bam_path || '',
        fastqPath: p.fastq_path || '',
        referencePath: p.reference_fasta || '',
        doradoModel: p.dorado_model || 'sup',
        modifiedBases: p.modified_bases || '6mA 4mC_5mC',
        trimAdapters: p.trim_adapters !== false,
        runModkit: p.run_modkit !== false,
        runMultimerQc: p.run_multimer_qc === true,
        expectedPlasmidSize: p.expected_plasmid_size ?? 7000,
        enableRotatingReferenceFrames: p.enable_rotating_reference_frames !== false,
        rotationScanStepBp: p.rotation_scan_step_bp ?? 1,

        minFastqReadLength: p.min_fastq_read_length ?? 0,
        runAssembly: p.run_assembly === true,
        assemblyTool: p.wf_clone_assembly_tool || 'flye',
        assemblyApproxSize: p.wf_clone_approx_size ?? 7000,
        assemblyCoverage: p.wf_clone_assm_coverage ?? 60,
        assemblyTrimLength: p.wf_clone_trim_length ?? 0,
        assemblyMinQuality: p.wf_clone_min_quality ?? 9,
        wfCloneWorkflowDir: p.wf_clone_workflow_dir || '',
        wfCloneSource: p.wf_clone_source || 'epi2me-labs/wf-clone-validation',
        wfCloneRevision: p.wf_clone_revision || 'v1.8.3',
        wfCloneProfile: p.wf_clone_profile || 'singularity',
        wfCloneSample: p.wf_clone_sample || '',
        wfCloneLargeConstruct: p.wf_clone_large_construct === true,
        emitSummary: p.emit_summary !== false,
        batchSize: p.dorado_batch_size ?? null,
        modkitFilterThreshold: p.modkit_filter_threshold ?? null,
        qualityFilter: (p.min_qscore === 15 ? 'strict' : p.min_qscore === 7 ? 'permissive' : 'standard'),
    };
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
    const [igvLoading, setIgvLoading] = useState(false);
    const [igvError, setIgvError] = useState<string | null>(null);
    const [multimerLoading, setMultimerLoading] = useState(false);
    const [multimerError, setMultimerError] = useState<string | null>(null);
    const [multimerReport, setMultimerReport] = useState<MultimerReportData | null>(null);
    const [methylationLoading, setMethylationLoading] = useState(false);
    const [methylationError, setMethylationError] = useState<string | null>(null);
    const [methylationReport, setMethylationReport] = useState<MethylationReportData | null>(null);
    const [showRawTopLoci, setShowRawTopLoci] = useState(false);
    const [includeBoundaryJunctions, setIncludeBoundaryJunctions] = useState(DEFAULT_INCLUDE_BOUNDARY_JUNCTIONS);
    const [selectedMotifPoint, setSelectedMotifPoint] = useState<SelectedMotifPoint | null>(null);
    const [strandFilter, setStrandFilter] = useState<(typeof METHYLATION_STRAND_FILTERS)[number]>('both');
    const [motifMinCoverage, setMotifMinCoverage] = useState<number>(DEFAULT_MOTIF_MIN_COVERAGE);
    const [requireStrandConcordance, setRequireStrandConcordance] = useState(true);
    const igvContainerRef = useRef<HTMLDivElement | null>(null);
    const igvLoadTokenRef = useRef(0);
    const igvBrowserRef = useRef<any | null>(null);
    const [igvReadsTrackLoaded, setIgvReadsTrackLoaded] = useState(false);
    const [igvReadsTrackLoading, setIgvReadsTrackLoading] = useState(false);
    const themeColors = useThemeColors();
    const basePlotlyLayout = useThemePlotlyLayout();

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
                setInitialValues(normalizeInitialValues(clonedJob));
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
        queryFn: () => fetchJobs({ include_children: true }),
        refetchInterval: 5000,
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
        refetchInterval: selectedJob?.status === 'running' ? 4000 : 15000,
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
    const stageOutputs = (stagePayload?.stage_outputs || {}) as StageOutputsMap;
    const selectedJobParams = (selectedJob?.params || {}) as Record<string, unknown>;
    const selectedReferenceFastaPath = typeof selectedJobParams.reference_fasta === 'string'
        ? selectedJobParams.reference_fasta
        : null;
    const selectedReferenceFastaUrl = selectedReferenceFastaPath
        ? toStreamHref(selectedReferenceFastaPath, selectedJob?.id || undefined)
        : null;
    const hasFastqInput = hasMeaningfulValue(selectedJobParams.fastq_path);
    const hasBamInput = hasMeaningfulValue(selectedJobParams.bam_path);
    const hasPod5Input = hasMeaningfulValue(selectedJobParams.pod5_dir);
    const isFastqOnlyRun = hasFastqInput && !hasBamInput && !hasPod5Input;
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
    const igvReady = !igvArtifacts.missingReason;
    const igvReadinessChecks = useMemo(
        () => [
            {
                label: 'Aligned BAM',
                ok: Boolean(igvArtifacts.bamUrl),
                path: igvArtifacts.bamPath,
            },
            {
                label: 'BAM index (.bai/.csi)',
                ok: Boolean(igvArtifacts.baiUrl),
                path: igvArtifacts.baiPath,
            },
            {
                label: 'Reference FASTA',
                ok: Boolean(igvArtifacts.fastaUrl),
                path: igvArtifacts.fastaPath,
            },
            {
                label: 'Reference FASTA index (.fai)',
                ok: Boolean(igvArtifacts.faiUrl),
                path: igvArtifacts.faiPath,
            },
        ],
        [
            igvArtifacts.bamPath,
            igvArtifacts.bamUrl,
            igvArtifacts.baiPath,
            igvArtifacts.baiUrl,
            igvArtifacts.fastaPath,
            igvArtifacts.fastaUrl,
            igvArtifacts.faiPath,
            igvArtifacts.faiUrl,
        ]
    );
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
    const dimerFastqDownloadHref = multimerArtifacts.dimerFastqPath
        ? toDownloadHref(multimerArtifacts.dimerFastqPath, selectedJob?.id || undefined)
        : null;
    const dimerFastaDownloadHref = multimerArtifacts.dimerFastaPath
        ? toDownloadHref(multimerArtifacts.dimerFastaPath, selectedJob?.id || undefined)
        : null;
    const dimerLengthsDownloadHref = multimerArtifacts.dimerLengthsPath
        ? toDownloadHref(multimerArtifacts.dimerLengthsPath, selectedJob?.id || undefined)
        : null;
    const dimerSummaryDownloadHref = multimerArtifacts.dimerSummaryPath
        ? toDownloadHref(multimerArtifacts.dimerSummaryPath, selectedJob?.id || undefined)
        : null;
    const dimerConsensusDownloadHref = multimerArtifacts.dimerConsensusPath
        ? toDownloadHref(multimerArtifacts.dimerConsensusPath, selectedJob?.id || undefined)
        : null;
    const dominantDimerConsensusDownloadHref = multimerArtifacts.dominantDimerConsensusPath
        ? toDownloadHref(multimerArtifacts.dominantDimerConsensusPath, selectedJob?.id || undefined)
        : null;
    const dominantDimerConsensusMetadataDownloadHref = multimerArtifacts.dominantDimerConsensusMetadataPath
        ? toDownloadHref(multimerArtifacts.dominantDimerConsensusMetadataPath, selectedJob?.id || undefined)
        : null;
    const dimerJunctionDownloadHref = multimerArtifacts.dimerJunctionPath
        ? toDownloadHref(multimerArtifacts.dimerJunctionPath, selectedJob?.id || undefined)
        : null;
    const dimerJunctionEventsDownloadHref = multimerArtifacts.dimerJunctionEventsPath
        ? toDownloadHref(multimerArtifacts.dimerJunctionEventsPath, selectedJob?.id || undefined)
        : null;
    const dimerJunctionClustersDownloadHref = multimerArtifacts.dimerJunctionClustersPath
        ? toDownloadHref(multimerArtifacts.dimerJunctionClustersPath, selectedJob?.id || undefined)
        : null;
    const dimerJunctionHotspotsDownloadHref = multimerArtifacts.dimerJunctionHotspotsPath
        ? toDownloadHref(multimerArtifacts.dimerJunctionHotspotsPath, selectedJob?.id || undefined)
        : null;
    const dimerJunctionRotatedDownloadHref = multimerArtifacts.dimerJunctionRotatedPath
        ? toDownloadHref(multimerArtifacts.dimerJunctionRotatedPath, selectedJob?.id || undefined)
        : null;
    const dimerJunctionRotationSummaryDownloadHref = multimerArtifacts.dimerJunctionRotationSummaryPath
        ? toDownloadHref(multimerArtifacts.dimerJunctionRotationSummaryPath, selectedJob?.id || undefined)
        : null;
    const dimerBreakpointScreenDownloadHref = multimerArtifacts.dimerBreakpointScreenPath
        ? toDownloadHref(multimerArtifacts.dimerBreakpointScreenPath, selectedJob?.id || undefined)
        : null;
    const dimerReadsDownloadHref = multimerArtifacts.dimerReadsPath
        ? toDownloadHref(multimerArtifacts.dimerReadsPath, selectedJob?.id || undefined)
        : null;
    const dimerReadLedgerDownloadHref = multimerArtifacts.dimerReadLedgerPath
        ? toDownloadHref(multimerArtifacts.dimerReadLedgerPath, selectedJob?.id || undefined)
        : null;
    const dimerBreakpointReadsDownloadHref = multimerArtifacts.dimerBreakpointReadsPath
        ? toDownloadHref(multimerArtifacts.dimerBreakpointReadsPath, selectedJob?.id || undefined)
        : null;
    const dimerRotatedRemapSummaryDownloadHref = multimerArtifacts.dimerRotatedRemapSummaryPath
        ? toDownloadHref(multimerArtifacts.dimerRotatedRemapSummaryPath, selectedJob?.id || undefined)
        : null;
    const dimerRotatedRemapBreakpointsDownloadHref = multimerArtifacts.dimerRotatedRemapBreakpointsPath
        ? toDownloadHref(multimerArtifacts.dimerRotatedRemapBreakpointsPath, selectedJob?.id || undefined)
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
    const multimerMetrics = multimerReport?.metrics || {};
    const dimerMetrics = useMemo(
        () => parseNumericMetricsFromSummaryTable(multimerReport?.dimerSummary || null),
        [multimerReport?.dimerSummary]
    );
    const expectedPlasmidSize = Number.isFinite(multimerMetrics.expected_plasmid_size)
        ? multimerMetrics.expected_plasmid_size
        : Number.parseFloat(String(selectedJobParams.expected_plasmid_size ?? ''));
    const dimerSummaryLookup = useMemo(() => {
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
    const readDimerSummaryNumber = useCallback(
        (keys: string[]): number | null => {
            for (const key of keys) {
                const raw = dimerSummaryLookup.get(key.toLowerCase());
                if (raw == null) continue;
                const value = Number.parseFloat(raw);
                if (Number.isFinite(value)) return value;
            }
            return null;
        },
        [dimerSummaryLookup]
    );
    const dimerJunctionEvidenceRows = useMemo(
        () => {
            const clusterRows = multimerReport?.dimerJunctionClusters || [];
            if (clusterRows.length > 0) {
                return [...clusterRows].sort((a, b) => a.positionModRef - b.positionModRef);
            }
            return clustersFromLegacyProfile(multimerReport?.dimerJunctionRows || []);
        },
        [multimerReport?.dimerJunctionClusters, multimerReport?.dimerJunctionRows]
    );
    const dimerNonBoundaryEvidenceRows = useMemo(
        () => dimerJunctionEvidenceRows.filter((row) => row.inBoundaryWindow !== true),
        [dimerJunctionEvidenceRows]
    );
    const dimerDisplayEvidenceRows = useMemo(
        () => (includeBoundaryJunctions ? dimerJunctionEvidenceRows : dimerNonBoundaryEvidenceRows),
        [includeBoundaryJunctions, dimerJunctionEvidenceRows, dimerNonBoundaryEvidenceRows]
    );
    const dimerHotspots = useMemo(
        () => [...dimerDisplayEvidenceRows]
            .sort((a, b) => {
                const crossing = (b.crossingReads || 0) - (a.crossingReads || 0);
                if (crossing !== 0) return crossing;
                const support = (b.supportReads || 0) - (a.supportReads || 0);
                if (support !== 0) return support;
                return a.positionModRef - b.positionModRef;
            })
            .slice(0, 20),
        [dimerDisplayEvidenceRows]
    );
    const topDimerHotspot = dimerHotspots.length > 0 ? dimerHotspots[0] : null;
    const boundaryWindowBp = readDimerSummaryNumber(['boundary_window_bp']);
    const boundaryWindowSupportReads = readDimerSummaryNumber(['boundary_window_support_reads']);
    const boundaryWindowSupportPct = readDimerSummaryNumber(['boundary_window_support_pct']);
    const rotationSelectedOffsetBp = readDimerSummaryNumber(['rotation_selected_offset_bp']);
    const rotationDominantPosRotated = readDimerSummaryNumber(['rotation_dominant_hotspot_position_rotated']);
    const rotationDominantPosModRef = readDimerSummaryNumber(['rotation_dominant_hotspot_position_mod_ref']);
    const rotationBoundarySupportPct = readDimerSummaryNumber(['rotation_selected_boundary_support_pct']);
    const dominantSplitPos = readDimerSummaryNumber(['dominant_split_junction_position_mod_ref']);
    const dominantSplitSupportReads = readDimerSummaryNumber(['dominant_split_junction_support_reads']);
    const dominantSplitSupportPct = readDimerSummaryNumber(['dominant_split_junction_support_pct']);
    const dominantSplitSupportPctOfSplit = readDimerSummaryNumber(['dominant_split_junction_support_pct_of_split']);
    const splitSupportReads = readDimerSummaryNumber(['split_support_reads']);
    const screenedPrimaryBreakpointPos = readDimerSummaryNumber(['screened_primary_breakpoint_position_mod_ref']);
    const screenedPrimaryBreakpointSupportReads = readDimerSummaryNumber(['screened_primary_breakpoint_support_reads']);
    const screenedPrimaryBreakpointBoundaryStartFraction = readDimerSummaryNumber(['screened_primary_breakpoint_boundary_start_fraction']);
    const screenedPrimaryBreakpointSeamFraction = readDimerSummaryNumber(['screened_primary_breakpoint_seam_fraction']);
    const screenedPrimaryBreakpointSplitToSeamRatio = readDimerSummaryNumber(['screened_primary_breakpoint_split_to_seam_ratio']);
    const informativeBreakpointCount = readDimerSummaryNumber(['informative_breakpoint_count']);
    const artifactBreakpointCount = readDimerSummaryNumber(['artifact_breakpoint_count']);
    const seamOnlyUnresolvedFlag = readDimerSummaryNumber(['seam_only_unresolved_flag']);
    const boundaryDominantArtifactFlag = readDimerSummaryNumber(['boundary_dominant_artifact_flag']);
    const screenedPrimaryBreakpointConfidence = (dimerSummaryLookup.get('screened_primary_breakpoint_confidence') || '').trim().toLowerCase();
    const breakpointModelStatus = (dimerSummaryLookup.get('breakpoint_model_status') || '').trim().toLowerCase();
    const splitJunctionHotspots = useMemo(() => {
        const grouped = new Map<number, number>();
        for (const row of multimerReport?.dimerReadJunctions || []) {
            if (row.crossesJunction !== true) continue;
            if (row.positionModRef == null || !Number.isFinite(row.positionModRef) || row.positionModRef <= 0) continue;
            if (!row.method || !row.method.toLowerCase().includes('split')) continue;
            const pos = Math.round(row.positionModRef);
            grouped.set(pos, (grouped.get(pos) || 0) + 1);
        }
        return Array.from(grouped.entries())
            .map(([positionModRef, supportReads]) => ({ positionModRef, supportReads }))
            .sort((a, b) => b.supportReads - a.supportReads || a.positionModRef - b.positionModRef);
    }, [multimerReport?.dimerReadJunctions]);
    const topSplitHotspot = splitJunctionHotspots.length > 0 ? splitJunctionHotspots[0] : null;
    const screenedPrimaryBreakpoint = (screenedPrimaryBreakpointPos != null
        && screenedPrimaryBreakpointSupportReads != null
        && screenedPrimaryBreakpointSupportReads > 0
        && ['high', 'medium'].includes(screenedPrimaryBreakpointConfidence))
        ? {
            positionModRef: Math.round(screenedPrimaryBreakpointPos),
            supportReads: Math.round(screenedPrimaryBreakpointSupportReads),
            supportPercent: dominantSplitSupportPct ?? dominantSplitSupportPctOfSplit ?? null,
            confidence: screenedPrimaryBreakpointConfidence,
        }
        : null;
    const splitSupportedScreenRows = useMemo(
        () => (multimerReport?.dimerBreakpointScreenRows || [])
            .filter((row) => row.splitSupportReads > 0 && row.artifactFlag !== true)
            .filter((row) => {
                const confidence = (row.confidence || '').trim().toLowerCase();
                return confidence === 'high' || confidence === 'medium';
            })
            .sort((a, b) => b.splitSupportReads - a.splitSupportReads || a.positionModRef - b.positionModRef),
        [multimerReport?.dimerBreakpointScreenRows]
    );
    const hasSplitSupportedEvidence = useMemo(() => {
        if (breakpointModelStatus === 'split_supported' || breakpointModelStatus === 'provisional_split_supported') return true;
        return splitSupportedScreenRows.length > 0;
    }, [breakpointModelStatus, splitSupportedScreenRows]);
    const likelyDimerizationLocus = screenedPrimaryBreakpoint || ((dominantSplitPos != null && dominantSplitSupportReads != null && dominantSplitSupportReads >= MIN_CONFIDENT_SPLIT_SUPPORT_READS)
        ? {
            positionModRef: Math.round(dominantSplitPos),
            supportReads: Math.round(dominantSplitSupportReads),
            supportPercent: dominantSplitSupportPct ?? dominantSplitSupportPctOfSplit ?? null,
        }
        : null);
    const boundaryDominantArtifactLikely = useMemo(() => {
        if (boundaryDominantArtifactFlag != null) {
            return Math.round(boundaryDominantArtifactFlag) === 1;
        }
        if (!Number.isFinite(boundaryWindowSupportPct as number)) return false;
        if ((boundaryWindowSupportPct as number) < 30) return false;
        if (likelyDimerizationLocus) return false;
        return true;
    }, [boundaryDominantArtifactFlag, boundaryWindowSupportPct, likelyDimerizationLocus]);
    const multimerClassCounts = useMemo(() => {
        const readMetric = (keys: string[]): number => {
            for (const key of keys) {
                const value = multimerMetrics[key];
                if (Number.isFinite(value)) return Number(value);
            }
            return 0;
        };
        return {
            monomer: readMetric(['monomer_like_reads', 'monomer_reads']),
            dimer: readMetric(['dimer_candidate_reads', 'dimer_reads']),
            trimer: readMetric(['trimer_reads']),
            highOrder: readMetric(['multimer_candidate_reads', 'tetramer_plus_reads']),
        };
    }, [multimerMetrics]);
    const dimerCandidateReads = Number.isFinite(dimerMetrics.dimer_candidate_reads)
        ? Math.round(dimerMetrics.dimer_candidate_reads)
        : Math.round(multimerClassCounts.dimer);
    const alignedDimerReads = Number.isFinite(dimerMetrics.aligned_dimer_reads)
        ? Math.round(dimerMetrics.aligned_dimer_reads)
        : null;
    const junctionSpanningReads = Number.isFinite(dimerMetrics.junction_spanning_reads)
        ? Math.round(dimerMetrics.junction_spanning_reads)
        : (() => {
            const totalCrossing = dimerJunctionEvidenceRows.reduce((sum, row) => sum + Math.max(0, row.crossingReads || 0), 0);
            return totalCrossing > 0 ? totalCrossing : null;
        })();
    const splitEvidencePending = !hasSplitSupportedEvidence
        && ((junctionSpanningReads ?? 0) > 0 || (dimerCandidateReads ?? 0) > 0);
    const dimerReferenceLength = Number.isFinite(dimerMetrics.reference_length)
        ? Math.round(dimerMetrics.reference_length)
        : null;
    const dimerHotspotRows = useMemo(
        () => dimerHotspots.map((row) => {
            const sequence = multimerReport?.referenceSequence || '';
            const window = sequence && row.positionModRef > 0 && row.positionModRef <= sequence.length
                ? formatCircularJunctionWindow(sequence, row.positionModRef, 50)
                : null;
            const denominator = dimerCandidateReads > 0 ? dimerCandidateReads : 0;
            const supportSource = row.crossingReads > 0 ? row.crossingReads : row.supportReads;
            const supportPercent = denominator > 0
                ? ((supportSource / denominator) * 100)
                : (row.supportPercent != null ? row.supportPercent : null);
            return {
                ...row,
                supportPercent,
                window100bp: window?.text || null,
                windowLabel: window?.label || null,
            };
        }),
        [dimerHotspots, multimerReport?.referenceSequence, dimerCandidateReads]
    );
    const dimerBreakpointScreenTopRows = useMemo(
        () => (multimerReport?.dimerBreakpointScreenRows || []).slice(0, 20),
        [multimerReport?.dimerBreakpointScreenRows]
    );
    const dimerReferenceSequenceRows = useMemo(
        () => buildSequenceRows(multimerReport?.referenceSequence || '', REFERENCE_SEQUENCE_LINE_WIDTH),
        [multimerReport?.referenceSequence]
    );
    const dimerMaxSupportReads = useMemo(
        () => dimerDisplayEvidenceRows.reduce((max, row) => {
            const supportSource = row.crossingReads > 0 ? row.crossingReads : row.supportReads;
            return Math.max(max, Math.max(0, supportSource || 0));
        }, 0),
        [dimerDisplayEvidenceRows]
    );
    const dimerSequenceHighlightsByPosition = useMemo(() => {
        const highlights = new Map<number, DimerSequenceHighlight>();
        const sequenceLen = (multimerReport?.referenceSequence || '').length;
        const denominator = dimerCandidateReads > 0 ? dimerCandidateReads : null;

        for (const row of dimerDisplayEvidenceRows) {
            const position = Math.round(row.positionModRef || 0);
            if (!Number.isFinite(position) || position <= 0) continue;
            if (sequenceLen > 0 && position > sequenceLen) continue;

            const supportSource = Math.max(0, row.crossingReads > 0 ? row.crossingReads : row.supportReads);
            if (supportSource <= 0) continue;
            const supportPercent = denominator ? ((supportSource / denominator) * 100) : null;
            const alpha = readCountToHighlightAlpha(supportSource, dimerMaxSupportReads);
            const highlight: DimerSequenceHighlight = {
                readCount: supportSource,
                supportPercent,
                backgroundColor: toAlphaColor(themeColors.warning, alpha),
            };
            const existing = highlights.get(position);
            if (!existing || highlight.readCount > existing.readCount) {
                highlights.set(position, highlight);
            }
        }
        return highlights;
    }, [
        dimerDisplayEvidenceRows,
        multimerReport?.referenceSequence,
        dimerCandidateReads,
        dimerMaxSupportReads,
        themeColors.warning,
    ]);
    const dimerReadTableHasMethod = useMemo(
        () => (multimerReport?.dimerReadJunctions || []).some((row) => Boolean(row.method)),
        [multimerReport?.dimerReadJunctions]
    );
    const dimerReadTableHasOrientation = useMemo(
        () => (multimerReport?.dimerReadJunctions || []).some((row) => Boolean(row.orientation)),
        [multimerReport?.dimerReadJunctions]
    );
    const dimerReadTableHasMissingLeft = useMemo(
        () => (multimerReport?.dimerReadJunctions || []).some((row) => row.missingLeftBp != null),
        [multimerReport?.dimerReadJunctions]
    );
    const dimerReadTableHasMissingRight = useMemo(
        () => (multimerReport?.dimerReadJunctions || []).some((row) => row.missingRightBp != null),
        [multimerReport?.dimerReadJunctions]
    );
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
    const dimerJunctionPlotData = useMemo<Data[]>(() => {
        const rows = dimerDisplayEvidenceRows;
        if (rows.length === 0) return [];

        const denominator = dimerCandidateReads > 0 ? dimerCandidateReads : null;
        const byPosition = new Map<number, {
            crossingReads: number;
            supportReads: number;
            supportPercent: number | null;
            method: string;
            orientation: string;
            source: string;
            inBoundaryWindow: boolean | null;
        }>();

        for (const row of rows) {
            if (!Number.isFinite(row.positionModRef) || row.positionModRef <= 0) continue;
            const pos = Math.round(row.positionModRef);
            const crossingReads = Number.isFinite(row.crossingReads) ? Math.max(0, row.crossingReads) : 0;
            const supportReads = Number.isFinite(row.supportReads) ? Math.max(0, row.supportReads) : 0;
            const supportSource = crossingReads > 0 ? crossingReads : supportReads;
            // For this plot, support % must mean fraction of all dimer reads.
            // Cluster TSV `support_percent` is crossing/read_count (often ~100%) and
            // causes misleading spikes if used directly.
            const supportPercent = denominator
                ? (supportSource / denominator) * 100
                : (
                    Number.isFinite(row.supportPercent as number)
                        ? Number(row.supportPercent)
                        : null
                );

            const existing = byPosition.get(pos);
            if (!existing) {
                byPosition.set(pos, {
                    crossingReads,
                    supportReads,
                    supportPercent,
                    method: row.method || '',
                    orientation: row.orientation || '',
                    source: row.source || '',
                    inBoundaryWindow: row.inBoundaryWindow ?? null,
                });
                continue;
            }

            existing.crossingReads = Math.max(existing.crossingReads, crossingReads);
            existing.supportReads = Math.max(existing.supportReads, supportReads);
            if (supportPercent != null) {
                existing.supportPercent = existing.supportPercent == null
                    ? supportPercent
                    : Math.max(existing.supportPercent, supportPercent);
            }
            if (!existing.method && row.method) existing.method = row.method;
            if (!existing.orientation && row.orientation) existing.orientation = row.orientation;
            if (!existing.source && row.source) existing.source = row.source;
            if (existing.inBoundaryWindow == null) existing.inBoundaryWindow = row.inBoundaryWindow ?? null;
        }

        const aggregatedRows = Array.from(byPosition.entries())
            .map(([positionModRef, value]) => ({
                positionModRef,
                ...value,
            }))
            .sort((a, b) => a.positionModRef - b.positionModRef);

        if (aggregatedRows.length === 0) return [];

        const referenceSpan = Number.isFinite(dimerReferenceLength as number) && (dimerReferenceLength as number) > 0
            ? Math.round(dimerReferenceLength as number)
            : (Number.isFinite(multimerReport?.referenceLength as number) && (multimerReport?.referenceLength as number) > 0
                ? Math.round(multimerReport?.referenceLength as number)
                : Math.round(aggregatedRows[aggregatedRows.length - 1]?.positionModRef || 0) || null);
        const boundaryW = Number.isFinite(boundaryWindowBp as number) && (boundaryWindowBp as number) > 0
            ? Math.round(boundaryWindowBp as number)
            : null;

        let denseRows = aggregatedRows;
        if (referenceSpan && referenceSpan <= 25000) {
            const byPosDense = new Map<number, typeof aggregatedRows[number]>();
            for (const row of aggregatedRows) {
                byPosDense.set(row.positionModRef, row);
            }
            denseRows = [];
            for (let pos = 1; pos <= referenceSpan; pos++) {
                const row = byPosDense.get(pos);
                if (row) {
                    denseRows.push(row);
                    continue;
                }
                denseRows.push({
                    positionModRef: pos,
                    crossingReads: 0,
                    supportReads: 0,
                    supportPercent: 0,
                    method: '',
                    orientation: '',
                    source: 'profile',
                    inBoundaryWindow: boundaryW != null ? (pos <= boundaryW || pos > (referenceSpan - boundaryW)) : null,
                });
            }
        }

        const MAX_PLOT_POINTS = 12000;
        const nonZeroRows = denseRows.filter((row) => (row.crossingReads > 0 || row.supportReads > 0));
        const zeroRows = denseRows.filter((row) => (row.crossingReads <= 0 && row.supportReads <= 0));

        let plotRows = denseRows;
        if (denseRows.length > MAX_PLOT_POINTS) {
            if (nonZeroRows.length >= MAX_PLOT_POINTS) {
                // Keep informative points only when signal is dense.
                plotRows = nonZeroRows.slice(0, MAX_PLOT_POINTS).sort((a, b) => a.positionModRef - b.positionModRef);
            } else {
                // Preserve all signal peaks and sample the long zero runs for continuity.
                const keepZeros = Math.max(0, MAX_PLOT_POINTS - nonZeroRows.length);
                const zeroStride = keepZeros > 0 ? Math.ceil(zeroRows.length / keepZeros) : Number.MAX_SAFE_INTEGER;
                const sampledZeroRows = keepZeros > 0
                    ? zeroRows.filter((_, idx) => idx % zeroStride === 0).slice(0, keepZeros)
                    : [];
                plotRows = [...nonZeroRows, ...sampledZeroRows].sort((a, b) => a.positionModRef - b.positionModRef);
            }
        }

        const barRows = plotRows.filter((row) => (row.crossingReads > 0 || row.supportReads > 0));
        const xValues = plotRows.map((row) => row.positionModRef);
        const yCrossing = barRows.map((row) => (row.crossingReads > 0 ? row.crossingReads : row.supportReads));
        const ySupportPercent = plotRows.map((row) => (
            Number.isFinite(row.supportPercent as number) ? row.supportPercent : 0
        ));

        return [
            {
                type: 'bar',
                name: 'Crossing support',
                x: barRows.map((row) => row.positionModRef),
                y: yCrossing,
                marker: {
                    color: barRows.map((row) => (
                        row.inBoundaryWindow === true ? themeColors.warning : themeColors.accentPrimary
                    )),
                    line: { color: themeColors.textPrimary, width: 0.5 },
                },
                customdata: barRows.map((row) => [
                    row.crossingReads,
                    row.supportReads,
                    Number.isFinite(row.supportPercent as number) ? row.supportPercent : 0,
                    row.method || '',
                    row.orientation || '',
                    row.source,
                    row.inBoundaryWindow === true ? 'boundary-window' : 'non-boundary',
                ]),
                hovertemplate: [
                    'Junction position: %{x}',
                    'Crossing reads: %{customdata[0]}',
                    'Support reads: %{customdata[1]}',
                    'Support %: %{customdata[2]:.2f}%',
                    'Method: %{customdata[3]}',
                    'Orientation: %{customdata[4]}',
                    'Source: %{customdata[5]}',
                    'Region: %{customdata[6]}',
                    '<extra></extra>',
                ].join('<br>'),
            },
            {
                type: 'scatter',
                mode: 'lines',
                name: 'Support (% of dimer reads)',
                x: xValues,
                y: ySupportPercent,
                line: { color: themeColors.warning, width: 2 },
                connectgaps: true,
                hovertemplate: 'Junction position: %{x}<br>Support: %{y:.2f}%<extra></extra>',
                yaxis: 'y2',
            },
        ];
    }, [
        dimerDisplayEvidenceRows,
        dimerCandidateReads,
        dimerReferenceLength,
        multimerReport?.referenceLength,
        boundaryWindowBp,
        themeColors.accentPrimary,
        themeColors.warning,
        themeColors.textPrimary,
    ]);
    const dimerJunctionLayout = useMemo<Partial<Layout>>(() => ({
        ...basePlotlyLayout,
        margin: { l: 44, r: 44, t: 20, b: 48 },
        legend: {
            orientation: 'h',
            x: 0,
            xanchor: 'left',
            y: 1.02,
            yanchor: 'bottom',
            font: { color: themeColors.textSecondary, size: 11 },
        },
        xaxis: {
            title: { text: 'Junction Position on Reference (bp)', font: { color: themeColors.textSecondary } },
            tickfont: { color: themeColors.textSecondary },
            gridcolor: `${themeColors.borderPrimary}55`,
            ...(Number.isFinite(dimerReferenceLength as number) && (dimerReferenceLength as number) > 0
                ? { range: [1, Math.round(dimerReferenceLength as number)] as [number, number] }
                : {}),
        },
        yaxis: {
            title: { text: 'Crossing Read Count', font: { color: themeColors.textSecondary } },
            tickfont: { color: themeColors.textSecondary },
            gridcolor: `${themeColors.borderPrimary}66`,
            rangemode: 'tozero',
        },
        yaxis2: {
            title: { text: 'Support (% of dimer reads)', font: { color: themeColors.textSecondary } },
            tickfont: { color: themeColors.textSecondary },
            overlaying: 'y',
            side: 'right',
            rangemode: 'tozero',
            ticksuffix: '%',
        },
    }), [basePlotlyLayout, themeColors, dimerReferenceLength]);
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
                || multimerArtifacts.dimerJunctionUrl
                || multimerArtifacts.dimerJunctionEventsUrl
                || multimerArtifacts.dimerJunctionClustersUrl
                || multimerArtifacts.dimerJunctionHotspotsUrl
                || multimerArtifacts.dimerReadsUrl
                || multimerArtifacts.dimerReadLedgerUrl
                || multimerArtifacts.dimerBreakpointReadsUrl
                || multimerArtifacts.dimerRotatedRemapSummaryUrl
                || multimerArtifacts.dimerRotatedRemapBreakpointsUrl
                || multimerArtifacts.dimerConsensusUrl
                || multimerArtifacts.dominantDimerConsensusUrl
                || multimerArtifacts.dominantDimerConsensusMetadataUrl
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
                let dimerJunctionRows: DimerJunctionProfileRow[] = [];
                let dimerJunctionClusters: DimerJunctionClusterRow[] = [];
                let dimerJunctionHotspots: DimerJunctionClusterRow[] = [];
                let dimerBreakpointScreenRows: DimerBreakpointScreenRow[] = [];
                let dimerReadJunctions: DimerReadJunctionRow[] = [];
                let legacyDimerReadRows: DimerReadJunctionRow[] = [];
                let ledgerDimerReadRows: DimerReadJunctionRow[] = [];
                let eventDimerReadRows: DimerReadJunctionRow[] = [];
                let dimerConsensusPreview: string | null = null;
                let dominantDimerConsensusPreview: string | null = null;
                let dominantDimerConsensusMetadata: SummaryTable | null = null;
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

                if (multimerArtifacts.dimerJunctionUrl) {
                    try {
                        const dimerJunctionText = await fetchTextRange(multimerArtifacts.dimerJunctionUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        dimerJunctionRows = parseDimerJunctionProfile(dimerJunctionText, 5000);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer junction profile unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (multimerArtifacts.dimerReadsUrl) {
                    try {
                        const dimerReadsText = await fetchTextRange(multimerArtifacts.dimerReadsUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        legacyDimerReadRows = parseDimerReadJunctions(dimerReadsText, 1500);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer read-junction table unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }
                if (multimerArtifacts.dimerReadLedgerUrl) {
                    try {
                        const dimerLedgerText = await fetchTextRange(multimerArtifacts.dimerReadLedgerUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        ledgerDimerReadRows = parseDimerReadJunctions(dimerLedgerText, 5000);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer full read ledger unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (multimerArtifacts.dimerJunctionEventsUrl) {
                    try {
                        const dimerEventsText = await fetchTextRange(multimerArtifacts.dimerJunctionEventsUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        eventDimerReadRows = parseDimerJunctionEvents(dimerEventsText, 1500);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer junction events unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (multimerArtifacts.dimerJunctionClustersUrl) {
                    try {
                        const dimerClustersText = await fetchTextRange(multimerArtifacts.dimerJunctionClustersUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        dimerJunctionClusters = parseDimerJunctionClusters(dimerClustersText, 5000);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer junction clusters unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }
                if (multimerArtifacts.dimerJunctionHotspotsUrl) {
                    try {
                        const dimerHotspotsText = await fetchTextRange(multimerArtifacts.dimerJunctionHotspotsUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        dimerJunctionHotspots = parseDimerJunctionClusters(dimerHotspotsText, 5000, 'hotspots');
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer hotspot table unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }
                if (multimerArtifacts.dimerBreakpointScreenUrl) {
                    try {
                        const dimerBreakpointText = await fetchTextRange(multimerArtifacts.dimerBreakpointScreenUrl, MULTIMER_CANDIDATES_MAX_BYTES);
                        dimerBreakpointScreenRows = parseDimerBreakpointScreen(dimerBreakpointText, 5000);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dimer breakpoint screen unavailable (${err instanceof Error ? err.message : String(err)})`);
                        }
                    }
                }

                if (ledgerDimerReadRows.length > 0) {
                    const eventByReadId = new Map<string, DimerReadJunctionRow>();
                    for (const row of eventDimerReadRows) {
                        if (!row.readId) continue;
                        eventByReadId.set(row.readId, row);
                    }
                    dimerReadJunctions = ledgerDimerReadRows.map((row) => {
                        if (!row.readId) return row;
                        const eventRow = eventByReadId.get(row.readId);
                        if (!eventRow) return row;
                        return {
                            ...row,
                            start: eventRow.start ?? row.start,
                            end: eventRow.end ?? row.end,
                            positionModRef: eventRow.positionModRef ?? row.positionModRef,
                            crossesJunction: eventRow.crossesJunction ?? row.crossesJunction,
                            method: eventRow.method ?? row.method,
                            orientation: eventRow.orientation ?? row.orientation,
                            missingLeftBp: eventRow.missingLeftBp ?? row.missingLeftBp,
                            missingRightBp: eventRow.missingRightBp ?? row.missingRightBp,
                            source: eventRow.source,
                        };
                    });
                } else {
                    dimerReadJunctions = eventDimerReadRows.length > 0 ? eventDimerReadRows : legacyDimerReadRows;
                }
                if (dimerJunctionClusters.length === 0) {
                    if (eventDimerReadRows.length > 0) {
                        dimerJunctionClusters = clustersFromEvents(eventDimerReadRows);
                    } else if (dimerJunctionRows.length > 0) {
                        dimerJunctionClusters = clustersFromLegacyProfile(dimerJunctionRows);
                    }
                }
                if (dimerJunctionRows.length === 0 && dimerJunctionClusters.length > 0) {
                    dimerJunctionRows = dimerJunctionClusters
                        .map((row) => ({
                            positionModRef: row.positionModRef,
                            readCount: row.supportReads,
                            spanningReads: row.crossingReads,
                        }))
                        .sort((a, b) => a.positionModRef - b.positionModRef);
                }

                if (dimerJunctionHotspots.length > 0) {
                    const hotspotByPos = new Map<number, DimerJunctionClusterRow>();
                    for (const hotspot of dimerJunctionHotspots) {
                        hotspotByPos.set(hotspot.positionModRef, hotspot);
                    }

                    if (dimerJunctionClusters.length > 0) {
                        const mergedPositions = new Set<number>();
                        dimerJunctionClusters = dimerJunctionClusters.map((row) => {
                            const hotspot = hotspotByPos.get(row.positionModRef);
                            if (!hotspot) return row;
                            mergedPositions.add(row.positionModRef);
                            return {
                                ...row,
                                supportPercent: row.supportPercent ?? hotspot.supportPercent,
                                inBoundaryWindow: hotspot.inBoundaryWindow ?? row.inBoundaryWindow,
                            };
                        });
                        for (const hotspot of dimerJunctionHotspots) {
                            if (mergedPositions.has(hotspot.positionModRef)) continue;
                            dimerJunctionClusters.push(hotspot);
                        }
                        dimerJunctionClusters.sort((a, b) => a.positionModRef - b.positionModRef);
                    } else {
                        dimerJunctionClusters = dimerJunctionHotspots;
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
                if (multimerArtifacts.dominantDimerConsensusMetadataUrl) {
                    try {
                        const dominantMetadataText = await fetchTextRange(multimerArtifacts.dominantDimerConsensusMetadataUrl, MULTIMER_SUMMARY_MAX_BYTES);
                        dominantDimerConsensusMetadata = parseSummaryTable(dominantMetadataText);
                    } catch (err) {
                        if (!isFetchNotFoundError(err)) {
                            warnings.push(`Dominant dimer consensus metadata unavailable (${err instanceof Error ? err.message : String(err)})`);
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
                    dimerJunctionRows,
                    dimerJunctionClusters,
                    dimerBreakpointScreenRows,
                    dimerReadJunctions,
                    dimerConsensusPreview,
                    dominantDimerConsensusPreview,
                    dominantDimerConsensusMetadata,
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
    }, [
        selectedJob?.id,
        shouldShowMultimerInspector,
        multimerArtifacts.summaryUrl,
        multimerArtifacts.lengthsUrl,
        multimerArtifacts.candidatesUrl,
        multimerArtifacts.dimerSummaryUrl,
        multimerArtifacts.dimerJunctionUrl,
        multimerArtifacts.dimerJunctionEventsUrl,
        multimerArtifacts.dimerJunctionClustersUrl,
        multimerArtifacts.dimerJunctionHotspotsUrl,
        multimerArtifacts.dimerBreakpointScreenUrl,
        multimerArtifacts.dimerReadsUrl,
        multimerArtifacts.dimerConsensusUrl,
        multimerArtifacts.dominantDimerConsensusUrl,
        multimerArtifacts.dominantDimerConsensusMetadataUrl,
        selectedReferenceFastaUrl,
        multimerArtifacts.missingReason,
    ]);

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
                    if (igvArtifacts.fastaUrl) {
                        try {
                            const fastaText = await fetchTextRange(igvArtifacts.fastaUrl, REFERENCE_FASTA_MAX_BYTES);
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
    }, [
        selectedJob?.id,
        shouldShowMethylationInspector,
        methylationArtifacts.summaryUrl,
        methylationArtifacts.bedUrl,
        methylationArtifacts.missingReason,
        igvArtifacts.fastaUrl,
    ]);

    useEffect(() => {
        if (!igvModalOpen) return;
        if (!igvReady) {
            setIgvLoading(false);
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            setIgvError(igvArtifacts.missingReason);
            return;
        }
        if (!igvContainerRef.current) {
            setIgvLoading(false);
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            return;
        }

        let cancelled = false;
        let igvBrowser: any = null;
        const loadToken = ++igvLoadTokenRef.current;
        const isCurrentLoad = () => igvLoadTokenRef.current === loadToken;

        const initIgv = async () => {
            setIgvLoading(true);
            setIgvError(null);
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            if (igvContainerRef.current) {
                igvContainerRef.current.innerHTML = '';
            }

            try {
                const igv = await withTimeout(
                    loadIgvLibrary(),
                    IGV_INIT_TIMEOUT_MS,
                    `IGV initialization timed out after ${Math.round(IGV_INIT_TIMEOUT_MS / 1000)}s while loading script`
                );
                if (cancelled || !igvContainerRef.current) return;
                const initialLocus = await withTimeout(
                    detectInitialLocusFromFasta(igvArtifacts.fastaUrl),
                    Math.max(5000, Math.floor(IGV_INIT_TIMEOUT_MS / 2)),
                    `IGV initialization timed out after ${Math.round(IGV_INIT_TIMEOUT_MS / 1000)}s while preparing reference`
                );
                if (cancelled || !igvContainerRef.current) return;

                igvBrowser = await withTimeout(
                    igv.createBrowser(igvContainerRef.current, {
                        ...(initialLocus ? { locus: initialLocus } : {}),
                        reference: {
                            fastaURL: igvArtifacts.fastaUrl,
                            indexURL: igvArtifacts.faiUrl,
                            indexed: true,
                        },
                        tracks: [],
                    }),
                    IGV_INIT_TIMEOUT_MS,
                    `IGV initialization timed out after ${Math.round(IGV_INIT_TIMEOUT_MS / 1000)}s`
                );
                igvBrowserRef.current = igvBrowser;

                if (isCurrentLoad() && !cancelled) {
                    setIgvLoading(false);
                }

                if (!cancelled && initialLocus && igvBrowser && typeof igvBrowser.search === 'function') {
                    // Do not block modal readiness on async locus search; some datasets can make this slow.
                    void igvBrowser.search(initialLocus).catch(() => {
                        // keep viewer open even if locus search fails
                    });
                }
            } catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                if (isCurrentLoad()) {
                    const needsScriptHint = /igv-script|load igv script|window\.igv|unavailable/i.test(msg);
                    const suffix = needsScriptHint
                        ? ' Ensure local igv.min.js is available to /api/files/igv-script.'
                        : '';
                    setIgvError(`Failed to initialize IGV viewer: ${msg}.${suffix}`);
                }
                try {
                    igvBrowser?.dispose?.();
                } catch {
                    // no-op
                }
                igvBrowserRef.current = null;
            } finally {
                if (isCurrentLoad() && !cancelled) {
                    setIgvLoading(false);
                }
            }
        };

        initIgv();

        return () => {
            cancelled = true;
            try {
                igvBrowser?.dispose?.();
            } catch {
                // no-op
            }
            igvBrowserRef.current = null;
            setIgvReadsTrackLoaded(false);
            setIgvReadsTrackLoading(false);
            if (igvContainerRef.current) {
                igvContainerRef.current.innerHTML = '';
            }
        };
    }, [
        igvModalOpen,
        igvReady,
        igvArtifacts.fastaUrl,
        igvArtifacts.faiUrl,
        igvArtifacts.missingReason,
    ]);

    const handleLoadIgvReadsTrack = useCallback(async () => {
        if (igvReadsTrackLoaded || igvReadsTrackLoading) return;
        const browser = igvBrowserRef.current;
        if (!browser || typeof browser.loadTrack !== 'function') {
            setIgvError('IGV browser is not ready yet.');
            return;
        }
        if (!igvArtifacts.bamUrl || !igvArtifacts.baiUrl) {
            setIgvError('Aligned BAM and index are required to load reads track.');
            return;
        }
        setIgvReadsTrackLoading(true);
        setIgvError(null);
        try {
            await browser.loadTrack({
                name: 'Aligned Reads',
                type: 'alignment',
                format: 'bam',
                url: igvArtifacts.bamUrl,
                indexURL: igvArtifacts.baiUrl,
                showSoftClips: true,
                showCoverage: false,
                // FASTQ/dimer runs are typically small enough to render across full plasmids.
                // A tiny visibilityWindow can make tracks appear "empty" until deep zoom.
                visibilityWindow: -1,
                samplingWindowSize: 40,
                samplingDepth: 2000,
                maxRows: 60,
            });
            if (typeof browser.search === 'function') {
                const locus = await detectInitialLocusFromFasta(igvArtifacts.fastaUrl);
                if (locus) {
                    await browser.search(locus);
                }
            }
            setIgvReadsTrackLoaded(true);
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            setIgvError(`Failed to load aligned reads track: ${msg}`);
        } finally {
            setIgvReadsTrackLoading(false);
        }
    }, [igvReadsTrackLoaded, igvReadsTrackLoading, igvArtifacts.bamUrl, igvArtifacts.baiUrl]);

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
                        <h1 className="text-3xl font-bold text-[var(--text-primary)]">NGS Data Visualization Toolkit</h1>
                        <p className="text-[var(--text-secondary)]">
                            Walled garden for Nanopore orchestration and run monitoring
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setView('launch')}
                            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${view === 'launch'
                                ? 'text-white'
                                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
                                }`}
                            style={view === 'launch' ? { backgroundColor: 'var(--accent-secondary)' } : undefined}
                        >
                            Launch
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
            </header>

            {view === 'launch' ? (
                <NanoporeTemplate
                    onBack={() => setView('runs')}
                    initialValues={initialValues}
                />
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
                                placeholder="Search nanopore jobs by name or ID..."
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
                                        <th className="text-left px-4 py-2">Status</th>
                                        <th className="text-left px-4 py-2">Stage</th>
                                        <th className="text-left px-4 py-2">Created</th>
                                        <th className="text-left px-4 py-2">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {isLoading ? (
                                        <tr>
                                            <td colSpan={5} className="px-4 py-6 text-center text-[var(--text-secondary)]">Loading nanopore jobs...</td>
                                        </tr>
                                    ) : filteredJobs.length === 0 ? (
                                        <tr>
                                            <td colSpan={5} className="px-4 py-6 text-center text-[var(--text-secondary)]">No nanopore jobs found for current filters.</td>
                                        </tr>
                                    ) : (
                                        filteredJobs.map((job) => (
                                            <tr
                                                key={job.id}
                                                className={`border-t border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)] ${selectedJobId === job.id ? 'bg-[var(--bg-tertiary)]' : ''}`}
                                            >
                                                <td className="px-4 py-2 text-[var(--text-primary)]">{job.name}</td>
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
                                                                setInitialValues(normalizeInitialValues(job));
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
                                        onClick={() => setIgvModalOpen(true)}
                                        title={igvArtifacts.missingReason || 'Open IGV genome viewer'}
                                        className="px-3 py-1.5 text-xs rounded border transition-colors text-[var(--text-primary)] border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)]"
                                    >
                                        Open IGV
                                    </button>
                                    <span className="text-xs text-[var(--text-secondary)]">{selectedJob.id}</span>
                                </div>
                            )}
                        </div>

                        {!selectedJob ? (
                            <p className="text-sm text-[var(--text-secondary)]">Select a nanopore run to inspect parameters, stage progress, and artifacts.</p>
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
                                        ['FASTQ path', selectedJob.params?.fastq_path],
                                        ['Reference FASTA', selectedJob.params?.reference_fasta],
                                        ['Dorado model', selectedJob.params?.dorado_model],
                                        ['Modified bases', selectedJob.params?.modified_bases],
                                        ['Min qscore', selectedJob.params?.min_qscore],
                                        ['Trim adapters', selectedJob.params?.trim_adapters],
                                        ['Run modkit', selectedJob.params?.run_modkit],
                                        ['Run multimer QC', selectedJob.params?.run_multimer_qc],
                                        ['Expected plasmid size', selectedJob.params?.expected_plasmid_size],

                                        ['Min FASTQ read length', selectedJob.params?.min_fastq_read_length],
                                        ['Run assembly', selectedJob.params?.run_assembly],
                                        ['Assembly tool', selectedJob.params?.wf_clone_assembly_tool],
                                        ['Approx size (bp)', selectedJob.params?.wf_clone_approx_size],
                                        ['Assembly coverage', selectedJob.params?.wf_clone_assm_coverage],
                                        ['Assembly trim length', selectedJob.params?.wf_clone_trim_length],
                                        ['Assembly min quality', selectedJob.params?.wf_clone_min_quality],
                                        ['wf-clone workflow dir', selectedJob.params?.wf_clone_workflow_dir],
                                        ['wf-clone source', selectedJob.params?.wf_clone_source],
                                        ['wf-clone revision', selectedJob.params?.wf_clone_revision],
                                        ['wf-clone profile', selectedJob.params?.wf_clone_profile],
                                        ['wf-clone sample', selectedJob.params?.wf_clone_sample],
                                        ['Large construct mode', selectedJob.params?.wf_clone_large_construct],
                                    ].map(([label, value]) => (
                                        <div key={label} className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3">
                                            <div className="text-xs text-[var(--text-secondary)] mb-1">{label}</div>
                                            <div className="text-[var(--text-primary)] text-sm break-all">{formatParamValue(value)}</div>
                                        </div>
                                    ))}
                                </div>

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
                                    {!igvReady && (
                                        <p className="text-xs text-[var(--text-secondary)]">
                                            IGV unavailable: {igvArtifacts.missingReason}
                                        </p>
                                    )}
                                    {isFastqOnlyRun && !igvReady && (
                                        <p className="text-xs text-[var(--text-secondary)]">
                                            FASTQ-only runs require `reference_fasta` + `fastq_align` outputs to inspect alignments in IGV.
                                        </p>
                                    )}
                                </div>

                                {shouldShowMultimerInspector && (
                                    <div className="space-y-2">
                                        <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">FASTQ Multimer QC</h4>
                                        {multimerLoading ? (
                                            <p className="text-sm text-[var(--text-secondary)]">Loading multimer QC outputs...</p>
                                        ) : multimerReport === null ? (
                                            <p className="text-sm text-[var(--text-secondary)]">
                                                {multimerError || multimerArtifacts.missingReason || 'No multimer QC outputs available for this run.'}
                                            </p>
                                        ) : (
                                            <div className="space-y-3">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    {multimerSummaryDownloadHref && (
                                                        <a
                                                            href={multimerSummaryDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download multimer summary
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
                                                            Download candidate sites
                                                        </a>
                                                    )}
                                                    {dimerFastqDownloadHref && (
                                                        <a
                                                            href={dimerFastqDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dimer FASTQ
                                                        </a>
                                                    )}
                                                    {dimerFastaDownloadHref && (
                                                        <a
                                                            href={dimerFastaDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dimer FASTA
                                                        </a>
                                                    )}
                                                    {dimerLengthsDownloadHref && (
                                                        <a
                                                            href={dimerLengthsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dimer lengths
                                                        </a>
                                                    )}
                                                    {dimerSummaryDownloadHref && (
                                                        <a
                                                            href={dimerSummaryDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dimer summary
                                                        </a>
                                                    )}
                                                    {dimerConsensusDownloadHref && (
                                                        <a
                                                            href={dimerConsensusDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dimer consensus
                                                        </a>
                                                    )}
                                                    {dominantDimerConsensusDownloadHref && (
                                                        <a
                                                            href={dominantDimerConsensusDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dominant dimer consensus
                                                        </a>
                                                    )}
                                                    {dominantDimerConsensusMetadataDownloadHref && (
                                                        <a
                                                            href={dominantDimerConsensusMetadataDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download dominant consensus metadata
                                                        </a>
                                                    )}
                                                    {dimerJunctionDownloadHref && (
                                                        <a
                                                            href={dimerJunctionDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download junction profile
                                                        </a>
                                                    )}
                                                    {dimerJunctionEventsDownloadHref && (
                                                        <a
                                                            href={dimerJunctionEventsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download junction events
                                                        </a>
                                                    )}
                                                    {dimerJunctionClustersDownloadHref && (
                                                        <a
                                                            href={dimerJunctionClustersDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download junction clusters
                                                        </a>
                                                    )}
                                                    {dimerJunctionHotspotsDownloadHref && (
                                                        <a
                                                            href={dimerJunctionHotspotsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download hotspot table
                                                        </a>
                                                    )}
                                                    {dimerJunctionRotatedDownloadHref && (
                                                        <a
                                                            href={dimerJunctionRotatedDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download rotated profile
                                                        </a>
                                                    )}
                                                    {dimerJunctionRotationSummaryDownloadHref && (
                                                        <a
                                                            href={dimerJunctionRotationSummaryDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download rotation summary
                                                        </a>
                                                    )}
                                                    {dimerBreakpointScreenDownloadHref && (
                                                        <a
                                                            href={dimerBreakpointScreenDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download breakpoint screen
                                                        </a>
                                                    )}
                                                    {dimerReadsDownloadHref && (
                                                        <a
                                                            href={dimerReadsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download read-junction table
                                                        </a>
                                                    )}
                                                    {dimerReadLedgerDownloadHref && (
                                                        <a
                                                            href={dimerReadLedgerDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download full read ledger
                                                        </a>
                                                    )}
                                                    {dimerBreakpointReadsDownloadHref && (
                                                        <a
                                                            href={dimerBreakpointReadsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download breakpoint read list
                                                        </a>
                                                    )}
                                                    {dimerRotatedRemapSummaryDownloadHref && (
                                                        <a
                                                            href={dimerRotatedRemapSummaryDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download frame-scan summary
                                                        </a>
                                                    )}
                                                    {dimerRotatedRemapBreakpointsDownloadHref && (
                                                        <a
                                                            href={dimerRotatedRemapBreakpointsDownloadHref}
                                                            className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                        >
                                                            Download frame-scan breakpoints
                                                        </a>
                                                    )}
                                                </div>

                                                {(multimerArtifacts.summaryPath
                                                    || multimerArtifacts.lengthsPath
                                                    || multimerArtifacts.candidatesPath
                                                    || multimerArtifacts.dimerSummaryPath
                                                    || multimerArtifacts.dimerConsensusPath
                                                    || multimerArtifacts.dominantDimerConsensusPath
                                                    || multimerArtifacts.dominantDimerConsensusMetadataPath
                                                    || multimerArtifacts.dimerJunctionPath
                                                    || multimerArtifacts.dimerJunctionEventsPath
                                                    || multimerArtifacts.dimerJunctionClustersPath
                                                    || multimerArtifacts.dimerJunctionHotspotsPath
                                                    || multimerArtifacts.dimerJunctionRotatedPath
                                                    || multimerArtifacts.dimerJunctionRotationSummaryPath
                                                    || multimerArtifacts.dimerBreakpointScreenPath
                                                    || multimerArtifacts.dimerReadsPath
                                                    || multimerArtifacts.dimerReadLedgerPath
                                                    || multimerArtifacts.dimerBreakpointReadsPath
                                                    || multimerArtifacts.dimerRotatedRemapSummaryPath
                                                    || multimerArtifacts.dimerRotatedRemapBreakpointsPath) && (
                                                    <div className="text-[11px] text-[var(--text-secondary)] font-mono break-all">
                                                        {multimerArtifacts.summaryPath && <div>summary: {multimerArtifacts.summaryPath}</div>}
                                                        {multimerArtifacts.lengthsPath && <div>lengths: {multimerArtifacts.lengthsPath}</div>}
                                                        {multimerArtifacts.candidatesPath && <div>candidates: {multimerArtifacts.candidatesPath}</div>}
                                                        {multimerArtifacts.dimerSummaryPath && <div>dimer summary: {multimerArtifacts.dimerSummaryPath}</div>}
                                                        {multimerArtifacts.dimerConsensusPath && <div>dimer consensus: {multimerArtifacts.dimerConsensusPath}</div>}
                                                        {multimerArtifacts.dominantDimerConsensusPath && <div>dominant dimer consensus: {multimerArtifacts.dominantDimerConsensusPath}</div>}
                                                        {multimerArtifacts.dominantDimerConsensusMetadataPath && <div>dominant consensus metadata: {multimerArtifacts.dominantDimerConsensusMetadataPath}</div>}
                                                        {multimerArtifacts.dimerJunctionPath && <div>dimer junction profile: {multimerArtifacts.dimerJunctionPath}</div>}
                                                        {multimerArtifacts.dimerJunctionEventsPath && <div>dimer junction events: {multimerArtifacts.dimerJunctionEventsPath}</div>}
                                                        {multimerArtifacts.dimerJunctionClustersPath && <div>dimer junction clusters: {multimerArtifacts.dimerJunctionClustersPath}</div>}
                                                        {multimerArtifacts.dimerJunctionHotspotsPath && <div>dimer hotspot table: {multimerArtifacts.dimerJunctionHotspotsPath}</div>}
                                                        {multimerArtifacts.dimerJunctionRotatedPath && <div>dimer rotated profile: {multimerArtifacts.dimerJunctionRotatedPath}</div>}
                                                        {multimerArtifacts.dimerJunctionRotationSummaryPath && <div>dimer rotation summary: {multimerArtifacts.dimerJunctionRotationSummaryPath}</div>}
                                                        {multimerArtifacts.dimerBreakpointScreenPath && <div>dimer breakpoint screen: {multimerArtifacts.dimerBreakpointScreenPath}</div>}
                                                        {multimerArtifacts.dimerReadsPath && <div>dimer read junctions: {multimerArtifacts.dimerReadsPath}</div>}
                                                        {multimerArtifacts.dimerReadLedgerPath && <div>dimer read ledger: {multimerArtifacts.dimerReadLedgerPath}</div>}
                                                        {multimerArtifacts.dimerBreakpointReadsPath && <div>dimer breakpoint reads: {multimerArtifacts.dimerBreakpointReadsPath}</div>}
                                                        {multimerArtifacts.dimerRotatedRemapSummaryPath && <div>dimer frame-scan summary: {multimerArtifacts.dimerRotatedRemapSummaryPath}</div>}
                                                        {multimerArtifacts.dimerRotatedRemapBreakpointsPath && <div>dimer frame-scan breakpoints: {multimerArtifacts.dimerRotatedRemapBreakpointsPath}</div>}
                                                    </div>
                                                )}

                                                {multimerError && (
                                                    <p className="text-xs text-amber-300">{multimerError}</p>
                                                )}

                                                <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-tertiary)]">
                                                        <div className="text-[var(--text-secondary)]">Total reads</div>
                                                        <div className="text-[var(--text-primary)] font-mono">{Math.round(multimerMetrics.total_reads ?? 0)}</div>
                                                    </div>
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-tertiary)]">
                                                        <div className="text-[var(--text-secondary)]">Monomer-like</div>
                                                        <div className="text-[var(--text-primary)] font-mono">{Math.round(multimerClassCounts.monomer)}</div>
                                                    </div>
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-tertiary)]">
                                                        <div className="text-[var(--text-secondary)]">Dimer candidates</div>
                                                        <div className="text-[var(--text-primary)] font-mono">{Math.round(multimerClassCounts.dimer)}</div>
                                                    </div>
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-tertiary)]">
                                                        <div className="text-[var(--text-secondary)]">Higher-order</div>
                                                        <div className="text-[var(--text-primary)] font-mono">{Math.round(multimerClassCounts.trimer + multimerClassCounts.highOrder)}</div>
                                                    </div>
                                                    <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-tertiary)]">
                                                        <div className="text-[var(--text-secondary)]">Mean length</div>
                                                        <div className="text-[var(--text-primary)] font-mono">
                                                            {Number.isFinite(multimerMetrics.mean_read_length) ? `${multimerMetrics.mean_read_length.toFixed(1)} bp` : '—'}
                                                        </div>
                                                    </div>
                                                </div>

                                                <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                    <div className="text-xs text-[var(--text-secondary)] mb-2">Read length distribution + class composition</div>
                                                    {multimerHistogramPlotData.length > 0 ? (
                                                        <Plot
                                                            data={multimerHistogramPlotData}
                                                            layout={multimerHistogramLayout}
                                                            config={multimerPlotConfig}
                                                            style={{ width: '100%', height: '320px' }}
                                                            useResizeHandler
                                                        />
                                                    ) : (
                                                        <p className="text-xs text-[var(--text-secondary)]">No parseable read lengths available.</p>
                                                    )}
                                                    {multimerClassLegendItems.length > 0 && (
                                                        <div className="flex flex-wrap gap-2 mt-2">
                                                            {multimerClassLegendItems.map((item) => (
                                                                <div
                                                                    key={`multimer-class-${item.label}`}
                                                                    className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-[var(--border-primary)] text-[11px] text-[var(--text-primary)]"
                                                                >
                                                                    <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: item.color }} />
                                                                    <span>{item.label}</span>
                                                                    <span className="font-mono text-[var(--text-secondary)]">{Math.round(item.value)}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                    {Number.isFinite(expectedPlasmidSize) && expectedPlasmidSize > 0 && (
                                                        <p className="text-xs text-[var(--text-secondary)] mt-2">
                                                            Expected plasmid size: <span className="font-mono">{Math.round(expectedPlasmidSize)} bp</span>
                                                        </p>
                                                    )}
                                                </div>

                                                {multimerReport.candidates.length > 0 && (
                                                    <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                        <div className="text-xs text-[var(--text-secondary)] mb-2">Multimer candidates (first 200)</div>
                                                        <div className="overflow-x-auto">
                                                            <table className="w-full text-xs">
                                                                <thead>
                                                                    <tr className="border-b border-[var(--border-primary)]">
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Read index</th>
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Length (bp)</th>
                                                                        <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Classification</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {multimerReport.candidates.slice(0, 200).map((row, idx) => (
                                                                        <tr key={`multimer-candidate-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                            <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.readIndex ?? '—'}</td>
                                                                            <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.readLength ?? '—'}</td>
                                                                            <td className="px-2 py-1 text-[var(--text-primary)]">{row.classification}</td>
                                                                        </tr>
                                                                    ))}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                    </div>
                                                )}

                                                {(multimerReport.dimerSummary
                                                    || multimerReport.dimerJunctionRows.length > 0
                                                    || multimerReport.dimerJunctionClusters.length > 0
                                                    || multimerReport.dimerBreakpointScreenRows.length > 0
                                                    || multimerReport.dimerReadJunctions.length > 0
                                                    || multimerReport.dimerConsensusPreview
                                                    || multimerReport.dominantDimerConsensusPreview
                                                    || multimerReport.dominantDimerConsensusMetadata
                                                    || dimerSummaryDownloadHref
                                                    || dimerConsensusDownloadHref
                                                    || dominantDimerConsensusDownloadHref
                                                    || dominantDimerConsensusMetadataDownloadHref
                                                    || dimerJunctionDownloadHref
                                                    || dimerJunctionEventsDownloadHref
                                                    || dimerJunctionClustersDownloadHref
                                                    || dimerJunctionHotspotsDownloadHref
                                                    || dimerJunctionRotatedDownloadHref
                                                    || dimerJunctionRotationSummaryDownloadHref
                                                    || dimerBreakpointScreenDownloadHref) && (
                                                    <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3 space-y-3">
                                                        <div className="text-xs text-[var(--text-secondary)]">Dimer junction evidence</div>
                                                        <div className="grid grid-cols-2 md:grid-cols-8 gap-2 text-xs">
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Dimer reads</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{dimerCandidateReads}</div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Aligned dimer reads</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{alignedDimerReads ?? '—'}</div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Crossing-support reads</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{junctionSpanningReads ?? '—'}</div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Top hotspot (view)</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{hasSplitSupportedEvidence ? (topDimerHotspot?.positionModRef ?? '—') : 'pending'}</div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Likely non-boundary hotspot</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{likelyDimerizationLocus?.positionModRef ?? '—'}</div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Rotated hotspot</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{rotationDominantPosModRef != null ? Math.round(rotationDominantPosModRef) : '—'}</div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Dominant split hotspot</div>
                                                                <div className="text-[var(--text-primary)] font-mono">
                                                                    {dominantSplitPos != null ? Math.round(dominantSplitPos) : (topSplitHotspot?.positionModRef ?? '—')}
                                                                </div>
                                                            </div>
                                                            <div className="rounded border border-[var(--border-primary)] px-2 py-2 bg-[var(--bg-secondary)]">
                                                                <div className="text-[var(--text-secondary)]">Reference length</div>
                                                                <div className="text-[var(--text-primary)] font-mono">{dimerReferenceLength ? `${dimerReferenceLength} bp` : '—'}</div>
                                                            </div>
                                                        </div>
                                                        {(boundaryWindowSupportPct != null || likelyDimerizationLocus || dominantSplitSupportReads != null || breakpointModelStatus) && (
                                                            <p className="text-xs text-[var(--text-secondary)]">
                                                                {boundaryWindowSupportPct != null ? (
                                                                    <>Boundary-window support (±{boundaryWindowBp != null ? Math.round(boundaryWindowBp) : '—'} bp): <span className="font-mono text-[var(--text-primary)]">{boundaryWindowSupportReads != null ? Math.round(boundaryWindowSupportReads) : '—'}</span> reads (<span className="font-mono text-[var(--text-primary)]">{boundaryWindowSupportPct.toFixed(2)}%</span>). </>
                                                                ) : null}
                                                                {dominantSplitSupportReads != null ? (
                                                                    <> Split-only support: <span className="font-mono text-[var(--text-primary)]">{splitSupportReads != null ? Math.round(splitSupportReads) : Math.max(0, splitJunctionHotspots.reduce((sum, row) => sum + row.supportReads, 0))}</span> reads total; dominant split locus <span className="font-mono text-[var(--text-primary)]">{dominantSplitPos != null ? Math.round(dominantSplitPos) : (topSplitHotspot?.positionModRef ?? '—')}</span> with <span className="font-mono text-[var(--text-primary)]">{Math.round(dominantSplitSupportReads)}</span> reads {dominantSplitSupportPct != null ? <>({dominantSplitSupportPct.toFixed(2)}% of all junction support)</> : null}{dominantSplitSupportPctOfSplit != null ? <> / <span className="font-mono text-[var(--text-primary)]">{dominantSplitSupportPctOfSplit.toFixed(2)}%</span> of split support</> : null}. </>
                                                                ) : null}
                                                                {screenedPrimaryBreakpointPos != null && screenedPrimaryBreakpointSupportReads != null && screenedPrimaryBreakpointSupportReads > 0 ? (
                                                                    <> Screened breakpoint call: <span className="font-mono text-[var(--text-primary)]">{Math.round(screenedPrimaryBreakpointPos)}</span> with <span className="font-mono text-[var(--text-primary)]">{Math.round(screenedPrimaryBreakpointSupportReads)}</span> supporting split reads (confidence <span className="font-mono text-[var(--text-primary)]">{screenedPrimaryBreakpointConfidence || 'unknown'}</span>). </>
                                                                ) : (
                                                                    <> Screened breakpoint call: insufficient split-supported evidence in this run. </>
                                                                )}
                                                                {screenedPrimaryBreakpointPos != null && screenedPrimaryBreakpointSupportReads != null && screenedPrimaryBreakpointSupportReads > 0 ? (
                                                                    <> Screen metrics: boundary-start fraction <span className="font-mono text-[var(--text-primary)]">{screenedPrimaryBreakpointBoundaryStartFraction != null ? `${screenedPrimaryBreakpointBoundaryStartFraction.toFixed(2)}%` : '—'}</span>, seam fraction <span className="font-mono text-[var(--text-primary)]">{screenedPrimaryBreakpointSeamFraction != null ? `${screenedPrimaryBreakpointSeamFraction.toFixed(2)}%` : '—'}</span>, split:seam ratio <span className="font-mono text-[var(--text-primary)]">{screenedPrimaryBreakpointSplitToSeamRatio != null ? screenedPrimaryBreakpointSplitToSeamRatio.toFixed(3) : '—'}</span>. </>
                                                                ) : null}
                                                                {breakpointModelStatus ? (
                                                                    <> Breakpoint model status: <span className="font-mono text-[var(--text-primary)]">{breakpointModelStatus}</span> (informative <span className="font-mono text-[var(--text-primary)]">{informativeBreakpointCount != null ? Math.round(informativeBreakpointCount) : '—'}</span>, artifact <span className="font-mono text-[var(--text-primary)]">{artifactBreakpointCount != null ? Math.round(artifactBreakpointCount) : '—'}</span>, seam-only unresolved flag <span className="font-mono text-[var(--text-primary)]">{seamOnlyUnresolvedFlag != null ? Math.round(seamOnlyUnresolvedFlag) : '—'}</span>). </>
                                                                ) : null}
                                                                {likelyDimerizationLocus ? (
                                                                    <>Most likely non-boundary locus: <span className="font-mono text-[var(--text-primary)]">{likelyDimerizationLocus.positionModRef}</span> with <span className="font-mono text-[var(--text-primary)]">{Math.round(likelyDimerizationLocus.supportReads)}</span> supporting reads{likelyDimerizationLocus.supportPercent != null ? <> (<span className="font-mono text-[var(--text-primary)]">{likelyDimerizationLocus.supportPercent.toFixed(2)}%</span>)</> : null}.</>
                                                                ) : (
                                                                    <>No split-supported non-boundary hotspot reported in current outputs.</>
                                                                )}
                                                                {rotationSelectedOffsetBp != null && rotationDominantPosModRef != null ? (
                                                                    <> Rotating-frame normalization: offset <span className="font-mono text-[var(--text-primary)]">{Math.round(rotationSelectedOffsetBp)}</span> bp, hotspot <span className="font-mono text-[var(--text-primary)]">{Math.round(rotationDominantPosModRef)}</span> (rotated position <span className="font-mono text-[var(--text-primary)]">{rotationDominantPosRotated != null ? Math.round(rotationDominantPosRotated) : '—'}</span>) with boundary support <span className="font-mono text-[var(--text-primary)]">{rotationBoundarySupportPct != null ? `${rotationBoundarySupportPct.toFixed(2)}%` : '—'}</span>.</>
                                                                ) : null}
                                                            </p>
                                                        )}
                                                        {splitEvidencePending && (
                                                            <p className="text-xs text-amber-300">
                                                                Split support is not yet sufficient for a high-confidence breakpoint call. Seam-only junction signal is treated as unresolved and may reflect read-start/linearization effects.
                                                            </p>
                                                        )}
                                                        {boundaryDominantArtifactLikely && (
                                                            <p className="text-xs text-amber-300">
                                                                Boundary-window signal dominates this run and non-boundary support is below confidence thresholds. Treat position-1 hotspots as likely linearization/read-start artifact unless split-support increases.
                                                            </p>
                                                        )}

                                                        <div className="flex flex-wrap items-center gap-2 text-xs">
                                                            <span className="text-[var(--text-secondary)]">Hotspot view:</span>
                                                            <button
                                                                type="button"
                                                                onClick={() => setIncludeBoundaryJunctions(false)}
                                                                className={`px-2 py-1 rounded border transition-colors ${!includeBoundaryJunctions
                                                                    ? 'text-[var(--text-primary)]'
                                                                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                                    }`}
                                                                style={{
                                                                    borderColor: 'var(--border-primary)',
                                                                    backgroundColor: !includeBoundaryJunctions ? 'var(--bg-secondary)' : 'transparent',
                                                                }}
                                                            >
                                                                Non-boundary only
                                                            </button>
                                                            <button
                                                                type="button"
                                                                onClick={() => setIncludeBoundaryJunctions(true)}
                                                                className={`px-2 py-1 rounded border transition-colors ${includeBoundaryJunctions
                                                                    ? 'text-[var(--text-primary)]'
                                                                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                                    }`}
                                                                style={{
                                                                    borderColor: 'var(--border-primary)',
                                                                    backgroundColor: includeBoundaryJunctions ? 'var(--bg-secondary)' : 'transparent',
                                                                }}
                                                            >
                                                                Include boundary
                                                            </button>
                                                        </div>

                                                        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Junction crossing support profile</div>
                                                                {dimerJunctionPlotData.length > 0 ? (
                                                                    <Plot
                                                                        data={dimerJunctionPlotData}
                                                                        layout={dimerJunctionLayout}
                                                                        config={multimerPlotConfig}
                                                                        style={{ width: '100%', height: '320px' }}
                                                                        useResizeHandler
                                                                    />
                                                                ) : (
                                                                    <p className="text-xs text-[var(--text-secondary)]">
                                                                        No parseable junction evidence yet. Run with FASTQ + reference to generate junction events or profiles.
                                                                    </p>
                                                                )}
                                                            </div>
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">
                                                                    Top junction hotspots (first 20{includeBoundaryJunctions ? ', including boundary positions' : ', non-boundary only'})
                                                                </div>
                                                                {hasSplitSupportedEvidence && dimerHotspotRows.length > 0 ? (
                                                                    <div className="overflow-x-auto">
                                                                        <table className="w-full text-xs">
                                                                            <thead>
                                                                                <tr className="border-b border-[var(--border-primary)]">
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Position</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Crossing reads</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Support reads</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Support %</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Boundary</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Method</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Orientation</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Source</th>
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">~100 bp window</th>
                                                                                </tr>
                                                                            </thead>
                                                                            <tbody>
                                                                                {dimerHotspotRows.map((row, idx) => (
                                                                                    <tr key={`dimer-hotspot-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.positionModRef}</td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.crossingReads}</td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.supportReads}</td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">
                                                                                            {row.supportPercent != null ? `${row.supportPercent.toFixed(1)}%` : '—'}
                                                                                        </td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                                            {row.inBoundaryWindow == null ? '—' : (row.inBoundaryWindow ? 'yes' : 'no')}
                                                                                        </td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{row.method || '—'}</td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{row.orientation || '—'}</td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{row.source}</td>
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">
                                                                                            {row.window100bp && row.windowLabel
                                                                                                ? `${row.windowLabel} ${row.window100bp}`
                                                                                                : 'n/a'}
                                                                                        </td>
                                                                                    </tr>
                                                                                ))}
                                                                            </tbody>
                                                                        </table>
                                                                    </div>
                                                                ) : (
                                                                    <p className="text-xs text-[var(--text-secondary)]">
                                                                        {splitEvidencePending
                                                                            ? 'Split-aware hotspot ranking pending; current run is seam-dominant without sufficient split support.'
                                                                            : (includeBoundaryJunctions
                                                                                ? 'No hotspot rows available.'
                                                                                : 'No non-boundary hotspot rows available. Switch to "Include boundary" to inspect linearization-window loci.')}
                                                                    </p>
                                                                )}
                                                                {dimerSummaryLookup.get('consensus_status') && (
                                                                    <p className="text-xs text-[var(--text-secondary)] mt-2">
                                                                        Consensus status: <span className="font-mono text-[var(--text-primary)]">{dimerSummaryLookup.get('consensus_status')}</span>
                                                                    </p>
                                                                )}
                                                                {hasSplitSupportedEvidence && likelyDimerizationLocus && (
                                                                    <p className="text-xs text-[var(--text-secondary)] mt-2">
                                                                        Estimated non-boundary dimerization locus: <span className="font-mono text-[var(--text-primary)]">{likelyDimerizationLocus.positionModRef}</span>
                                                                        {' '}(<span className="font-mono text-[var(--text-primary)]">{Math.round(likelyDimerizationLocus.supportReads)}</span> supporting reads)
                                                                        {multimerReport.referenceName ? (
                                                                            <> on <span className="font-mono text-[var(--text-primary)]">{multimerReport.referenceName}</span></>
                                                                        ) : null}
                                                                    </p>
                                                                )}
                                                            </div>
                                                        </div>

                                                        {dimerBreakpointScreenTopRows.length > 0 && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Artifact-screened breakpoint ranking (split-aware)</div>
                                                                <div className="overflow-x-auto">
                                                                    <table className="w-full text-xs">
                                                                        <thead>
                                                                            <tr className="border-b border-[var(--border-primary)]">
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Position</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Total support</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Seam</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Split</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Split % (position)</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Split % (all split)</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Boundary-start %</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Seam %</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Split:Seam</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Boundary</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Artifact</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Confidence</th>
                                                                            </tr>
                                                                        </thead>
                                                                        <tbody>
                                                                            {dimerBreakpointScreenTopRows.map((row, idx) => (
                                                                                <tr key={`dimer-screen-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.positionModRef}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.totalSupportReads}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.seamSupportReads}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.splitSupportReads}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.splitPctOfPosition != null ? `${row.splitPctOfPosition.toFixed(2)}%` : '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.splitPctOfAllSplit != null ? `${row.splitPctOfAllSplit.toFixed(2)}%` : '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.boundaryStartFraction != null ? `${row.boundaryStartFraction.toFixed(2)}%` : '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.seamFraction != null ? `${row.seamFraction.toFixed(2)}%` : '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.splitToSeamRatio != null ? row.splitToSeamRatio.toFixed(3) : '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)]">{row.inBoundaryWindow == null ? '—' : (row.inBoundaryWindow ? 'yes' : 'no')}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)]">{row.artifactFlag == null ? '—' : (row.artifactFlag ? 'yes' : 'no')}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)]">{row.confidence || '—'}</td>
                                                                                </tr>
                                                                            ))}
                                                                        </tbody>
                                                                    </table>
                                                                </div>
                                                            </div>
                                                        )}

                                                        {dimerReferenceSequenceRows.length > 0 && hasSplitSupportedEvidence && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3 space-y-2">
                                                                <div className="text-xs text-[var(--text-secondary)]">Linear reference map (left-to-right) with dimer junction overlays</div>
                                                                <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-secondary)]">
                                                                    <span>Read-support intensity scale:</span>
                                                                    <span className="font-mono">1 → {Math.max(1, dimerMaxSupportReads)}</span>
                                                                    <span className="inline-block w-20 h-2 rounded" style={{ background: `linear-gradient(to right, ${toAlphaColor(themeColors.warning, 0.18)}, ${toAlphaColor(themeColors.warning, 0.92)})` }} />
                                                                </div>
                                                                <div className="max-h-72 overflow-auto border border-[var(--border-primary)] rounded bg-[var(--bg-tertiary)] p-2">
                                                                    {dimerReferenceSequenceRows.map((row) => {
                                                                        const segments = buildDimerHighlightedSequenceSegments(row, dimerSequenceHighlightsByPosition);
                                                                        return (
                                                                            <div key={`dimer-seq-row-${row.start}`} className="flex gap-2 items-start mb-1 last:mb-0">
                                                                                <span className="w-24 shrink-0 text-right text-[10px] text-[var(--text-secondary)] font-mono pt-0.5">
                                                                                    {row.start}-{row.end}
                                                                                </span>
                                                                                <span className="font-mono text-[11px] leading-5 break-all text-[var(--text-primary)]">
                                                                                    {segments.map((segment, idx) => (
                                                                                        segment.highlight ? (
                                                                                            <span
                                                                                                key={`dimer-seg-${row.start}-${idx}`}
                                                                                                style={{ backgroundColor: segment.highlight.backgroundColor }}
                                                                                                className="rounded-[2px] px-[1px]"
                                                                                                title={`pos ${segment.position} | support ${segment.highlight.readCount} read${segment.highlight.readCount === 1 ? '' : 's'}${segment.highlight.supportPercent != null ? ` (${segment.highlight.supportPercent.toFixed(2)}%)` : ''}`}
                                                                                            >
                                                                                                {segment.text}
                                                                                            </span>
                                                                                        ) : (
                                                                                            <span key={`dimer-seg-${row.start}-${idx}`}>{segment.text}</span>
                                                                                        )
                                                                                    ))}
                                                                                </span>
                                                                            </div>
                                                                        );
                                                                    })}
                                                                </div>
                                                                <p className="text-xs text-[var(--text-secondary)]">
                                                                    Highlighted bases are junction right-breakpoint positions from {includeBoundaryJunctions ? 'all hotspot evidence' : 'non-boundary hotspot evidence'}; darker color means higher supporting read count.
                                                                </p>
                                                            </div>
                                                        )}
                                                        {dimerReferenceSequenceRows.length > 0 && !hasSplitSupportedEvidence && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <p className="text-xs text-[var(--text-secondary)]">
                                                                    Reference overlay is hidden until split-supported breakpoint evidence is detected. Current run is seam-dominant and may reflect read-start/linearization effects.
                                                                </p>
                                                            </div>
                                                        )}

                                                        {multimerReport.dimerReadJunctions.length > 0 && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Read-level junction evidence (first 200)</div>
                                                                <div className="overflow-x-auto">
                                                                    <table className="w-full text-xs">
                                                                        <thead>
                                                                            <tr className="border-b border-[var(--border-primary)]">
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Read ID</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Start</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">End</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Position (mod ref)</th>
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Crosses junction</th>
                                                                                {dimerReadTableHasMethod && (
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Method</th>
                                                                                )}
                                                                                {dimerReadTableHasOrientation && (
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Orientation</th>
                                                                                )}
                                                                                {dimerReadTableHasMissingLeft && (
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Missing left (bp)</th>
                                                                                )}
                                                                                {dimerReadTableHasMissingRight && (
                                                                                    <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Missing right (bp)</th>
                                                                                )}
                                                                                <th className="text-left font-medium text-[var(--text-secondary)] px-2 py-1">Source</th>
                                                                            </tr>
                                                                        </thead>
                                                                        <tbody>
                                                                            {multimerReport.dimerReadJunctions.slice(0, 200).map((row, idx) => (
                                                                                <tr key={`dimer-read-junction-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.readId || '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.start ?? '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.end ?? '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.positionModRef ?? '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)]">
                                                                                        {row.crossesJunction == null ? '—' : (row.crossesJunction ? 'yes' : 'no')}
                                                                                    </td>
                                                                                    {dimerReadTableHasMethod && (
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{row.method || '—'}</td>
                                                                                    )}
                                                                                    {dimerReadTableHasOrientation && (
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)]">{row.orientation || '—'}</td>
                                                                                    )}
                                                                                    {dimerReadTableHasMissingLeft && (
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.missingLeftBp ?? '—'}</td>
                                                                                    )}
                                                                                    {dimerReadTableHasMissingRight && (
                                                                                        <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row.missingRightBp ?? '—'}</td>
                                                                                    )}
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)]">{row.source}</td>
                                                                                </tr>
                                                                            ))}
                                                                        </tbody>
                                                                    </table>
                                                                </div>
                                                            </div>
                                                        )}

                                                        {multimerReport.dimerConsensusPreview && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Dimer consensus preview</div>
                                                                <pre className="text-[11px] leading-relaxed text-[var(--text-primary)] font-mono whitespace-pre-wrap break-words max-h-[280px] overflow-auto">
                                                                    {multimerReport.dimerConsensusPreview}
                                                                </pre>
                                                            </div>
                                                        )}
                                                        {multimerReport.dominantDimerConsensusPreview && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Dominant dimer consensus preview</div>
                                                                <pre className="text-[11px] leading-relaxed text-[var(--text-primary)] font-mono whitespace-pre-wrap break-words max-h-[280px] overflow-auto">
                                                                    {multimerReport.dominantDimerConsensusPreview}
                                                                </pre>
                                                                <p className="text-[11px] text-[var(--text-secondary)] mt-2">
                                                                    This sequence is generated from the dominant breakpoint-supported subset when available, otherwise from the most abundant exact dimer read.
                                                                </p>
                                                            </div>
                                                        )}
                                                        {multimerReport.dominantDimerConsensusMetadata && (
                                                            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded p-3">
                                                                <div className="text-xs text-[var(--text-secondary)] mb-2">Dominant consensus metadata</div>
                                                                <div className="overflow-x-auto">
                                                                    <table className="w-full text-xs">
                                                                        <thead>
                                                                            <tr className="border-b border-[var(--border-primary)]">
                                                                                {multimerReport.dominantDimerConsensusMetadata.header.slice(0, 2).map((column, idx) => (
                                                                                    <th
                                                                                        key={`dominant-consensus-meta-col-${idx}`}
                                                                                        className="text-left font-medium text-[var(--text-secondary)] px-2 py-1"
                                                                                    >
                                                                                        {column || `col_${idx + 1}`}
                                                                                    </th>
                                                                                ))}
                                                                            </tr>
                                                                        </thead>
                                                                        <tbody>
                                                                            {multimerReport.dominantDimerConsensusMetadata.rows.slice(0, 30).map((row, idx) => (
                                                                                <tr key={`dominant-consensus-meta-row-${idx}`} className="border-b border-[var(--border-primary)]/40">
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] font-mono">{row[0] || '—'}</td>
                                                                                    <td className="px-2 py-1 text-[var(--text-primary)] break-all">{row[1] || '—'}</td>
                                                                                </tr>
                                                                            ))}
                                                                        </tbody>
                                                                    </table>
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
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
                                            {(methylationArtifacts.summaryPath || methylationArtifacts.bedPath) && (
                                                <div className="text-[11px] text-[var(--text-secondary)] font-mono break-all">
                                                    {methylationArtifacts.summaryPath && (
                                                        <div>summary: {methylationArtifacts.summaryPath}</div>
                                                    )}
                                                    {methylationArtifacts.bedPath && (
                                                        <div>bed: {methylationArtifacts.bedPath}</div>
                                                    )}
                                                </div>
                                            )}

                                            {methylationError && (
                                                <p className="text-xs text-amber-300">{methylationError}</p>
                                            )}

                                            <div className="text-xs text-[var(--text-secondary)]">
                                                modkit codes: <span className="font-mono">a=6mA</span>, <span className="font-mono">m=5mC</span>, <span className="font-mono">h=5hmC</span>.
                                                Plotly chart is plasmid-focused: Dam at <span className="font-mono">GATC</span> (6mA) and Dcm at <span className="font-mono">CCWGG</span> (5mC) on both strands.
                                                Dcm percentages use <span className="font-mono">max(m, h)</span> per site (not sum) to avoid inflated totals.
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
                                                Control note: for non-methylating strains, most motif sites should typically remain at or below ~5% at adequate depth (default {motifMinCoverage}x + strand concordance required).
                                            </div>

                                            {methylationPlotData.length > 0 ? (
                                                <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-3">
                                                    <div className="bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded p-3">
                                                        <div className="text-xs text-[var(--text-secondary)] mb-2">Motif-targeted strand bar chart (Dam/Dcm)</div>
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
                                                            <div>Motif site shading intensity tracks % modified (low to high).</div>
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
                                                                <span>+/- strands use distinct hues.</span>
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
                                                            <p className="text-xs text-[var(--text-secondary)]">Reference sequence is not available for this run.</p>
                                                        )}
                                                        {selectedSequencePosition != null && (
                                                            <p className="text-xs text-[var(--text-secondary)]">
                                                                Highlighted site from selected bar: <span className="font-mono">{selectedSequencePosition}</span>
                                                            </p>
                                                        )}
                                                    </div>
                                                </div>
                                            ) : (
                                                <p className="text-xs text-[var(--text-secondary)]">No per-site methylation points available for plotting yet.</p>
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
                                                    <div className="text-xs text-[var(--text-secondary)] mb-2">modkit summary (first 100 rows)</div>
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
                                                        Raw modkit loci preview (debug, not Dam/Dcm motif-filtered)
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
                                                ) : (
                                                    <p className="text-xs text-[var(--text-secondary)]">
                                                        Hidden by default to avoid confusion with motif-filtered Dam/Dcm calls above.
                                                    </p>
                                                )}
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
                <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/70 backdrop-blur-sm">
                    <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl shadow-2xl w-[96vw] h-[94vh] max-w-none max-h-none flex flex-col">
                        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-primary)]">
                            <div>
                                <h2 className="text-xl font-semibold text-[var(--text-primary)]">IGV Alignment Viewer</h2>
                                {selectedJob && (
                                    <p className="text-sm text-[var(--text-secondary)] mt-1">
                                        {selectedJob.name}
                                    </p>
                                )}
                                <p className="text-xs text-[var(--text-secondary)] mt-1">
                                    Performance mode: IGV starts reference-only. Load heavy tracks manually.
                                </p>
                                <div className="mt-2 flex flex-wrap items-center gap-2">
                                    <button
                                        type="button"
                                        onClick={() => void handleLoadIgvReadsTrack()}
                                        disabled={igvLoading || igvReadsTrackLoaded || igvReadsTrackLoading || !igvArtifacts.bamUrl || !igvArtifacts.baiUrl}
                                        className="px-2.5 py-1 text-xs rounded border border-[var(--border-primary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        {igvReadsTrackLoaded ? 'Reads track loaded' : igvReadsTrackLoading ? 'Loading reads...' : 'Load reads track'}
                                    </button>
                                </div>
                            </div>
                            <button
                                onClick={() => {
                                    setIgvModalOpen(false);
                                    setIgvError(null);
                                }}
                                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-2xl font-light transition-colors"
                            >
                                ×
                            </button>
                        </div>

                        <div className="flex-1 overflow-hidden p-4 min-h-0">
                            <div className="relative w-full h-full min-h-[520px]">
                                <div
                                    ref={igvContainerRef}
                                    className="w-full h-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded"
                                />
                                {igvLoading && (
                                    <div className="absolute inset-0 rounded flex items-center justify-center bg-[var(--bg-primary)]/65 text-[var(--text-secondary)]">
                                        Loading IGV viewer...
                                    </div>
                                )}
                                {!igvLoading && igvError && (
                                    <div className="absolute top-3 left-3 right-3 rounded border border-red-500/40 bg-red-500/10 text-red-300 text-xs px-3 py-2">
                                        {igvError}
                                    </div>
                                )}
                                {!igvLoading && !igvError && !igvReadsTrackLoaded && (
                                    <div className="absolute bottom-3 left-3 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)]/85 text-[var(--text-secondary)] text-xs px-3 py-2">
                                        Reference loaded. Click "Load reads track" to render alignments.
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="flex justify-end px-6 py-4 border-t border-[var(--border-primary)]">
                            <button
                                onClick={() => {
                                    setIgvModalOpen(false);
                                    setIgvError(null);
                                }}
                                className="px-4 py-2 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-primary)] text-[var(--text-primary)] rounded-lg transition-colors"
                            >
                                Close
                            </button>
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
