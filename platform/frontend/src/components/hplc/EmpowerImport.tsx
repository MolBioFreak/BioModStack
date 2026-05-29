/**
 * Empower 3 Import + SST Review
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import {
    exportEmpowerPlasmidTracking,
    exportEmpowerSstMaster,
    importEmpowerFiles,
    listAnalyticalDatasets,
    listEmpowerSst,
    loadAnalyticalDataset,
    updateEmpowerInjection,
} from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';
import { AssayPrimaryButton } from '../assay/AssayWorkbenchPrimitives';

interface EmpowerInjection {
    id?: number;
    import_id?: number;
    sample_name: string;
    sample_type: string;
    injection_number?: string | null;
    method_name?: string | null;
    run_date?: string | null;
    source_file?: string | null;
    total_area?: number | null;
    primary_peak_area?: number | null;
    primary_peak_percent?: number | null;
    primary_peak_rt?: number | null;
    primary_peak_resolution?: number | null;
    sample_role?: string | null;
    sample_role_source?: string | null;
    peak_count?: number | null;
    qc_flags?: string[];
    is_excluded?: boolean;
    note?: string | null;
    flag?: string | null;
}

interface SstSummary {
    sst_group: string;
    n_injections: number;
    area_mean: number;
    area_rsd: number;
    percent_primary_mean: number;
    percent_primary_rsd: number;
    rt_mean: number;
    rt_rsd: number;
    resolution_mean: number;
    resolution_rsd: number;
}

interface PlotlyPayload {
    data: Plotly.Data[];
    layout: Partial<Plotly.Layout>;
}

interface EmpowerSummary {
    n_injections: number;
    n_chromatograms: number;
    total_peak_rows: number;
    native_peak_rows: number;
    sample_role_counts: Record<string, number>;
    flag_counts: Record<string, number>;
    flagged_injection_count: number;
    primary_percent_mean?: number | null;
    primary_percent_rsd?: number | null;
    primary_rt_median?: number | null;
    total_area_rsd?: number | null;
    run_date_min?: string | null;
    run_date_max?: string | null;
    role_source_note?: string | null;
}

interface PeakTableRow {
    injection_id?: number | null;
    injection_number?: string | null;
    sample_name?: string | null;
    sample_role?: string | null;
    peak_id?: number | null;
    retention_time?: number | null;
    area?: number | null;
    area_percent?: number | null;
    height?: number | null;
    peak_source?: string | null;
    is_primary_peak?: boolean;
}

interface PeakRegionSummary {
    injection_id?: number | null;
    sample_name?: string | null;
    primary_area_percent?: number | null;
    pre_primary_area_percent?: number | null;
    post_primary_area_percent?: number | null;
    peak_count?: number | null;
}

interface AnalyticalDatasetSummary {
    dataset_id: string;
    dataset_label?: string | null;
    created_at?: string | null;
    metadata?: Record<string, unknown>;
}

interface AnalyticalDatasetDetail {
    dataset_id: string;
    dataset_label?: string | null;
    primary_import_id?: string | null;
    analysis_runs?: Array<{
        analysis_kind?: string;
        result_summary?: Record<string, unknown>;
        plotly_json?: Record<string, PlotlyPayload | null | undefined>;
    }>;
    chromatography_injections?: Array<Record<string, unknown>>;
    peak_table?: PeakTableRow[];
}

interface EmpowerImportPayload {
    dataset_id?: string | null;
    import_id?: number | null;
    injections?: EmpowerInjection[];
    sst_summary?: SstSummary[];
    errors?: string[];
    chromatogram_plotly_json?: PlotlyPayload | null;
    qc_plotly_json?: PlotlyPayload | null;
    composition_plotly_json?: PlotlyPayload | null;
    empower_summary?: EmpowerSummary | null;
    peak_table?: PeakTableRow[];
    peak_region_summary?: PeakRegionSummary[];
}

const supportedEmpowerImportExtensions = new Set(['csv', 'txt', 'cdf', 'arw', 'zip']);
const nativeEmpowerDatabaseExtensions = new Set(['raw', 'dat', 'mdb', 'accdb', 'db']);

function extensionOf(file: File): string {
    return file.name.toLowerCase().split('.').pop() ?? '';
}

function unsupportedNativeFiles(files: File[]): string[] {
    return files.filter((file) => nativeEmpowerDatabaseExtensions.has(extensionOf(file))).map((file) => file.name);
}

function formatNumber(value: number | null | undefined, digits = 2): string {
    return typeof value === 'number' && Number.isFinite(value)
        ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
        : '--';
}

function formatPercent(value: number | null | undefined, digits = 2): string {
    return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}%` : '--';
}

export function EmpowerImport() {
    const [files, setFiles] = useState<File[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [persistedNotice, setPersistedNotice] = useState('');
    const [importId, setImportId] = useState<number | null>(null);
    const [injections, setInjections] = useState<EmpowerInjection[]>([]);
    const [sstSummary, setSstSummary] = useState<SstSummary[]>([]);
    const [errors, setErrors] = useState<string[]>([]);
    const [chromatogramPlot, setChromatogramPlot] = useState<PlotlyPayload | null>(null);
    const [qcPlot, setQcPlot] = useState<PlotlyPayload | null>(null);
    const [compositionPlot, setCompositionPlot] = useState<PlotlyPayload | null>(null);
    const [empowerSummary, setEmpowerSummary] = useState<EmpowerSummary | null>(null);
    const [peakTable, setPeakTable] = useState<PeakTableRow[]>([]);
    const [peakRegionSummary, setPeakRegionSummary] = useState<PeakRegionSummary[]>([]);
    const [persistedDatasets, setPersistedDatasets] = useState<AnalyticalDatasetSummary[]>([]);
    const [selectedDatasetId, setSelectedDatasetId] = useState('');
    const [loadingDataset, setLoadingDataset] = useState(false);

    const [peakProminence, setPeakProminence] = useState(100);
    const [baselineMethod, setBaselineMethod] = useState('snip');
    const plotlyLayout = useThemePlotlyLayout();

    const totalInjections = useMemo(() => injections.length, [injections]);

    const applyEmpowerPayload = useCallback((response: EmpowerImportPayload) => {
        setImportId(response.import_id ?? null);
        setInjections(response.injections ?? []);
        setSstSummary(response.sst_summary ?? []);
        setErrors(response.errors ?? []);
        setChromatogramPlot(response.chromatogram_plotly_json ?? null);
        setQcPlot(response.qc_plotly_json ?? null);
        setCompositionPlot(response.composition_plotly_json ?? null);
        setEmpowerSummary(response.empower_summary ?? null);
        setPeakTable(response.peak_table ?? []);
        setPeakRegionSummary(response.peak_region_summary ?? []);
    }, []);

    const resetEmpowerReview = useCallback(() => {
        setImportId(null);
        setInjections([]);
        setSstSummary([]);
        setErrors([]);
        setChromatogramPlot(null);
        setQcPlot(null);
        setCompositionPlot(null);
        setEmpowerSummary(null);
        setPeakTable([]);
        setPeakRegionSummary([]);
    }, []);

    const refreshPersistedDatasets = useCallback(async () => {
        const datasets = await listAnalyticalDatasets('chromatography', 25) as AnalyticalDatasetSummary[];
        setPersistedDatasets(datasets);
        if (!selectedDatasetId && datasets.length > 0) {
            setSelectedDatasetId(datasets[0].dataset_id);
        }
    }, [selectedDatasetId]);

    const handleLoadPersistedDataset = useCallback(async () => {
        if (!selectedDatasetId) return;
        setLoadingDataset(true);
        setError('');
        try {
            const detail = await loadAnalyticalDataset(selectedDatasetId) as AnalyticalDatasetDetail;
            const reviewRun = (detail.analysis_runs ?? []).find((run) => run.analysis_kind === 'empower_import_review') ?? detail.analysis_runs?.[0];
            const resultSummary = reviewRun?.result_summary ?? {};
            const plotly = reviewRun?.plotly_json ?? {};
            setImportId(null);
            setInjections((detail.chromatography_injections ?? []).map((row) => ({
                sample_name: String(row.sample_name ?? row.sample_id ?? row.injection_name ?? 'unknown'),
                sample_type: String(row.sample_type ?? 'UNSPECIFIED_BY_EXPORT'),
                injection_number: row.injection_index == null ? String(row.injection_name ?? '') : String(row.injection_index),
                method_name: row.method == null ? null : String(row.method),
                total_area: null,
                primary_peak_area: typeof row.primary_peak_area === 'number' ? row.primary_peak_area : null,
                primary_peak_percent: typeof row.primary_peak_percent === 'number' ? row.primary_peak_percent : null,
                primary_peak_rt: typeof row.primary_peak_rt === 'number' ? row.primary_peak_rt : null,
                sample_role: row.sample_type == null ? null : String(row.sample_type),
                peak_count: typeof row.peak_count === 'number' ? row.peak_count : null,
                qc_flags: [],
            })));
            setSstSummary((resultSummary.sst_summary as SstSummary[]) ?? []);
            setChromatogramPlot((plotly.chromatogram_plotly_json as PlotlyPayload | null | undefined) ?? null);
            setQcPlot((plotly.qc_plotly_json as PlotlyPayload | null | undefined) ?? null);
            setCompositionPlot((plotly.composition_plotly_json as PlotlyPayload | null | undefined) ?? null);
            setEmpowerSummary((resultSummary.empower_summary as EmpowerSummary | null | undefined) ?? null);
            setPeakTable(detail.peak_table ?? []);
            setPeakRegionSummary((resultSummary.peak_region_summary as PeakRegionSummary[]) ?? []);
            setPersistedNotice(`Loaded persisted Empower dataset${detail.dataset_label ? `: ${detail.dataset_label}` : ''}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load persisted Empower dataset');
        } finally {
            setLoadingDataset(false);
        }
    }, [selectedDatasetId]);

    useEffect(() => {
        void refreshPersistedDatasets().catch(() => undefined);
    }, [refreshPersistedDatasets]);

    const handleClearCurrentReview = useCallback(() => {
        resetEmpowerReview();
        setError('');
        setPersistedNotice('');
    }, [resetEmpowerReview]);

    const handleFiles = (event: React.ChangeEvent<HTMLInputElement>) => {
        const selected = event.target.files ? Array.from(event.target.files) : [];
        setFiles(selected);
        const nativeFiles = unsupportedNativeFiles(selected);
        if (nativeFiles.length) {
            setError(
                `Unsupported native Empower database/RAW files (${nativeFiles.join(', ')}). Upload Empower AIA .cdf, ARW chromatogram text, a ZIP containing .cdf/.arw, or CSV/ASCII injection/peak export.`,
            );
        } else if (selected.some((file) => !supportedEmpowerImportExtensions.has(extensionOf(file)))) {
            setError('Select Empower AIA .cdf, ARW chromatogram text, ZIP containing .cdf/.arw, or CSV/ASCII exports with sample, sample type, injection, area, and retention-time columns.');
        } else {
            setError('');
        }
    };

    const handleImport = useCallback(async () => {
        if (!files.length) {
            setError('Select at least one Empower CSV/ASCII export (.csv or .txt).');
            return;
        }
        const nativeFiles = unsupportedNativeFiles(files);
        if (nativeFiles.length) {
            setError(
                `Unsupported native Empower database/RAW files (${nativeFiles.join(', ')}). Upload Empower AIA .cdf, ARW chromatogram text, a ZIP containing .cdf/.arw, or CSV/ASCII injection/peak export.`,
            );
            return;
        }
        const unsupported = files.filter((file) => !supportedEmpowerImportExtensions.has(extensionOf(file)));
        if (unsupported.length) {
            setError(`Unsupported Empower import file type: ${unsupported.map((file) => file.name).join(', ')}. Upload .cdf, .arw, .zip, .csv, or .txt exports.`);
            return;
        }

        setLoading(true);
        setError('');
        setPersistedNotice('');
        resetEmpowerReview();

        try {
            const response = await importEmpowerFiles(files, {
                persist: true,
                baselineMethod,
                peakProminence,
            }) as EmpowerImportPayload;
            applyEmpowerPayload(response);
            if (response.dataset_id) {
                setSelectedDatasetId(response.dataset_id);
            }
            await refreshPersistedDatasets();
            setPersistedNotice(response.dataset_id ? `Saved Empower import to BMS DB service dataset ${response.dataset_id}` : 'Parsed Empower import; no durable dataset id returned');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Import failed');
        } finally {
            setLoading(false);
        }
    }, [files, baselineMethod, peakProminence, applyEmpowerPayload, refreshPersistedDatasets, resetEmpowerReview]);

    const handleRefreshSst = useCallback(async () => {
        if (!importId) return;
        const summary = await listEmpowerSst(importId);
        setSstSummary(summary);
    }, [importId]);

    const handleInjectionUpdate = async (idx: number) => {
        const inj = injections[idx];
        if (!inj.id) return;
        const payload = {
            sample_name: inj.sample_name,
            sample_type: inj.sample_type,
            injection_number: inj.injection_number,
            is_excluded: inj.is_excluded,
            note: inj.note,
            flag: inj.flag,
        };
        await updateEmpowerInjection(inj.id, payload);
        await handleRefreshSst();
    };

    const handleExport = async (type: 'sst' | 'plasmid') => {
        if (!importId) return;
        const blob = type === 'sst'
            ? await exportEmpowerSstMaster(importId)
            : await exportEmpowerPlasmidTracking(importId);
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = type === 'sst' ? 'sst_master.csv' : 'plasmid_tracking.csv';
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    };

    const sampleTypeOptions = ['UNSPECIFIED_BY_EXPORT', 'SST', 'STANDARD', 'SAMPLE', 'BLANK', 'CONTROL'];

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-semibold text-text-primary">Empower 3 Chromatogram Import + SST Review</h3>
                <p className="text-text-secondary text-sm">
                    AIA/ARW/ZIP/CSV imports; injections, peaks, SST, plasmid logs.
                </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <label className="block text-xs text-text-muted">Empower exports (.cdf, .arw, .zip, .csv, .txt)</label>
                        <input
                            type="file"
                            multiple
                            accept=".cdf,.arw,.zip,.csv,.txt"
                            onChange={handleFiles}
                            className="block w-full text-xs text-text-secondary"
                        />
                        <p className="text-xs text-text-muted">
                            Proprietary DB/RAW needs Empower export to AIA/ARW or CSV/ASCII.
                        </p>
                        {files.length > 0 && (
                            <div className="text-xs text-text-muted">{files.length} file(s) selected</div>
                        )}
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <h4 className="text-sm font-medium text-text-primary">Parsing Defaults</h4>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Baseline Method</label>
                            <select
                                value={baselineMethod}
                                onChange={(e) => setBaselineMethod(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            >
                                <option value="snip">SNIP</option>
                                <option value="als">ALS</option>
                                <option value="linear">Linear</option>
                                <option value="none">None</option>
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Peak Prominence: {peakProminence}</label>
                            <input
                                type="range"
                                min="10"
                                max="500"
                                step="10"
                                value={peakProminence}
                                onChange={(e) => setPeakProminence(parseInt(e.target.value, 10))}
                                className="w-full accent-accent-primary"
                            />
                        </div>
                    </div>

                    <AssayPrimaryButton onClick={handleImport} disabled={loading || !files.length} className="w-full">
                        {loading ? 'Importing...' : 'Import Empower Data'}
                    </AssayPrimaryButton>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <div className="flex items-center justify-between gap-2">
                            <h4 className="text-sm font-medium text-text-primary">Persisted Empower imports</h4>
                            <button
                                type="button"
                                onClick={() => void refreshPersistedDatasets()}
                                className="border border-border-primary bg-bg-tertiary px-2 py-1 text-xs text-text-secondary hover:text-text-primary"
                            >
                                Refresh
                            </button>
                        </div>
                        <select
                            value={selectedDatasetId}
                            onChange={(event) => setSelectedDatasetId(event.target.value)}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-xs"
                        >
                            <option value="">{persistedDatasets.length ? 'Select persisted dataset' : 'No persisted chromatography datasets yet'}</option>
                            {persistedDatasets.map((dataset) => (
                                <option key={dataset.dataset_id} value={dataset.dataset_id}>
                                    {(dataset.dataset_label ?? dataset.dataset_id).slice(0, 90)}{dataset.created_at ? ` · ${new Date(dataset.created_at).toLocaleDateString()}` : ''}
                                </option>
                            ))}
                        </select>
                        <button
                            type="button"
                            onClick={() => void handleLoadPersistedDataset()}
                            disabled={!selectedDatasetId || loadingDataset}
                            className="w-full border border-accent-primary bg-accent-primary/10 px-3 py-2 text-xs text-accent-primary hover:bg-accent-primary/20 disabled:opacity-50"
                        >
                            {loadingDataset ? 'Loading persisted dataset...' : 'Load selected persisted import + plots'}
                        </button>
                        <p className="text-xs text-text-muted">Reloads durable Empower review plots from the BMS DB service analytical store, not browser cache.</p>
                    </div>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                    {errors.length > 0 && (
                        <div className="p-3 bg-warning/20 border border-warning text-warning text-xs space-y-1">
                            {errors.map((msg, idx) => (
                                <div key={idx}>{msg}</div>
                            ))}
                        </div>
                    )}
                    {(persistedNotice || totalInjections > 0) && (
                        <div className="border border-accent-primary/40 bg-accent-primary/10 p-3 text-xs text-text-secondary">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <div className="font-semibold text-text-primary">Durable analytical-store review</div>
                                    <div>{persistedNotice || 'Latest Empower import is loaded from the BMS DB service response.'}</div>
                                </div>
                                <button
                                    type="button"
                                    onClick={handleClearCurrentReview}
                                    className="border border-border-primary bg-bg-tertiary px-2 py-1 text-text-secondary hover:text-text-primary"
                                >
                                    Clear current Empower review
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                                <h4 className="text-sm font-medium text-text-primary">Import Summary</h4>
                                <p className="text-xs text-text-muted">Total injections: {totalInjections}</p>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => handleExport('sst')}
                                    disabled={!importId}
                                    className="px-3 py-1 bg-bg-tertiary border border-border-primary text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"
                                >
                                    Export SST Master
                                </button>
                                <button
                                    onClick={() => handleExport('plasmid')}
                                    disabled={!importId}
                                    className="px-3 py-1 bg-bg-tertiary border border-border-primary text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"
                                >
                                    Export Plasmid Log
                                </button>
                            </div>
                        </div>
                        {empowerSummary && (
                            <div className="mt-4 space-y-3">
                                <div className="grid gap-3 md:grid-cols-4">
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">Chromatograms</div>
                                        <div className="mt-1 text-lg font-semibold text-text-primary">{formatNumber(empowerSummary.n_chromatograms, 0)}</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">Native peaks</div>
                                        <div className="mt-1 text-lg font-semibold text-text-primary">{formatNumber(empowerSummary.native_peak_rows, 0)}</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">Primary % mean</div>
                                        <div className="mt-1 text-lg font-semibold text-text-primary">{formatPercent(empowerSummary.primary_percent_mean)}</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">Flagged injections</div>
                                        <div className="mt-1 text-lg font-semibold text-text-primary">{formatNumber(empowerSummary.flagged_injection_count, 0)}</div>
                                    </div>
                                </div>
                                <div className="grid gap-3 md:grid-cols-3 text-xs">
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="font-semibold text-text-primary">Batch precision</div>
                                        <div className="mt-1 text-text-secondary">Primary %RSD: {formatPercent(empowerSummary.primary_percent_rsd)}</div>
                                        <div className="text-text-secondary">Total area %RSD: {formatPercent(empowerSummary.total_area_rsd)}</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="font-semibold text-text-primary">Retention timing</div>
                                        <div className="mt-1 text-text-secondary">Median primary RT: {formatNumber(empowerSummary.primary_rt_median, 3)} min</div>
                                        <div className="text-text-secondary">Run window: {empowerSummary.run_date_min ?? '--'} to {empowerSummary.run_date_max ?? '--'}</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="font-semibold text-text-primary">Sample roles</div>
                                        <div className="mt-1 flex flex-wrap gap-1">
                                            {Object.entries(empowerSummary.sample_role_counts ?? {}).map(([role, count]) => (
                                                <span key={role} className="rounded border border-border-primary px-2 py-1 text-text-secondary">{role}: {count}</span>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                                {Object.keys(empowerSummary.flag_counts ?? {}).length > 0 && (
                                    <div className="border border-warning/50 bg-warning/10 p-3 text-xs text-warning">
                                        <span className="font-semibold">QC flags: </span>
                                        {Object.entries(empowerSummary.flag_counts).map(([flag, count]) => `${flag}: ${count}`).join(', ')}
                                    </div>
                                )}
                                {empowerSummary.role_source_note && (
                                    <p className="text-xs text-text-muted">{empowerSummary.role_source_note}</p>
                                )}
                            </div>
                        )}
                    </div>

                    {chromatogramPlot && chromatogramPlot.data.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <h4 className="text-sm font-medium text-text-primary mb-3">Chromatogram Overlay</h4>
                            <Plot
                                data={chromatogramPlot.data}
                                layout={{ ...plotlyLayout, ...chromatogramPlot.layout, autosize: true, height: 420 }}
                                useResizeHandler
                                style={{ width: '100%' }}
                            />
                        </div>
                    )}

                    {qcPlot && qcPlot.data.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <h4 className="text-sm font-medium text-text-primary mb-3">Batch QC</h4>
                            <Plot
                                data={qcPlot.data}
                                layout={{ ...plotlyLayout, ...qcPlot.layout, autosize: true, height: 360 }}
                                useResizeHandler
                                style={{ width: '100%' }}
                            />
                        </div>
                    )}

                    {compositionPlot && compositionPlot.data.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <h4 className="text-sm font-medium text-text-primary mb-3">Peak Composition</h4>
                            <Plot
                                data={compositionPlot.data}
                                layout={{ ...plotlyLayout, ...compositionPlot.layout, autosize: true, height: 360 }}
                                useResizeHandler
                                style={{ width: '100%' }}
                            />
                        </div>
                    )}

                    {sstSummary.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h4 className="text-sm font-medium text-text-primary">SST Summary</h4>
                                <button
                                    onClick={handleRefreshSst}
                                    className="px-2 py-1 text-xs border border-border-primary text-text-secondary"
                                >
                                    Refresh
                                </button>
                            </div>
                            <div className="overflow-auto">
                                <table className="w-full text-xs">
                                    <thead className="bg-bg-tertiary">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Group</th>
                                            <th className="px-3 py-2 text-left">n</th>
                                            <th className="px-3 py-2 text-left">Area Mean</th>
                                            <th className="px-3 py-2 text-left">Area %RSD</th>
                                            <th className="px-3 py-2 text-left">%Primary Mean</th>
                                            <th className="px-3 py-2 text-left">%Primary %RSD</th>
                                            <th className="px-3 py-2 text-left">RT Mean</th>
                                            <th className="px-3 py-2 text-left">RT %RSD</th>
                                            <th className="px-3 py-2 text-left">Res Mean</th>
                                            <th className="px-3 py-2 text-left">Res %RSD</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sstSummary.map((row) => (
                                            <tr key={row.sst_group} className="border-t border-border-primary">
                                                <td className="px-3 py-2 text-text-primary">{row.sst_group}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.n_injections}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.area_mean.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.area_rsd.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.percent_primary_mean.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.percent_primary_rsd.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.rt_mean.toFixed(3)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.rt_rsd.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.resolution_mean.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.resolution_rsd.toFixed(2)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {peakRegionSummary.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <h4 className="text-sm font-medium text-text-primary mb-3">Isoform / Region Summary</h4>
                            <div className="overflow-auto max-h-[320px]">
                                <table className="w-full text-xs">
                                    <thead className="bg-bg-tertiary">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Sample</th>
                                            <th className="px-3 py-2 text-left">Primary %</th>
                                            <th className="px-3 py-2 text-left">Pre-primary %</th>
                                            <th className="px-3 py-2 text-left">Post-primary %</th>
                                            <th className="px-3 py-2 text-left">Peak count</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {peakRegionSummary.map((row, idx) => (
                                            <tr key={`${row.injection_id ?? idx}-${row.sample_name ?? 'sample'}`} className="border-t border-border-primary">
                                                <td className="px-3 py-2 text-text-primary">{row.sample_name ?? '--'}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatPercent(row.primary_area_percent)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatPercent(row.pre_primary_area_percent)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatPercent(row.post_primary_area_percent)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatNumber(row.peak_count, 0)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {peakTable.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                                <h4 className="text-sm font-medium text-text-primary">Flattened Peak Table</h4>
                                <span className="text-xs text-text-muted">{formatNumber(peakTable.length, 0)} real Empower peak rows</span>
                            </div>
                            <div className="overflow-auto max-h-[420px]">
                                <table className="w-full text-xs">
                                    <thead className="bg-bg-tertiary">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Sample</th>
                                            <th className="px-3 py-2 text-left">Role</th>
                                            <th className="px-3 py-2 text-left">Peak</th>
                                            <th className="px-3 py-2 text-left">RT</th>
                                            <th className="px-3 py-2 text-left">Area</th>
                                            <th className="px-3 py-2 text-left">Area %</th>
                                            <th className="px-3 py-2 text-left">Source</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {peakTable.slice(0, 500).map((row, idx) => (
                                            <tr key={`${row.injection_id ?? 'inj'}-${row.peak_id ?? idx}-${idx}`} className="border-t border-border-primary">
                                                <td className="px-3 py-2 text-text-primary">{row.sample_name ?? '--'}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.sample_role ?? '--'}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.peak_id ?? '--'}{row.is_primary_peak ? ' ★' : ''}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatNumber(row.retention_time, 3)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatNumber(row.area, 2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{formatPercent(row.area_percent)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.peak_source ?? '--'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            {peakTable.length > 500 && (
                                <p className="mt-2 text-xs text-text-muted">Showing first 500 peaks in-browser; exports/API retain the full table.</p>
                            )}
                        </div>
                    )}

                    {injections.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <h4 className="text-sm font-medium text-text-primary mb-3">Injection Review</h4>
                            <div className="overflow-auto max-h-[420px]">
                                <table className="w-full text-xs">
                                    <thead className="bg-bg-tertiary">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Sample</th>
                                            <th className="px-3 py-2 text-left">Type</th>
                                            <th className="px-3 py-2 text-left">Inj #</th>
                                            <th className="px-3 py-2 text-left">Primary Area</th>
                                            <th className="px-3 py-2 text-left">% Primary</th>
                                            <th className="px-3 py-2 text-left">RT</th>
                                            <th className="px-3 py-2 text-left">Res</th>
                                            <th className="px-3 py-2 text-left">Note</th>
                                            <th className="px-3 py-2 text-left">Exclude</th>
                                            <th className="px-3 py-2 text-left"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {injections.map((inj, idx) => (
                                            <tr key={idx} className="border-t border-border-primary">
                                                <td className="px-3 py-2 text-text-primary">
                                                    <input
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs w-40"
                                                        value={inj.sample_name}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, sample_name: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2">
                                                    <select
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs"
                                                        value={inj.sample_type}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, sample_type: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    >
                                                        {sampleTypeOptions.map((option) => (
                                                            <option key={option} value={option}>{option}</option>
                                                        ))}
                                                    </select>
                                                </td>
                                                <td className="px-3 py-2">
                                                    <input
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs w-20"
                                                        value={inj.injection_number ?? ''}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, injection_number: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_area?.toFixed(2) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_percent?.toFixed(2) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_rt?.toFixed(3) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_resolution?.toFixed(2) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2">
                                                    <input
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs w-32"
                                                        value={inj.note ?? ''}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, note: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-center">
                                                    <input
                                                        type="checkbox"
                                                        checked={inj.is_excluded ?? false}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, is_excluded: e.target.checked };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2">
                                                    <button
                                                        onClick={() => handleInjectionUpdate(idx)}
                                                        className="px-2 py-1 text-xs border border-border-primary text-text-secondary hover:text-text-primary"
                                                    >
                                                        Save
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {injections.length === 0 && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Import Empower exports to review injections
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
