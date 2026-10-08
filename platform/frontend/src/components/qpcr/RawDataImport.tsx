/**
 * qPCR Raw Data Import - File upload and plate visualization
 */

import { Fragment, useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import Plot from 'react-plotly.js';
import { uploadQpcrFile } from '../../api/client';
import {
    clearAssaySnapshot,
    loadAssaySnapshot,
    makeAssaySnapshot,
    QPCR_RAW_IMPORT_CACHE_KEY,
    saveAssaySnapshot,
} from '../assayPersistence';
import { useThemePlotlyLayout } from '../useThemeColors';
import { AssayPrimaryButton } from '../assay/AssayWorkbenchPrimitives';
import { resolveQpcrInitialTab, highlightSelectedWellAmplificationTraces, highlightSelectedWellStandardCurvePoints, type QpcrRawImportTab } from './plotHelpers';
import {
    QPCR_PLATE_COLUMNS,
    QPCR_PLATE_ROWS,
    buildQpcrAssayReviewMetrics,
    buildQpcrChannelAnalytics,
    buildQpcrDilutionWorksheetRows,
    buildQpcrPlateMap,
    buildQpcrTargetOptions,
    buildSelectedQpcrWellAnalytics,
    formatQpcrMetric,
    type QpcrAssaySummaryLike,
    type QpcrDilutionWorksheetInput,
    type QpcrPlateStatus,
    type QpcrPlateWellSummary,
    type QpcrWellEntry,
} from './plateMap';

interface Well extends QpcrWellEntry {
    well_position: string;
    sample_name: string;
    target_name: string;
    task: string;
    ct: number | string | null;
    quantity?: number | string | null;
}

interface StandardCurveStats {
    target_name?: string;
    slope?: number;
    intercept?: number;
    r_squared?: number;
    efficiency?: number;
    efficiency_percent?: number;
    residual_std?: number;
    n_points?: number;
    is_valid?: boolean;
    flags?: string[];
}

interface RawQpcrImportResponse {
    filename?: string;
    n_wells?: number;
    targets?: string[];
    samples?: string[];
    wells?: Well[];
    results_plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    amplification_plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    standard_curve_plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    standard_curve_stats?: StandardCurveStats;
    standard_curve_stats_by_target?: Record<string, StandardCurveStats>;
    eds_summary?: {
        ct_values_are_authoritative?: boolean;
        ct_provenance?: string;
        ct_algorithm?: string;
        recommendation?: string;
        qslib_error?: string;
        ct_values_calculated_from_multicomponentdata?: number;
        ct_result_table_detected?: boolean;
    };
    assay_summary?: QpcrAssaySummaryLike & {
        standard_curves?: Record<string, { r_squared?: number; efficiency_percent?: number; is_valid?: boolean; flags?: string[] }>;
        ntc_qc?: unknown[];
        flag_counts?: Record<string, number>;
    };
}

const PLATE_TARGET_ALL = '__all__';

const reviewFocusLabelByTab: Record<QpcrRawImportTab, string> = {
    heatmap: 'plate map first',
    curves: 'amplification curves first',
    table: 'parsed result rows first',
    stdcurve: 'standard curve first',
};

function plateStatusLabel(status: QpcrPlateStatus): string {
    switch (status) {
        case 'standard': return 'Standard';
        case 'ntc': return 'NTC';
        case 'review': return 'Review';
        case 'mixed': return 'Mixed';
        case 'sample': return 'Sample';
        case 'empty':
        default:
            return 'Empty';
    }
}

function plateWellClassName(well: QpcrPlateWellSummary, selected: boolean): string {
    const base = 'relative flex aspect-square w-full max-w-[1.72rem] min-w-0 flex-col items-center justify-center overflow-hidden rounded-full border text-center transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-accent-primary focus:ring-offset-1 focus:ring-offset-bg-secondary min-[1850px]:max-w-[1.95rem]';
    const selectedClass = selected ? 'ring-2 ring-accent-primary ring-offset-1 ring-offset-bg-secondary scale-105 shadow-lg shadow-accent-primary/25' : 'hover:scale-105 hover:border-accent-primary';
    switch (well.status) {
        case 'standard':
            return `${base} ${selectedClass} border-sky-300 bg-sky-500/25 text-sky-50`;
        case 'ntc':
            return `${base} ${selectedClass} border-violet-300 bg-violet-500/20 text-violet-100`;
        case 'review':
            return `${base} ${selectedClass} border-warning bg-warning/20 text-warning`;
        case 'mixed':
            return `${base} ${selectedClass} border-fuchsia-300 bg-fuchsia-500/25 text-fuchsia-50`;
        case 'sample':
            return `${base} ${selectedClass} border-emerald-300 bg-emerald-500/25 text-emerald-50`;
        case 'empty':
        default:
            return `${base} ${selectedClass} border-border-primary bg-bg-tertiary/40 text-text-muted opacity-50`;
    }
}

function plateLegendDotClassName(status: QpcrPlateStatus): string {
    switch (status) {
        case 'standard':
            return 'inline-block h-3 w-3 rounded-full border border-sky-300 bg-sky-500/40';
        case 'ntc':
            return 'inline-block h-3 w-3 rounded-full border border-violet-300 bg-violet-500/40';
        case 'review':
            return 'inline-block h-3 w-3 rounded-full border border-warning bg-warning/30';
        case 'mixed':
            return 'inline-block h-3 w-3 rounded-full border border-fuchsia-300 bg-fuchsia-500/40';
        case 'sample':
            return 'inline-block h-3 w-3 rounded-full border border-emerald-300 bg-emerald-500/40';
        case 'empty':
        default:
            return 'inline-block h-3 w-3 rounded-full border border-border-primary bg-bg-tertiary/40 opacity-60';
    }
}

function plateWellStyle(well: QpcrPlateWellSummary, ctMin: number, ctMax: number): CSSProperties | undefined {
    if (well.status === 'empty' || well.ctSummary.mean === null) return undefined;
    const min = Number.isFinite(ctMin) ? ctMin : 10;
    const max = Number.isFinite(ctMax) && ctMax > min ? ctMax : min + 1;
    const normalized = Math.max(0, Math.min(1, (well.ctSummary.mean - min) / (max - min)));
    const hue = Math.round(156 - normalized * 126);
    return {
        background: `linear-gradient(145deg, hsla(${hue}, 84%, 45%, 0.42), rgba(15, 23, 42, 0.78))`,
        boxShadow: `inset 0 0 0 ${well.entries.length > 1 ? 5 : 3}px rgba(255, 255, 255, ${0.08 + (1 - normalized) * 0.09})`,
    };
}

function renderEntryMetric(value: unknown, digits = 3): string {
    return formatQpcrMetric(value, digits, '--');
}

function rowContainsValue(row: Record<string, unknown>, keys: string[]): boolean {
    return keys.some((key) => row[key] !== null && row[key] !== undefined && row[key] !== '');
}

export function RawDataImport() {
    const [file, setFile] = useState<File | null>(null);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [cacheNotice, setCacheNotice] = useState('');
    const [ctMin, setCtMin] = useState(10);
    const [ctMax, setCtMax] = useState(35);
    const [preferredReviewFocus, setPreferredReviewFocus] = useState<QpcrRawImportTab>('heatmap');
    const [selectedPlateTarget, setSelectedPlateTarget] = useState<string>(PLATE_TARGET_ALL);
    const [selectedWellPosition, setSelectedWellPosition] = useState('A1');
    const [dilutionInputs, setDilutionInputs] = useState<Record<string, QpcrDilutionWorksheetInput>>({});

    const plotlyLayout = useThemePlotlyLayout();

    useEffect(() => {
        let cancelled = false;
        void loadAssaySnapshot<Record<string, unknown>>(QPCR_RAW_IMPORT_CACHE_KEY)
            .then((snapshot) => {
                if (cancelled || !snapshot) return;
                setResult(snapshot.payload);
                setPreferredReviewFocus(resolveQpcrInitialTab(snapshot.payload));
                setCacheNotice(`Restored cached qPCR import${snapshot.label ? `: ${snapshot.label}` : ''}`);
            })
            .catch(() => undefined);
        return () => {
            cancelled = true;
        };
    }, []);

    const handleClearCachedImport = useCallback(async () => {
        await clearAssaySnapshot(QPCR_RAW_IMPORT_CACHE_KEY);
        setResult(null);
        setError('');
        setCacheNotice('');
        setPreferredReviewFocus('heatmap');
        setSelectedPlateTarget(PLATE_TARGET_ALL);
        setSelectedWellPosition('A1');
        setDilutionInputs({});
    }, []);

    const handleUpload = useCallback(async () => {
        if (!file) {
            setError('Please select a file');
            return;
        }

        setLoading(true);
        setError('');
        setCacheNotice('');
        setResult(null);

        try {
            const response = await uploadQpcrFile(file);
            const payload = response as Record<string, unknown>;
            const label = typeof response?.filename === 'string' ? response.filename : file.name;
            const snapshot = makeAssaySnapshot(payload, label);
            setResult(payload);
            setDilutionInputs({});
            setPreferredReviewFocus(resolveQpcrInitialTab(response));
            if (await saveAssaySnapshot(QPCR_RAW_IMPORT_CACHE_KEY, snapshot)) {
                setCacheNotice(`Saved qPCR import cache for ${snapshot.label ?? 'last upload'}`);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Upload failed');
        } finally {
            setLoading(false);
        }
    }, [file]);

    const r = result as RawQpcrImportResponse | null;
    const assayFlags = r?.assay_summary?.flag_counts ? Object.entries(r.assay_summary.flag_counts) : [];
    const curveStatsByTarget = r?.standard_curve_stats_by_target ? Object.entries(r.standard_curve_stats_by_target) : [];
    const standardCurveCards = curveStatsByTarget.length > 0
        ? curveStatsByTarget
        : r?.standard_curve_stats
            ? [[r.standard_curve_stats.target_name ?? 'Standard curve', r.standard_curve_stats] as [string, StandardCurveStats]]
            : [];
    const curveQualityFlags = standardCurveCards.flatMap(([target, stats]) => (stats.flags ?? []).map(flag => `${target}: ${flag}`));
    const standardCurveBestR2 = standardCurveCards.reduce<number | null>((best, [, stats]) => {
        if (stats.r_squared === undefined) return best;
        return best === null ? stats.r_squared : Math.max(best, stats.r_squared);
    }, null);
    const standardCurveEfficiencyValues = standardCurveCards
        .map(([, stats]) => stats.efficiency_percent ?? stats.efficiency)
        .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    const standardCurveStandardsCount = standardCurveCards.reduce((sum, [, stats]) => sum + (stats.n_points ?? 0), 0);
    const edsCtIsComputed = r?.eds_summary?.ct_values_are_authoritative === false;
    const activePlateTarget = selectedPlateTarget === PLATE_TARGET_ALL ? undefined : selectedPlateTarget;
    const plateTargetOptions = useMemo(() => buildQpcrTargetOptions(r?.wells ?? []), [r?.wells]);
    const plateMap = useMemo(() => buildQpcrPlateMap(r?.wells ?? [], { targetName: activePlateTarget }), [r?.wells, activePlateTarget]);
    const plateMapByPosition = useMemo(() => new Map(plateMap.map((well) => [well.position, well])), [plateMap]);
    const selectedWellAnalytics = useMemo(
        () => buildSelectedQpcrWellAnalytics(selectedWellPosition, r?.wells ?? [], r?.assay_summary, activePlateTarget),
        [activePlateTarget, r?.assay_summary, r?.wells, selectedWellPosition],
    );
    const assayReviewMetrics = useMemo(
        () => buildQpcrAssayReviewMetrics(r?.wells ?? [], r?.assay_summary),
        [r?.assay_summary, r?.wells],
    );
    const channelAnalytics = useMemo(
        () => buildQpcrChannelAnalytics(r?.wells ?? []),
        [r?.wells],
    );
    const dilutionWorksheetRows = useMemo(
        () => buildQpcrDilutionWorksheetRows(r?.wells ?? [], r?.assay_summary, dilutionInputs),
        [dilutionInputs, r?.assay_summary, r?.wells],
    );
    const selectedCurveTarget = activePlateTarget ?? (selectedWellAnalytics.targets.length === 1 ? selectedWellAnalytics.targets[0] : undefined);
    const highlightedAmplificationData = useMemo(
        () => highlightSelectedWellAmplificationTraces(r?.amplification_plotly_json?.data ?? [], selectedWellPosition, selectedCurveTarget) as Plotly.Data[],
        [r?.amplification_plotly_json?.data, selectedCurveTarget, selectedWellPosition],
    );
    const highlightedStandardCurveData = useMemo(
        () => highlightSelectedWellStandardCurvePoints(r?.standard_curve_plotly_json?.data ?? [], selectedWellPosition, selectedCurveTarget) as Plotly.Data[],
        [r?.standard_curve_plotly_json?.data, selectedCurveTarget, selectedWellPosition],
    );
    const activeReviewFocusLabel = reviewFocusLabelByTab[preferredReviewFocus] ?? reviewFocusLabelByTab.heatmap;

    useEffect(() => {
        if (!r?.wells?.length) {
            setSelectedPlateTarget(PLATE_TARGET_ALL);
            setSelectedWellPosition('A1');
            return;
        }
        const firstPopulatedWell = buildQpcrPlateMap(r.wells).find((well) => well.entries.length > 0);
        setSelectedPlateTarget(PLATE_TARGET_ALL);
        setSelectedWellPosition(firstPopulatedWell?.position ?? 'A1');
    }, [r?.wells]);

    const handlePlateTargetChange = useCallback((target: string) => {
        setSelectedPlateTarget(target);
        const nextTarget = target === PLATE_TARGET_ALL ? undefined : target;
        const firstPopulatedWell = buildQpcrPlateMap(r?.wells ?? [], { targetName: nextTarget }).find((well) => well.entries.length > 0);
        setSelectedWellPosition(firstPopulatedWell?.position ?? 'A1');
    }, [r?.wells]);

    const handleDilutionInputChange = useCallback((key: string, field: keyof QpcrDilutionWorksheetInput, value: string) => {
        setDilutionInputs((current) => ({
            ...current,
            [key]: {
                ...(current[key] ?? {}),
                [field]: value,
            },
        }));
    }, []);

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Raw Data Import</h3>

            <div className="grid grid-cols-1 gap-6 min-[1500px]:grid-cols-[minmax(280px,360px)_minmax(0,1fr)]">
                {/* Left - Input */}
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-sm font-medium text-text-primary mb-2">QuantStudio .eds / StepOnePlus exports</label>
                        <p className="text-xs text-text-muted mb-3">
                            Upload QuantStudio .eds, StepOnePlus/QuantStudio Excel, or real instrument CSV. Large uploads are allowed through the BMS nginx proxy.
                        </p>

                        <input
                            type="file"
                            accept=".eds,.xlsx,.xls,.csv"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
                            className="block w-full text-sm text-text-secondary file:mr-4 file:py-2 file:px-4 file:border-0 file:text-sm file:font-medium file:bg-accent-primary file:text-white"
                        />

                        {file && (
                            <div className="mt-2 text-xs text-text-secondary">
                                Selected: {file.name} ({(file.size / 1024).toFixed(1)} KB)
                            </div>
                        )}
                    </div>

                    <AssayPrimaryButton onClick={handleUpload} disabled={loading || !file} className="w-full">
                        {loading ? 'Uploading...' : 'Upload & Parse'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                    {(cacheNotice || r) && (
                        <div className="border border-accent-primary/40 bg-accent-primary/10 p-3 text-xs text-text-secondary">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <div className="font-semibold text-text-primary">Review cache</div>
                                    <div>{cacheNotice || 'Latest qPCR import is cached in this browser after upload.'}</div>
                                </div>
                                <button
                                    type="button"
                                    onClick={handleClearCachedImport}
                                    className="border border-border-primary bg-bg-tertiary px-2 py-1 text-text-secondary hover:text-text-primary"
                                >
                                    Clear cached qPCR import
                                </button>
                            </div>
                        </div>
                    )}

                    {r && (
                        <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                            <div className="mb-2"><strong>File:</strong> {r.filename}</div>
                            <div className="mb-2"><strong>Wells:</strong> {r.n_wells}</div>
                            <div className="mb-2"><strong>Targets:</strong> {r.targets?.join(', ')}</div>
                            <div className="text-xs text-text-muted">
                                Samples: {r.samples?.slice(0, 5).join(', ')}{r.samples && r.samples.length > 5 ? '...' : ''}
                            </div>
                            {edsCtIsComputed && (
                                <div className="mt-3 border border-warning/60 bg-warning/10 p-3 text-xs text-warning">
                                    <div className="font-semibold text-warning">EDS Cq/Ct values are computed, not authoritative</div>
                                    <div className="mt-1 text-text-secondary">
                                        This EDS archive did not expose a scalar result table; BMS calculated Cq/Ct from multicomponent curves. Use the QuantStudio/StepOnePlus Excel Results export as the known-correct source when both files are present.
                                    </div>
                                    <div className="mt-2 text-text-muted">
                                        {r.eds_summary?.ct_values_calculated_from_multicomponentdata ?? 0} values computed from curves; scalar result table detected: {r.eds_summary?.ct_result_table_detected ? 'yes' : 'no'}.
                                    </div>
                                    {r.eds_summary?.recommendation && <div className="mt-2 text-text-secondary">{r.eds_summary.recommendation}</div>}
                                </div>
                            )}
                            {r.assay_summary && (
                                <div className="mt-3 border-t border-border-primary pt-3 text-xs text-text-secondary">
                                    <div className="font-medium text-text-primary mb-1">Assay QC</div>
                                    <div>Standard curves: {Object.keys(r.assay_summary.standard_curves ?? {}).length}</div>
                                    <div>Quantified samples: {r.assay_summary.quantities?.length ?? 0}</div>
                                    <div>Replicate QC groups: {r.assay_summary.replicate_qc?.length ?? 0}</div>
                                    <div>NTCs: {r.assay_summary.ntc_qc?.length ?? 0}</div>
                                    <div>Spike recoveries: {r.assay_summary.spike_recovery?.length ?? 0}</div>
                                    {assayFlags.length > 0 && (
                                        <div className="mt-1 text-warning">
                                            Flags: {assayFlags.map(([flag, count]) => `${flag} (${count})`).join(', ')}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {r?.wells?.length ? (
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-2">Plate Ct Color Range</label>
                            <div className="grid grid-cols-2 gap-2">
                                <div>
                                    <label className="text-xs text-text-muted">Min</label>
                                    <input
                                        type="number"
                                        value={ctMin}
                                        onChange={(e) => setCtMin(parseInt(e.target.value))}
                                        className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs text-text-muted">Max</label>
                                    <input
                                        type="number"
                                        value={ctMax}
                                        onChange={(e) => setCtMax(parseInt(e.target.value))}
                                        className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                                    />
                                </div>
                            </div>
                        </div>
                    ) : null}
                </div>

                {/* Right - Results */}
                <div className="min-w-0">
                    {r && (
                        <div className="space-y-4">
                            <div className="border border-border-primary bg-bg-secondary p-4">
                                <div className="flex flex-wrap items-start justify-between gap-4">
                                    <div className="max-w-3xl">
                                        <div className="text-xs font-semibold uppercase tracking-[0.24em] text-accent-primary">qPCR instrument review workbench</div>
                                        <h4 className="mt-1 text-xl font-black text-text-primary">Plate, amplification, curve QC, and result rows in one review surface</h4>
                                        <p className="mt-1 text-xs leading-5 text-text-secondary">
                                            BMS keeps the parsed QuantStudio/StepOnePlus import visible as a single scientific workbench: the compact 96-well plate, selected-well QC, amplification curves, standard curve, and raw result rows stay available without mode-tab hunting. Suggested upload focus: {activeReviewFocusLabel}.
                                        </p>
                                    </div>
                                </div>
                                <div className="mt-4 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-5">
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-text-muted">Parsed rows</div>
                                        <div className="text-lg font-bold text-text-primary">{assayReviewMetrics.parsedRows}</div>
                                        <div className="text-text-muted">{assayReviewMetrics.populatedWells} populated wells</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-text-muted">Targets</div>
                                        <div className="text-lg font-bold text-text-primary">{assayReviewMetrics.targetCount}</div>
                                        <div className="text-text-muted">{assayReviewMetrics.standardRows} standard rows</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-text-muted">Replicate CV</div>
                                        <div className="text-lg font-bold text-text-primary">{assayReviewMetrics.replicateCvMeanLabel}</div>
                                        <div className="text-text-muted">max {assayReviewMetrics.replicateCvMaxLabel}; {assayReviewMetrics.replicateGroups} groups</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-text-muted">Spike recovery</div>
                                        <div className="text-lg font-bold text-text-primary">{assayReviewMetrics.spikeRecoveryMeanLabel}</div>
                                        <div className="text-text-muted">{assayReviewMetrics.spikeRecoveryRangeLabel}; {assayReviewMetrics.spikeRecoveryCount} rows</div>
                                    </div>
                                    <div className="border border-border-primary bg-bg-tertiary p-3">
                                        <div className="text-text-muted">Review flags</div>
                                        <div className="text-lg font-bold text-text-primary">{assayReviewMetrics.reviewRows}</div>
                                        <div className="text-text-muted">{assayReviewMetrics.ntcRows} NTC rows</div>
                                    </div>
                                </div>
                            </div>

                            <div className="grid gap-4 min-[1320px]:grid-cols-[minmax(320px,430px)_minmax(0,1fr)]">
                                <div className="min-w-0 space-y-4">
                                    <section className="min-w-0 border border-border-primary bg-bg-secondary p-4">
                                    <div className="flex flex-wrap items-end justify-between gap-3">
                                        <div>
                                            <div className="text-base font-semibold text-text-primary">96-well Plate Map</div>
                                            <div className="text-xs text-text-muted">
                                                Fit-to-panel 96-well map; all A-H rows and 1-12 columns stay visible with no horizontal slider. Click a well to inspect labels, target rows, Cq/Ct, standards, replicate QC, and spike/recovery analytics.
                                            </div>
                                        </div>
                                        {plateTargetOptions.length > 0 && (
                                            <label className="text-xs text-text-muted">
                                                Target view
                                                <select
                                                    value={selectedPlateTarget}
                                                    onChange={(event) => handlePlateTargetChange(event.target.value)}
                                                    className="ml-2 min-w-40 border border-border-primary bg-bg-tertiary px-2 py-1 text-sm text-text-primary"
                                                >
                                                    <option value={PLATE_TARGET_ALL}>All targets</option>
                                                    {plateTargetOptions.map((target) => (
                                                        <option key={target} value={target}>{target}</option>
                                                    ))}
                                                </select>
                                            </label>
                                        )}
                                    </div>

                                    <div
                                        data-qpcr-plate-map-fit="no-horizontal-scroll"
                                        className="mt-4 w-full overflow-hidden border border-border-primary bg-bg-tertiary/40 p-2.5"
                                    >
                                        <div
                                            data-qpcr-plate-grid="fit-panel"
                                            className="grid w-full min-w-0 items-center gap-x-1 gap-y-1.5"
                                            style={{ gridTemplateColumns: '1rem repeat(12, minmax(0, 1fr))' }}
                                        >
                                            <div />
                                            {QPCR_PLATE_COLUMNS.map((column) => (
                                                <div key={`col-${column}`} className="min-w-0 text-center text-[9px] font-semibold text-text-muted">{column}</div>
                                            ))}
                                            {QPCR_PLATE_ROWS.map((row) => (
                                                <Fragment key={row}>
                                                    <div className="text-center text-[9px] font-semibold text-text-muted">{row}</div>
                                                    {QPCR_PLATE_COLUMNS.map((column) => {
                                                        const position = `${row}${column}`;
                                                        const well = plateMapByPosition.get(position);
                                                        if (!well) return null;
                                                        const selected = selectedWellAnalytics.position === position;
                                                        return (
                                                            <button
                                                                key={position}
                                                                type="button"
                                                                aria-label={`Select well ${position}: ${well.sampleLabel}, ${well.targetLabel}, ${well.taskLabel}, Cq ${well.ctMeanLabel}`}
                                                                aria-pressed={selected}
                                                                title={`${position} | ${well.sampleLabel} | ${well.targetLabel} | ${well.taskLabel} | Cq ${well.ctMeanLabel}`}
                                                                onClick={() => setSelectedWellPosition(position)}
                                                                className={`${plateWellClassName(well, selected)} justify-self-center`}
                                                                style={plateWellStyle(well, ctMin, ctMax)}
                                                            >
                                                                <span className="max-w-full truncate text-[8px] font-black leading-none tracking-tight min-[1850px]:text-[9px]">{position}</span>
                                                                <span className="mt-0.5 max-w-full truncate text-[7px] font-semibold leading-none min-[1850px]:text-[8px]">{well.ctMeanLabel}</span>
                                                                {well.entries.length > 1 && (
                                                                    <span className="absolute -right-0.5 -top-0.5 rounded-full border border-bg-secondary bg-bg-primary px-0.5 text-[7px] font-bold leading-3 text-text-primary">
                                                                        {well.entries.length}
                                                                    </span>
                                                                )}
                                                            </button>
                                                        );
                                                    })}
                                                </Fragment>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="mt-4 flex flex-wrap gap-3 text-xs text-text-secondary">
                                        {(['sample', 'standard', 'ntc', 'mixed', 'review', 'empty'] as QpcrPlateStatus[]).map((status) => (
                                            <div key={status} className="flex items-center gap-1.5">
                                                <span className={plateLegendDotClassName(status)} />
                                                <span>{plateStatusLabel(status)}</span>
                                            </div>
                                        ))}
                                    </div>
                                    </section>

                                    <div className="border border-border-primary bg-bg-secondary p-4 text-sm">
                                        <div className="flex items-start justify-between gap-3 border-b border-border-primary pb-3">
                                            <div>
                                                <div className="text-xs uppercase tracking-wide text-text-muted">Selected well analytics</div>
                                                <div className="mt-1 text-3xl font-black text-text-primary">{selectedWellAnalytics.position}</div>
                                            </div>
                                            <span className="rounded-full border border-border-primary bg-bg-tertiary px-2 py-1 text-xs text-text-secondary">
                                                {plateStatusLabel(selectedWellAnalytics.status)}
                                            </span>
                                        </div>

                                        <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                                            <div className="border border-border-primary bg-bg-tertiary p-2">
                                                <div className="text-text-muted">Cq mean</div>
                                                <div className="text-lg font-semibold text-text-primary">{selectedWellAnalytics.ctSummary.meanLabel}</div>
                                            </div>
                                            <div className="border border-border-primary bg-bg-tertiary p-2">
                                                <div className="text-text-muted">Cq range</div>
                                                <div className="text-lg font-semibold text-text-primary">{selectedWellAnalytics.ctSummary.rangeLabel}</div>
                                            </div>
                                            <div className="border border-border-primary bg-bg-tertiary p-2">
                                                <div className="text-text-muted">Targets</div>
                                                <div className="font-semibold text-text-primary">{selectedWellAnalytics.targets.length}</div>
                                            </div>
                                            <div className="border border-border-primary bg-bg-tertiary p-2">
                                                <div className="text-text-muted">Parsed rows</div>
                                                <div className="font-semibold text-text-primary">{selectedWellAnalytics.entries.length}</div>
                                            </div>
                                        </div>

                                        <div className="mt-3 space-y-1 text-xs text-text-secondary">
                                            <div><span className="text-text-muted">Sample:</span> {selectedWellAnalytics.sampleLabel}</div>
                                            <div><span className="text-text-muted">Target:</span> {selectedWellAnalytics.targetLabel}</div>
                                            <div><span className="text-text-muted">Task:</span> {selectedWellAnalytics.taskLabel}</div>
                                            <div><span className="text-text-muted">Cq min/max:</span> {selectedWellAnalytics.ctSummary.minLabel} / {selectedWellAnalytics.ctSummary.maxLabel}</div>
                                        </div>

                                        {selectedWellAnalytics.entries.length > 0 ? (
                                            <div className="mt-4 max-h-60 overflow-auto border border-border-primary bg-bg-tertiary">
                                                <table className="w-full text-xs">
                                                    <thead className="sticky top-0 bg-bg-primary text-text-primary">
                                                        <tr>
                                                            <th className="px-2 py-1 text-left">Target</th>
                                                            <th className="px-2 py-1 text-left">Task</th>
                                                            <th className="px-2 py-1 text-left">Cq</th>
                                                            <th className="px-2 py-1 text-left">Qty</th>
                                                            <th className="px-2 py-1 text-left">Status</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {selectedWellAnalytics.entries.map((entry, index) => (
                                                            <tr key={`${selectedWellAnalytics.position}-${entry.target_name ?? 'target'}-${index}`} className="border-t border-border-primary">
                                                                <td className="px-2 py-1 text-text-primary">{entry.target_name ?? '--'}</td>
                                                                <td className="px-2 py-1 text-text-secondary">{entry.task ?? '--'}</td>
                                                                <td className="px-2 py-1 text-text-primary">{entry.ct ?? 'Undet.'}</td>
                                                                <td className="px-2 py-1 text-text-secondary">{entry.quantity !== undefined && entry.quantity !== null ? renderEntryMetric(entry.quantity, 3) : '--'}</td>
                                                                <td className="px-2 py-1 text-text-muted">{entry.ct_status ?? (entry.reporter ? `${entry.reporter}${entry.threshold ? ` @ ${renderEntryMetric(entry.threshold, 3)}` : ''}` : '--')}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ) : (
                                            <div className="mt-4 border border-border-primary bg-bg-tertiary p-3 text-xs text-text-muted">
                                                No parsed qPCR rows for this 96-well position in the current target view.
                                            </div>
                                        )}

                                        {selectedWellAnalytics.quantities.length > 0 && (
                                            <div className="mt-4 border border-border-primary bg-bg-tertiary p-3 text-xs">
                                                <div className="mb-2 font-semibold text-text-primary">Standard-curve quantity</div>
                                                {selectedWellAnalytics.quantities.map((row, index) => (
                                                    <div key={`qty-${index}`} className="mb-1 text-text-secondary">
                                                        {row.target_name ?? selectedWellAnalytics.targetLabel}: estimated {renderEntryMetric(row.estimated_quantity, 3)}; log10 {renderEntryMetric(row.log10_estimated_quantity, 3)}
                                                    </div>
                                                ))}
                                            </div>
                                        )}

                                        {selectedWellAnalytics.replicateQc.length > 0 && (
                                            <div className="mt-4 border border-border-primary bg-bg-tertiary p-3 text-xs">
                                                <div className="mb-2 font-semibold text-text-primary">Replicate QC</div>
                                                {selectedWellAnalytics.replicateQc.map((row, index) => (
                                                    <div key={`rep-${index}`} className="mb-2 text-text-secondary">
                                                        <div>{row.target_name ?? selectedWellAnalytics.targetLabel} · n={renderEntryMetric(row.n, 0)}</div>
                                                        <div>mean {renderEntryMetric(row.ct_mean, 3)} · SD {renderEntryMetric(row.ct_sd, 3)} · CV {renderEntryMetric(row.ct_cv_percent, 2)}% · range {renderEntryMetric(row.ct_range, 3)}</div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}

                                        {selectedWellAnalytics.spikeRecovery.length > 0 && (
                                            <div className="mt-4 border border-border-primary bg-bg-tertiary p-3 text-xs">
                                                <div className="mb-2 font-semibold text-text-primary">Spike / recovery</div>
                                                {selectedWellAnalytics.spikeRecovery.map((row, index) => (
                                                    <div key={`spike-${index}`} className="mb-1 text-text-secondary">
                                                        {rowContainsValue(row, ['target_name']) ? `${row.target_name}: ` : ''}{rowContainsValue(row, ['recovery_percent']) ? `${renderEntryMetric(row.recovery_percent, 2)}% recovery` : JSON.stringify(row)}
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                <section className="min-w-0 space-y-4">
                                    <div className="border border-border-primary bg-bg-secondary p-4">
                                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-primary">Always-visible amplification curves</div>
                                                <h4 className="text-base font-semibold text-text-primary">Amplification curve review</h4>
                                                <div className="mt-1 text-xs text-text-muted">The clicked well traces are brightened while other amplification traces are dimmed for rapid IPC/target curve review.</div>
                                            </div>
                                            <span className="text-xs text-text-muted">{r.amplification_plotly_json?.data?.length ?? 0} traces</span>
                                        </div>
                                        {r.amplification_plotly_json ? (
                                            <Plot
                                                data={highlightedAmplificationData}
                                                layout={{ ...plotlyLayout, ...r.amplification_plotly_json.layout, autosize: true, height: 360 }}
                                                useResizeHandler
                                                style={{ width: '100%' }}
                                            />
                                        ) : (
                                            <div className="border border-border-primary bg-bg-tertiary p-6 text-center text-xs text-text-muted">
                                                No raw amplification curve payload was present in this parsed import.
                                            </div>
                                        )}
                                    </div>

                                    <div className="border border-border-primary bg-bg-secondary p-4">
                                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-primary">Always-visible standard curve</div>
                                                <h4 className="text-base font-semibold text-text-primary">MIQE standard curve review</h4>
                                            </div>
                                            <span className="text-xs text-text-muted">{standardCurveCards.length} target curve(s)</span>
                                        </div>
                                        <div className="mb-3 border border-border-primary bg-bg-tertiary/70 p-3">
                                            <div className="text-sm font-black text-text-primary">Standard curve fit + sample quantity calls</div>
                                            <p className="mt-1 text-xs leading-5 text-text-secondary">
                                                Polished Plotly review surface for real STANDARD wells, target-specific fit lines, experimental projections, efficiency, R², residual spread, and QC flags. Selected well spot is overlaid on the active target standard curve when the clicked well has a quantity call.
                                            </p>
                                        </div>
                                        {r.standard_curve_plotly_json ? (
                                            <div className="space-y-4">
                                                <div className="min-w-0 border border-border-primary bg-bg-tertiary/40 p-3">
                                                    <Plot
                                                        data={highlightedStandardCurveData}
                                                        layout={{ ...plotlyLayout, ...r.standard_curve_plotly_json.layout, autosize: true, height: 620, legend: { ...(r.standard_curve_plotly_json.layout?.legend ?? {}), orientation: 'h' } }}
                                                        useResizeHandler
                                                        style={{ width: '100%' }}
                                                    />
                                                </div>
                                                <div className="grid grid-cols-2 gap-2 text-xs xl:grid-cols-4">
                                                    <div className="border border-border-primary bg-bg-tertiary p-2"><span className="block text-text-muted">Targets</span><span className="text-base font-semibold text-text-primary">{standardCurveCards.length}</span></div>
                                                    <div className="border border-border-primary bg-bg-tertiary p-2"><span className="block text-text-muted">Standards</span><span className="text-base font-semibold text-text-primary">{standardCurveStandardsCount || '--'}</span></div>
                                                    <div className="border border-border-primary bg-bg-tertiary p-2"><span className="block text-text-muted">Best R²</span><span className="text-base font-semibold text-text-primary">{standardCurveBestR2 !== null ? standardCurveBestR2.toFixed(4) : '--'}</span></div>
                                                    <div className="border border-border-primary bg-bg-tertiary p-2"><span className="block text-text-muted">Efficiency</span><span className="text-base font-semibold text-text-primary">{standardCurveEfficiencyValues.length ? `${Math.min(...standardCurveEfficiencyValues).toFixed(1)}–${Math.max(...standardCurveEfficiencyValues).toFixed(1)}%` : '--'}</span></div>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="border border-border-primary bg-bg-tertiary p-6 text-center text-xs text-text-muted">
                                                No STANDARD wells detected. Standard curve requires wells with Task=STANDARD and known Quantity.
                                            </div>
                                        )}
                                        {standardCurveCards.length > 0 && (
                                            <div className="mt-4 space-y-3">
                                                {standardCurveCards.map(([target, stats]) => {
                                                    const efficiency = stats.efficiency_percent ?? stats.efficiency;
                                                    const status = stats.is_valid === false ? 'Review' : 'Pass';
                                                    return (
                                                        <div key={target} className="border border-border-primary bg-bg-tertiary p-3 text-xs">
                                                            <div className="mb-3 flex items-center justify-between gap-2">
                                                                <span className="font-semibold text-text-primary">{target}</span>
                                                                <span className={stats.is_valid === false ? 'rounded-full border border-warning/60 bg-warning/10 px-2 py-0.5 text-warning' : 'rounded-full border border-success/60 bg-success/10 px-2 py-0.5 text-success'}>{status}</span>
                                                            </div>
                                                            <div className="grid grid-cols-2 gap-2">
                                                                <div className="border border-border-primary bg-bg-secondary p-2"><span className="block text-text-muted">R²</span><span className="text-base font-semibold text-text-primary">{stats.r_squared?.toFixed(4) ?? '--'}</span></div>
                                                                <div className="border border-border-primary bg-bg-secondary p-2"><span className="block text-text-muted">Efficiency</span><span className="text-base font-semibold text-text-primary">{efficiency?.toFixed(1) ?? '--'}%</span></div>
                                                                <div className="border border-border-primary bg-bg-secondary p-2"><span className="block text-text-muted">Slope</span><span className="font-semibold text-text-primary">{stats.slope?.toFixed(4) ?? '--'}</span></div>
                                                                <div className="border border-border-primary bg-bg-secondary p-2"><span className="block text-text-muted">Intercept</span><span className="font-semibold text-text-primary">{stats.intercept?.toFixed(4) ?? '--'}</span></div>
                                                                <div className="border border-border-primary bg-bg-secondary p-2"><span className="block text-text-muted">Residual SD</span><span className="font-semibold text-text-primary">{stats.residual_std?.toFixed(4) ?? '--'}</span></div>
                                                                <div className="border border-border-primary bg-bg-secondary p-2"><span className="block text-text-muted">Standard points</span><span className="font-semibold text-text-primary">{stats.n_points ?? '--'}</span></div>
                                                            </div>
                                                            {stats.flags && stats.flags.length > 0 && (
                                                                <div className="mt-3 rounded border border-warning/50 bg-warning/10 p-2 text-warning">
                                                                    {stats.flags.join('; ')}
                                                                </div>
                                                            )}
                                                        </div>
                                                    );
                                                })}
                                                <div className={`rounded border p-2 text-xs ${curveQualityFlags.length ? 'border-warning/60 bg-warning/10 text-warning' : 'border-success/60 bg-success/10 text-success'}`}>
                                                    <span className="font-semibold">Curve quality flags:</span>{' '}
                                                    {curveQualityFlags.length ? curveQualityFlags.join('; ') : 'none'}
                                                </div>
                                            </div>
                                        )}
                                    </div>


                                    <div className="border border-border-primary bg-bg-secondary p-4 text-sm">
                                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-primary">Channel / assay split</div>
                                                <h4 className="text-base font-semibold text-text-primary">Reporter, target, and passive-reference analytics</h4>
                                                <div className="mt-1 text-xs text-text-muted">Multiplex imports stay separated by target/reporter channel, including IPC channels and ROX normalization provenance.</div>
                                            </div>
                                            <span className="text-xs text-text-muted">{channelAnalytics.length} channel(s)</span>
                                        </div>
                                        {channelAnalytics.length > 0 ? (
                                            <div className="overflow-auto border border-border-primary bg-bg-tertiary">
                                                <table className="w-full text-xs">
                                                    <thead className="sticky top-0 bg-bg-primary">
                                                        <tr>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Target</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Reporter</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Passive ref.</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Role</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Rows</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Cq mean</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Threshold</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {channelAnalytics.map((row) => (
                                                            <tr key={row.key} className="border-t border-border-primary">
                                                                <td className="px-2 py-1.5 text-text-primary">{row.targetName}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{row.reporter}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{row.normalizationLabel}</td>
                                                                <td className="px-2 py-1.5 text-text-muted">{row.role}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{row.rows} rows / {row.wellCount} wells</td>
                                                                <td className="px-2 py-1.5 text-text-primary">{row.ctMeanLabel}</td>
                                                                <td className="px-2 py-1.5 text-text-muted">{row.thresholdLabel} · baseline {row.baselineLabel}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ) : (
                                            <div className="border border-border-primary bg-bg-tertiary p-4 text-xs text-text-muted">No channel metadata was present in this parsed qPCR import.</div>
                                        )}
                                    </div>

                                    <div className="border border-border-primary bg-bg-secondary p-4 text-sm">
                                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-primary">Dilution-corrected DNA amount worksheet</div>
                                                <h4 className="text-base font-semibold text-text-primary">Standard-curve quantities → original-sample amount and % detection</h4>
                                                <div className="mt-1 text-xs text-text-muted">Enter per-triplicate dilution factor and expected quantity/copies. BMS links each group to the standard-curve quantity calls and reports Original-sample amount plus % detection.</div>
                                            </div>
                                            <span className="text-xs text-text-muted">{dilutionWorksheetRows.length} group(s)</span>
                                        </div>
                                        {dilutionWorksheetRows.length > 0 ? (
                                            <div className="overflow-auto border border-border-primary bg-bg-tertiary">
                                                <table className="w-full min-w-[860px] text-xs">
                                                    <thead className="sticky top-0 bg-bg-primary">
                                                        <tr>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Sample / target</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Wells</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Mean quantity</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Dilution factor</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Expected</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Original-sample amount</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">% detection</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Triplicate CV</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {dilutionWorksheetRows.map((row) => (
                                                            <tr key={row.key} className="border-t border-border-primary align-top">
                                                                <td className="px-2 py-1.5 text-text-primary">
                                                                    <div className="font-semibold">{row.sampleName}</div>
                                                                    <div className="text-text-secondary">{row.targetName} · {row.task}</div>
                                                                </td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{row.wellPositions.join(', ') || '--'} · n={row.n}</td>
                                                                <td className="px-2 py-1.5 text-text-primary">{row.meanEstimatedQuantityLabel}</td>
                                                                <td className="px-2 py-1.5">
                                                                    <input
                                                                        type="number"
                                                                        inputMode="decimal"
                                                                        min="0"
                                                                        step="any"
                                                                        value={row.dilutionFactor}
                                                                        onChange={(event) => handleDilutionInputChange(row.key, 'dilutionFactor', event.target.value)}
                                                                        className="w-24 border border-border-primary bg-bg-secondary px-2 py-1 text-text-primary"
                                                                        aria-label={`Dilution factor for ${row.sampleName} ${row.targetName}`}
                                                                    />
                                                                </td>
                                                                <td className="px-2 py-1.5">
                                                                    <input
                                                                        type="number"
                                                                        inputMode="decimal"
                                                                        min="0"
                                                                        step="any"
                                                                        value={row.expectedQuantity}
                                                                        onChange={(event) => handleDilutionInputChange(row.key, 'expectedQuantity', event.target.value)}
                                                                        className="w-28 border border-border-primary bg-bg-secondary px-2 py-1 text-text-primary"
                                                                        aria-label={`Expected quantity for ${row.sampleName} ${row.targetName}`}
                                                                    />
                                                                </td>
                                                                <td className="px-2 py-1.5 text-text-primary">{row.correctedQuantityLabel}</td>
                                                                <td className="px-2 py-1.5 text-text-primary">{row.percentDetectionLabel}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{row.quantityCvPercentLabel}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ) : (
                                            <div className="border border-border-primary bg-bg-tertiary p-4 text-xs text-text-muted">No standard-curve quantity calls were present. Upload a Results export with STANDARD wells and calculated quantities to enable dilution/recovery analytics.</div>
                                        )}
                                    </div>

                                    <div className="border border-border-primary bg-bg-secondary p-4 text-sm">
                                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-primary">Full parsed results</div>
                                                <h4 className="text-base font-semibold text-text-primary">Imported qPCR result rows</h4>
                                                <div className="mt-1 text-xs text-text-muted">Right-side readable table for cross-checking wells without leaving the plate/curve view.</div>
                                            </div>
                                            <span className="text-xs text-text-muted">{r.wells?.length ?? 0} row(s)</span>
                                        </div>
                                        {r.wells && r.wells.length > 0 ? (
                                            <div className="max-h-96 overflow-auto border border-border-primary bg-bg-tertiary">
                                                <table className="w-full text-xs">
                                                    <thead className="sticky top-0 bg-bg-primary">
                                                        <tr>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Well</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Sample</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Target</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Task</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Cq</th>
                                                            <th className="px-2 py-1.5 text-left text-text-primary">Quantity</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {r.wells.map((well, i) => (
                                                            <tr key={`${well.well_position}-${well.target_name}-${i}`} className="border-t border-border-primary">
                                                                <td className="px-2 py-1.5 text-text-primary">{well.well_position}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{well.sample_name}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{well.target_name}</td>
                                                                <td className="px-2 py-1.5 text-text-muted">{well.task}</td>
                                                                <td className="px-2 py-1.5 text-text-primary">{well.ct ?? 'Undet.'}</td>
                                                                <td className="px-2 py-1.5 text-text-secondary">{well.quantity !== undefined && well.quantity !== null ? renderEntryMetric(well.quantity, 3) : '--'}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ) : (
                                            <div className="border border-border-primary bg-bg-tertiary p-6 text-center text-xs text-text-muted">
                                                The parser returned no qPCR result rows for this file.
                                            </div>
                                        )}
                                    </div>
                                </section>


                            </div>
                        </div>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Upload an EDS, Excel, or CSV file to view plate data
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
