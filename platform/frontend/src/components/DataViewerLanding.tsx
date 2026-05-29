import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { importProteinBaseBundle, uploadFile } from '../lib/api';
import type { Job } from '../lib/api';

type RequestedFormat = 'auto' | 'proteinbase_jsonl' | 'tabular_csv' | 'jsonl_records';
type ResolvedFormat = 'proteinbase_jsonl' | 'tabular_csv' | 'jsonl_records' | 'unknown';

type PreviewFieldMap = {
    sequence: string | null;
    plddt: string | null;
    structure: string | null;
};

type ImportPreview = {
    resolvedFormat: ResolvedFormat;
    label: string;
    importable: boolean;
    recordCount: number | null;
    columns: string[];
    detectedFields: PreviewFieldMap;
    notes: string[];
    warnings: string[];
    sampleNames: string[];
};

type FeedbackMessage = {
    kind: 'error' | 'success';
    text: string;
};

type DataViewerLandingProps = {
    jobs: Job[];
    jobsLoading: boolean;
    onBrowseJobs: () => void;
    onSelectJob: (jobId: string) => void;
    onImportComplete: (job: Job) => void;
};

const SEQUENCE_COLUMN_PATTERNS = [/^sequence$/i, /aa.*seq/i, /protein.*sequence/i, /binder.*sequence/i];
const PLDDT_COLUMN_PATTERNS = [/plddt/i, /confidence/i, /mean.*lddt/i];
const STRUCTURE_COLUMN_PATTERNS = [/structure/i, /pdb/i, /cif/i, /mmcif/i, /url/i, /path/i];

function isRecordObject(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function slugify(value: string): string {
    return value
        .trim()
        .replace(/\.[^.]+$/, '')
        .replace(/[^a-zA-Z0-9._-]+/g, '_')
        .replace(/^[_.-]+|[_.-]+$/g, '')
        || 'proteinbase_import';
}

function prettifyFileStem(fileName: string): string {
    const stem = fileName.replace(/\.[^.]+$/, '');
    return stem
        .split(/[_\-.]+/)
        .filter(Boolean)
        .map((chunk) => chunk.charAt(0).toUpperCase() + chunk.slice(1))
        .join(' ')
        || 'Imported Dataset';
}

function pickLikelyColumn(columns: string[], patterns: RegExp[]): string | null {
    return columns.find((column) => patterns.some((pattern) => pattern.test(column))) ?? null;
}

function splitDelimitedLine(line: string, delimiter: string): string[] {
    const cells: string[] = [];
    let current = '';
    let inQuotes = false;

    for (let index = 0; index < line.length; index += 1) {
        const char = line[index];

        if (char === '"') {
            if (inQuotes && line[index + 1] === '"') {
                current += '"';
                index += 1;
            } else {
                inQuotes = !inQuotes;
            }
            continue;
        }

        if (char === delimiter && !inQuotes) {
            cells.push(current.trim());
            current = '';
            continue;
        }

        current += char;
    }

    cells.push(current.trim());
    return cells;
}

function detectDelimiter(line: string): string | null {
    const candidates = [',', '\t', ';'];
    let bestDelimiter: string | null = null;
    let bestCount = 1;

    candidates.forEach((candidate) => {
        const columns = splitDelimitedLine(line, candidate);
        if (columns.length > bestCount) {
            bestCount = columns.length;
            bestDelimiter = candidate;
        }
    });

    return bestDelimiter;
}

function nonEmptyLines(text: string): string[] {
    return text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
}

function detectJsonlPreview(text: string): ImportPreview {
    const lines = nonEmptyLines(text);
    if (lines.length === 0) {
        return {
            resolvedFormat: 'unknown',
            label: 'Empty file',
            importable: false,
            recordCount: 0,
            columns: [],
            detectedFields: { sequence: null, plddt: null, structure: null },
            notes: [],
            warnings: ['The selected file is empty.'],
            sampleNames: [],
        };
    }

    const parsedObjects: Record<string, unknown>[] = [];
    let parseFailures = 0;

    lines.slice(0, 25).forEach((line) => {
        try {
            const parsed = JSON.parse(line) as unknown;
            if (isRecordObject(parsed)) {
                parsedObjects.push(parsed);
            } else {
                parseFailures += 1;
            }
        } catch {
            parseFailures += 1;
        }
    });

    if (parsedObjects.length === 0) {
        return {
            resolvedFormat: 'unknown',
            label: 'Unrecognized text file',
            importable: false,
            recordCount: lines.length,
            columns: [],
            detectedFields: { sequence: null, plddt: null, structure: null },
            notes: [],
            warnings: ['Could not parse the file as JSONL records.'],
            sampleNames: [],
        };
    }

    const columnSet = new Set<string>();
    parsedObjects.forEach((record) => Object.keys(record).forEach((key) => columnSet.add(key)));
    const columns = Array.from(columnSet).sort();

    return {
        resolvedFormat: 'jsonl_records',
        label: 'Generic JSONL records',
        importable: false,
        recordCount: lines.length,
        columns,
        detectedFields: {
            sequence: pickLikelyColumn(columns, SEQUENCE_COLUMN_PATTERNS),
            plddt: pickLikelyColumn(columns, PLDDT_COLUMN_PATTERNS),
            structure: pickLikelyColumn(columns, STRUCTURE_COLUMN_PATTERNS),
        },
        notes: [
            'JSONL structure detected. This preview shows likely sequence / confidence columns for future importers.',
        ],
        warnings: parseFailures > 0
            ? ['Some sampled lines were not valid JSON objects.']
            : ['Generic JSONL preview is available, but only ProteinBase bundles import end-to-end today.'],
        sampleNames: parsedObjects
            .map((record) => (typeof record.name === 'string' ? record.name : typeof record.id === 'string' ? record.id : null))
            .filter((value): value is string => Boolean(value))
            .slice(0, 3),
    };
}

function detectProteinBasePreview(text: string): ImportPreview {
    const genericJsonl = detectJsonlPreview(text);
    if (genericJsonl.resolvedFormat !== 'jsonl_records') {
        return genericJsonl;
    }

    const lines = nonEmptyLines(text);
    const parsedObjects: Record<string, unknown>[] = [];
    lines.slice(0, 25).forEach((line) => {
        try {
            const parsed = JSON.parse(line) as unknown;
            if (isRecordObject(parsed)) {
                parsedObjects.push(parsed);
            }
        } catch {
            // Ignore here; genericJsonl already reported the parse failure if relevant.
        }
    });

    const metricNames = new Set<string>();
    parsedObjects.forEach((record) => {
        const evaluations = Array.isArray(record.evaluations) ? record.evaluations : [];
        evaluations.forEach((evaluation) => {
            if (!isRecordObject(evaluation)) {
                return;
            }
            const metric = evaluation.metric;
            if (typeof metric === 'string' && metric.trim()) {
                metricNames.add(metric.trim());
            }
        });
    });

    const metrics = Array.from(metricNames);
    const hasProteinBaseStructureMetric = metrics.some((metric) => /^(boltz2|esmfold)_structure_prediction$/i.test(metric));
    const hasProteinBaseMetadata = parsedObjects.some((record) => typeof record.sequence === 'string'
        || typeof record.protein_url === 'string'
        || typeof record.length_aa === 'number'
        || typeof record.author === 'string');
    const looksProteinBase = parsedObjects.some((record) => Array.isArray(record.evaluations))
        && hasProteinBaseStructureMetric
        && hasProteinBaseMetadata;

    if (!looksProteinBase) {
        return genericJsonl;
    }

    const plddtMetric = metrics.find((metric) => /plddt/i.test(metric)) ?? null;
    const structureMetric = metrics.find((metric) => /structure_prediction/i.test(metric)) ?? null;

    return {
        resolvedFormat: 'proteinbase_jsonl',
        label: 'ProteinBase JSONL bundle',
        importable: true,
        recordCount: lines.length,
        columns: genericJsonl.columns,
        detectedFields: {
            sequence: genericJsonl.detectedFields.sequence ?? 'sequence',
            plddt: plddtMetric,
            structure: structureMetric ?? 'evaluations[*].value.url',
        },
        notes: [
            'ProteinBase evaluation payload detected. Import will create a synthetic completed job in the existing Results Viewer.',
            'Structure metrics map into existing design analytics fields.',
            'Rows without a structure prediction URL are skipped during import so malformed edge cases do not blank the viewer.',
        ],
        warnings: structureMetric
            ? []
            : ['No structure_prediction metric was found in the sampled rows. Records without structure URLs will be skipped.'],
        sampleNames: parsedObjects
            .map((record) => (typeof record.name === 'string' ? record.name : typeof record.id === 'string' ? record.id : null))
            .filter((value): value is string => Boolean(value))
            .slice(0, 3),
    };
}

function detectTabularPreview(text: string): ImportPreview {
    const lines = nonEmptyLines(text);
    if (lines.length === 0) {
        return {
            resolvedFormat: 'unknown',
            label: 'Empty file',
            importable: false,
            recordCount: 0,
            columns: [],
            detectedFields: { sequence: null, plddt: null, structure: null },
            notes: [],
            warnings: ['The selected file is empty.'],
            sampleNames: [],
        };
    }

    const delimiter = detectDelimiter(lines[0]);
    if (!delimiter) {
        return {
            resolvedFormat: 'unknown',
            label: 'Unrecognized text file',
            importable: false,
            recordCount: lines.length,
            columns: [],
            detectedFields: { sequence: null, plddt: null, structure: null },
            notes: [],
            warnings: ['Could not detect CSV/TSV-style columns in the selected file.'],
            sampleNames: [],
        };
    }

    const columns = splitDelimitedLine(lines[0], delimiter)
        .map((column) => column.replace(/^"|"$/g, '').trim())
        .filter(Boolean);

    return {
        resolvedFormat: 'tabular_csv',
        label: delimiter === '\t' ? 'TSV / tabular dataset' : 'CSV / tabular dataset',
        importable: false,
        recordCount: Math.max(lines.length - 1, 0),
        columns,
        detectedFields: {
            sequence: pickLikelyColumn(columns, SEQUENCE_COLUMN_PATTERNS),
            plddt: pickLikelyColumn(columns, PLDDT_COLUMN_PATTERNS),
            structure: pickLikelyColumn(columns, STRUCTURE_COLUMN_PATTERNS),
        },
        notes: [
            'Columns auto-detected; sequence / pLDDT / structure hints are shown when present.',
        ],
        warnings: [
            'Generic CSV / TSV import preview is wired, but only ProteinBase JSONL currently creates a synthetic completed job from the UI.',
        ],
        sampleNames: [],
    };
}

function detectImportPreview(file: File, text: string, requestedFormat: RequestedFormat): ImportPreview {
    if (requestedFormat === 'proteinbase_jsonl') {
        const preview = detectProteinBasePreview(text);
        return preview.resolvedFormat === 'proteinbase_jsonl'
            ? preview
            : {
                ...preview,
                warnings: [
                    'ProteinBase JSONL was selected manually, but the sampled records did not match the expected ProteinBase shape.',
                    ...preview.warnings,
                ],
            };
    }

    if (requestedFormat === 'tabular_csv') {
        return detectTabularPreview(text);
    }

    if (requestedFormat === 'jsonl_records') {
        return detectJsonlPreview(text);
    }

    const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
    const firstNonEmptyLine = nonEmptyLines(text)[0] ?? '';
    const looksLikeJsonl = firstNonEmptyLine.startsWith('{') || firstNonEmptyLine.startsWith('[');

    if (extension === 'csv' || extension === 'tsv') {
        return detectTabularPreview(text);
    }

    if (looksLikeJsonl || extension === 'jsonl' || extension === 'ndjson' || extension === 'json') {
        return detectProteinBasePreview(text);
    }

    const proteinBasePreview = detectProteinBasePreview(text);
    if (proteinBasePreview.resolvedFormat === 'proteinbase_jsonl') {
        return proteinBasePreview;
    }

    const tabularPreview = detectTabularPreview(text);
    if (tabularPreview.resolvedFormat === 'tabular_csv') {
        return tabularPreview;
    }

    return proteinBasePreview;
}

function formatRelativeTime(timestamp: string): string {
    const millis = Date.parse(timestamp);
    if (Number.isNaN(millis)) {
        return timestamp;
    }

    const deltaMs = Date.now() - millis;
    const deltaHours = Math.round(deltaMs / (1000 * 60 * 60));
    if (Math.abs(deltaHours) < 24) {
        return `${Math.max(deltaHours, 0)}h ago`;
    }

    const deltaDays = Math.round(deltaHours / 24);
    return `${Math.max(deltaDays, 0)}d ago`;
}

function formatJobSubtitle(job: Job): string {
    const source = job.selection_dataset_name || job.stage_mode || job.model_id || job.mode;
    return `${source} • ${job.design_count.toLocaleString()} designs • ${formatRelativeTime(job.created_at)}`;
}

export function DataViewerLanding({
    jobs,
    jobsLoading,
    onBrowseJobs,
    onSelectJob,
    onImportComplete,
}: DataViewerLandingProps) {
    const queryClient = useQueryClient();
    const [requestedFormat, setRequestedFormat] = useState<RequestedFormat>('auto');
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [datasetName, setDatasetName] = useState('');
    const [jobName, setJobName] = useState('');
    const [datasetNameTouched, setDatasetNameTouched] = useState(false);
    const [jobNameTouched, setJobNameTouched] = useState(false);
    const [preview, setPreview] = useState<ImportPreview | null>(null);
    const [previewBusy, setPreviewBusy] = useState(false);
    const [feedback, setFeedback] = useState<FeedbackMessage | null>(null);

    const recentJobs = useMemo(
        () => [...jobs]
            .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
            .slice(0, 6),
        [jobs],
    );

    useEffect(() => {
        let cancelled = false;

        if (!selectedFile) {
            setPreview(null);
            setPreviewBusy(false);
            return () => {
                cancelled = true;
            };
        }

        setPreviewBusy(true);
        setPreview(null);

        const nextDatasetName = prettifyFileStem(selectedFile.name);
        if (!datasetNameTouched) {
            setDatasetName(nextDatasetName);
        }
        if (!jobNameTouched) {
            setJobName(`${nextDatasetName} Import`);
        }

        void (async () => {
            try {
                const text = await selectedFile.text();
                const nextPreview = detectImportPreview(selectedFile, text, requestedFormat);
                if (!cancelled) {
                    setPreview(nextPreview);
                }
            } catch (error) {
                if (!cancelled) {
                    setPreview({
                        resolvedFormat: 'unknown',
                        label: 'Unreadable file',
                        importable: false,
                        recordCount: null,
                        columns: [],
                        detectedFields: { sequence: null, plddt: null, structure: null },
                        notes: [],
                        warnings: [error instanceof Error ? error.message : 'Failed to read the selected file.'],
                        sampleNames: [],
                    });
                }
            } finally {
                if (!cancelled) {
                    setPreviewBusy(false);
                }
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [datasetNameTouched, jobNameTouched, requestedFormat, selectedFile]);

    const importMutation = useMutation({
        mutationFn: async () => {
            if (!selectedFile) {
                throw new Error('Choose a dataset file first.');
            }
            if (!preview?.importable) {
                throw new Error('Only ProteinBase JSONL imports today.');
            }
            const trimmedDatasetName = datasetName.trim();
            if (!trimmedDatasetName) {
                throw new Error('Dataset name is required.');
            }

            const uploadTarget = `inputs/data_imports/${Date.now()}_${slugify(selectedFile.name)}`;
            const uploadResponse = await uploadFile(uploadTarget, selectedFile);
            const importResponse = await importProteinBaseBundle({
                bundle_path: uploadResponse.data.path,
                dataset_name: trimmedDatasetName,
                job_name: jobName.trim() || undefined,
            });
            return importResponse.data;
        },
        onMutate: () => {
            setFeedback(null);
        },
        onSuccess: async (job) => {
            setFeedback({
                kind: 'success',
                text: `Imported ${job.name} (${job.design_count.toLocaleString()} designs). Opening Results...`,
            });
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['jobs'] }),
                queryClient.invalidateQueries({ queryKey: ['job', job.id] }),
            ]);
            onImportComplete(job);
        },
        onError: (error) => {
            setFeedback({
                kind: 'error',
                text: error instanceof Error ? error.message : 'Import failed.',
            });
        },
    });

    return (
        <div className="mb-8 grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]">
            <section className="rounded-3xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-slate-950/40 backdrop-blur">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-300">Data hub</div>
                        <h2 className="mt-2 text-2xl font-semibold text-white">Import into Results Viewer</h2>
                        <p className="mt-2 max-w-3xl text-sm text-slate-300">
                            Choose file → preview fields → import completed dataset, or open existing jobs.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={onBrowseJobs}
                        className="rounded-xl border border-slate-700 bg-slate-800/80 px-4 py-2 text-sm font-medium text-slate-100 transition-colors hover:border-slate-500 hover:bg-slate-800"
                    >
                        Browse workflows
                    </button>
                </div>

                <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.9fr)]">
                    <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-950/70 p-5">
                        <div className="grid gap-4 md:grid-cols-2">
                            <label className="space-y-2 text-sm text-slate-200">
                                <span className="block font-medium text-slate-100">Import format</span>
                                <select
                                    value={requestedFormat}
                                    onChange={(event) => setRequestedFormat(event.target.value as RequestedFormat)}
                                    className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                >
                                    <option value="auto">Auto-detect</option>
                                    <option value="proteinbase_jsonl">ProteinBase JSONL bundle</option>
                                    <option value="tabular_csv">Generic CSV / TSV table</option>
                                    <option value="jsonl_records">Generic JSONL records</option>
                                </select>
                            </label>

                            <label className="space-y-2 text-sm text-slate-200">
                                <span className="block font-medium text-slate-100">Dataset file</span>
                                <input
                                    type="file"
                                    accept=".jsonl,.ndjson,.json,.csv,.tsv,.txt"
                                    onChange={(event) => {
                                        setFeedback(null);
                                        setDatasetNameTouched(false);
                                        setJobNameTouched(false);
                                        setSelectedFile(event.target.files?.[0] ?? null);
                                    }}
                                    className="block w-full cursor-pointer rounded-xl border border-dashed border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300 file:mr-4 file:cursor-pointer file:rounded-lg file:border-0 file:bg-cyan-500/20 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-cyan-200"
                                />
                            </label>
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <label className="space-y-2 text-sm text-slate-200">
                                <span className="block font-medium text-slate-100">Dataset name</span>
                                <input
                                    type="text"
                                    value={datasetName}
                                    onChange={(event) => {
                                        setDatasetNameTouched(true);
                                        setDatasetName(event.target.value);
                                    }}
                                    placeholder="ProteinBase RBX1 Selected Submissions"
                                    className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-cyan-500"
                                />
                            </label>

                            <label className="space-y-2 text-sm text-slate-200">
                                <span className="block font-medium text-slate-100">Import job name</span>
                                <input
                                    type="text"
                                    value={jobName}
                                    onChange={(event) => {
                                        setJobNameTouched(true);
                                        setJobName(event.target.value);
                                    }}
                                    placeholder="ProteinBase RBX1 Import"
                                    className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-cyan-500"
                                />
                            </label>
                        </div>

                        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-sm font-medium text-white">Preview</span>
                                {previewBusy && (
                                    <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-[11px] text-cyan-200">
                                        Inspecting file...
                                    </span>
                                )}
                                {preview && !previewBusy && (
                                    <span className={`rounded-full border px-2 py-0.5 text-[11px] ${preview.importable
                                        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                                        : 'border-amber-500/30 bg-amber-500/10 text-amber-200'
                                        }`}>
                                        {preview.label}
                                    </span>
                                )}
                            </div>

                            {!selectedFile && (
                                <p className="mt-3 text-sm text-slate-400">
                                    Drop a dataset file to auto-detect ProteinBase, JSONL, or CSV/TSV.
                                </p>
                            )}

                            {selectedFile && preview && (
                                <div className="mt-4 space-y-4">
                                    <div className="grid gap-3 md:grid-cols-4">
                                        <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-3">
                                            <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Rows</div>
                                            <div className="mt-1 text-lg font-semibold text-white">{preview.recordCount ?? '—'}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-3">
                                            <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Sequence field</div>
                                            <div className="mt-1 text-sm font-medium text-slate-100">{preview.detectedFields.sequence ?? 'not found'}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-3">
                                            <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">pLDDT field</div>
                                            <div className="mt-1 text-sm font-medium text-slate-100">{preview.detectedFields.plddt ?? 'not found'}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-3">
                                            <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Structure field</div>
                                            <div className="mt-1 text-sm font-medium text-slate-100">{preview.detectedFields.structure ?? 'not found'}</div>
                                        </div>
                                    </div>

                                    {preview.columns.length > 0 && (
                                        <div>
                                            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Detected columns</div>
                                            <div className="flex flex-wrap gap-2">
                                                {preview.columns.slice(0, 12).map((column) => (
                                                    <span
                                                        key={column}
                                                        className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-300"
                                                    >
                                                        {column}
                                                    </span>
                                                ))}
                                                {preview.columns.length > 12 && (
                                                    <span className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-400">
                                                        +{preview.columns.length - 12} more
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                    )}

                                    {preview.sampleNames.length > 0 && (
                                        <div>
                                            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Sample entries</div>
                                            <div className="flex flex-wrap gap-2">
                                                {preview.sampleNames.map((sampleName) => (
                                                    <span
                                                        key={sampleName}
                                                        className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-300"
                                                    >
                                                        {sampleName}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    <div className="grid gap-4 md:grid-cols-2">
                                        <div>
                                            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Import handling</div>
                                            <ul className="space-y-2 text-sm text-slate-300">
                                                {preview.notes.map((note) => (
                                                    <li key={note} className="rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2">
                                                        {note}
                                                    </li>
                                                ))}
                                            </ul>
                                        </div>
                                        <div>
                                            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Warnings / edge cases</div>
                                            <ul className="space-y-2 text-sm text-slate-300">
                                                {(preview.warnings.length > 0 ? preview.warnings : ['No sampled warnings.']).map((warning) => (
                                                    <li key={warning} className="rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2">
                                                        {warning}
                                                    </li>
                                                ))}
                                            </ul>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        {feedback && (
                            <div className={`rounded-2xl border px-4 py-3 text-sm ${feedback.kind === 'error'
                                ? 'border-rose-500/30 bg-rose-500/10 text-rose-100'
                                : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100'
                                }`}>
                                {feedback.text}
                            </div>
                        )}

                        <div className="flex flex-wrap gap-3">
                            <button
                                type="button"
                                onClick={() => importMutation.mutate()}
                                disabled={importMutation.isPending || !preview?.importable || !selectedFile}
                                className="rounded-xl bg-cyan-500 px-4 py-2.5 text-sm font-semibold text-slate-950 transition-colors hover:bg-cyan-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                            >
                                {importMutation.isPending ? 'Importing dataset...' : 'Import into Results Viewer'}
                            </button>
                            <button
                                type="button"
                                onClick={onBrowseJobs}
                                className="rounded-xl border border-slate-700 bg-slate-900 px-4 py-2.5 text-sm font-medium text-slate-100 transition-colors hover:border-slate-500 hover:bg-slate-800"
                            >
                                View existing datasets
                            </button>
                        </div>
                    </div>

                    <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-950/70 p-5">
                        <div>
                            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Existing data</div>
                            <h3 className="mt-2 text-lg font-semibold text-white">Open a recent workflow or imported dataset</h3>
                            <p className="mt-2 text-sm text-slate-400">
                                Recent jobs and imported datasets appear here.
                            </p>
                        </div>

                        {jobsLoading && recentJobs.length === 0 ? (
                            <div className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-6 text-sm text-slate-400">
                                Loading jobs...
                            </div>
                        ) : recentJobs.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-slate-700 bg-slate-900/70 px-4 py-6 text-sm text-slate-400">
                                No existing protein-design jobs yet. Import a ProteinBase bundle to seed the viewer.
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {recentJobs.map((job) => (
                                    <button
                                        key={job.id}
                                        type="button"
                                        onClick={() => onSelectJob(job.id)}
                                        className="w-full rounded-2xl border border-slate-800 bg-slate-900/80 px-4 py-3 text-left transition-colors hover:border-cyan-500/40 hover:bg-slate-900"
                                    >
                                        <div className="flex items-start justify-between gap-3">
                                            <div className="min-w-0">
                                                <div className="truncate text-sm font-semibold text-white">{job.name}</div>
                                                <div className="mt-1 truncate text-xs text-slate-400">{formatJobSubtitle(job)}</div>
                                            </div>
                                            <span className={`rounded-full px-2 py-0.5 text-[11px] ${job.status === 'completed'
                                                ? 'bg-emerald-500/15 text-emerald-200'
                                                : job.status === 'running'
                                                    ? 'bg-cyan-500/15 text-cyan-200'
                                                    : job.status === 'failed'
                                                        ? 'bg-rose-500/15 text-rose-200'
                                                        : 'bg-slate-700 text-slate-300'
                                                }`}>
                                                {job.status}
                                            </span>
                                        </div>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </section>
        </div>
    );
}
