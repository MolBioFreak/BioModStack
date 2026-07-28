/**
 * NanoporeTemplate – ONT Nanopore methylation basecalling and analysis.
 *
 * Self-contained: imports only shared UI primitives (BMS-lite extractable).
 * No MolstarViewer, TargetAntigenSelector, or protein-design dependencies.
 *
 * Pattern follows OligoDesignerTemplate for consistency.
 */

import { useState, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchFiles, submitOntNgsJob, uploadFile } from '../lib/api';


// ============================================================================
// Type Definitions
// ============================================================================
type DoradoModel = 'sup' | 'hac' | 'fast';
type DoradoMolecule = 'dna' | 'rna';
type DoradoMode = 'simplex' | 'duplex';
type ModifiedBases = '6mA' | '5mC_5hmC' | 'none';
type AssemblyTool = 'flye' | 'canu';
type FlyeReadQuality = 'nano-hq' | 'nano-corr' | 'nano-raw';
type MinimapPreset = 'map-ont' | 'map-hifi' | 'map-pb' | 'sr';
type InputSource = 'pod5' | 'bam' | 'fastq';
type WorkflowKey = 'clone' | 'plasmidQc' | 'bamQc' | 'dna' | 'rna' | 'duplex' | 'modified' | 'barcode';
type PathField = 'pod5Dir' | 'bamPath' | 'fastqPath' | 'referencePath';
type PathPickerMode = 'file' | 'directory';
type ReferenceTab = 'browse' | 'paste' | 'create';
type PathFilter = 'unknown' | 'bam' | 'fastq' | 'fasta';

interface PathPickerState {
    field: PathField;
    title: string;
    mode: PathPickerMode;
    filter: PathFilter;
}

interface SavedReferenceEntry {
    id: string;
    name: string;
    source: 'fasta' | 'path';
    fasta?: string;
    path?: string;
    createdAt: string;
    updatedAt: string;
}

interface NanoporeTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
}

// ============================================================================
// Constants
// ============================================================================
const DORADO_MODELS: Record<DoradoModel, { label: string; description: string }> = {
    sup: { label: 'Super Accurate (SUP)', description: 'Highest accuracy; best for methylation.' },
    hac: { label: 'High Accuracy (HAC)', description: 'Balanced speed/accuracy.' },
    fast: { label: 'Fast', description: 'Fastest; lower accuracy.' },
};

const MODIFIED_BASES_OPTIONS: Record<ModifiedBases, { label: string; description: string }> = {
    '6mA': { label: '6mA only', description: 'Adenine methylation.' },
    '5mC_5hmC': { label: '5mC + 5hmC', description: 'Cytosine + hydroxymethyl-cytosine.' },
    none: { label: 'No modification detection', description: 'No modification tags.' },
};

const QSCORE_LABELS: Record<number, string> = {
    5: 'Very permissive',
    7: 'Permissive',
    10: 'Standard',
    15: 'Strict',
    20: 'Very strict',
    30: 'Ultra-strict',
};

function getQscoreLabel(q: number): string {
    // Find the closest label
    const keys = Object.keys(QSCORE_LABELS).map(Number).sort((a, b) => a - b);
    for (let i = keys.length - 1; i >= 0; i--) {
        if (q >= keys[i]) return QSCORE_LABELS[keys[i]];
    }
    return QSCORE_LABELS[keys[0]];
}

const FASTQ_DEFAULT_MINIMAP_PRESET: MinimapPreset = 'map-ont';
const FASTQ_DEFAULT_EXPECTED_PLASMID_SIZE_BP = 7000;
const FASTQ_MAX_EXPECTED_PLASMID_SIZE_BP = 100_000_000;
const FASTQ_DEFAULT_MIN_READ_LENGTH_BP = 0;
const FASTQ_MAX_MIN_READ_LENGTH_BP = 10_000_000;
const FASTQ_DEFAULT_IGV_TRACK_WINDOW_BP = 100;
const FASTQ_MAX_IGV_TRACK_WINDOW_BP = 100_000;
const FASTQ_DEFAULT_IGV_REPORT_MAX_SITES = 40;
const FASTQ_MAX_IGV_REPORT_MAX_SITES = 10_000;
const FASTQ_DEFAULT_IGV_REPORT_FLANKING_BP = 200;
const FASTQ_MAX_IGV_REPORT_FLANKING_BP = 100_000;

const FASTQ_MINIMAP_PRESETS: Record<MinimapPreset, string> = {
    'map-ont': 'map-ont (ONT reads)',
    'map-hifi': 'map-hifi (PacBio HiFi)',
    'map-pb': 'map-pb (PacBio CLR)',
    'sr': 'sr (short reads)',
};

const FASTQ_SUPPORTED_MINIMAP_PRESETS = new Set<MinimapPreset>(Object.keys(FASTQ_MINIMAP_PRESETS) as MinimapPreset[]);

function normalizeFastqMinimapPreset(value: unknown): MinimapPreset {
    return typeof value === 'string' && FASTQ_SUPPORTED_MINIMAP_PRESETS.has(value as MinimapPreset)
        ? (value as MinimapPreset)
        : FASTQ_DEFAULT_MINIMAP_PRESET;
}

function coerceIntegerInput(value: unknown, fallback: number, min: number, max: number): number {
    const parsed = typeof value === 'number'
        ? value
        : Number.parseInt(String(value ?? ''), 10);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

function coerceNumberInput(value: unknown, fallback: number, min: number, max: number): number {
    const parsed = typeof value === 'number' ? value : Number.parseFloat(String(value ?? ''));
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
}

function isIntegerInRange(value: number, min: number, max: number): boolean {
    return Number.isFinite(value) && Number.isInteger(value) && value >= min && value <= max;
}

const REFERENCE_LIBRARY_STORAGE_KEY = 'bms.nanopore.referenceLibrary.v1';

// SVG icon for the header (sequencer/nanopore)
const NanoporeIcon = () => (
    <svg className="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="4" y="3" width="16" height="18" rx="2" />
        <path d="M8 7h8M8 11h8" strokeDasharray="2 2" />
        <circle cx="12" cy="16" r="2" />
        <path d="M4 7h-2M4 11h-2M22 7h-2M22 11h-2" strokeWidth="1" />
    </svg>
);

function normalizeBrowserPath(path: string): string {
    const trimmed = path.trim();
    if (!trimmed || trimmed === '/') return '/';
    return trimmed.replace(/^\/+/, '');
}

function parentBrowserPath(path: string): string {
    const normalized = normalizeBrowserPath(path);
    if (normalized === '/') return '/';
    const parts = normalized.split('/').filter(Boolean);
    parts.pop();
    return parts.length > 0 ? parts.join('/') : '/';
}

function formatPathDisplay(path: string): string {
    if (!path) return '';
    return path.split('/').filter(Boolean).pop() || 'Selected input';
}

function matchesPathFilter(fileName: string, filter: PathFilter): boolean {
    const name = fileName.toLowerCase();
    if (filter === 'unknown') return true;
    if (filter === 'bam') return name.endsWith('.bam');
    if (filter === 'fastq') return /\.(fastq|fq)(\.gz)?$/i.test(name);
    if (filter === 'fasta') return /\.(fasta|fa|fna)(\.gz)?$/i.test(name);
    return true;
}

function extractApiErrorMessage(err: unknown): string {
    const fallback = err instanceof Error ? err.message : 'Request failed';
    if (!err || typeof err !== 'object') {
        return fallback;
    }

    const maybe = err as {
        response?: {
            status?: number;
            data?: {
                detail?: {
                    validation_errors?: unknown;
                } | string;
            };
        };
        message?: string;
    };

    if (maybe.response?.status === 422) {
        const detail = maybe.response.data?.detail;
        if (detail && typeof detail === 'object') {
            const errors = (detail as { validation_errors?: unknown }).validation_errors;
            if (Array.isArray(errors) && errors.length > 0) {
                return errors.map((item) => String(item)).join(' | ');
            }
        }
        if (typeof detail === 'string' && detail.trim()) {
            return detail;
        }
    }

    return maybe.message || fallback;
}

function sanitizeFileStem(value: string): string {
    const cleaned = value
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, '_')
        .replace(/^_+|_+$/g, '');
    return cleaned || 'reference';
}

function normalizeFastaText(raw: string): string | null {
    const trimmed = raw.trim();
    if (!trimmed) return null;

    const lines = trimmed
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
    if (lines.length === 0) return null;

    // If user pasted plain sequence without header, wrap it as a FASTA entry.
    if (!lines[0].startsWith('>')) {
        const seq = lines.join('').replace(/\s+/g, '').toUpperCase();
        if (!seq || /[^ACGTN]/i.test(seq)) return null;
        return `>reference\n${seq}\n`;
    }

    let sawHeader = false;
    let sawBases = false;
    const normalized: string[] = [];
    for (const line of lines) {
        if (line.startsWith('>')) {
            sawHeader = true;
            normalized.push(line);
            continue;
        }
        const seq = line.replace(/\s+/g, '').toUpperCase();
        if (!seq || /[^ACGTN]/i.test(seq)) return null;
        sawBases = true;
        normalized.push(seq);
    }
    if (!sawHeader || !sawBases) return null;
    return `${normalized.join('\n')}\n`;
}

function readFirstString(record: Record<string, unknown>, keys: string[]): string | undefined {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
    }
    return undefined;
}

function parseFastaRecordsForTemplate(fasta: string): Array<{ name: string; sequence: string }> {
    const normalized = normalizeFastaText(fasta);
    if (!normalized) return [];
    const lines = normalized.split(/\r?\n/);
    const records: Array<{ name: string; sequence: string }> = [];
    let currentName = '';
    let currentSeq: string[] = [];

    const flush = () => {
        if (!currentName || currentSeq.length === 0) return;
        records.push({
            name: currentName,
            sequence: currentSeq.join(''),
        });
    };

    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) continue;
        if (line.startsWith('>')) {
            flush();
            currentName = line.replace(/^>\s*/, '').trim() || 'reference';
            currentSeq = [];
            continue;
        }
        currentSeq.push(line.replace(/\s+/g, '').toUpperCase());
    }
    flush();
    return records;
}

function parseReferenceLibrary(): SavedReferenceEntry[] {
    if (typeof window === 'undefined') return [];
    try {
        const raw = window.localStorage.getItem(REFERENCE_LIBRARY_STORAGE_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        const list = Array.isArray(parsed)
            ? parsed
            : (
                (parsed && typeof parsed === 'object'
                    && (
                        Array.isArray((parsed as Record<string, unknown>).entries)
                        || Array.isArray((parsed as Record<string, unknown>).references)
                        || Array.isArray((parsed as Record<string, unknown>).items)
                    ))
                    ? (
                        ((parsed as Record<string, unknown>).entries as unknown[])
                        || ((parsed as Record<string, unknown>).references as unknown[])
                        || ((parsed as Record<string, unknown>).items as unknown[])
                    )
                    : []
            );
        if (!Array.isArray(list)) return [];
        return list
            .map((item) => {
                if (!item || typeof item !== 'object') return null;
                const record = item as Record<string, unknown>;
                const rawSource = typeof record.source === 'string'
                    ? record.source.trim().toLowerCase()
                    : '';
                const legacyFasta = readFirstString(record, [
                    'fasta',
                    'fastaText',
                    'sequence',
                    'content',
                    'reference_fasta_text',
                    'referenceText',
                ]);
                const legacyPath = readFirstString(record, [
                    'path',
                    'referencePath',
                    'reference_path',
                    'reference_fasta',
                    'filePath',
                ]);
                const normalizedLegacyFasta = legacyFasta ? (normalizeFastaText(legacyFasta) || legacyFasta) : undefined;

                let source: 'fasta' | 'path' | null = null;
                if (rawSource === 'path') {
                    source = 'path';
                } else if (rawSource === 'fasta' || rawSource === 'create' || rawSource === 'created') {
                    source = 'fasta';
                } else if (normalizedLegacyFasta) {
                    source = 'fasta';
                } else if (legacyPath) {
                    source = 'path';
                }

                const name = String(record.name || '').trim()
                    || (legacyPath ? (inferReferenceNameFromPath(legacyPath) || '') : '')
                    || (normalizedLegacyFasta ? (inferReferenceNameFromFasta(normalizedLegacyFasta) || '') : '')
                    || 'reference';
                const id = String(record.id || '').trim()
                    || `${sanitizeFileStem(name || legacyPath || 'reference')}_${String(record.createdAt || record.updatedAt || 'legacy').replace(/[^a-zA-Z0-9._-]+/g, '_')}`;
                if (!id || !source) return null;
                return {
                    id,
                    name: normalizeReferenceLabel(name),
                    source,
                    fasta: source === 'fasta' ? normalizedLegacyFasta : undefined,
                    path: source === 'path' ? legacyPath : undefined,
                    createdAt: typeof record.createdAt === 'string' ? record.createdAt : new Date().toISOString(),
                    updatedAt: typeof record.updatedAt === 'string' ? record.updatedAt : new Date().toISOString(),
                } as SavedReferenceEntry;
            })
            .filter((item): item is SavedReferenceEntry => item !== null);
    } catch {
        return [];
    }
}

function persistReferenceLibrary(entries: SavedReferenceEntry[]): void {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(REFERENCE_LIBRARY_STORAGE_KEY, JSON.stringify(entries));
    } catch {
        // ignore storage failures
    }
}

function inferReferenceNameFromFasta(fasta: string): string | null {
    const firstHeader = fasta
        .split(/\r?\n/)
        .map((line) => line.trim())
        .find((line) => line.startsWith('>'));
    if (!firstHeader) return null;
    const name = firstHeader.replace(/^>\s*/, '').trim();
    if (!name) return null;
    return name.slice(0, 80);
}

function inferReferenceNameFromPath(path: string): string | null {
    const normalized = path.trim().replace(/\\/g, '/');
    if (!normalized) return null;
    const leaf = normalized.split('/').filter(Boolean).pop() || '';
    if (!leaf) return null;
    return leaf.replace(/\.(fasta|fa|fna)(\.gz)?$/i, '').slice(0, 80) || leaf.slice(0, 80);
}

function normalizeReferenceLabel(name: string): string {
    const trimmed = name.trim().replace(/\s+/g, ' ');
    if (!trimmed) return 'reference';
    return trimmed.slice(0, 80);
}

// ============================================================================
// Main Component
// ============================================================================
export function NanoporeTemplate({ onBack, initialValues }: NanoporeTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    // Analysis workflows use the scheduler's fixed safe policy; this surface does
    // not inspect or admit runtime resources.
    const gpuOptions: Array<{ index: number; label: string }> = [];

    // ============================================================================
    // State: Core Configuration
    // ============================================================================
    const [jobName, setJobName] = useState(initialValues?.jobName as string || '');
    const [inputSource, setInputSource] = useState<InputSource>(initialValues?.inputSource as InputSource || 'pod5');
    const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowKey>(() => {
        if (typeof initialValues?.selectedWorkflow === 'string') return initialValues.selectedWorkflow as WorkflowKey;
        if (initialValues?.runAssembly === true) return 'clone';
        if (initialValues?.inputSource === 'bam') return 'bamQc';
        if (initialValues?.inputSource === 'fastq') return 'plasmidQc';
        if (initialValues?.doradoMolecule === 'rna') return 'rna';
        if (initialValues?.modifiedBases && initialValues.modifiedBases !== 'none') return 'modified';
        if (initialValues?.barcodeKit) return 'barcode';
        if (initialValues?.doradoMode === 'duplex') return 'duplex';
        return 'dna';
    });
    const [pod5Dir, setPod5Dir] = useState(initialValues?.pod5Dir as string || '');
    const [bamPath, setBamPath] = useState(initialValues?.bamPath as string || '');
    const [bamForceRealign, setBamForceRealign] = useState<boolean>(
        (initialValues?.bamForceRealign as boolean | undefined)
        ?? (initialValues?.bam_force_realign as boolean | undefined)
        ?? false
    );
    const [fastqPath, setFastqPath] = useState(initialValues?.fastqPath as string || '');
    const [referencePath, setReferencePath] = useState(initialValues?.referencePath as string || '');

    // ============================================================================
    // State: Basecalling Config
    // ============================================================================
    const [doradoModel, setDoradoModel] = useState<DoradoModel>(initialValues?.doradoModel as DoradoModel || 'sup');
    const [doradoMolecule, setDoradoMolecule] = useState<DoradoMolecule>(initialValues?.doradoMolecule as DoradoMolecule || 'dna');
    const [doradoMode, setDoradoMode] = useState<DoradoMode>(initialValues?.doradoMode as DoradoMode || 'simplex');
    const [duplexPairs, setDuplexPairs] = useState(initialValues?.duplexPairs as string || '');
    const [barcodeKit, setBarcodeKit] = useState<'' | 'SQK-RBK114-96'>(initialValues?.barcodeKit as '' | 'SQK-RBK114-96' || '');
    const [sampleSheet, setSampleSheet] = useState(initialValues?.sampleSheet as string || '');
    const [modifiedBases, setModifiedBases] = useState<ModifiedBases>(initialValues?.modifiedBases as ModifiedBases || 'none');

    // ============================================================================
    // State: QC Settings
    // ============================================================================
    const [minQscore, setMinQscore] = useState<number>((initialValues?.minQscore as number | undefined) ?? 10);
    const [bamMinMapq, setBamMinMapq] = useState<number>(() => {
        const raw = Number(
            (initialValues?.bamMinMapq as number | undefined)
            ?? (initialValues?.bam_min_mapq as number | undefined)
            ?? 0
        );
        if (!Number.isFinite(raw)) return 0;
        return Math.min(60, Math.max(0, Math.round(raw)));
    });
    const [trimAdapters, setTrimAdapters] = useState(doradoMolecule === 'rna' || doradoMode === 'duplex' ? true : initialValues?.trimAdapters !== false);

    // ============================================================================
    // State: Analysis Options
    // ============================================================================
    const [runModkit, setRunModkit] = useState<boolean>((initialValues?.runModkit as boolean | undefined) ?? false);
    const [runFastqQc, setRunFastqQc] = useState<boolean>(
        (initialValues?.runFastqQc as boolean | undefined)
        ?? (initialValues?.runMultimerQc as boolean | undefined)
        ?? true
    );
    const [expectedPlasmidSize, setExpectedPlasmidSize] = useState<number>(() => coerceIntegerInput(
        initialValues?.expectedPlasmidSize ?? initialValues?.expected_plasmid_size,
        FASTQ_DEFAULT_EXPECTED_PLASMID_SIZE_BP,
        1,
        FASTQ_MAX_EXPECTED_PLASMID_SIZE_BP,
    ));
    const [minFastqReadLength, setMinFastqReadLength] = useState<number>(() => coerceIntegerInput(
        initialValues?.minFastqReadLength ?? initialValues?.min_fastq_read_length,
        FASTQ_DEFAULT_MIN_READ_LENGTH_BP,
        0,
        FASTQ_MAX_MIN_READ_LENGTH_BP,
    ));
    const [fastqMinimap2Preset, setFastqMinimap2Preset] = useState<MinimapPreset>(() => normalizeFastqMinimapPreset(
        initialValues?.fastqMinimap2Preset ?? initialValues?.fastq_minimap2_preset,
    ));
    const [fastqMinimap2AllowSecondary, setFastqMinimap2AllowSecondary] = useState<boolean>(
        (initialValues?.fastqMinimap2AllowSecondary as boolean | undefined)
        ?? (initialValues?.fastq_minimap2_allow_secondary as boolean | undefined)
        ?? true
    );
    const [igvTrackWindowBp, setIgvTrackWindowBp] = useState<number>(() => coerceIntegerInput(
        initialValues?.igvTrackWindowBp ?? initialValues?.igv_track_window_bp,
        FASTQ_DEFAULT_IGV_TRACK_WINDOW_BP,
        1,
        FASTQ_MAX_IGV_TRACK_WINDOW_BP,
    ));
    const [igvReportMaxSites, setIgvReportMaxSites] = useState<number>(() => coerceIntegerInput(
        initialValues?.igvReportMaxSites ?? initialValues?.igv_report_max_sites,
        FASTQ_DEFAULT_IGV_REPORT_MAX_SITES,
        1,
        FASTQ_MAX_IGV_REPORT_MAX_SITES,
    ));
    const [igvReportFlankingBp, setIgvReportFlankingBp] = useState<number>(() => coerceIntegerInput(
        initialValues?.igvReportFlankingBp ?? initialValues?.igv_report_flanking_bp,
        FASTQ_DEFAULT_IGV_REPORT_FLANKING_BP,
        0,
        FASTQ_MAX_IGV_REPORT_FLANKING_BP,
    ));
    const [runAssembly, setRunAssembly] = useState(initialValues?.runAssembly as boolean || false);
    const [assemblyTool, setAssemblyTool] = useState<AssemblyTool>(initialValues?.assemblyTool as AssemblyTool || 'flye');
    const [assemblyApproxSize, setAssemblyApproxSize] = useState<number>(initialValues?.assemblyApproxSize as number || 7000);
    const [assemblyCoverage, setAssemblyCoverage] = useState<number>(initialValues?.assemblyCoverage as number || 60);
    const [assemblyTrimLength, setAssemblyTrimLength] = useState<number>(initialValues?.assemblyTrimLength as number || 0);
    const [assemblyMinQuality, setAssemblyMinQuality] = useState<number>(initialValues?.assemblyMinQuality as number || 9);
    const [wfCloneSample, setWfCloneSample] = useState(initialValues?.wfCloneSample as string || '');
    const [wfCloneLargeConstruct, setWfCloneLargeConstruct] = useState(initialValues?.wfCloneLargeConstruct === true);
    const [wfCloneFlyeQuality, setWfCloneFlyeQuality] = useState<FlyeReadQuality>(
        initialValues?.wfCloneFlyeQuality as FlyeReadQuality || 'nano-hq',
    );
    const [wfCloneNonUniformCoverage, setWfCloneNonUniformCoverage] = useState(initialValues?.wfCloneNonUniformCoverage === true);
    const [wfCloneCanuFast, setWfCloneCanuFast] = useState(initialValues?.wfCloneCanuFast === true);
    const [wfCloneCutsiteMismatch, setWfCloneCutsiteMismatch] = useState<number>(
        coerceIntegerInput(initialValues?.wfCloneCutsiteMismatch, 1, 0, 10),
    );
    const [wfClonePrimerMismatch, setWfClonePrimerMismatch] = useState<number>(
        coerceIntegerInput(initialValues?.wfClonePrimerMismatch, 2, 0, 10),
    );
    const [wfCloneExpectedCoverage, setWfCloneExpectedCoverage] = useState<number>(
        Number(initialValues?.wfCloneExpectedCoverage ?? 95),
    );
    const [wfCloneExpectedIdentity, setWfCloneExpectedIdentity] = useState<number>(
        Number(initialValues?.wfCloneExpectedIdentity ?? 99),
    );
    const [enableRotatingReferenceFrames, setEnableRotatingReferenceFrames] = useState(initialValues?.enableRotatingReferenceFrames !== false);
    const [rotationScanStepBp, setRotationScanStepBp] = useState(() => coerceIntegerInput(initialValues?.rotationScanStepBp, 1, 1, 10_000));
    const [singleRefSplitMinMapq, setSingleRefSplitMinMapq] = useState(() => coerceIntegerInput(initialValues?.singleRefSplitMinMapq, 20, 0, 60));
    const [singleRefSplitMinSegmentBp, setSingleRefSplitMinSegmentBp] = useState(() => coerceIntegerInput(initialValues?.singleRefSplitMinSegmentBp, 250, 1, 1_000_000));
    const [singleRefSplitMaxQueryGapBp, setSingleRefSplitMaxQueryGapBp] = useState(() => coerceIntegerInput(initialValues?.singleRefSplitMaxQueryGapBp, 500, 0, 1_000_000));
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(() => {
        const raw = (initialValues?.pinnedGpus ?? initialValues?.pinned_gpus ?? initialValues?.pinned_gpu) as unknown;
        if (Array.isArray(raw)) {
            return raw
                .map((value) => Number(value))
                .filter((value) => Number.isInteger(value) && value >= 0)
                .sort((a, b) => a - b);
        }
        const single = Number(raw);
        if (Number.isInteger(single) && single >= 0) {
            return [single];
        }
        return [];
    });
    const [lockGpus, setLockGpus] = useState<boolean>(() => {
        const raw = (initialValues?.lockGpus ?? initialValues?.lock_gpus) as unknown;
        return raw === true;
    });

    // ============================================================================
    // State: Advanced (collapsed by default)
    // ============================================================================
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [batchSize, setBatchSize] = useState<number | null>((initialValues?.batchSize as number | null | undefined) ?? null);
    const [emitSummary, setEmitSummary] = useState(initialValues?.emitSummary !== false);
    const [modkitFilterThreshold, setModkitFilterThreshold] = useState<number | null>(
        (initialValues?.modkitFilterThreshold as number | null | undefined) ?? null
    );

    // ============================================================================
    // State: UI
    // ============================================================================
    const [error, setError] = useState<string | null>(null);
    const [pathPicker, setPathPicker] = useState<PathPickerState | null>(null);
    const [browserPath, setBrowserPath] = useState<string>('/');
    const [referenceTab, setReferenceTab] = useState<ReferenceTab>('browse');
    const [pastedFasta, setPastedFasta] = useState('');
    const [newFastaName, setNewFastaName] = useState('');
    const [newFastaSeq, setNewFastaSeq] = useState('');
    const [savedReferences, setSavedReferences] = useState<SavedReferenceEntry[]>(() => parseReferenceLibrary());
    const [selectedSavedReferenceId, setSelectedSavedReferenceId] = useState('');
    const [saveReferenceName, setSaveReferenceName] = useState('');
    const [referenceLibraryNotice, setReferenceLibraryNotice] = useState<string | null>(null);

    const { data: browserData, isLoading: browserLoading } = useQuery({
        queryKey: ['files', browserPath, pathPicker?.field, pathPicker?.mode, pathPicker?.filter],
        queryFn: () => fetchFiles(browserPath),
        enabled: pathPicker !== null,
    });

    const browserEntries = browserData?.data?.entries ?? [];

    // ============================================================================
    // Computed
    // ============================================================================
    const hasFastqReferenceInput = useMemo(() => {
        if (referenceTab === 'browse') return referencePath.trim() !== '';
        if (referenceTab === 'paste') return normalizeFastaText(pastedFasta) !== null;
        if (!newFastaName.trim() || !newFastaSeq.trim()) return false;
        return normalizeFastaText(`>${newFastaName.trim()}\n${newFastaSeq.replace(/\s/g, '').toUpperCase()}`) !== null;
    }, [newFastaName, newFastaSeq, pastedFasta, referencePath, referenceTab]);

    const hasValidFastqNumericControls = useMemo(() => (
        isIntegerInRange(expectedPlasmidSize, 1, FASTQ_MAX_EXPECTED_PLASMID_SIZE_BP)
        && isIntegerInRange(minFastqReadLength, 0, FASTQ_MAX_MIN_READ_LENGTH_BP)
        && isIntegerInRange(igvTrackWindowBp, 1, FASTQ_MAX_IGV_TRACK_WINDOW_BP)
        && isIntegerInRange(igvReportMaxSites, 1, FASTQ_MAX_IGV_REPORT_MAX_SITES)
        && isIntegerInRange(igvReportFlankingBp, 0, FASTQ_MAX_IGV_REPORT_FLANKING_BP)
    ), [expectedPlasmidSize, igvReportFlankingBp, igvReportMaxSites, igvTrackWindowBp, minFastqReadLength]);

    const canSubmit = useMemo(() => {
        if (!jobName.trim()) return false;
        const requiresReference = selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'bamQc' || selectedWorkflow === 'modified';
        if (requiresReference && !hasFastqReferenceInput) return false;
        if (inputSource === 'pod5') return pod5Dir.trim() !== '';
        if (inputSource === 'bam') return bamPath.trim() !== '';
        // FASTQ can run either BMS plasmid QC or the vendor clone-validation
        // workflow. They are deliberately mutually exclusive, not sequential.
        return fastqPath.trim() !== ''
            && (runFastqQc || runAssembly)
            && hasFastqReferenceInput
            && hasValidFastqNumericControls;
    }, [jobName, inputSource, pod5Dir, bamPath, fastqPath, runAssembly, runFastqQc, hasFastqReferenceInput, hasValidFastqNumericControls, selectedWorkflow]);
    const selectedSavedReference = useMemo(
        () => savedReferences.find((entry) => entry.id === selectedSavedReferenceId) || null,
        [savedReferences, selectedSavedReferenceId]
    );

    const methylationEnabled = modifiedBases !== 'none';
    const canRunModkit = selectedWorkflow === 'modified' && methylationEnabled;
    const fastqCliPreview = useMemo(() => {
        if (inputSource !== 'fastq' || !runFastqQc) return '';
        const referenceHint = referencePath.trim() || '<uploaded/pasted FASTA>';
        return [
            `--fastq_path ${fastqPath || '<fastq path>'}`,
            `--reference_fasta ${referenceHint}`,
            `--run_fastq_qc true`,
            `--expected_plasmid_size ${expectedPlasmidSize}`,
            `--min_fastq_read_length ${minFastqReadLength}`,
            `--fastq_minimap2_preset ${fastqMinimap2Preset}`,
            `--fastq_minimap2_allow_secondary ${fastqMinimap2AllowSecondary}`,
            `--igv_track_window_bp ${igvTrackWindowBp}`,
            `--igv_report_max_sites ${igvReportMaxSites}`,
            `--igv_report_flanking_bp ${igvReportFlankingBp}`,
        ].join(' \\\n  ');
    }, [
        expectedPlasmidSize,
        fastqMinimap2AllowSecondary,
        fastqMinimap2Preset,
        fastqPath,
        igvReportFlankingBp,
        igvReportMaxSites,
        igvTrackWindowBp,
        inputSource,
        minFastqReadLength,
        referencePath,
        runFastqQc,
    ]);

    const applySavedReferences = (entries: SavedReferenceEntry[]) => {
        const sorted = [...entries].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
        setSavedReferences(sorted);
        persistReferenceLibrary(sorted);
    };

    const resolveCurrentReferenceDraft = (): { source: 'fasta' | 'path'; fasta?: string; path?: string; suggestedName: string } | null => {
        if (referenceTab === 'browse') {
            const normalizedPath = referencePath.trim();
            if (!normalizedPath) return null;
            const suggestedName = inferReferenceNameFromPath(normalizedPath) || 'reference_path';
            return {
                source: 'path',
                path: normalizedPath,
                suggestedName,
            };
        }

        const createTabFasta = (
            referenceTab === 'create'
            && newFastaName.trim()
            && newFastaSeq.trim()
        )
            ? `>${newFastaName.trim()}\n${newFastaSeq.replace(/\s/g, '').toUpperCase()}`
            : '';
        const manualFastaText = referenceTab === 'paste'
            ? pastedFasta
            : (referenceTab === 'create' ? createTabFasta : '');
        const normalizedFasta = normalizeFastaText(manualFastaText);
        if (!normalizedFasta) return null;
        const suggestedName = inferReferenceNameFromFasta(normalizedFasta) || (newFastaName.trim() || 'reference_fasta');
        return {
            source: 'fasta',
            fasta: normalizedFasta,
            suggestedName,
        };
    };

    const handleSaveCurrentReference = () => {
        setReferenceLibraryNotice(null);
        const draft = resolveCurrentReferenceDraft();
        if (!draft) {
            setReferenceLibraryNotice('No valid reference to save. Choose a FASTA path or provide valid FASTA text.');
            return;
        }

        const desiredName = normalizeReferenceLabel(saveReferenceName || draft.suggestedName);
        const now = new Date().toISOString();
        const existing = savedReferences.find((entry) => entry.name.toLowerCase() === desiredName.toLowerCase());
        let nextSelectedId = '';
        let nextEntries: SavedReferenceEntry[] = [];

        if (existing) {
            const updated: SavedReferenceEntry = {
                ...existing,
                source: draft.source,
                fasta: draft.source === 'fasta' ? draft.fasta : undefined,
                path: draft.source === 'path' ? draft.path : undefined,
                updatedAt: now,
            };
            nextSelectedId = updated.id;
            nextEntries = savedReferences.map((entry) => (entry.id === existing.id ? updated : entry));
            setReferenceLibraryNotice(`Updated saved reference "${desiredName}".`);
        } else {
            const created: SavedReferenceEntry = {
                id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                name: desiredName,
                source: draft.source,
                fasta: draft.source === 'fasta' ? draft.fasta : undefined,
                path: draft.source === 'path' ? draft.path : undefined,
                createdAt: now,
                updatedAt: now,
            };
            nextSelectedId = created.id;
            nextEntries = [...savedReferences, created];
            setReferenceLibraryNotice(`Saved reference "${desiredName}".`);
        }

        applySavedReferences(nextEntries);
        setSelectedSavedReferenceId(nextSelectedId);
        setSaveReferenceName('');
    };

    const handleLoadSavedReference = () => {
        const target = selectedSavedReference || savedReferences[0] || null;
        if (!target) {
            setReferenceLibraryNotice('Select a saved reference to load.');
            return;
        }
        if (target.source === 'path' && target.path) {
            setReferenceTab('browse');
            setReferencePath(target.path);
            setSelectedSavedReferenceId(target.id);
            setReferenceLibraryNotice(`Loaded saved path reference "${target.name}".`);
            return;
        }
        if (target.source === 'fasta' && target.fasta) {
            const normalized = normalizeFastaText(target.fasta) || target.fasta;
            const records = parseFastaRecordsForTemplate(normalized);
            if (records.length === 1) {
                setReferenceTab('create');
                setNewFastaName(records[0].name);
                setNewFastaSeq(records[0].sequence);
            } else {
                setReferenceTab('paste');
            }
            setReferencePath('');
            setPastedFasta(normalized);
            setSelectedSavedReferenceId(target.id);
            setReferenceLibraryNotice(`Loaded saved FASTA reference "${target.name}".`);
            return;
        }
        setReferenceLibraryNotice(`Saved reference "${target.name}" is missing content.`);
    };

    const handleDeleteSavedReference = () => {
        if (!selectedSavedReference) {
            setReferenceLibraryNotice('Select a saved reference to delete.');
            return;
        }
        const confirmed = window.confirm(`Delete saved reference "${selectedSavedReference.name}"?`);
        if (!confirmed) return;
        const filtered = savedReferences.filter((entry) => entry.id !== selectedSavedReference.id);
        applySavedReferences(filtered);
        setSelectedSavedReferenceId('');
        setReferenceLibraryNotice(`Deleted saved reference "${selectedSavedReference.name}".`);
    };

    // ============================================================================
    // Job Submission
    // ============================================================================
    const submitMutation = useMutation({
        mutationFn: async () => {
            let effectiveReferencePath = '';

            const createTabFasta = (
                referenceTab === 'create'
                && newFastaName.trim()
                && newFastaSeq.trim()
            )
                ? `>${newFastaName.trim()}\n${newFastaSeq.replace(/\s/g, '').toUpperCase()}`
                : '';
            const manualFastaText = referenceTab === 'paste'
                ? pastedFasta
                : (referenceTab === 'create' ? createTabFasta : '');

            if (referenceTab === 'browse') {
                effectiveReferencePath = referencePath.trim();
            } else if (manualFastaText.trim()) {
                const normalizedFasta = normalizeFastaText(manualFastaText);
                if (!normalizedFasta) {
                    throw new Error('Reference FASTA is invalid. Provide FASTA headers and A/C/G/T/N sequence.');
                }
                const stemSource = referenceTab === 'create' && newFastaName.trim()
                    ? newFastaName.trim()
                    : (jobName.trim() || 'nanopore_reference');
                const fileName = `${sanitizeFileStem(stemSource)}_${Date.now()}.fasta`;
                const fastaFile = new File([normalizedFasta], fileName, { type: 'text/plain' });
                const uploadResponse = await uploadFile('inputs/nanopore/references', fastaFile);
                const uploadedPath = String(uploadResponse?.data?.path || '').trim();
                if (!uploadedPath) {
                    throw new Error('Reference FASTA upload succeeded but no server path was returned.');
                }
                effectiveReferencePath = uploadedPath;
            }

            const requiresReference = selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'bamQc' || selectedWorkflow === 'modified';
            if (requiresReference && !effectiveReferencePath) {
                throw new Error('This workflow requires a reference FASTA (path or pasted sequence).');
            }

            const workflowId = selectedWorkflow === 'clone'
                ? 'wf_clone_validation'
                : selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'bamQc'
                    ? 'ont_plasmid_qc'
                    : selectedWorkflow === 'rna'
                        ? 'ont_basecall_rna'
                        : selectedWorkflow === 'modified'
                            ? 'ont_methylation_analysis'
                            : 'ont_basecall_dna';
            const jobPayload = {
                name: jobName || `nanopore_${Date.now()}`,
                params: {
                    reference_fasta: effectiveReferencePath || undefined,
                    min_qscore: inputSource === 'pod5' ? minQscore : undefined,
                    run_modkit: runModkit && canRunModkit,
                    run_fastq_qc: inputSource === 'fastq' && !runAssembly ? runFastqQc : false,
                    run_multimer_qc: inputSource === 'fastq' && !runAssembly ? runFastqQc : false,
                    run_assembly: selectedWorkflow === 'clone',
                    ...(inputSource === 'pod5' && {
                        pod5_dir: pod5Dir,
                        dorado_quality_mode: doradoModel,
                        dorado_basecall_mode: doradoMode,
                        ont_molecule_type: doradoMolecule,
                        modified_bases: modifiedBases,
                        duplex_pairs: doradoMode === 'duplex' ? duplexPairs : undefined,
                        barcode_kit: barcodeKit || undefined,
                        sample_sheet: barcodeKit ? (sampleSheet || undefined) : undefined,
                        trim_adapters: trimAdapters,
                        emit_summary: emitSummary,
                        ...(batchSize !== null && { dorado_batch_size: batchSize }),
                    }),
                    ...(inputSource === 'bam' && {
                        bam_path: bamPath,
                        bam_force_realign: bamForceRealign,
                        bam_min_mapq: bamMinMapq,
                    }),
                    ...(inputSource === 'fastq' && {
                        fastq_path: fastqPath,
                        expected_plasmid_size: expectedPlasmidSize,
                        min_fastq_read_length: minFastqReadLength,
                        fastq_minimap2_preset: fastqMinimap2Preset,
                        fastq_minimap2_allow_secondary: fastqMinimap2AllowSecondary,
                        igv_track_window_bp: igvTrackWindowBp,
                        igv_report_max_sites: igvReportMaxSites,
                        igv_report_flanking_bp: igvReportFlankingBp,
                    }),
                    ...(selectedWorkflow === 'clone' && {
                        wf_clone_assembly_tool: assemblyTool,
                        wf_clone_approx_size: assemblyApproxSize,
                        wf_clone_assm_coverage: assemblyCoverage,
                        wf_clone_trim_length: assemblyTrimLength,
                        wf_clone_min_quality: assemblyMinQuality,
                        wf_clone_large_construct: wfCloneLargeConstruct,
                        wf_clone_flye_quality: wfCloneFlyeQuality,
                        wf_clone_non_uniform_coverage: wfCloneNonUniformCoverage,
                        wf_clone_canu_fast: wfCloneCanuFast,
                        wf_clone_cutsite_mismatch: wfCloneCutsiteMismatch,
                        wf_clone_primer_mismatch: wfClonePrimerMismatch,
                        wf_clone_expected_coverage: wfCloneExpectedCoverage,
                        wf_clone_expected_identity: wfCloneExpectedIdentity,
                        ...(wfCloneSample.trim() && { wf_clone_sample: wfCloneSample.trim() }),
                    }),
                    ...((selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'bamQc') && {
                        enable_rotating_reference_frames: enableRotatingReferenceFrames,
                        rotation_scan_step_bp: rotationScanStepBp,
                        single_ref_split_min_mapq: singleRefSplitMinMapq,
                        single_ref_split_min_segment_bp: singleRefSplitMinSegmentBp,
                        single_ref_split_max_query_gap_bp: singleRefSplitMaxQueryGapBp,
                    }),
                    ...(runModkit && canRunModkit && modkitFilterThreshold != null && { modkit_filter_threshold: modkitFilterThreshold }),
                }
            };
            return submitOntNgsJob(workflowId, jobPayload);
        },
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            const submittedJobId = response.data?.id;
            if (submittedJobId) {
                navigate(`/jobs/${submittedJobId}`);
                return;
            }
            navigate('/ngs');
        },
        onError: (err: unknown) => {
            setError(extractApiErrorMessage(err));
        }
    });

    const handleSubmit = () => {
        setError(null);
        if (!jobName.trim()) {
            setError('Please enter a job name');
            return;
        }
        if (inputSource === 'pod5' && !pod5Dir.trim()) {
            setError('Please specify a POD5 data directory');
            return;
        }
        if (inputSource === 'pod5' && doradoMolecule === 'rna' && doradoMode === 'duplex') {
            setError('RNA duplex is unsupported by the locked Dorado runtime.');
            return;
        }
        if (inputSource === 'pod5' && doradoMode === 'duplex' && !duplexPairs.trim()) {
            setError('Duplex basecalling requires a confined read-pairs file.');
            return;
        }
        if (inputSource === 'pod5' && doradoMode === 'duplex' && barcodeKit) {
            setError('Barcode classification and duplex cannot be combined in the locked runtime.');
            return;
        }
        if (inputSource === 'pod5' && modifiedBases !== 'none' && (doradoMolecule !== 'dna' || doradoModel !== 'hac' || doradoMode !== 'simplex')) {
            setError('Modified-base calling requires DNA HAC simplex.');
            return;
        }
        if (inputSource === 'bam' && !bamPath.trim()) {
            setError('Please specify a BAM file path');
            return;
        }
        if (inputSource === 'fastq' && !fastqPath.trim()) {
            setError('Please specify a FASTQ file path');
            return;
        }
        if (inputSource === 'fastq' && !runFastqQc && !runAssembly) {
            setError('Choose either FASTQ plasmid QC or vendor clone validation before submitting a FASTQ analysis.');
            return;
        }
        const requiresReference = selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'bamQc' || selectedWorkflow === 'modified';
        if (requiresReference && !hasFastqReferenceInput) {
            setError('This workflow requires a reference FASTA path or a pasted/created FASTA sequence.');
            return;
        }
        if (inputSource === 'fastq' && !hasValidFastqNumericControls) {
            setError('FASTQ QC numeric controls must be finite integers within the displayed bounds.');
            return;
        }
        submitMutation.mutate();
    };

    const getPathFieldValue = (field: PathField): string => {
        if (field === 'pod5Dir') return pod5Dir;
        if (field === 'bamPath') return bamPath;
        if (field === 'fastqPath') return fastqPath;
        return referencePath;
    };

    const setPathFieldValue = (field: PathField, value: string) => {
        if (field === 'pod5Dir') {
            setPod5Dir(value);
            return;
        }
        if (field === 'bamPath') {
            setBamPath(value);
            return;
        }
        if (field === 'fastqPath') {
            setFastqPath(value);
            return;
        }
        setReferencePath(value);
    };

    const openPathPicker = (next: PathPickerState) => {
        const currentValue = getPathFieldValue(next.field);
        const startPath = currentValue
            ? (next.mode === 'directory' ? normalizeBrowserPath(currentValue) : parentBrowserPath(currentValue))
            : '/';
        setBrowserPath(startPath);
        setPathPicker(next);
    };

    const closePathPicker = () => {
        setPathPicker(null);
        setBrowserPath('/');
    };

    const selectPathFromBrowser = (path: string) => {
        if (!pathPicker) return;
        setPathFieldValue(pathPicker.field, normalizeBrowserPath(path));
        closePathPicker();
    };

    const normalizedBrowserPath = normalizeBrowserPath(browserPath);
    const canSelectCurrentDirectory = Boolean(
        pathPicker && pathPicker.mode === 'directory' && normalizedBrowserPath !== '/'
    );

    const selectWorkflow = (workflow: WorkflowKey) => {
        setError(null);
        setSelectedWorkflow(workflow);
        if (workflow === 'clone') {
            setInputSource('fastq');
            setRunAssembly(true);
            setRunFastqQc(false);
            setRunModkit(false);
            setBarcodeKit('');
            setSampleSheet('');
            setShowAdvanced(true);
            return;
        }
        if (workflow === 'plasmidQc') {
            setInputSource('fastq');
            setRunAssembly(false);
            setRunFastqQc(true);
            setRunModkit(false);
            setBarcodeKit('');
            return;
        }
        if (workflow === 'bamQc') {
            setInputSource('bam');
            setRunAssembly(false);
            setRunFastqQc(false);
            setRunModkit(false);
            setBarcodeKit('');
            return;
        }
        setInputSource('pod5');
        setRunAssembly(false);
        setRunFastqQc(false);
        if (workflow === 'rna') {
            setDoradoMolecule('rna');
            setDoradoMode('simplex');
            setModifiedBases('none');
            setBarcodeKit('');
            setRunModkit(false);
            return;
        }
        setDoradoMolecule('dna');
        if (workflow === 'duplex') {
            setDoradoMode('duplex');
            setModifiedBases('none');
            setBarcodeKit('');
            setRunModkit(false);
            return;
        }
        setDoradoMode('simplex');
        if (workflow === 'modified') {
            setDoradoModel('hac');
            setModifiedBases('5mC_5hmC');
            setBarcodeKit('');
            setRunModkit(true);
            return;
        }
        if (workflow === 'barcode') {
            setModifiedBases('none');
            setBarcodeKit('SQK-RBK114-96');
            setRunModkit(false);
            return;
        }
        setModifiedBases('none');
        setBarcodeKit('');
        setRunModkit(false);
    };

    // ============================================================================
    // Render
    // ============================================================================
    return (
        <div className="nanopore-template p-4 lg:p-6 space-y-5 max-w-[1440px] mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <button
                        onClick={onBack}
                        className="p-2 hover:bg-[var(--bg-tertiary)] rounded-lg transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    >
                        <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M17 10a.75.75 0 01-.75.75H5.612l4.158 3.96a.75.75 0 11-1.04 1.08l-5.5-5.25a.75.75 0 010-1.08l5.5-5.25a.75.75 0 111.04 1.08L5.612 9.25H16.25A.75.75 0 0117 10z" clipRule="evenodd" />
                        </svg>
                    </button>
                    <div className="text-[var(--accent-secondary)]">
                        <NanoporeIcon />
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold text-[var(--text-primary)]">Nanopore Sequencing</h1>
                    </div>
                </div>
            </div>

            {/* Operator-first workflow chooser: do not make users infer a workflow from low-level switches. */}
            <section className="bg-[var(--bg-secondary)] rounded-lg p-4 space-y-3" aria-label="Choose an NGS workflow">
                <div>
                    <h2 className="text-base font-semibold text-[var(--text-primary)]">Choose what you want to do</h2>
                    <p className="text-sm text-[var(--text-secondary)] mt-1">Choose one path first. The form below then exposes only its compatible inputs and locked runtime settings.</p>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                    {[
                        { key: 'clone' as const, title: 'Validate a known plasmid / clone from FASTQ', input: 'FASTQ + expected construct FASTA', result: 'Vendor wf-clone-validation HTML report, assembly, construct evidence', tone: 'border-[var(--accent-secondary)]' },
                        { key: 'plasmidQc' as const, title: 'QC plasmid reads', input: 'FASTQ + reference FASTA', result: 'BMS plasmid QC, alignment and generic multimer evidence', tone: 'border-[var(--border-primary)]' },
                        { key: 'bamQc' as const, title: 'Analyze aligned plasmid BAM', input: 'BAM + reference FASTA', result: 'BMS plasmid QC and alignment evidence', tone: 'border-[var(--border-primary)]' },
                        { key: 'dna' as const, title: 'Basecall DNA simplex', input: 'DNA POD5', result: 'Dorado calls BAM, summary and runtime provenance', tone: 'border-[var(--border-primary)]' },
                        { key: 'rna' as const, title: 'Basecall RNA', input: 'RNA POD5', result: 'RNA004 simplex calls BAM, trimming and provenance', tone: 'border-[var(--border-primary)]' },
                        { key: 'duplex' as const, title: 'Basecall DNA duplex', input: 'DNA POD5 + validated read-pairs file', result: 'Duplex BAM with retained stereo model provenance', tone: 'border-[var(--border-primary)]' },
                        { key: 'modified' as const, title: 'Call modified bases', input: 'DNA POD5', result: 'HAC simplex calls plus modkit tables', tone: 'border-[var(--border-primary)]' },
                        { key: 'barcode' as const, title: 'Classify and demultiplex RBK114', input: 'DNA POD5', result: 'Canonical barcodeNN units and demux outputs', tone: 'border-[var(--border-primary)]' },
                    ].map((workflow) => (
                        <button
                            key={workflow.key}
                            type="button"
                            onClick={() => selectWorkflow(workflow.key)}
                            aria-pressed={selectedWorkflow === workflow.key}
                            className={`rounded-lg border-2 p-3 text-left hover:bg-[var(--bg-tertiary)] transition-colors ${selectedWorkflow === workflow.key ? 'border-[var(--accent-secondary)] bg-[color-mix(in_srgb,var(--accent-secondary)_12%,transparent)] ring-1 ring-[var(--accent-secondary)]' : workflow.tone}`}
                        >
                            <div className="font-medium text-[var(--text-primary)] text-sm">{workflow.title}</div>
                            <div className="text-xs text-[var(--text-secondary)] mt-1"><strong>Provide:</strong> {workflow.input}</div>
                            <div className="text-xs text-[var(--text-secondary)] mt-1"><strong>Get:</strong> {workflow.result}</div>
                        </button>
                    ))}
                </div>
                <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/50 p-3 text-xs text-[var(--text-secondary)]">
                    <strong className="text-[var(--text-primary)]">How to use this page:</strong> choose a workflow → choose the requested input with Browse or paste/create the reference → enter a job name → submit. Completed jobs appear through <strong className="text-[var(--text-primary)]">Runs</strong>; no completed sequencing results are shown until an input is actually processed. Selecting a workflow never starts an instrument run.
                </div>
            </section>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
            {/* Job Name */}
            <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">Job Name *</label>
                <input
                    type="text"
                    value={jobName}
                    onChange={(e) => setJobName(e.target.value)}
                    placeholder="my_nanopore_run"
                    className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 text-[var(--text-primary)]"
                />
                {false && (
                    <div className="mt-4">
                        <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                            GPU Pinning {pinnedGpus.length > 0 && <span className="text-[var(--accent-secondary)]">({pinnedGpus.length} selected)</span>}
                        </label>
                        <div className="flex flex-wrap gap-2">
                            <button
                                onClick={() => setPinnedGpus([])}
                                className={`px-3 py-2 rounded border text-sm transition-colors ${pinnedGpus.length === 0
                                    ? 'text-[var(--text-primary)]'
                                    : 'text-[var(--text-secondary)] border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)]'
                                    }`}
                                style={pinnedGpus.length === 0
                                    ? {
                                        borderColor: 'var(--accent-secondary)',
                                        backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                                    }
                                    : undefined}
                            >
                                Auto
                            </button>
                            {gpuOptions.map((gpu) => (
                                <button
                                    key={gpu.index}
                                    onClick={() => {
                                        setPinnedGpus((prev) => (
                                            prev.includes(gpu.index)
                                                ? prev.filter((g) => g !== gpu.index)
                                                : [...prev, gpu.index].sort((a, b) => a - b)
                                        ));
                                    }}
                                    className={`px-3 py-2 rounded border text-sm transition-colors ${pinnedGpus.includes(gpu.index)
                                        ? 'text-[var(--text-primary)]'
                                        : 'text-[var(--text-secondary)] border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)]'
                                        }`}
                                    style={pinnedGpus.includes(gpu.index)
                                        ? {
                                            borderColor: 'var(--accent-secondary)',
                                            backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                                        }
                                        : undefined}
                                >
                                    {gpu.label}
                                </button>
                            ))}
                        </div>
                        {pinnedGpus.length > 0 && (
                            <label className="mt-3 flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={lockGpus}
                                    onChange={(e) => setLockGpus(e.target.checked)}
                                    className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                                />
                                <span className="text-xs text-[var(--text-secondary)]">Lock selected GPU(s)</span>
                            </label>
                        )}
                    </div>
                )}
            </div>

            {/* Data Source */}
            <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">Primary Input *</label>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-3">
                    {[selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc'
                        ? { key: 'fastq' as const, label: 'FASTQ Analysis' }
                        : selectedWorkflow === 'bamQc'
                            ? { key: 'bam' as const, label: 'Existing BAM' }
                            : { key: 'pod5' as const, label: 'POD5 Raw Reads' }
                    ].map((source) => (
                        <button
                            key={source.key}
                            onClick={() => setInputSource(source.key)}
                            className={`px-3 py-2 rounded border text-sm text-left transition-colors ${inputSource === source.key
                                ? ''
                                : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)]'
                                }`}
                            style={inputSource === source.key ? {
                                borderColor: 'var(--accent-secondary)',
                                backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                                color: 'var(--text-primary)',
                            } : undefined}
                        >
                            {source.label}
                        </button>
                    ))}
                </div>

                {inputSource === 'pod5' && (
                    <>
                        <div className="flex items-center gap-2">
                            <div className="flex-1 bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 min-h-10">
                                {pod5Dir ? (
                                    <span className="text-[var(--text-primary)] font-mono text-sm break-all">{formatPathDisplay(pod5Dir)}</span>
                                ) : (
                                    <span className="text-[var(--text-secondary)] text-sm">Select POD5 directory</span>
                                )}
                            </div>
                            <button
                                onClick={() => openPathPicker({ field: 'pod5Dir', title: 'Select POD5 Directory', mode: 'directory', filter: 'unknown' })}
                                className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-primary)] text-sm hover:bg-[var(--bg-tertiary)] transition-colors"
                            >
                                Browse
                            </button>
                            {pod5Dir && (
                                <button
                                    onClick={() => setPod5Dir('')}
                                    className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                    </>
                )}

                {inputSource === 'bam' && (
                    <>
                        <div className="flex items-center gap-2">
                            <div className="flex-1 bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 min-h-10">
                                {bamPath ? (
                                    <span className="text-[var(--text-primary)] font-mono text-sm break-all">{formatPathDisplay(bamPath)}</span>
                                ) : (
                                    <span className="text-[var(--text-secondary)] text-sm">Select BAM file</span>
                                )}
                            </div>
                            <button
                                onClick={() => openPathPicker({ field: 'bamPath', title: 'Select BAM File', mode: 'file', filter: 'bam' })}
                                className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-primary)] text-sm hover:bg-[var(--bg-tertiary)] transition-colors"
                            >
                                Browse
                            </button>
                            {bamPath && (
                                <button
                                    onClick={() => setBamPath('')}
                                    className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                        <p className="text-xs text-[var(--text-secondary)] mt-1">
                            BAM pass-through by default; realign only when needed.
                        </p>
                    </>
                )}

                {inputSource === 'fastq' && (
                    <>
                        <div className="flex items-center gap-2">
                            <div className="flex-1 bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 min-h-10">
                                {fastqPath ? (
                                    <span className="text-[var(--text-primary)] font-mono text-sm break-all">{formatPathDisplay(fastqPath)}</span>
                                ) : (
                                    <span className="text-[var(--text-secondary)] text-sm">Select FASTQ file</span>
                                )}
                            </div>
                            <button
                                onClick={() => openPathPicker({ field: 'fastqPath', title: 'Select FASTQ File', mode: 'file', filter: 'fastq' })}
                                className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-primary)] text-sm hover:bg-[var(--bg-tertiary)] transition-colors"
                            >
                                Browse
                            </button>
                            {fastqPath && (
                                <button
                                    onClick={() => setFastqPath('')}
                                    className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                    </>
                )}
            </div>

            {/* Reference FASTA — tabbed input */}
            <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">Reference FASTA</label>
                <div className="flex gap-1 mb-3">
                    {(['browse', 'paste', 'create'] as ReferenceTab[]).map((tab) => (
                        <button
                            key={tab}
                            onClick={() => setReferenceTab(tab)}
                            className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${referenceTab === tab
                                ? 'text-[var(--text-primary)]'
                                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'
                                }`}
                            style={referenceTab === tab ? {
                                backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 18%, transparent)',
                                borderColor: 'var(--accent-secondary)',
                            } : undefined}
                        >
                            {tab === 'browse' ? 'Browse File' : tab === 'paste' ? 'Paste FASTA' : 'Create New'}
                        </button>
                    ))}
                </div>

                <div className="mb-3 p-3 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/40 space-y-2">
                    <div className="text-xs font-medium text-[var(--text-secondary)]">Saved References</div>
                    <div className="flex flex-wrap items-center gap-2">
                        <select
                            value={selectedSavedReferenceId}
                            onChange={(e) => setSelectedSavedReferenceId(e.target.value)}
                            className="min-w-[14rem] flex-1 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded px-3 py-2 text-[var(--text-primary)] text-sm"
                        >
                            <option value="">Select saved reference...</option>
                            {savedReferences.map((entry) => (
                                <option key={entry.id} value={entry.id}>
                                    {entry.name} {entry.source === 'path' ? '(path)' : '(fasta)'}
                                </option>
                            ))}
                        </select>
                        <button
                            onClick={handleLoadSavedReference}
                            disabled={savedReferences.length === 0}
                            className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-primary)] text-sm hover:bg-[var(--bg-secondary)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            Load
                        </button>
                        <button
                            onClick={handleDeleteSavedReference}
                            disabled={!selectedSavedReference}
                            className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] hover:bg-[var(--bg-secondary)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            Delete
                        </button>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <input
                            type="text"
                            value={saveReferenceName}
                            onChange={(e) => setSaveReferenceName(e.target.value)}
                            placeholder="Saved name (optional)"
                            className="min-w-[16rem] flex-1 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded px-3 py-2 text-[var(--text-primary)] text-sm"
                        />
                        <button
                            onClick={handleSaveCurrentReference}
                            className="px-3 py-2 rounded border text-[var(--text-primary)] text-sm transition-colors"
                            style={{
                                borderColor: 'var(--accent-secondary)',
                                backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                            }}
                        >
                            Save Current
                        </button>
                    </div>
                    {referenceLibraryNotice && (
                        <p className="text-xs text-[var(--text-secondary)]">{referenceLibraryNotice}</p>
                    )}
                </div>

                {referenceTab === 'browse' && (
                    <>
                        <div className="flex items-center gap-2">
                            <div className="flex-1 bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 min-h-10">
                                {referencePath ? (
                                    <span className="text-[var(--text-primary)] font-mono text-sm break-all">{formatPathDisplay(referencePath)}</span>
                                ) : (
                                    <span className="text-[var(--text-secondary)] text-sm">Select reference FASTA file</span>
                                )}
                            </div>
                            <button
                                onClick={() => openPathPicker({ field: 'referencePath', title: 'Select Reference FASTA', mode: 'file', filter: 'fasta' })}
                                className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-primary)] text-sm hover:bg-[var(--bg-tertiary)] transition-colors"
                            >
                                Browse
                            </button>
                            {referencePath && (
                                <button
                                    onClick={() => setReferencePath('')}
                                    className="px-3 py-2 rounded border border-[var(--border-primary)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                    </>
                )}

                {referenceTab === 'paste' && (
                    <>
                        <textarea
                            value={pastedFasta}
                            onChange={(e) => {
                                setPastedFasta(e.target.value);
                                // Auto-extract: write to a temp file on submit
                            }}
                            placeholder=">my_reference\nATCGATCG...\n\nPaste one or more FASTA sequences."
                            rows={6}
                            className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 text-[var(--text-primary)] text-sm font-mono resize-y"
                        />
                        <p className="text-xs text-[var(--text-secondary)] mt-1">
                            Uploaded at submit.
                        </p>
                        {pastedFasta && (
                            <div className="mt-1 text-xs text-[var(--text-secondary)]">
                                {pastedFasta.split('\n').filter(l => l.startsWith('>')).length} sequence(s) detected
                                {' · '}
                                {pastedFasta.split('\n').filter(l => !l.startsWith('>') && l.trim()).join('').length} bp total
                            </div>
                        )}
                    </>
                )}

                {referenceTab === 'create' && (
                    <div className="space-y-2">
                        <input
                            type="text"
                            value={newFastaName}
                            onChange={(e) => setNewFastaName(e.target.value)}
                            placeholder="Sequence name (e.g., pUC19_reference)"
                            className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 text-[var(--text-primary)] text-sm"
                        />
                        <textarea
                            value={newFastaSeq}
                            onChange={(e) => setNewFastaSeq(e.target.value.replace(/[^ATCGatcgNn\s]/g, ''))}
                            placeholder="Paste raw nucleotide sequence (ATCG only)"
                            rows={4}
                            className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 text-[var(--text-primary)] text-sm font-mono resize-y"
                        />
                        {newFastaSeq && (
                            <div className="text-xs text-[var(--text-secondary)]">
                                {newFastaSeq.replace(/\s/g, '').length} bp
                            </div>
                        )}
                        <button
                            onClick={() => {
                                if (newFastaName.trim() && newFastaSeq.trim()) {
                                    const fasta = `>${newFastaName.trim()}\n${newFastaSeq.replace(/\s/g, '')}`;
                                    setPastedFasta(fasta);
                                    setReferenceTab('paste');
                                }
                            }}
                            disabled={!newFastaName.trim() || !newFastaSeq.trim()}
                            className="px-3 py-1.5 rounded border text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            style={{
                                borderColor: 'var(--accent-secondary)',
                                color: 'var(--text-primary)',
                                backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                            }}
                        >
                            Use as Reference
                        </button>
                    </div>
                )}
            </div>

            {/* Locked P4 molecule, mode, and multiplexing controls */}
            {inputSource === 'pod5' && (
                <div className="bg-[var(--bg-secondary)] rounded-lg p-4 space-y-3 xl:col-span-4">
                    <div className="grid grid-cols-2 gap-3">
                        <label className="text-sm text-[var(--text-secondary)]">Molecule
                            <select value={doradoMolecule} onChange={(event) => {
                                const value = event.target.value as DoradoMolecule;
                                setDoradoMolecule(value);
                                if (value === 'rna') { setDoradoMode('simplex'); setModifiedBases('none'); setBarcodeKit(''); setTrimAdapters(true); setRunAssembly(false); }
                            }} className="block w-full mt-1 bg-[var(--bg-tertiary)] rounded p-2">
                                <option value="dna">DNA — R10.4.1 E8.2 5 kHz</option>
                                <option value="rna">RNA004 — 4 kHz</option>
                            </select>
                        </label>
                        <label className="text-sm text-[var(--text-secondary)]">Basecalling mode
                            <select value={doradoMode} onChange={(event) => { const value = event.target.value as DoradoMode; setDoradoMode(value); if (value === 'duplex') { setBarcodeKit(''); setModifiedBases('none'); setTrimAdapters(true); } }} className="block w-full mt-1 bg-[var(--bg-tertiary)] rounded p-2">
                                <option value="simplex">Simplex</option>
                                <option value="duplex" disabled={doradoMolecule === 'rna'}>Duplex (explicit pairs)</option>
                            </select>
                        </label>
                    </div>
                    {doradoMode === 'duplex' && (
                        <input value={duplexPairs} onChange={(event) => setDuplexPairs(event.target.value)} placeholder="Confined duplex pairs file" className="w-full bg-[var(--bg-tertiary)] rounded p-2" />
                    )}
                    {doradoMolecule === 'dna' && doradoMode === 'simplex' && (
                        <div className="grid grid-cols-2 gap-3">
                            <select value={barcodeKit} onChange={(event) => {
                                const value = event.target.value as '' | 'SQK-RBK114-96';
                                setBarcodeKit(value);
                                if (value) { setModifiedBases('none'); setRunModkit(false); setRunAssembly(false); }
                                else setSampleSheet('');
                            }} disabled={modifiedBases !== 'none'} className="bg-[var(--bg-tertiary)] rounded p-2 disabled:opacity-40">
                                <option value="">No barcode classification</option>
                                <option value="SQK-RBK114-96">SQK-RBK114-96</option>
                            </select>
                            <input value={sampleSheet} onChange={(event) => setSampleSheet(event.target.value)} disabled={!barcodeKit} placeholder="Optional confined sample sheet" className="bg-[var(--bg-tertiary)] rounded p-2 disabled:opacity-40" />
                        </div>
                    )}
                    <div className="text-xs text-[var(--text-secondary)]">Locked basecalling settings are applied automatically for this workflow.</div>
                </div>
            )}

            {/* Basecalling Model */}
            {inputSource === 'pod5' && (
                <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-3">Basecalling Model</label>
                    <div className="grid grid-cols-3 gap-3">
                        {(Object.entries(DORADO_MODELS) as [DoradoModel, typeof DORADO_MODELS[DoradoModel]][]).map(([key, model]) => (
                            <button
                                key={key}
                                onClick={() => setDoradoModel(key)}
                                className={`p-3 rounded-lg border-2 transition-all text-left ${doradoModel === key
                                    ? ''
                                    : 'border-[var(--border-primary)] hover:border-[var(--border-secondary)] bg-[var(--bg-tertiary)]/60'
                                    }`}
                                style={doradoModel === key ? {
                                    borderColor: 'var(--accent-secondary)',
                                    backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                                } : undefined}
                            >
                                <div className="font-medium text-[var(--text-primary)] text-sm">{model.label}</div>
                                <div className="text-xs text-[var(--text-secondary)] mt-1">{model.description}</div>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Methylation Detection */}
            {inputSource === 'pod5' && (
                <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-3">Modified Base Detection</label>
                    <div className="grid grid-cols-2 gap-3">
                        {(Object.entries(MODIFIED_BASES_OPTIONS) as [ModifiedBases, typeof MODIFIED_BASES_OPTIONS[ModifiedBases]][]).map(([key, opt]) => (
                            <button
                                key={key}
                                onClick={() => { setModifiedBases(key); if (key !== 'none') { setBarcodeKit(''); setSampleSheet(''); } }}
                                disabled={key !== 'none' && (doradoMolecule !== 'dna' || doradoModel !== 'hac' || doradoMode !== 'simplex' || Boolean(barcodeKit))}
                                className={`p-3 rounded-lg border-2 transition-all text-left disabled:opacity-40 disabled:cursor-not-allowed ${modifiedBases === key
                                    ? ''
                                    : 'border-[var(--border-primary)] hover:border-[var(--border-secondary)] bg-[var(--bg-tertiary)]/60'
                                    }`}
                                style={modifiedBases === key ? {
                                    borderColor: 'var(--accent-secondary)',
                                    backgroundColor: 'color-mix(in srgb, var(--accent-secondary) 12%, transparent)',
                                } : undefined}
                            >
                                <div className="font-medium text-[var(--text-primary)] text-sm">{opt.label}</div>
                                <div className="text-xs text-[var(--text-secondary)] mt-1">{opt.description}</div>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* POD5 basecall quality filter */}
            {inputSource === 'pod5' && (
                <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
                        Basecall Quality Filter — <span className="text-[var(--accent-secondary)] font-semibold">Q{minQscore}</span>
                        <span className="ml-2 text-xs font-normal text-[var(--text-secondary)]">{getQscoreLabel(minQscore)}</span>
                    </label>
                    <input
                        type="range"
                        min={0}
                        max={30}
                        step={1}
                        value={minQscore}
                        onChange={(e) => setMinQscore(parseInt(e.target.value))}
                        className="w-full mt-2 accent-[var(--accent-secondary)]"
                    />
                    <div className="flex justify-between text-[10px] text-[var(--text-secondary)] mt-1">
                        <span>Q0 (no filter)</span>
                        <span>Q10</span>
                        <span>Q15</span>
                        <span>Q20</span>
                        <span>Q30 (ultra-strict)</span>
                    </div>
                </div>
            )}

            {/* BAM alignment quality filter */}
            {inputSource === 'bam' && (
                <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4">
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
                        Alignment Quality Filter — <span className="text-[var(--accent-secondary)] font-semibold">MAPQ {'>='} {bamMinMapq}</span>
                    </label>
                    <input
                        type="range"
                        min={0}
                        max={60}
                        step={1}
                        value={bamMinMapq}
                        onChange={(e) => setBamMinMapq(Math.max(0, Math.min(60, parseInt(e.target.value, 10))))}
                        className="w-full mt-2 accent-[var(--accent-secondary)]"
                    />
                    <div className="flex justify-between text-[10px] text-[var(--text-secondary)] mt-1">
                        <span>0 (off)</span>
                        <span>10</span>
                        <span>20</span>
                        <span>30</span>
                        <span>60</span>
                    </div>
                </div>
            )}

            {/* Analysis Toggles — mode-aware: only relevant options shown */}
            <div className="bg-[var(--bg-secondary)] rounded-lg p-4 space-y-3 xl:col-span-6">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">Analysis Options</label>

                {/* Trim adapters — POD5 only */}
                {inputSource === 'pod5' && (
                    <label className="flex items-center gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={trimAdapters}
                            onChange={(e) => setTrimAdapters(e.target.checked)}
                            disabled={doradoMolecule === 'rna' || doradoMode === 'duplex'}
                            className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                        />
                        <div>
                            <span className="text-sm text-[var(--text-primary)]">Trim adapters{doradoMolecule === 'rna' ? ' (required for RNA)' : doradoMode === 'duplex' ? ' (fixed by duplex mode)' : ''}</span>
                        </div>
                    </label>
                )}

                {/* Modkit — POD5 or BAM */}
                {canRunModkit && (
                    <label className="flex items-center gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={runModkit}
                            onChange={(e) => setRunModkit(e.target.checked)}
                            className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                        />
                        <div>
                            <span className="text-sm text-[var(--text-primary)]">Run modkit analysis</span>
                            <p className="text-xs text-[var(--text-secondary)]">
                                {inputSource === 'bam' ? 'Summarize MM/ML tags.' : 'Generate methylation tables.'}
                            </p>
                        </div>
                    </label>
                )}

                {/* BAM realignment toggle */}
                {inputSource === 'bam' && (
                    <label className="flex items-center gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={bamForceRealign}
                            onChange={(e) => setBamForceRealign(e.target.checked)}
                            className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                        />
                        <div>
                            <span className="text-sm text-[var(--text-primary)]">Force BAM realignment (Dorado aligner)</span>
                            <p className="text-xs text-[var(--text-secondary)]">Default: pass-through.</p>
                        </div>
                    </label>
                )}

                {/* Assembly — vendor wf-clone-validation accepts FASTQ or BAM and BMS also accepts DNA POD5. */}
                {(inputSource === 'fastq' || inputSource === 'bam' || (doradoMolecule === 'dna' && !barcodeKit)) && (
                    <label className="flex items-center gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={runAssembly}
                            onChange={(e) => {
                                const value = e.target.checked;
                                setRunAssembly(value);
                                if (value && inputSource === 'fastq') setRunFastqQc(false);
                            }}
                            className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                        />
                        <div>
                            <span className="text-sm text-[var(--text-primary)]">Consensus assembly (wf-clone-validation)</span>
                        </div>
                    </label>
                )}

                {/* Standalone plasmid QC is an alternative to vendor clone validation for FASTQ inputs. */}
                {inputSource === 'fastq' && (
                    <label className="flex items-center gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={runFastqQc}
                            onChange={(e) => {
                                const value = e.target.checked;
                                setRunFastqQc(value);
                                if (value) setRunAssembly(false);
                            }}
                            className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                        />
                        <div>
                            <span className="text-sm text-[var(--text-primary)]">FASTQ plasmid QC</span>
                        </div>
                    </label>
                )}

                {inputSource === 'fastq' && runFastqQc && (
                    <div className="space-y-3 border-t border-[var(--border-primary)] pt-3">
                        <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">FASTQ QC core controls</div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            <label className="text-xs text-[var(--text-secondary)]">
                                Expected plasmid size (bp)
                                <input
                                    type="number"
                                    min={1}
                                    max={FASTQ_MAX_EXPECTED_PLASMID_SIZE_BP}
                                    value={expectedPlasmidSize}
                                    onChange={(e) => setExpectedPlasmidSize(coerceIntegerInput(
                                        e.target.value,
                                        FASTQ_DEFAULT_EXPECTED_PLASMID_SIZE_BP,
                                        1,
                                        FASTQ_MAX_EXPECTED_PLASMID_SIZE_BP,
                                    ))}
                                    className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                />
                            </label>

                            <label className="text-xs text-[var(--text-secondary)]">
                                Min read length (bp)
                                <input
                                    type="number"
                                    min={0}
                                    max={FASTQ_MAX_MIN_READ_LENGTH_BP}
                                    value={minFastqReadLength}
                                    onChange={(e) => setMinFastqReadLength(coerceIntegerInput(
                                        e.target.value,
                                        FASTQ_DEFAULT_MIN_READ_LENGTH_BP,
                                        0,
                                        FASTQ_MAX_MIN_READ_LENGTH_BP,
                                    ))}
                                    className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                />
                            </label>

                            <label className="text-xs text-[var(--text-secondary)]">
                                Alignment preset (`minimap2 -x`)
                                <select
                                    value={fastqMinimap2Preset}
                                    onChange={(e) => setFastqMinimap2Preset(normalizeFastqMinimapPreset(e.target.value))}
                                    className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                >
                                    {Object.entries(FASTQ_MINIMAP_PRESETS).map(([preset, label]) => (
                                        <option key={preset} value={preset}>{label}</option>
                                    ))}
                                </select>
                            </label>
                        </div>

                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={fastqMinimap2AllowSecondary}
                                onChange={(e) => setFastqMinimap2AllowSecondary(e.target.checked)}
                                className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                            />
                            <span className="text-xs text-[var(--text-secondary)]">Keep secondary alignments</span>
                        </label>

                    </div>
                )}
            </div>

            {/* Advanced Options */}
            <div className={`bg-[var(--bg-secondary)] rounded-lg p-4 ${runAssembly && !barcodeKit ? 'xl:col-span-12' : 'xl:col-span-6'}`}>
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="flex items-center gap-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors w-full"
                >
                    <svg className={`w-4 h-4 transition-transform ${showAdvanced ? 'rotate-90' : ''}`} viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
                    </svg>
                    <span className="font-medium">Advanced Controls</span>
                </button>
                {showAdvanced && (
                    <div className="mt-4 space-y-4 pl-6 border-l-2 border-[var(--border-primary)]">
                        {inputSource === 'pod5' && (
                            <div>
                                <label className="text-xs text-[var(--text-secondary)] mb-1 block">Dorado batch size (GPU memory tuning)</label>
                                <input
                                    type="number"
                                    value={batchSize ?? ''}
                                    onChange={(e) => setBatchSize(e.target.value ? parseInt(e.target.value) : null)}
                                    placeholder="Auto"
                                    className="w-32 bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-1.5 text-[var(--text-primary)] text-sm"
                                />
                            </div>
                        )}
                        {inputSource === 'pod5' && (
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={emitSummary}
                                    onChange={(e) => setEmitSummary(e.target.checked)}
                                    className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                                />
                                <span className="text-sm text-[var(--text-primary)]">Emit sequencing summary TSV</span>
                            </label>
                        )}
                        {runModkit && canRunModkit && (
                            <div className="space-y-2">
                                <label className="text-xs text-[var(--text-secondary)] mb-1 block">
                                    modkit filter threshold ({modkitFilterThreshold != null ? modkitFilterThreshold.toFixed(2) : 'auto'})
                                </label>
                                <div className="flex flex-wrap gap-2">
                                    {[
                                        { key: 'auto', label: 'Auto (Recommended)', value: null },
                                        { key: 'balanced', label: 'Balanced 0.50', value: 0.5 },
                                        { key: 'strict', label: 'Strict 0.70', value: 0.7 },
                                        { key: 'very-strict', label: 'Very strict 0.85', value: 0.85 },
                                    ].map((opt) => (
                                        <button
                                            key={opt.key}
                                            type="button"
                                            onClick={() => setModkitFilterThreshold(opt.value)}
                                            className={`px-2 py-1 rounded border text-xs transition-colors ${modkitFilterThreshold === opt.value
                                                ? 'text-[var(--text-primary)]'
                                                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                                }`}
                                            style={{
                                                borderColor: 'var(--border-primary)',
                                                backgroundColor: modkitFilterThreshold === opt.value ? 'var(--bg-secondary)' : 'transparent',
                                            }}
                                        >
                                            {opt.label}
                                        </button>
                                    ))}
                                </div>
                                <div className="flex items-center gap-2">
                                    <label className="text-[10px] text-[var(--text-secondary)]">Custom:</label>
                                    <input
                                        type="number"
                                        min="0"
                                        max="1"
                                        step="0.01"
                                        value={modkitFilterThreshold ?? ''}
                                        onChange={(e) => {
                                            if (e.target.value === '') {
                                                setModkitFilterThreshold(null);
                                                return;
                                            }
                                            const value = Number.parseFloat(e.target.value);
                                            if (Number.isFinite(value)) {
                                                setModkitFilterThreshold(Math.max(0, Math.min(1, value)));
                                            }
                                        }}
                                        placeholder="auto"
                                        className="w-24 bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1 text-[var(--text-primary)] text-xs"
                                    />
                                </div>
                                <div className="text-[10px] text-[var(--text-secondary)]">
                                    Auto threshold; strict lowers false positives.
                                </div>
                            </div>
                        )}
                        {inputSource === 'fastq' && runFastqQc && (
                            <div className="space-y-3 border-t border-[var(--border-primary)] pt-3">
                                <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">IGV track/report tuning</div>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        IGV track window (bp)
                                        <input
                                            type="number"
                                            min={1}
                                            max={FASTQ_MAX_IGV_TRACK_WINDOW_BP}
                                            value={igvTrackWindowBp}
                                            onChange={(e) => setIgvTrackWindowBp(coerceIntegerInput(
                                                e.target.value,
                                                FASTQ_DEFAULT_IGV_TRACK_WINDOW_BP,
                                                1,
                                                FASTQ_MAX_IGV_TRACK_WINDOW_BP,
                                            ))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        IGV report max sites
                                        <input
                                            type="number"
                                            min={1}
                                            max={FASTQ_MAX_IGV_REPORT_MAX_SITES}
                                            value={igvReportMaxSites}
                                            onChange={(e) => setIgvReportMaxSites(coerceIntegerInput(
                                                e.target.value,
                                                FASTQ_DEFAULT_IGV_REPORT_MAX_SITES,
                                                1,
                                                FASTQ_MAX_IGV_REPORT_MAX_SITES,
                                            ))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        IGV report flanking (bp)
                                        <input
                                            type="number"
                                            min={0}
                                            max={FASTQ_MAX_IGV_REPORT_FLANKING_BP}
                                            value={igvReportFlankingBp}
                                            onChange={(e) => setIgvReportFlankingBp(coerceIntegerInput(
                                                e.target.value,
                                                FASTQ_DEFAULT_IGV_REPORT_FLANKING_BP,
                                                0,
                                                FASTQ_MAX_IGV_REPORT_FLANKING_BP,
                                            ))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                </div>
                            </div>
                        )}
                        {(selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'bamQc') && (
                            <div className="space-y-3 border-t border-[var(--border-primary)] pt-3">
                                <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Plasmid dimer QC</div>
                                <label className="flex items-center gap-2 cursor-pointer">
                                    <input type="checkbox" checked={enableRotatingReferenceFrames} onChange={(e) => setEnableRotatingReferenceFrames(e.target.checked)} className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)]" />
                                    <span className="text-xs text-[var(--text-secondary)]">Evaluate circular-reference rotations</span>
                                </label>
                                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                                    <label className="text-xs text-[var(--text-secondary)]">Rotation scan step (bp)
                                        <input type="number" min={1} max={10000} value={rotationScanStepBp} onChange={(e) => setRotationScanStepBp(coerceIntegerInput(e.target.value, 1, 1, 10000))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">Minimum split MAPQ
                                        <input type="number" min={0} max={60} value={singleRefSplitMinMapq} onChange={(e) => setSingleRefSplitMinMapq(coerceIntegerInput(e.target.value, 20, 0, 60))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">Minimum split segment (bp)
                                        <input type="number" min={1} max={1000000} value={singleRefSplitMinSegmentBp} onChange={(e) => setSingleRefSplitMinSegmentBp(coerceIntegerInput(e.target.value, 250, 1, 1000000))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">Maximum split query gap (bp)
                                        <input type="number" min={0} max={1000000} value={singleRefSplitMaxQueryGapBp} onChange={(e) => setSingleRefSplitMaxQueryGapBp(coerceIntegerInput(e.target.value, 500, 0, 1000000))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                </div>
                            </div>
                        )}
                        {runAssembly && !barcodeKit && (
                            <div className="space-y-3 border-t border-[var(--border-primary)] pt-3">
                                <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">wf-clone-validation</div>
                                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Assembly tool
                                        <select
                                            value={assemblyTool}
                                            onChange={(e) => setAssemblyTool(e.target.value as AssemblyTool)}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        >
                                            <option value="flye">Flye (default)</option>
                                            <option value="canu">Canu</option>
                                        </select>
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Approx plasmid size (bp)
                                        <input
                                            type="number"
                                            min={1}
                                            value={assemblyApproxSize}
                                            onChange={(e) => setAssemblyApproxSize(Math.max(1, parseInt(e.target.value || '7000', 10)))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Assembly coverage
                                        <input
                                            type="number"
                                            min={1}
                                            value={assemblyCoverage}
                                            onChange={(e) => setAssemblyCoverage(Math.max(1, parseInt(e.target.value || '60', 10)))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Min quality
                                        <input
                                            type="number"
                                            min={0}
                                            value={assemblyMinQuality}
                                            onChange={(e) => setAssemblyMinQuality(Math.max(0, parseInt(e.target.value || '9', 10)))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)] md:col-span-2">
                                        Trim length (bp)
                                        <input
                                            type="number"
                                            min={0}
                                            value={assemblyTrimLength}
                                            onChange={(e) => setAssemblyTrimLength(Math.max(0, parseInt(e.target.value || '0', 10)))}
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Sample name override
                                        <input
                                            type="text"
                                            value={wfCloneSample}
                                            onChange={(e) => setWfCloneSample(e.target.value)}
                                            placeholder="auto from job name"
                                            className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Flye read quality
                                        <select value={wfCloneFlyeQuality} onChange={(e) => setWfCloneFlyeQuality(e.target.value as FlyeReadQuality)} disabled={assemblyTool !== 'flye'} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm disabled:opacity-40">
                                            <option value="nano-hq">nano-hq — Guppy5+/SUP or Q20 (default)</option>
                                            <option value="nano-corr">nano-corr — corrected reads</option>
                                            <option value="nano-raw">nano-raw — legacy/high-error reads</option>
                                        </select>
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Expected reference coverage (%)
                                        <input type="number" min={0} max={100} step="0.01" value={wfCloneExpectedCoverage} onChange={(e) => setWfCloneExpectedCoverage(coerceNumberInput(e.target.value, 95, 0, 100))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Expected reference identity (%)
                                        <input type="number" min={0} max={100} step="0.01" value={wfCloneExpectedIdentity} onChange={(e) => setWfCloneExpectedIdentity(coerceNumberInput(e.target.value, 99, 0, 100))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Cut-site mismatch allowance
                                        <input type="number" min={0} max={10} value={wfCloneCutsiteMismatch} onChange={(e) => setWfCloneCutsiteMismatch(coerceIntegerInput(e.target.value, 1, 0, 10))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="text-xs text-[var(--text-secondary)]">
                                        Primer mismatch allowance
                                        <input type="number" min={0} max={10} value={wfClonePrimerMismatch} onChange={(e) => setWfClonePrimerMismatch(coerceIntegerInput(e.target.value, 2, 0, 10))} className="mt-1 w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm" />
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" checked={wfCloneNonUniformCoverage} onChange={(e) => setWfCloneNonUniformCoverage(e.target.checked)} disabled={assemblyTool !== 'flye'} className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)] disabled:opacity-40" />
                                        <span className="text-xs text-[var(--text-secondary)]">Non-uniform coverage / Flye metagenome mode</span>
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" checked={wfCloneCanuFast} onChange={(e) => setWfCloneCanuFast(e.target.checked)} disabled={assemblyTool !== 'canu'} className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)] disabled:opacity-40" />
                                        <span className="text-xs text-[var(--text-secondary)]">Canu fast mode (may reduce contiguity)</span>
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer md:col-span-2">
                                        <input
                                            type="checkbox"
                                            checked={wfCloneLargeConstruct}
                                            onChange={(e) => setWfCloneLargeConstruct(e.target.checked)}
                                            className="w-4 h-4 rounded border-[var(--border-primary)] text-[var(--accent-secondary)] focus:ring-[var(--accent-secondary)]"
                                        />
                                        <span className="text-xs text-[var(--text-secondary)]">Enable large construct mode</span>
                                    </label>
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
            </div>

            {/* Error Display */}
            {error && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm text-red-400">
                    {error}
                </div>
            )}

            {/* Submit Button */}
            <div className="flex justify-end gap-3">
                <button
                    onClick={onBack}
                    className="px-6 py-2.5 rounded-lg border border-[var(--border-primary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-secondary)] transition-colors"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={!canSubmit || submitMutation.isPending}
                    className={`px-6 py-2.5 rounded-lg font-medium transition-all ${canSubmit && !submitMutation.isPending
                        ? 'text-[var(--text-primary)] shadow-lg'
                        : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] cursor-not-allowed'
                        }`}
                    style={canSubmit && !submitMutation.isPending ? {
                        backgroundColor: 'var(--accent-secondary)',
                        boxShadow: '0 10px 20px color-mix(in srgb, var(--accent-secondary) 30%, transparent)',
                    } : undefined}
                >
                    {submitMutation.isPending ? (
                        <span className="flex items-center gap-2">
                            <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            Submitting...
                        </span>
                    ) : 'Submit Nanopore Job'}
                </button>
            </div>

            {pathPicker && (
                <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
                    <div className="w-full max-w-4xl h-[80vh] bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl flex flex-col shadow-2xl">
                        <div className="px-4 py-3 border-b border-[var(--border-primary)] flex items-center justify-between">
                            <div>
                                <h2 className="text-base font-semibold text-[var(--text-primary)]">{pathPicker.title}</h2>
                                <p className="text-xs text-[var(--text-secondary)]">
                                    {pathPicker.mode === 'directory' ? 'Choose directory' : 'Choose file'}
                                </p>
                            </div>
                            <button
                                onClick={closePathPicker}
                                className="p-1 rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                aria-label="Close path picker"
                            >
                                <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                                    <path fillRule="evenodd" d="M4.22 4.22a.75.75 0 011.06 0L10 8.94l4.72-4.72a.75.75 0 111.06 1.06L11.06 10l4.72 4.72a.75.75 0 11-1.06 1.06L10 11.06l-4.72 4.72a.75.75 0 11-1.06-1.06L8.94 10 4.22 5.28a.75.75 0 010-1.06z" clipRule="evenodd" />
                                </svg>
                            </button>
                        </div>

                        <div className="px-4 py-2 border-b border-[var(--border-primary)] flex items-center gap-2">
                            <button
                                onClick={() => setBrowserPath(parentBrowserPath(browserPath))}
                                disabled={normalizedBrowserPath === '/'}
                                className="px-2.5 py-1.5 rounded border border-[var(--border-primary)] text-sm text-[var(--text-primary)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--bg-tertiary)] transition-colors"
                            >
                                Up
                            </button>
                                <div className="flex-1 text-xs text-[var(--text-secondary)]">Browse the approved input library</div>
                            {canSelectCurrentDirectory && (
                                <button
                                    onClick={() => selectPathFromBrowser(normalizedBrowserPath)}
                                    className="px-2.5 py-1.5 rounded border border-[var(--accent-secondary)] text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                >
                                    Select Folder
                                </button>
                            )}
                        </div>

                        <div className="flex-1 overflow-auto p-2">
                            {browserLoading ? (
                                <div className="h-full flex items-center justify-center text-sm text-[var(--text-secondary)]">
                                    Loading files...
                                </div>
                            ) : browserEntries.length === 0 ? (
                                <div className="h-full flex items-center justify-center text-sm text-[var(--text-secondary)]">
                                    No files found in this location.
                                </div>
                            ) : (
                                <div className="space-y-1">
                                    {browserEntries.map((entry: UntypedApiValue) => {
                                        const isDirectory = Boolean(entry.is_directory);
                                        const isSelectable = pathPicker.mode === 'directory'
                                            ? isDirectory
                                            : (!isDirectory && matchesPathFilter(entry.name, pathPicker.filter));

                                        return (
                                            <div
                                                key={entry.path}
                                                onClick={() => {
                                                    if (isDirectory) {
                                                        setBrowserPath(normalizeBrowserPath(entry.path));
                                                        return;
                                                    }
                                                    if (isSelectable) {
                                                        selectPathFromBrowser(entry.path);
                                                    }
                                                }}
                                                className={`flex items-center gap-3 px-3 py-2 rounded border transition-colors ${isDirectory
                                                    ? 'border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)] cursor-pointer'
                                                    : isSelectable
                                                        ? 'border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)] cursor-pointer'
                                                        : 'border-transparent opacity-50 cursor-default'
                                                    }`}
                                            >
                                                <svg className={`w-4 h-4 flex-shrink-0 ${isDirectory ? 'text-[var(--accent-secondary)]' : 'text-[var(--text-secondary)]'}`} viewBox="0 0 20 20" fill="currentColor">
                                                    {isDirectory ? (
                                                        <path d="M2 5.25A2.25 2.25 0 014.25 3h3.38a2.25 2.25 0 011.59.66l.47.47a.75.75 0 00.53.22h5.5A2.25 2.25 0 0118 6.6v7.15A2.25 2.25 0 0115.75 16H4.25A2.25 2.25 0 012 13.75v-8.5z" />
                                                    ) : (
                                                        <path fillRule="evenodd" d="M4.5 2.75A1.75 1.75 0 002.75 4.5v11A1.75 1.75 0 004.5 17.25h11a1.75 1.75 0 001.75-1.75V7.56a1.75 1.75 0 00-.51-1.24l-2.8-2.8a1.75 1.75 0 00-1.24-.52H4.5zm7.75 1.8v2.2c0 .41.34.75.75.75h2.2l-2.95-2.95z" clipRule="evenodd" />
                                                    )}
                                                </svg>
                                                <div className="min-w-0 flex-1">
                                                    <div className="text-sm text-[var(--text-primary)] truncate">{entry.name}</div>
                                                </div>
                                                {isSelectable && (
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            selectPathFromBrowser(entry.path);
                                                        }}
                                                        className="px-2 py-1 rounded border border-[var(--border-primary)] text-xs text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                                                    >
                                                        Select
                                                    </button>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>

                        <div className="px-4 py-2 border-t border-[var(--border-primary)] text-xs text-[var(--text-secondary)] flex items-center justify-between">
                            <span>
                                {pathPicker.mode === 'file'
                                    ? (pathPicker.filter === 'unknown'
                                        ? 'File selection enabled.'
                                        : `Showing selectable ${pathPicker.filter.toUpperCase()} files.`)
                                    : 'Directory selection enabled.'}
                            </span>
                            <button
                                onClick={closePathPicker}
                                className="px-2.5 py-1 rounded border border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)] text-[var(--text-primary)] transition-colors"
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
