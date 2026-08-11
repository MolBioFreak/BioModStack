/**
 * NanoporeTemplate – ONT Nanopore methylation basecalling and analysis.
 *
 * Self-contained: imports only shared UI primitives (BMS-lite extractable).
 * No MolstarViewer, TargetAntigenSelector, or protein-design dependencies.
 *
 * Pattern follows OligoDesignerTemplate for consistency.
 */

import { useEffect, useState, useMemo } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
    commitMolBioSequenceImport,
    createMolBioNgsReference,
    fetchFiles,
    fetchMolBioNgsReferenceRevisions,
    fetchMolBioNgsReferences,
    fetchMolBioNgsStateRevision,
    fetchMolBioSequenceRevisions,
    fetchNucleotideSequences,
    importMolBioNgsBrowserReference,
    issueMolBioNgsReceipt,
    previewMolBioSequenceImport,
    submitOntNgsJob,
    submitPooledReferenceAssignment,
    type MolBioSequenceImportError,
    type MolBioSequenceImportPayload,
    type MolBioSequenceImportPreviewRecord,
    type MolBioSequenceRevision,
    type NucleotideSequenceListItem,
    type PooledReferenceAssignmentSubmitRequest,
} from '../lib/api';
import { useGlobalExperimentContext } from './experiments/GlobalExperimentContext';
import { useLiveGpuCatalog } from './useLiveGpuCatalog';


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
type WorkflowKey = 'clone' | 'plasmidQc' | 'constructScreening' | 'fastqQc' | 'bamQc' | 'dna' | 'rna' | 'duplex' | 'modified' | 'barcode' | 'pooledAssignment';
type PathField = 'pod5Dir' | 'bamPath' | 'fastqPath';
type PathPickerMode = 'file' | 'directory';
type ReferenceTab = 'managed' | 'paste' | 'create' | 'legacy';

interface SavedReferenceEntry {
    id: string;
    name: string;
    source: 'fasta' | 'path';
    fasta?: string;
    path?: string;
    createdAt: string;
    updatedAt: string;
}
type PathFilter = 'unknown' | 'bam' | 'fastq';

interface PathPickerState {
    field: PathField;
    title: string;
    mode: PathPickerMode;
    filter: PathFilter;
}

interface ApprovedComparisonPanel {
    id: string;
    version: number;
    status: 'APPROVED';
    label: string;
    snapshot_sha256: string;
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

const LEGACY_REFERENCE_LIBRARY_STORAGE_KEY = 'bms.nanopore.referenceLibrary.v1';
const REFERENCE_COORDINATE_CONTRACT = 'fasta-1-based-inclusive';

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

function readLegacyReferenceImportHints(): SavedReferenceEntry[] {
    if (typeof window === 'undefined') return [];
    try {
        const raw = window.localStorage.getItem(LEGACY_REFERENCE_LIBRARY_STORAGE_KEY);
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

function newIdempotencyKey(prefix: string): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return `${prefix}-${crypto.randomUUID()}`;
    }
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function revisionLabel(revision: MolBioSequenceRevision): string {
    return `r${revision.revision_number} · ${revision.is_current ? 'current' : 'historical'}`;
}

interface MolBioSequenceImportPanelProps {
    onCommitted: () => void;
}

interface RawDnaImportRow {
    id: string;
    name: string;
    sequence: string;
    topology: 'circular' | 'linear';
}

function createRawDnaImportRow(index: number): RawDnaImportRow {
    return { id: `raw-dna-${index}-${Date.now()}`, name: '', sequence: '', topology: 'circular' };
}

function MolBioSequenceImportPanel({ onCommitted }: MolBioSequenceImportPanelProps) {
    const queryClient = useQueryClient();
    const [format, setFormat] = useState<'fasta' | 'genbank' | 'raw_dna'>('fasta');
    const [topologyDefault, setTopologyDefault] = useState<'circular' | 'linear'>('circular');
    const [sourceText, setSourceText] = useState('');
    const [rawRows, setRawRows] = useState<RawDnaImportRow[]>(() => [createRawDnaImportRow(0)]);
    const [previewPayload, setPreviewPayload] = useState<MolBioSequenceImportPayload | null>(null);
    const [previewRecords, setPreviewRecords] = useState<Array<MolBioSequenceImportPreviewRecord & { errors: string[] }>>([]);
    const [previewErrors, setPreviewErrors] = useState<MolBioSequenceImportError[]>([]);
    const [message, setMessage] = useState('');

    const buildPayload = (): MolBioSequenceImportPayload => {
        if (format === 'raw_dna') {
            const rawRowsPayload = rawRows
                .filter((row) => row.name.trim() && row.sequence.replace(/\s/g, '').trim())
                .map((row) => ({
                    name: row.name.trim(),
                    sequence: row.sequence.replace(/\s/g, '').toUpperCase(),
                    topology: row.topology,
                }));
            if (rawRowsPayload.length === 0) throw new Error('Add at least one named raw-DNA row before preview.');
            return {
                source_format: 'raw_dna',
                raw_rows: rawRowsPayload,
                topology_default: topologyDefault,
                idempotency_key: newIdempotencyKey('molbio-sequence-import'),
            };
        }

        if (!sourceText.trim()) {
            throw new Error(`Provide ${format === 'fasta' ? 'FASTA' : 'GenBank'} text before preview.`);
        }
        return {
            source_format: format,
            source_text: sourceText,
            topology_default: topologyDefault,
            idempotency_key: newIdempotencyKey('molbio-sequence-import'),
        };
    };

    const previewMutation = useMutation({
        mutationFn: (payload: MolBioSequenceImportPayload) => previewMolBioSequenceImport(payload),
        onSuccess: (response, payload) => {
            const errors = response.data.errors || [];
            setPreviewPayload(payload);
            setPreviewRecords(response.data.records.map((record) => ({
                ...record,
                errors: errors
                    .filter((error) => error.record_ordinal === record.record_ordinal)
                    .map((error) => error.message),
            })));
            setPreviewErrors(errors.filter((error) => error.record_ordinal == null));
            setMessage('Preview ready. Review every independent record before commit.');
        },
        onError: (error: unknown) => {
            setPreviewPayload(null);
            setPreviewRecords([]);
            setPreviewErrors([]);
            setMessage(extractApiErrorMessage(error));
        },
    });

    const commitMutation = useMutation({
        mutationFn: () => {
            if (!previewPayload) throw new Error('Preview the import before committing it.');
            return commitMolBioSequenceImport(previewPayload);
        },
        onSuccess: (response) => {
            setMessage(`Committed ${response.data.records.length} saved sequence record(s).`);
            setPreviewPayload(null);
            setPreviewRecords([]);
            setPreviewErrors([]);
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-sequences'] });
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-revisions'] });
            onCommitted();
        },
        onError: (error: unknown) => setMessage(extractApiErrorMessage(error)),
    });

    const handlePreview = () => {
        setMessage('');
        try {
            previewMutation.mutate(buildPayload());
        } catch (error: unknown) {
            setMessage(error instanceof Error ? error.message : 'Unable to build import preview.');
        }
    };

    return (
        <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/40 p-3" data-testid="molbio-sequence-import-panel">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Import saved MolBio sequence</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Server-authoritative preview creates immutable revisions. Raw sequence content stays in memory until the preview and commit requests.</p>
                </div>
                <div className="flex gap-2">
                    <select value={format} onChange={(event) => setFormat(event.target.value as typeof format)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]">
                        <option value="fasta">FASTA text</option>
                        <option value="genbank">GenBank text</option>
                        <option value="raw_dna">Explicit raw-DNA rows</option>
                    </select>
                    <select value={topologyDefault} onChange={(event) => setTopologyDefault(event.target.value as typeof topologyDefault)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]" aria-label="Import topology default">
                        <option value="circular">Default circular</option>
                        <option value="linear">Default linear</option>
                    </select>
                </div>
            </div>

            {format !== 'raw_dna' ? (
                <div className="mt-2">
                    <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder={format === 'fasta' ? '>construct-a\nATGC…' : 'LOCUS       construct-a…'} rows={4} className="w-full rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)]" />
                </div>
            ) : (
                <div className="mt-2 space-y-2">
                    {rawRows.map((row, index) => (
                        <div key={row.id} className="grid grid-cols-1 gap-2 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-2 md:grid-cols-[minmax(10rem,0.25fr)_minmax(0,1fr)_8rem_auto]">
                            <input value={row.name} onChange={(event) => setRawRows((previous) => previous.map((candidate) => candidate.id === row.id ? { ...candidate, name: event.target.value } : candidate))} placeholder={`Record ${index + 1} name`} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1.5 text-sm text-[var(--text-primary)]" />
                            <input value={row.sequence} onChange={(event) => setRawRows((previous) => previous.map((candidate) => candidate.id === row.id ? { ...candidate, sequence: event.target.value.replace(/[^ACGTNacgtn\s]/g, '') } : candidate))} placeholder="ATGC…" className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1.5 font-mono text-sm text-[var(--text-primary)]" />
                            <select value={row.topology} onChange={(event) => setRawRows((previous) => previous.map((candidate) => candidate.id === row.id ? { ...candidate, topology: event.target.value as RawDnaImportRow['topology'] } : candidate))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1.5 text-sm text-[var(--text-primary)]">
                                <option value="circular">Circular</option>
                                <option value="linear">Linear</option>
                            </select>
                            <button type="button" onClick={() => setRawRows((previous) => previous.length > 1 ? previous.filter((candidate) => candidate.id !== row.id) : previous)} disabled={rawRows.length <= 1} className="rounded border border-[var(--border-primary)] px-2 py-1.5 text-xs text-[var(--text-secondary)] disabled:cursor-not-allowed disabled:opacity-40">Remove</button>
                        </div>
                    ))}
                    <button type="button" onClick={() => setRawRows((previous) => [...previous, createRawDnaImportRow(previous.length)])} className="rounded border border-[var(--border-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]">Add raw-DNA row</button>
                </div>
            )}

            <div className="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" onClick={handlePreview} disabled={previewMutation.isPending || commitMutation.isPending} className="rounded border border-[var(--accent-secondary)] bg-[color-mix(in_srgb,var(--accent-secondary)_12%,transparent)] px-3 py-1.5 text-xs text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40">
                    {previewMutation.isPending ? 'Previewing…' : 'Preview import'}
                </button>
                <button type="button" onClick={() => commitMutation.mutate()} disabled={!previewPayload || previewRecords.some((record) => record.errors.length > 0) || previewErrors.length > 0 || previewMutation.isPending || commitMutation.isPending} className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-xs text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40">
                    {commitMutation.isPending ? 'Committing…' : 'Commit preview'}
                </button>
                {message && <span className="text-xs text-[var(--text-secondary)]">{message}</span>}
            </div>

            {(previewRecords.length > 0 || previewErrors.length > 0) && (
                <div className="mt-3 overflow-x-auto rounded border border-[var(--border-primary)]" data-testid="molbio-import-preview-records">
                    <table className="w-full min-w-[720px] text-left text-xs">
                        <thead className="bg-[var(--bg-secondary)] uppercase tracking-wide text-[var(--text-secondary)]">
                            <tr><th className="px-2 py-1.5">Independent record</th><th className="px-2 py-1.5">Digest</th><th className="px-2 py-1.5">Topology</th><th className="px-2 py-1.5">Errors</th></tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border-primary)]">
                            {previewRecords.map((record, index) => (
                                <tr key={`${record.name}-${index}`}>
                                    <td className="px-2 py-1.5 text-[var(--text-primary)]">{record.name}</td>
                                    <td className="px-2 py-1.5 font-mono text-[var(--text-secondary)]">{record.canonical_digest || '—'}</td>
                                    <td className="px-2 py-1.5 text-[var(--text-secondary)]">{record.topology || '—'}</td>
                                    <td className="px-2 py-1.5 text-rose-400">{record.errors.length > 0 ? record.errors.join(' · ') : 'None'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {previewErrors.length > 0 && <div className="border-t border-[var(--border-primary)] px-2 py-1.5 text-xs text-rose-400">{previewErrors.map((error) => error.message).join(' · ')}</div>}
                </div>
            )}
        </section>
    );
}

interface PooledTargetDraft {
    id: string;
    targetId: string;
    label: string;
    indistinguishableGroup: string;
    sequenceId: string;
    revisionId: string;
}

function createPooledTarget(index: number): PooledTargetDraft {
    const number = String(index + 1).padStart(2, '0');
    return {
        id: `pooled-target-${index}-${Date.now()}`,
        targetId: `target_${number}`,
        label: `Target ${index + 1}`,
        indistinguishableGroup: '',
        sequenceId: '',
        revisionId: '',
    };
}

interface PooledTargetRowProps {
    target: PooledTargetDraft;
    sequences: NucleotideSequenceListItem[];
    onChange: (target: PooledTargetDraft) => void;
}

function PooledTargetRow({ target, sequences, onChange }: PooledTargetRowProps) {
    const revisionsQuery = useQuery<MolBioSequenceRevision[]>({
        queryKey: ['pooled-reference-revisions', target.id, target.sequenceId],
        queryFn: async () => (await fetchMolBioSequenceRevisions(target.sequenceId)).data,
        enabled: Boolean(target.sequenceId),
        retry: false,
    });
    const revisions = revisionsQuery.data || [];
    const selectedRevision = revisions.find((revision) => revision.id === target.revisionId);

    return (
        <tr>
            <td className="px-2 py-2 align-top"><input value={target.targetId} onChange={(event) => onChange({ ...target, targetId: event.target.value })} className="w-full min-w-[8rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)]" aria-label="Stable target ID" /></td>
            <td className="px-2 py-2 align-top"><input value={target.label} onChange={(event) => onChange({ ...target, label: event.target.value })} className="w-full min-w-[10rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]" aria-label="Target label" /></td>
            <td className="px-2 py-2 align-top"><input value={target.indistinguishableGroup} onChange={(event) => onChange({ ...target, indistinguishableGroup: event.target.value })} placeholder="Optional" className="w-full min-w-[9rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]" aria-label="Indistinguishable group" /></td>
            <td className="px-2 py-2 align-top">
                <div className="space-y-1.5">
                    <select value={target.sequenceId} onChange={(event) => onChange({ ...target, sequenceId: event.target.value, revisionId: '' })} className="w-full min-w-[14rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]" aria-label="Pooled saved sequence">
                        <option value="">Select saved sequence…</option>
                        {sequences.map((sequence) => <option key={sequence.id} value={sequence.id}>{sequence.name}</option>)}
                    </select>
                    <select value={target.revisionId} onChange={(event) => onChange({ ...target, revisionId: event.target.value })} disabled={!target.sequenceId || revisionsQuery.isLoading} className="w-full min-w-[14rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)] disabled:opacity-50" aria-label="Pooled exact revision">
                        <option value="">Select exact revision…</option>
                        {revisions.map((revision) => <option key={revision.id} value={revision.id}>{revisionLabel(revision)}</option>)}
                    </select>
                </div>
            </td>
            <td className="px-2 py-2 align-top font-mono text-[10px] text-[var(--text-secondary)] break-all">{selectedRevision ? selectedRevision.content_sha256 : '—'}</td>
            <td className="px-2 py-2 align-top"><button type="button" onClick={() => onChange({ ...target, targetId: '', label: '', indistinguishableGroup: '', sequenceId: '', revisionId: '' })} className="rounded border border-[var(--border-primary)] px-2 py-1 text-xs text-[var(--text-secondary)]">Clear</button></td>
        </tr>
    );
}

interface PooledReferenceAssignmentPanelProps {
    fastqPath: string;
    sequences: NucleotideSequenceListItem[];
    onFastqBrowse: () => void;
}

function PooledReferenceAssignmentPanel({ fastqPath, sequences, onFastqBrowse }: PooledReferenceAssignmentPanelProps) {
    const queryClient = useQueryClient();
    const [targets, setTargets] = useState<PooledTargetDraft[]>(() => [createPooledTarget(0), createPooledTarget(1)]);
    const [minMapq, setMinMapq] = useState(20);
    const [minAlignmentScoreMargin, setMinAlignmentScoreMargin] = useState(5);
    const [message, setMessage] = useState('');

    const submitMutation = useMutation({
        mutationFn: async () => {
            if (!fastqPath.trim()) throw new Error('Select a FASTQ input before submitting pooled assignment.');
            if (targets.length < 2 || targets.length > 96) throw new Error('Pooled assignment requires 2-96 targets.');
            const targetIds = targets.map((target) => target.targetId.trim());
            if (targetIds.some((targetId) => !targetId) || new Set(targetIds).size !== targetIds.length) {
                throw new Error('Stable target IDs must be present and unique.');
            }
            if (targets.some((target) => !target.label.trim() || !target.sequenceId || !target.revisionId)) {
                throw new Error('Every pooled target needs a label, saved sequence, and exact immutable revision.');
            }
            if (!isIntegerInRange(minMapq, 0, 60) || !Number.isFinite(minAlignmentScoreMargin) || minAlignmentScoreMargin < 0) {
                throw new Error('Pooled alignment thresholds are invalid.');
            }

            const receiptTargets = await Promise.all(targets.map(async (target) => {
                const receiptResponse = await issueMolBioNgsReceipt(target.sequenceId, { revision_id: target.revisionId });
                const receiptId = receiptResponse.data.receipt_id.trim();
                if (!receiptId) throw new Error(`No receipt was returned for ${target.targetId}.`);
                return {
                    target_id: target.targetId.trim(),
                    label: target.label.trim(),
                    ...(target.indistinguishableGroup.trim() ? { indistinguishable_group: target.indistinguishableGroup.trim() } : {}),
                    molbio_ngs_receipt_id: receiptId,
                };
            }));

            const payload: PooledReferenceAssignmentSubmitRequest = {
                idempotency_key: newIdempotencyKey('pooled-reference-assignment'),
                fastq_path: fastqPath.trim(),
                targets: receiptTargets,
                min_mapq: minMapq,
                min_alignment_score_margin: minAlignmentScoreMargin,
            };
            return submitPooledReferenceAssignment(payload);
        },
        onSuccess: (response) => {
            setMessage(`Submitted pooled assignment ${response.data.assignment_job_id}.`);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
        onError: (error: unknown) => setMessage(extractApiErrorMessage(error)),
    });

    return (
        <section className="space-y-3 rounded-lg border border-[var(--accent-secondary)] bg-[var(--bg-secondary)] p-4 xl:col-span-12" data-testid="pooled-reference-assignment-panel">
            <div>
                <h2 className="text-base font-semibold text-[var(--text-primary)]">Pooled FASTQ reference assignment</h2>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">Select 2-96 exact saved MolBio revisions. The job stops at REVIEW and requires explicit target release before consensus.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/50 p-2 text-xs text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">FASTQ input</span>
                <span className="font-mono">{fastqPath ? formatPathDisplay(fastqPath) : 'Not selected'}</span>
                <button type="button" onClick={onFastqBrowse} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[var(--text-primary)]">Browse FASTQ</button>
                <span className="ml-auto">{targets.length}/96 targets</span>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                <label className="text-xs text-[var(--text-secondary)]">Minimum MAPQ
                    <input type="number" min={0} max={60} value={minMapq} onChange={(event) => setMinMapq(coerceIntegerInput(event.target.value, 20, 0, 60))} className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1.5 text-sm text-[var(--text-primary)]" />
                </label>
                <label className="text-xs text-[var(--text-secondary)]">Minimum alignment score margin
                    <input type="number" min={0} step="0.1" value={minAlignmentScoreMargin} onChange={(event) => setMinAlignmentScoreMargin(coerceNumberInput(event.target.value, 5, 0, 1000))} className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1.5 text-sm text-[var(--text-primary)]" />
                </label>
            </div>
            <div className="overflow-x-auto rounded border border-[var(--border-primary)]">
                <table className="w-full min-w-[1000px] text-left text-xs">
                    <thead className="bg-[var(--bg-tertiary)] uppercase tracking-wide text-[var(--text-secondary)]"><tr><th className="px-2 py-2">Stable target ID</th><th className="px-2 py-2">Label</th><th className="px-2 py-2">Indistinguishable group</th><th className="px-2 py-2">Exact saved revision</th><th className="px-2 py-2">Digest</th><th className="px-2 py-2">Row</th></tr></thead>
                    <tbody className="divide-y divide-[var(--border-primary)]">
                        {targets.map((target) => (
                            <PooledTargetRow key={target.id} target={target} sequences={sequences} onChange={(next) => setTargets((previous) => previous.map((candidate) => candidate.id === target.id ? next : candidate))} />
                        ))}
                    </tbody>
                </table>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={() => setTargets((previous) => previous.length < 96 ? [...previous, createPooledTarget(previous.length)] : previous)} disabled={targets.length >= 96} className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-xs text-[var(--text-primary)] disabled:opacity-40">Add target</button>
                <button type="button" onClick={() => setTargets((previous) => previous.length > 2 ? previous.slice(0, -1) : previous)} disabled={targets.length <= 2} className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-40">Remove last target</button>
                <button type="button" onClick={() => submitMutation.mutate()} disabled={submitMutation.isPending} className="rounded bg-blue-600 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40">{submitMutation.isPending ? 'Submitting pooled assignment…' : 'Submit pooled assignment'}</button>
                {message && <span className="text-xs text-[var(--text-secondary)]">{message}</span>}
            </div>
        </section>
    );
}

// ============================================================================
// Main Component
// ============================================================================
export function NanoporeTemplate({ onBack, initialValues }: NanoporeTemplateProps) {
    const experimentContext = useGlobalExperimentContext();
    const {
        workspaceId,
        globalExperimentId,
        stateRevisionId,
        selectedDomainExperiment,
        availability,
        contextHref,
    } = experimentContext;
    const exactDomainExperimentId = selectedDomainExperiment?.domain_experiment_id ?? null;
    // This is intentionally the URL/context-owned exact revision. Never replace it
    // with the global experiment ID or silently advance to a newer local head.
    const exactStateRevisionId = stateRevisionId;
    const [{ molbioSequenceId, molbioRevisionId }] = useState(() => {
        const params = new URLSearchParams(window.location.search);
        return {
            molbioSequenceId: params.get('molbio_sequence_id') || '',
            molbioRevisionId: params.get('molbio_revision_id') || '',
        };
    });
    const [selectedMolbioSequenceId, setSelectedMolbioSequenceId] = useState(molbioSequenceId);
    const [selectedMolbioRevisionId, setSelectedMolbioRevisionId] = useState(molbioRevisionId);
    const molbioRevisionPairError = Boolean(selectedMolbioSequenceId) !== Boolean(selectedMolbioRevisionId)
        ? 'Exact molecular sequence and revision IDs must be supplied together.'
        : null;
    const [approvedComparisonPanelId, setApprovedComparisonPanelId] = useState('');
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    // Pinning remains operator-selected; available labels are read from the
    // server-authoritative catalog and do not admit or reserve runtime resources.
    const { gpuOptions } = useLiveGpuCatalog();

    // ============================================================================
    // State: Core Configuration
    // ============================================================================
    const [jobName, setJobName] = useState(initialValues?.jobName as string || '');
    const [inputSource, setInputSource] = useState<InputSource>(initialValues?.inputSource as InputSource || 'pod5');
    const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowKey>(() => {
        if (typeof initialValues?.selectedWorkflow === 'string') return initialValues.selectedWorkflow as WorkflowKey;
        if (initialValues?.selectedWorkflow === 'constructScreening' || initialValues?.ontWorkflowId === 'ont_construct_screening') return 'constructScreening';
        if (initialValues?.selectedWorkflow === 'fastqQc' || initialValues?.ontWorkflowId === 'ont_fastq_qc') return 'fastqQc';
        if (initialValues?.runAssembly === true || initialValues?.ontWorkflowId === 'wf_clone_validation') return 'clone';
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
    const molbioSequencesQuery = useQuery<NucleotideSequenceListItem[]>({
        queryKey: ['molbio-ngs-sequences'],
        queryFn: async () => (await fetchNucleotideSequences({
            limit: 100,
            sequence_type: 'dna',
            sort_by: 'name',
            sort_desc: false,
        })).data,
        staleTime: 30_000,
        retry: false,
    });
    const molbioRevisionsQuery = useQuery<MolBioSequenceRevision[]>({
        queryKey: ['molbio-ngs-revisions', selectedMolbioSequenceId],
        queryFn: async () => (await fetchMolBioSequenceRevisions(selectedMolbioSequenceId)).data,
        enabled: Boolean(selectedMolbioSequenceId),
        retry: false,
    });
    const [selectedManagedReferenceRevisionId, setSelectedManagedReferenceRevisionId] = useState(
        typeof initialValues?.ngsReferenceRevisionId === 'string'
            ? initialValues.ngsReferenceRevisionId
            : '',
    );
    const managedReferencesQuery = useQuery({
        queryKey: ['molbio-ngs-references', exactDomainExperimentId],
        queryFn: () => fetchMolBioNgsReferences(exactDomainExperimentId as string),
        enabled: exactDomainExperimentId !== null,
        retry: false,
    });
    const managedReferenceRevisionQueries = useQueries({
        queries: (managedReferencesQuery.data ?? []).map((reference) => ({
            queryKey: ['molbio-ngs-reference-revisions', reference.id],
            queryFn: () => fetchMolBioNgsReferenceRevisions(reference.id),
            enabled: exactDomainExperimentId !== null,
            retry: false,
        })),
    });
    const exactStateRevisionQuery = useQuery({
        queryKey: ['molbio-ngs-state-revision', exactDomainExperimentId, exactStateRevisionId],
        queryFn: () => fetchMolBioNgsStateRevision(
            exactDomainExperimentId as string,
            exactStateRevisionId as string,
        ),
        enabled: exactDomainExperimentId !== null && exactStateRevisionId !== null,
        retry: false,
    });
    const { data: approvedPanelResponse } = useQuery({
        queryKey: ['approved-ngs-comparison-panels'],
        queryFn: async () => {
            const response = await fetch('/api/molbio/ngs-comparison-panels');
            if (!response.ok) throw new Error('Unable to load approved comparison panels.');
            return response.json() as Promise<{ panels?: ApprovedComparisonPanel[]; absence_label?: string | null }>;
        },
        enabled: Boolean(selectedMolbioSequenceId && selectedMolbioRevisionId),
        staleTime: 30_000,
    });
    const approvedComparisonPanels = approvedPanelResponse?.panels || [];

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
    const [showGpuPinning] = useState(false);
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
    const [referenceTab, setReferenceTab] = useState<ReferenceTab>('managed');
    const [pastedFasta, setPastedFasta] = useState('');
    const [pastedReferenceName, setPastedReferenceName] = useState('');
    const [newFastaName, setNewFastaName] = useState('');
    const [newFastaSeq, setNewFastaSeq] = useState('');
    const [referenceMoleculeType, setReferenceMoleculeType] = useState<'dna' | 'rna'>('dna');
    const [referenceTopology, setReferenceTopology] = useState<'linear' | 'circular'>('circular');
    const [legacyReferenceHints, setLegacyReferenceHints] = useState<SavedReferenceEntry[]>([]);
    const [legacyHintsLoaded, setLegacyHintsLoaded] = useState(false);
    const [selectedLegacyReferenceId, setSelectedLegacyReferenceId] = useState('');
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
    const managedReferenceOptions = useMemo(() => (
        (managedReferencesQuery.data ?? []).flatMap((resource, index) => (
            (managedReferenceRevisionQueries[index]?.data ?? []).map((revision) => ({ resource, revision }))
        ))
    ), [managedReferenceRevisionQueries, managedReferencesQuery.data]);
    const exactStateReferenceRevisionIds = useMemo(() => new Set(
        (exactStateRevisionQuery.data?.members ?? [])
            .filter((member) => member.role === 'ngs_reference' && member.entity_kind === 'ngs_reference_revision')
            .map((member) => member.entity_id),
    ), [exactStateRevisionQuery.data?.members]);
    const selectedManagedReference = useMemo(() => (
        managedReferenceOptions.find(({ revision }) => revision.id === selectedManagedReferenceRevisionId) ?? null
    ), [managedReferenceOptions, selectedManagedReferenceRevisionId]);
    const selectedReferenceIsExactStateMember = Boolean(
        selectedManagedReference
        && exactStateReferenceRevisionIds.has(selectedManagedReference.revision.id),
    );

    useEffect(() => {
        if (selectedManagedReferenceRevisionId || !exactStateRevisionQuery.isSuccess) return;
        const firstStateBoundReference = managedReferenceOptions.find(({ revision }) => (
            exactStateReferenceRevisionIds.has(revision.id)
        ));
        if (firstStateBoundReference) {
            setSelectedManagedReferenceRevisionId(firstStateBoundReference.revision.id);
        }
    }, [
        exactStateReferenceRevisionIds,
        exactStateRevisionQuery.isSuccess,
        managedReferenceOptions,
        selectedManagedReferenceRevisionId,
    ]);

    const hasValidFastqNumericControls = useMemo(() => (
        isIntegerInRange(expectedPlasmidSize, 1, FASTQ_MAX_EXPECTED_PLASMID_SIZE_BP)
        && isIntegerInRange(minFastqReadLength, 0, FASTQ_MAX_MIN_READ_LENGTH_BP)
        && isIntegerInRange(igvTrackWindowBp, 1, FASTQ_MAX_IGV_TRACK_WINDOW_BP)
        && isIntegerInRange(igvReportMaxSites, 1, FASTQ_MAX_IGV_REPORT_MAX_SITES)
        && isIntegerInRange(igvReportFlankingBp, 0, FASTQ_MAX_IGV_REPORT_FLANKING_BP)
    ), [expectedPlasmidSize, igvReportFlankingBp, igvReportMaxSites, igvTrackWindowBp, minFastqReadLength]);

    const requiresReference = selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'constructScreening' || selectedWorkflow === 'fastqQc' || selectedWorkflow === 'bamQc' || selectedWorkflow === 'modified';
    const molbioSequences = molbioSequencesQuery.data || [];
    const molbioRevisions = molbioRevisionsQuery.data || [];
    const selectedMolbioSequence = molbioSequences.find((sequence) => sequence.id === selectedMolbioSequenceId) || null;
    const selectedMolbioRevision = molbioRevisions.find((revision) => revision.id === selectedMolbioRevisionId) || null;
    const usesMolBioReceiptLane = Boolean(selectedMolbioSequenceId);
    const managedReferenceBlocker = useMemo(() => {
        if (usesMolBioReceiptLane) return molbioRevisionPairError;
        if (!exactDomainExperimentId) return 'Select an exact NGS/MolBio Domain Experiment.';
        if (!exactStateRevisionId) return 'Select an exact local state revision.';
        if (!availability.canMutateDomain) return availability.reason;
        if (managedReferencesQuery.isError) return 'Managed references could not be loaded.';
        if (exactStateRevisionQuery.isError) return 'The exact local state revision could not be loaded.';
        if (!selectedManagedReference) return 'Select an immutable managed reference revision.';
        if (!selectedReferenceIsExactStateMember) return 'The selected reference revision is not a member of the exact selected local state revision.';
        return null;
    }, [
        availability.canMutateDomain,
        availability.reason,
        exactDomainExperimentId,
        exactStateRevisionId,
        exactStateRevisionQuery.isError,
        managedReferencesQuery.isError,
        molbioRevisionPairError,
        selectedManagedReference,
        selectedReferenceIsExactStateMember,
        usesMolBioReceiptLane,
    ]);
    const canSubmit = useMemo(() => {
        if (selectedWorkflow === 'pooledAssignment' || !jobName.trim()) return false;
        if (molbioRevisionPairError) return false;
        if (!usesMolBioReceiptLane && !availability.canMutateDomain) return false;
        if (!usesMolBioReceiptLane && managedReferenceBlocker) return false;
        if (requiresReference && !usesMolBioReceiptLane && !selectedManagedReference) return false;
        if (inputSource === 'pod5') return pod5Dir.trim() !== '';
        if (inputSource === 'bam') return bamPath.trim() !== '';
        return fastqPath.trim() !== ''
            && (selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'constructScreening' || selectedWorkflow === 'fastqQc')
            && (Boolean(selectedManagedReference) || usesMolBioReceiptLane)
            && hasValidFastqNumericControls;
    }, [
        availability.canMutateDomain,
        bamPath,
        fastqPath,
        hasValidFastqNumericControls,
        inputSource,
        jobName,
        managedReferenceBlocker,
        molbioRevisionPairError,
        pod5Dir,
        selectedManagedReference,
        selectedWorkflow,
        usesMolBioReceiptLane,
    ]);
    const selectedLegacyReference = useMemo(
        () => legacyReferenceHints.find((entry) => entry.id === selectedLegacyReferenceId) ?? null,
        [legacyReferenceHints, selectedLegacyReferenceId],
    );

    const methylationEnabled = modifiedBases !== 'none';
    const canRunModkit = selectedWorkflow === 'modified' && methylationEnabled;
    const refreshManagedReferences = async () => {
        await queryClient.invalidateQueries({ queryKey: ['molbio-ngs-references', exactDomainExperimentId] });
        await queryClient.invalidateQueries({ queryKey: ['molbio-ngs-reference-revisions'] });
        await exactStateRevisionQuery.refetch();
    };

    const createManagedReferenceMutation = useMutation({
        mutationFn: async (input: { name: string; fasta: string }) => {
            if (!exactDomainExperimentId) throw new Error('Select an exact NGS/MolBio Domain Experiment before creating a reference.');
            const normalizedFasta = normalizeFastaText(input.fasta);
            if (!normalizedFasta) throw new Error('Reference FASTA is invalid. Provide FASTA headers and A/C/G/T/N sequence.');
            return createMolBioNgsReference({
                global_domain_experiment_id: exactDomainExperimentId,
                name: normalizeReferenceLabel(input.name),
                fasta: normalizedFasta,
                molecule_type: referenceMoleculeType,
                topology: referenceTopology,
                coordinate_contract: REFERENCE_COORDINATE_CONTRACT,
                source_provenance: { kind: 'inline_fasta', source_surface: 'nanopore-template' },
                idempotency_key: crypto.randomUUID(),
            });
        },
        onSuccess: async (result) => {
            await refreshManagedReferences();
            setSelectedManagedReferenceRevisionId(result.id);
            setReferenceTab('managed');
            setReferenceLibraryNotice(`Created immutable managed reference revision ${result.id}. Select a state revision that includes it before launch.`);
        },
        onError: (err: unknown) => setReferenceLibraryNotice(extractApiErrorMessage(err)),
    });

    const importLegacyReferenceMutation = useMutation({
        mutationFn: async (entry: SavedReferenceEntry) => {
            if (!exactDomainExperimentId) throw new Error('Select an exact NGS/MolBio Domain Experiment before importing a browser hint.');
            return importMolBioNgsBrowserReference({
                global_domain_experiment_id: exactDomainExperimentId,
                entry: {
                    id: entry.id,
                    name: entry.name,
                    source: entry.source,
                    fasta: entry.fasta,
                    path: entry.path,
                    createdAt: entry.createdAt,
                    updatedAt: entry.updatedAt,
                },
                name: entry.name,
                molecule_type: referenceMoleculeType,
                topology: referenceTopology,
                coordinate_contract: REFERENCE_COORDINATE_CONTRACT,
                idempotency_key: crypto.randomUUID(),
            });
        },
        onSuccess: async (result) => {
            await refreshManagedReferences();
            setSelectedManagedReferenceRevisionId(result.id);
            setReferenceTab('managed');
            setReferenceLibraryNotice(`Imported untrusted browser hint as immutable managed reference revision ${result.id}. Select a state revision that includes it before launch.`);
        },
        onError: (err: unknown) => setReferenceLibraryNotice(extractApiErrorMessage(err)),
    });

    // ============================================================================
    // Job Submission
    // ============================================================================
    const submitMutation = useMutation({
        mutationFn: async () => {
            let molbioNgsReceiptId = '';
            let comparisonPanelReceiptId = '';

            if (molbioRevisionPairError) {
                throw new Error(molbioRevisionPairError);
            }
            if (selectedMolbioSequenceId && selectedMolbioRevisionId) {
                const receiptResponse = await issueMolBioNgsReceipt(selectedMolbioSequenceId, { revision_id: selectedMolbioRevisionId });
                molbioNgsReceiptId = receiptResponse.data.receipt_id.trim();
                if (!molbioNgsReceiptId) throw new Error('The immutable MolBio handoff did not return a receipt.');
                if (approvedComparisonPanelId) {
                    const panelResponse = await fetch(`/api/molbio/ngs-comparison-panels/${encodeURIComponent(approvedComparisonPanelId)}/receipts`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ expected_receipt_id: molbioNgsReceiptId }),
                    });
                    if (!panelResponse.ok) throw new Error('Unable to prepare the approved comparison-panel receipt.');
                    const panelReceipt = await panelResponse.json() as { receipt_id?: string };
                    comparisonPanelReceiptId = String(panelReceipt.receipt_id || '').trim();
                    if (!comparisonPanelReceiptId) throw new Error('The approved comparison panel did not return a receipt.');
                }
            } else {
                if (managedReferenceBlocker) throw new Error(managedReferenceBlocker);
                if (!exactDomainExperimentId || !exactStateRevisionId || !selectedManagedReference) {
                    throw new Error('Exact Domain Experiment, state revision, and managed reference revision are required.');
                }
            }

            const workflowId = selectedWorkflow === 'clone'
                ? 'wf_clone_validation'
                : selectedWorkflow === 'constructScreening'
                    ? 'ont_construct_screening'
                    : selectedWorkflow === 'fastqQc'
                        ? 'ont_fastq_qc'
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
                    ...(molbioNgsReceiptId && { molbio_ngs_receipt_id: molbioNgsReceiptId }),
                    ...(comparisonPanelReceiptId && { ngs_comparison_panel_receipt_id: comparisonPanelReceiptId }),
                    min_qscore: inputSource === 'pod5' ? minQscore : undefined,
                    run_modkit: runModkit && canRunModkit,
                    run_fastq_qc: inputSource === 'fastq' && selectedWorkflow !== 'clone',
                    run_multimer_qc: inputSource === 'fastq' && selectedWorkflow !== 'clone',
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
                    ...((selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'constructScreening' || selectedWorkflow === 'fastqQc' || selectedWorkflow === 'bamQc') && {
                        enable_rotating_reference_frames: enableRotatingReferenceFrames,
                        rotation_scan_step_bp: rotationScanStepBp,
                        single_ref_split_min_mapq: singleRefSplitMinMapq,
                        single_ref_split_min_segment_bp: singleRefSplitMinSegmentBp,
                        single_ref_split_max_query_gap_bp: singleRefSplitMaxQueryGapBp,
                    }),
                    ...(runModkit && canRunModkit && modkitFilterThreshold != null && { modkit_filter_threshold: modkitFilterThreshold }),
                },
                ...(!molbioNgsReceiptId && exactDomainExperimentId && exactStateRevisionId && selectedManagedReference && {
                    managed_reference: {
                        global_domain_experiment_id: exactDomainExperimentId,
                        molbio_ngs_state_revision_id: exactStateRevisionId,
                        ngs_reference_revision_id: selectedManagedReference.revision.id,
                    },
                }),
            };
            return submitOntNgsJob(workflowId, jobPayload);
        },
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            const submittedJobId = response.data?.id;
            if (submittedJobId) {
                navigate(contextHref('/ngs', { section: 'analyses', job_id: submittedJobId }));
                return;
            }
            navigate(contextHref('/ngs'));
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
        if (molbioRevisionPairError) {
            setError(molbioRevisionPairError);
            return;
        }
        if (!usesMolBioReceiptLane && !availability.canMutateDomain) {
            setError(availability.reason);
            return;
        }
        if (!usesMolBioReceiptLane && managedReferenceBlocker) {
            setError(managedReferenceBlocker);
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
        return fastqPath;
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
        }
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
            setDoradoMolecule('dna');
            setDoradoMode('simplex');
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
            setDoradoMolecule('dna');
            setDoradoMode('simplex');
            setRunAssembly(false);
            setRunFastqQc(true);
            setRunModkit(false);
            setBarcodeKit('');
            return;
        }
        if (workflow === 'constructScreening') {
            setInputSource('fastq');
            setDoradoMolecule('dna');
            setDoradoMode('simplex');
            setRunAssembly(false);
            setRunFastqQc(true);
            setRunModkit(false);
            setBarcodeKit('');
            return;
        }
        if (workflow === 'fastqQc') {
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
        if (workflow === 'pooledAssignment') {
            setInputSource('fastq');
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
                        { key: 'clone' as const, title: 'Validate a known plasmid / clone', input: 'POD5, BAM, or FASTQ + saved MolBio revision', result: 'Vendor wf-clone-validation HTML report, assembly, construct evidence', tone: 'border-[var(--accent-secondary)]' },
                        { key: 'plasmidQc' as const, title: 'QC plasmid reads', input: 'POD5, BAM, or FASTQ + saved MolBio revision', result: 'BMS plasmid QC, alignment and generic multimer evidence', tone: 'border-[var(--border-primary)]' },
                        { key: 'constructScreening' as const, title: 'Screen a construct', input: 'POD5, BAM, or FASTQ + saved MolBio revision', result: 'BMS construct-screening evidence; not a competing clone report', tone: 'border-[var(--border-primary)]' },
                        { key: 'fastqQc' as const, title: 'ONT FASTQ QC', input: 'FASTQ + saved MolBio revision', result: 'Read and alignment QC only; no clone report', tone: 'border-[var(--border-primary)]' },
                        { key: 'bamQc' as const, title: 'Analyze aligned plasmid BAM', input: 'BAM + saved MolBio revision', result: 'BMS plasmid QC and alignment evidence', tone: 'border-[var(--border-primary)]' },
                        { key: 'dna' as const, title: 'Basecall DNA simplex', input: 'DNA POD5', result: 'Dorado calls BAM, summary and runtime provenance', tone: 'border-[var(--border-primary)]' },
                        { key: 'rna' as const, title: 'Basecall RNA', input: 'RNA POD5', result: 'RNA004 simplex calls BAM, trimming and provenance', tone: 'border-[var(--border-primary)]' },
                        { key: 'duplex' as const, title: 'Basecall DNA duplex', input: 'DNA POD5 + validated read-pairs file', result: 'Duplex BAM with retained stereo model provenance', tone: 'border-[var(--border-primary)]' },
                        { key: 'modified' as const, title: 'Call modified bases', input: 'DNA POD5', result: 'HAC simplex calls plus modkit tables', tone: 'border-[var(--border-primary)]' },
                        { key: 'barcode' as const, title: 'Classify and demultiplex RBK114', input: 'DNA POD5', result: 'Canonical barcodeNN units and demux outputs', tone: 'border-[var(--border-primary)]' },
                        { key: 'pooledAssignment' as const, title: 'Assign pooled FASTQ references', input: 'FASTQ + 2-96 exact saved revisions', result: 'Review-only assignment; explicit target release gates consensus', tone: 'border-[var(--accent-secondary)]' },
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
                    <strong className="text-[var(--text-primary)]">How to use this page:</strong> choose a workflow → choose the requested POD5, BAM, or FASTQ input → select a saved MolBio sequence and exact revision when required → enter a job name → submit. Completed jobs appear through <strong className="text-[var(--text-primary)]">Runs</strong>; no completed sequencing results are shown until an input is actually processed. Selecting a workflow never starts an instrument run.
                </div>
            </section>

            {selectedWorkflow === 'pooledAssignment' && (
                <PooledReferenceAssignmentPanel
                    fastqPath={fastqPath}
                    sequences={molbioSequences}
                    onFastqBrowse={() => openPathPicker({ field: 'fastqPath', title: 'Select FASTQ File', mode: 'file', filter: 'fastq' })}
                />
            )}

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
                {showGpuPinning && (
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
                    {(selectedWorkflow === 'clone' || selectedWorkflow === 'plasmidQc' || selectedWorkflow === 'constructScreening'
                        ? [
                            { key: 'pod5' as const, label: 'POD5 Raw Reads' },
                            { key: 'bam' as const, label: 'Existing BAM' },
                            { key: 'fastq' as const, label: 'FASTQ Analysis' },
                        ]
                        : selectedWorkflow === 'modified'
                            ? [{ key: 'pod5' as const, label: 'POD5 Raw Reads' }, { key: 'bam' as const, label: 'Existing BAM with MM/ML tags' }]
                            : selectedWorkflow === 'pooledAssignment' || selectedWorkflow === 'fastqQc'
                                ? [{ key: 'fastq' as const, label: 'FASTQ Analysis' }]
                                : selectedWorkflow === 'bamQc'
                                    ? [{ key: 'bam' as const, label: 'Existing BAM' }]
                                    : [{ key: 'pod5' as const, label: 'POD5 Raw Reads' }]
                    ).map((source) => (
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

            {/* Immutable MolBio reference plane */}
            {selectedWorkflow !== 'pooledAssignment' && (
                <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-8" data-testid="immutable-molbio-reference-panel">
                    <div className="mb-3">
                        <h2 className="text-sm font-semibold text-[var(--text-primary)]">Immutable MolBio reference</h2>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">Reference-requiring workflows use a saved sequence and one exact immutable revision. References are issued to the job only through a server receipt.</p>
                    </div>
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <label className="text-xs text-[var(--text-secondary)]">Saved MolBio sequence
                            <select
                                value={selectedMolbioSequenceId}
                                onChange={(event) => {
                                    setSelectedMolbioSequenceId(event.target.value);
                                    setSelectedMolbioRevisionId('');
                                    setApprovedComparisonPanelId('');
                                }}
                                className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-2 text-sm text-[var(--text-primary)]"
                                data-testid="molbio-sequence-selector"
                            >
                                <option value="">Select saved sequence…</option>
                                {molbioSequences.map((sequence) => <option key={sequence.id} value={sequence.id}>{sequence.name}</option>)}
                            </select>
                        </label>
                        <label className="text-xs text-[var(--text-secondary)]">Exact immutable revision
                            <select
                                value={selectedMolbioRevisionId}
                                onChange={(event) => setSelectedMolbioRevisionId(event.target.value)}
                                disabled={!selectedMolbioSequenceId || molbioRevisionsQuery.isLoading}
                                className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-2 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                                data-testid="molbio-revision-selector"
                            >
                                <option value="">Select exact revision…</option>
                                {molbioRevisions.map((revision) => <option key={revision.id} value={revision.id}>{revisionLabel(revision)}</option>)}
                            </select>
                        </label>
                    </div>
                    {molbioRevisionsQuery.isError && <p className="mt-2 text-xs text-rose-400">Unable to load immutable revisions for this saved sequence.</p>}
                    {selectedMolbioSequence && selectedMolbioRevision && (
                        <div className="mt-3 grid grid-cols-2 gap-2 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/50 p-3 text-xs md:grid-cols-5" data-testid="molbio-revision-summary">
                            <div><div className="text-[var(--text-secondary)]">Sequence</div><div className="mt-1 text-[var(--text-primary)]">{selectedMolbioSequence.name}</div></div>
                            <div><div className="text-[var(--text-secondary)]">Revision</div><div className="mt-1 text-[var(--text-primary)]">{selectedMolbioRevision.revision_number}</div></div>
                            <div><div className="text-[var(--text-secondary)]">Revision ID</div><div className="mt-1 break-all font-mono text-[var(--text-primary)]">{selectedMolbioRevision.id}</div></div>
                            <div><div className="text-[var(--text-secondary)]">Digest</div><div className="mt-1 break-all font-mono text-[var(--text-primary)]">{selectedMolbioRevision.content_sha256}</div></div>
                            <div><div className="text-[var(--text-secondary)]">Topology / state</div><div className="mt-1 text-[var(--text-primary)]">{selectedMolbioRevision.topology} · {selectedMolbioRevision.is_current ? 'current' : 'historical'}</div></div>
                        </div>
                    )}
                    {selectedMolbioSequenceId && ['plasmidQc', 'constructScreening', 'fastqQc', 'bamQc'].includes(selectedWorkflow) && (
                        <div className="mt-3 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/40 p-3">
                            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Approved comparison panel (optional)</label>
                            <select value={approvedComparisonPanelId} onChange={(event) => setApprovedComparisonPanelId(event.target.value)} className="w-full rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]">
                                <option value="">No comparison panel</option>
                                {approvedComparisonPanels.map((panel) => <option key={panel.id} value={panel.id}>{panel.label}</option>)}
                            </select>
                            {approvedComparisonPanels.length === 0 && <p className="mt-1 text-xs text-[var(--text-secondary)]">{approvedPanelResponse?.absence_label || 'No approved comparison panels are available.'}</p>}
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">Server-approved comparison panels are separate from the expected MolBio receipt reference.</p>
                        </div>
                    )}
                    <div className="mt-3">
                        <MolBioSequenceImportPanel onCommitted={() => {
                            setSelectedMolbioSequenceId('');
                            setSelectedMolbioRevisionId('');
                        }} />
                    </div>
                </div>
            )}
            {/* Server-managed immutable reference authority */}
            {selectedWorkflow !== 'pooledAssignment' && (
            <div className="bg-[var(--bg-secondary)] rounded-lg p-4 xl:col-span-4 space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                    <div><label className="block text-sm font-medium text-[var(--text-secondary)]">Managed Reference FASTA</label><p className="mt-1 text-xs text-[var(--text-secondary)]">Only immutable server-managed revisions can enter managed-reference submission.</p></div>
                    <div className="flex flex-wrap gap-1">{(['managed', 'paste', 'create', 'legacy'] as ReferenceTab[]).map((tab) => <button key={tab} type="button" onClick={() => setReferenceTab(tab)} className={`px-3 py-1.5 rounded text-xs font-medium ${referenceTab === tab ? 'text-[var(--text-primary)] bg-[var(--bg-tertiary)]' : 'text-[var(--text-secondary)]'}`}>{tab === 'managed' ? 'Managed' : tab === 'paste' ? 'Paste + Import' : tab === 'create' ? 'Create Managed' : 'Legacy Import'}</button>)}</div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/40 p-3 text-xs">
                    <div>Project ID: <span className="font-mono break-all">{workspaceId || 'not selected'}</span></div><div>Global Experiment ID: <span className="font-mono break-all">{globalExperimentId || 'not selected'}</span></div>
                    <div>Domain Experiment ID: <span className="font-mono break-all">{exactDomainExperimentId || 'not selected'}</span></div><div>Selected state revision ID: <span className="font-mono break-all">{exactStateRevisionId || 'not selected'}</span></div>
                    <div>Reference resource ID: <span className="font-mono break-all">{selectedManagedReference?.resource.id || 'not selected'}</span></div><div>Reference revision ID: <span className="font-mono break-all">{selectedManagedReference?.revision.id || 'not selected'}</span></div>
                    <div className="md:col-span-2">Canonical FASTA SHA-256: <span className="font-mono break-all">{selectedManagedReference?.revision.canonical_fasta_sha256 || 'not selected'}</span></div>
                </div>
                {usesMolBioReceiptLane ? <p className="rounded border border-cyan-500/40 bg-cyan-500/10 p-3 text-xs text-cyan-100">MolBio one-time receipt handoff is active for {selectedMolbioSequenceId}. Managed-reference authority cannot be mixed with this receipt/comparison-panel lane.</p> : managedReferenceBlocker ? <p role="alert" className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">Managed launch disabled: {managedReferenceBlocker}</p> : null}
                {referenceTab === 'managed' && <div className="space-y-2">
                    <select value={selectedManagedReferenceRevisionId} onChange={(event) => setSelectedManagedReferenceRevisionId(event.target.value)} disabled={usesMolBioReceiptLane || managedReferencesQuery.isLoading} className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-primary)] rounded px-3 py-2 text-sm disabled:opacity-50">
                        <option value="">Select immutable managed revision…</option>{managedReferenceOptions.map(({ resource, revision }) => <option key={revision.id} value={revision.id}>{resource.name} · rev {revision.revision_number} · {revision.id}{exactStateReferenceRevisionIds.has(revision.id) ? ' · selected-state member' : ' · not in selected state'}</option>)}
                    </select>{managedReferenceOptions.length === 0 && !managedReferencesQuery.isLoading && <p className="text-xs text-[var(--text-secondary)]">No managed references belong to this exact Domain Experiment.</p>}
                </div>}
                {(referenceTab === 'paste' || referenceTab === 'create' || referenceTab === 'legacy') && <div className="grid grid-cols-2 gap-2">
                    <label className="text-xs">Molecule type<select value={referenceMoleculeType} onChange={(event) => setReferenceMoleculeType(event.target.value as 'dna' | 'rna')} className="mt-1 w-full bg-[var(--bg-tertiary)] rounded p-2"><option value="dna">DNA</option><option value="rna">RNA</option></select></label>
                    <label className="text-xs">Topology<select value={referenceTopology} onChange={(event) => setReferenceTopology(event.target.value as 'linear' | 'circular')} className="mt-1 w-full bg-[var(--bg-tertiary)] rounded p-2"><option value="circular">Circular</option><option value="linear">Linear</option></select></label>
                </div>}
                {referenceTab === 'paste' && <div className="space-y-2">
                    <input value={pastedReferenceName} onChange={(event) => setPastedReferenceName(event.target.value)} placeholder="Managed reference name" className="w-full bg-[var(--bg-tertiary)] border rounded px-3 py-2 text-sm" /><textarea value={pastedFasta} onChange={(event) => setPastedFasta(event.target.value)} placeholder=">my_reference
ATCGATCG…" rows={6} className="w-full bg-[var(--bg-tertiary)] border rounded px-3 py-2 text-sm font-mono" />
                    <button type="button" onClick={() => createManagedReferenceMutation.mutate({ name: pastedReferenceName.trim() || inferReferenceNameFromFasta(pastedFasta) || 'pasted-reference', fasta: pastedFasta })} disabled={!availability.canMutateDomain || usesMolBioReceiptLane || createManagedReferenceMutation.isPending || normalizeFastaText(pastedFasta) === null} title={!availability.canMutateDomain ? availability.reason : undefined} className="px-3 py-2 rounded border disabled:opacity-40">{createManagedReferenceMutation.isPending ? 'Creating managed revision…' : 'Create managed reference first'}</button>
                </div>}
                {referenceTab === 'create' && <div className="space-y-2">
                    <input value={newFastaName} onChange={(event) => setNewFastaName(event.target.value)} placeholder="Managed reference and sequence name" className="w-full bg-[var(--bg-tertiary)] border rounded px-3 py-2 text-sm" /><textarea value={newFastaSeq} onChange={(event) => setNewFastaSeq(event.target.value.replace(/[^ATCGatcgNn\s]/g, ''))} placeholder="Raw nucleotide sequence (ATCGN only)" rows={4} className="w-full bg-[var(--bg-tertiary)] border rounded px-3 py-2 text-sm font-mono" />
                    <button type="button" onClick={() => createManagedReferenceMutation.mutate({ name: newFastaName, fasta: `>${newFastaName.trim()}\n${newFastaSeq.replace(/\s/g, '').toUpperCase()}` })} disabled={!availability.canMutateDomain || usesMolBioReceiptLane || createManagedReferenceMutation.isPending || !newFastaName.trim() || !newFastaSeq.trim()} title={!availability.canMutateDomain ? availability.reason : undefined} className="px-3 py-2 rounded border disabled:opacity-40">{createManagedReferenceMutation.isPending ? 'Creating managed revision…' : 'Create managed reference first'}</button>
                </div>}
                {referenceTab === 'legacy' && <div className="space-y-2 rounded border border-amber-500/40 p-3">
                    <p className="text-xs text-amber-100">Legacy browser entries are untrusted import hints only. They are read only on explicit action and never become scientific authority directly.</p><button type="button" onClick={() => { const hints = readLegacyReferenceImportHints(); setLegacyReferenceHints(hints); setLegacyHintsLoaded(true); setSelectedLegacyReferenceId(hints[0]?.id ?? ''); setReferenceLibraryNotice(hints.length ? `Loaded ${hints.length} untrusted browser hint(s).` : 'No legacy browser reference hints were found.'); }} className="px-3 py-2 rounded border text-sm">Read legacy browser hints</button>
                    {legacyHintsLoaded && legacyReferenceHints.length > 0 && <><select value={selectedLegacyReferenceId} onChange={(event) => setSelectedLegacyReferenceId(event.target.value)} className="w-full bg-[var(--bg-tertiary)] border rounded px-3 py-2 text-sm">{legacyReferenceHints.map((entry) => <option key={entry.id} value={entry.id}>{entry.name} · untrusted {entry.source} hint</option>)}</select>{selectedLegacyReference && <div className="text-xs"><div>Hint ID: <span className="font-mono">{selectedLegacyReference.id}</span></div>{selectedLegacyReference.source === 'path' && <div className="text-amber-200">Path-only hint: backend import will fail closed.</div>}</div>}<button type="button" onClick={() => selectedLegacyReference && importLegacyReferenceMutation.mutate(selectedLegacyReference)} disabled={!selectedLegacyReference || !availability.canMutateDomain || usesMolBioReceiptLane || importLegacyReferenceMutation.isPending} title={!availability.canMutateDomain ? availability.reason : undefined} className="px-3 py-2 rounded border disabled:opacity-40">{importLegacyReferenceMutation.isPending ? 'Importing…' : 'Import hint into exact Domain Experiment'}</button></>}
                </div>}
                {selectedMolbioSequenceId && ['plasmidQc', 'constructScreening', 'fastqQc', 'bamQc'].includes(selectedWorkflow) && <div className="p-3 rounded border"><label className="block text-xs mb-1">Approved comparison panel (MolBio receipt lane only)</label><select value={approvedComparisonPanelId} onChange={(event) => setApprovedComparisonPanelId(event.target.value)} className="w-full bg-[var(--bg-secondary)] border rounded px-3 py-2 text-sm"><option value="">No comparison panel</option>{approvedComparisonPanels.map((panel) => <option key={panel.id} value={panel.id}>{panel.label}</option>)}</select></div>}
                {referenceLibraryNotice && <p role="status" className="text-xs text-[var(--text-secondary)]">{referenceLibraryNotice}</p>}
            </div>
            )}

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
                {selectedWorkflow !== 'pooledAssignment' && (
                    <button
                        onClick={handleSubmit}
                        disabled={!canSubmit || submitMutation.isPending}
                        title={!usesMolBioReceiptLane && !availability.canMutateDomain
                            ? availability.reason
                            : (!usesMolBioReceiptLane && managedReferenceBlocker ? managedReferenceBlocker : undefined)}
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
                )}
            </div>
            {!canSubmit && (
                <p className="text-right text-xs text-[var(--text-secondary)]">
                    {!usesMolBioReceiptLane && !availability.canMutateDomain
                        ? `Run disabled: ${availability.reason}`
                        : (!usesMolBioReceiptLane && managedReferenceBlocker
                            ? `Run disabled: ${managedReferenceBlocker}`
                            : 'Run disabled until all required inputs and valid controls are present.')}
                </p>
            )}

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
