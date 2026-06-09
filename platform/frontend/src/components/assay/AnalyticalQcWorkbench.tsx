import { useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';

import { useThemePlotlyLayout } from '../useThemeColors';
import {
    analyzeAssayQcTable,
    detectAssayQcColumns,
    exportCleanedAssayRowsCsv,
    parseDelimitedTable,
    type AssayQcColumns,
    type AssayQcResult,
    type AssayQcStatRow,
    type SanitizedAssayRow,
} from './analyticalQc';
import {
    AssayEmptyState,
    AssayErrorNotice,
    AssayFieldLabel,
    AssayInputCard,
    AssayOutputCard,
    AssayPrimaryButton,
} from './AssayWorkbenchPrimitives';

type ColumnKey = keyof AssayQcColumns;

function formatNumber(value: number | null | undefined, digits = 2): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
    return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function formatPercent(value: number | null | undefined, digits = 1): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
    return `${value.toFixed(digits)}%`;
}

function StatTable({ rows, title }: { rows: AssayQcStatRow[]; title: string }) {
    if (!rows.length) return null;
    return (
        <div className="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
            <table className="w-full text-xs">
                <thead className="bg-[var(--bg-tertiary)] text-[var(--text-primary)]">
                    <tr>
                        <th className="px-3 py-2 text-left">{title}</th>
                        <th className="px-3 py-2 text-left">n</th>
                        <th className="px-3 py-2 text-left">Mean</th>
                        <th className="px-3 py-2 text-left">Median</th>
                        <th className="px-3 py-2 text-left">SD</th>
                        <th className="px-3 py-2 text-left">CV%</th>
                        <th className="px-3 py-2 text-left">Min</th>
                        <th className="px-3 py-2 text-left">Max</th>
                        <th className="px-3 py-2 text-left">QC flags</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={row.key} className="border-t border-[var(--border-primary)] text-[var(--text-secondary)]">
                            <td className="px-3 py-2 font-medium text-[var(--text-primary)]">{row.key.replace('||', ' / ')}</td>
                            <td className="px-3 py-2">{row.n}</td>
                            <td className="px-3 py-2">{formatNumber(row.mean)}</td>
                            <td className="px-3 py-2">{formatNumber(row.median)}</td>
                            <td className="px-3 py-2">{formatNumber(row.sd)}</td>
                            <td className="px-3 py-2">{formatPercent(row.cvPercent)}</td>
                            <td className="px-3 py-2">{formatNumber(row.min)}</td>
                            <td className="px-3 py-2">{formatNumber(row.max)}</td>
                            <td className="px-3 py-2 text-[var(--warning)]">{row.warningFlags.join('; ') || '--'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function CleanedRowsTable({ rows }: { rows: SanitizedAssayRow[] }) {
    if (!rows.length) return null;
    return (
        <div className="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
            <table className="w-full text-xs">
                <thead className="bg-[var(--bg-tertiary)] text-[var(--text-primary)]">
                    <tr>
                        <th className="px-3 py-2 text-left">Use</th>
                        <th className="px-3 py-2 text-left">Row</th>
                        <th className="px-3 py-2 text-left">Sample</th>
                        <th className="px-3 py-2 text-left">Run</th>
                        <th className="px-3 py-2 text-left">Group / bunch</th>
                        <th className="px-3 py-2 text-left">Raw value</th>
                        <th className="px-3 py-2 text-left">Clean value</th>
                        <th className="px-3 py-2 text-left">Flags</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.slice(0, 150).map((row) => (
                        <tr key={`${row.rowNumber}-${row.sampleId}`} className="border-t border-[var(--border-primary)] text-[var(--text-secondary)]">
                            <td className="px-3 py-2 text-[var(--text-primary)]">{row.included ? 'yes' : 'no'}</td>
                            <td className="px-3 py-2">{row.rowNumber}</td>
                            <td className="px-3 py-2 text-[var(--text-primary)]">{row.sampleId}</td>
                            <td className="px-3 py-2">{row.runId}</td>
                            <td className="px-3 py-2">{row.groupId}</td>
                            <td className="px-3 py-2 font-mono">{row.rawValue}</td>
                            <td className="px-3 py-2 font-mono">{formatNumber(row.value)}</td>
                            <td className="px-3 py-2 text-[var(--warning)]">{row.exclusionReason ?? (row.flags.join('; ') || '--')}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            {rows.length > 150 ? <p className="p-2 text-xs text-[var(--text-muted)]">Showing first 150 cleaned rows; CSV export includes all rows.</p> : null}
        </div>
    );
}

function ColumnSelect({
    label,
    value,
    headers,
    required = false,
    onChange,
}: {
    label: string;
    value: string;
    headers: string[];
    required?: boolean;
    onChange: (value: string) => void;
}) {
    return (
        <div>
            <AssayFieldLabel label={label} helper={required ? 'Required' : 'Optional'} />
            <select
                value={value}
                onChange={(event) => onChange(event.target.value)}
                className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"
            >
                <option value="">-- none --</option>
                {headers.map((header) => (
                    <option key={header} value={header}>{header}</option>
                ))}
            </select>
        </div>
    );
}

function makePlotPayload(result: AssayQcResult): Plotly.Data[] {
    const groupBars: Plotly.Data = {
        type: 'bar',
        name: 'Group mean',
        x: result.groupStats.map((row) => row.key),
        y: result.groupStats.map((row) => row.mean),
        error_y: {
            type: 'data',
            array: result.groupStats.map((row) => row.sd ?? 0),
            visible: true,
        },
        marker: { color: '#8b5cf6' },
    };
    const runMeans: Plotly.Data = {
        type: 'scatter',
        mode: 'markers',
        name: 'Run means',
        x: result.groupRunStats.map((row) => row.groupId ?? row.key),
        y: result.groupRunStats.map((row) => row.mean),
        text: result.groupRunStats.map((row) => `${row.groupId ?? ''} / ${row.runId ?? ''} n=${row.n}`),
        marker: { color: '#22d3ee', size: 10, symbol: 'diamond' },
    };
    return [groupBars, runMeans];
}

function downloadText(filename: string, text: string): void {
    const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

export function AnalyticalQcWorkbench() {
    const [rawText, setRawText] = useState('');
    const [columns, setColumns] = useState<AssayQcColumns>({ value: '' });
    const [groupRulesText, setGroupRulesText] = useState('');
    const [excludeTermsText, setExcludeTermsText] = useState('');
    const [zScoreThreshold, setZScoreThreshold] = useState(3);
    const [cvWarningThreshold, setCvWarningThreshold] = useState(10);
    const [result, setResult] = useState<AssayQcResult | null>(null);
    const [error, setError] = useState('');

    const parsed = useMemo(() => parseDelimitedTable(rawText), [rawText]);
    const detectedColumns = useMemo(() => detectAssayQcColumns(parsed.headers), [parsed.headers]);
    const plotlyLayout = useThemePlotlyLayout();

    useEffect(() => {
        setColumns((current) => ({
            value: current.value || detectedColumns.value,
            group: current.group || detectedColumns.group,
            run: current.run || detectedColumns.run,
            sample: current.sample || detectedColumns.sample,
            replicate: current.replicate || detectedColumns.replicate,
            include: current.include || detectedColumns.include,
        }));
    }, [detectedColumns]);

    const updateColumn = (key: ColumnKey, value: string) => {
        setColumns((current) => ({ ...current, [key]: value }));
        setResult(null);
    };

    const handleRunQc = () => {
        setError('');
        if (!parsed.headers.length || parsed.rows.length === 0) {
            setError('Paste a real CSV/TSV table with a header row before running QC.');
            return;
        }
        const activeColumns = { ...columns, value: columns.value || detectedColumns.value };
        if (!activeColumns.value) {
            setError('Select the numeric response/value column to sanitize and summarize.');
            return;
        }
        setColumns(activeColumns);
        setResult(analyzeAssayQcTable(parsed, {
            columns: activeColumns,
            groupRulesText,
            excludeTermsText,
            zScoreThreshold,
            cvWarningThreshold,
        }));
    };

    const plotData = result ? makePlotPayload(result) : [];

    return (
        <div className="space-y-5">
            <div>
                <h3 className="text-lg font-semibold text-[var(--text-primary)]">Manual Analytical Data QC</h3>
                <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    Paste rows → map columns → clean/exclude/bunch → QC stats.
                </p>
            </div>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
                <div className="space-y-4">
                    <AssayInputCard
                        title="Source table"
                        description="CSV/TSV with headers from instruments or spreadsheets."
                    >
                        <textarea
                            value={rawText}
                            onChange={(event) => {
                                setRawText(event.target.value);
                                setResult(null);
                            }}
                            rows={11}
                            placeholder="sample,run,group,area,include\nA01,run-1,5% spike,105432,yes"
                            className="w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 font-mono text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
                        />
                        <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--text-muted)]">
                            <span>{parsed.rows.length} data rows detected</span>
                            <span>•</span>
                            <span>{parsed.headers.length} columns</span>
                            <span>•</span>
                            <span>delimiter: {parsed.delimiter === '\t' ? 'tab' : parsed.delimiter}</span>
                        </div>
                    </AssayInputCard>

                    <AssayInputCard
                        title="Column mapping"
                        description="Auto-detected headers; editable mappings."
                    >
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                            <ColumnSelect label="Numeric value" required value={columns.value || detectedColumns.value} headers={parsed.headers} onChange={(value) => updateColumn('value', value)} />
                            <ColumnSelect label="Run / batch" value={columns.run || detectedColumns.run || ''} headers={parsed.headers} onChange={(value) => updateColumn('run', value)} />
                            <ColumnSelect label="Group / condition" value={columns.group || detectedColumns.group || ''} headers={parsed.headers} onChange={(value) => updateColumn('group', value)} />
                            <ColumnSelect label="Sample ID" value={columns.sample || detectedColumns.sample || ''} headers={parsed.headers} onChange={(value) => updateColumn('sample', value)} />
                            <ColumnSelect label="Replicate / injection" value={columns.replicate || detectedColumns.replicate || ''} headers={parsed.headers} onChange={(value) => updateColumn('replicate', value)} />
                            <ColumnSelect label="Include/exclude flag" value={columns.include || detectedColumns.include || ''} headers={parsed.headers} onChange={(value) => updateColumn('include', value)} />
                        </div>
                    </AssayInputCard>

                    <AssayInputCard
                        title="Manual cleaning and bunching"
                        description="Text rules for row excludes and label bunching."
                    >
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div>
                                <AssayFieldLabel
                                    label="Exclude rows containing"
                                    helper="One token per line or comma-separated; matched against sample/run/group/replicate labels."
                                />
                                <textarea
                                    value={excludeTermsText}
                                    onChange={(event) => {
                                        setExcludeTermsText(event.target.value);
                                        setResult(null);
                                    }}
                                    rows={5}
                                    placeholder="bad injection\nblank\nrerun"
                                    className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 font-mono text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
                                />
                            </div>
                            <div>
                                <AssayFieldLabel
                                    label="Group/bunch rules"
                                    helper="Examples: 5% spike = spike5, 5% / LIN = LIN-01, LIN-03 or LIN-01, LIN-03 -> LIN."
                                />
                                <textarea
                                    value={groupRulesText}
                                    onChange={(event) => {
                                        setGroupRulesText(event.target.value);
                                        setResult(null);
                                    }}
                                    rows={5}
                                    placeholder="5% spike = 5% spike, spike5\nLIN = LIN-01, LIN-03"
                                    className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 font-mono text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
                                />
                            </div>
                        </div>
                        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                            <div>
                                <AssayFieldLabel label="Outlier z-score flag" helper="Rows are flagged, not silently removed." />
                                <input
                                    type="number"
                                    min="0.5"
                                    step="0.5"
                                    value={zScoreThreshold}
                                    onChange={(event) => setZScoreThreshold(Number(event.target.value))}
                                    className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                                />
                            </div>
                            <div>
                                <AssayFieldLabel label="CV warning threshold %" helper="Flags group/run/cross-run precision risks." />
                                <input
                                    type="number"
                                    min="0"
                                    step="1"
                                    value={cvWarningThreshold}
                                    onChange={(event) => setCvWarningThreshold(Number(event.target.value))}
                                    className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                                />
                            </div>
                        </div>
                    </AssayInputCard>

                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
                        <button
                            type="button"
                            onClick={() => {
                                setRawText('');
                                setColumns({ value: '' });
                                setGroupRulesText('');
                                setExcludeTermsText('');
                                setResult(null);
                                setError('');
                            }}
                            className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        >
                            Clear QC setup
                        </button>
                        <AssayPrimaryButton onClick={handleRunQc}>Run Manual QC</AssayPrimaryButton>
                    </div>
                    {error ? <AssayErrorNotice message={error} /> : null}
                </div>

                <div className="space-y-4">
                    <AssayOutputCard
                        title="QC Summary"
                        description="Included/excluded rows, groups, runs."
                    >
                        {!result ? (
                            <AssayEmptyState
                                title="No manual QC run yet"
                                description="Paste a real table, map the numeric value column, optionally add cleaning/bunching rules, then run QC."
                            />
                        ) : (
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">Included</div>
                                        <div className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{result.summary.includedRows}</div>
                                    </div>
                                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">Excluded</div>
                                        <div className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{result.summary.excludedRows}</div>
                                    </div>
                                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">Groups</div>
                                        <div className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{result.summary.groupCount}</div>
                                    </div>
                                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3">
                                        <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">Runs</div>
                                        <div className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{result.summary.runCount}</div>
                                    </div>
                                </div>
                                {result.warnings.length ? (
                                    <div className="rounded-lg border border-[var(--warning)] bg-[color-mix(in_srgb,var(--warning)_12%,transparent)] p-3 text-xs text-[var(--warning)]">
                                        {result.warnings.map((warning) => <div key={warning}>{warning}</div>)}
                                    </div>
                                ) : null}
                                {plotData.length ? (
                                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)]">
                                        <Plot
                                            data={plotData}
                                            layout={{
                                                ...plotlyLayout,
                                                title: { text: 'Grouped assay QC means and run means' },
                                                autosize: true,
                                                height: 340,
                                                xaxis: { title: { text: 'Group / bunch' } },
                                                yaxis: { title: { text: columns.value || detectedColumns.value || 'Value' } },
                                                margin: { l: 52, r: 18, t: 52, b: 72 },
                                            }}
                                            useResizeHandler
                                            style={{ width: '100%' }}
                                        />
                                    </div>
                                ) : null}
                            </div>
                        )}
                    </AssayOutputCard>

                    {result ? (
                        <>
                            <AssayOutputCard title="Cross-run statistics" description="Between-run drift and precision.">
                                {result.crossRunStats.length ? (
                                    <div className="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
                                        <table className="w-full text-xs">
                                            <thead className="bg-[var(--bg-tertiary)] text-[var(--text-primary)]">
                                                <tr>
                                                    <th className="px-3 py-2 text-left">Group / bunch</th>
                                                    <th className="px-3 py-2 text-left">Runs</th>
                                                    <th className="px-3 py-2 text-left">Total n</th>
                                                    <th className="px-3 py-2 text-left">Mean of run means</th>
                                                    <th className="px-3 py-2 text-left">Between-run SD</th>
                                                    <th className="px-3 py-2 text-left">Between-run CV%</th>
                                                    <th className="px-3 py-2 text-left">Run means</th>
                                                    <th className="px-3 py-2 text-left">Flags</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {result.crossRunStats.map((row) => (
                                                    <tr key={row.groupId} className="border-t border-[var(--border-primary)] text-[var(--text-secondary)]">
                                                        <td className="px-3 py-2 font-medium text-[var(--text-primary)]">{row.groupId}</td>
                                                        <td className="px-3 py-2">{row.nRuns}</td>
                                                        <td className="px-3 py-2">{row.totalN}</td>
                                                        <td className="px-3 py-2">{formatNumber(row.meanOfRunMeans)}</td>
                                                        <td className="px-3 py-2">{formatNumber(row.betweenRunSd)}</td>
                                                        <td className="px-3 py-2">{formatPercent(row.betweenRunCvPercent)}</td>
                                                        <td className="px-3 py-2">{row.runMeans.map((run) => `${run.runId}: ${formatNumber(run.mean)} (n=${run.n})`).join('; ')}</td>
                                                        <td className="px-3 py-2 text-[var(--warning)]">{row.warningFlags.join('; ') || '--'}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                ) : (
                                    <AssayEmptyState title="No cross-run rollup" description="Map run/batch + value columns first." />
                                )}
                            </AssayOutputCard>

                            <AssayOutputCard title="Grouped and bunched summaries" description="Cleaned group/run stats.">
                                <div className="space-y-4">
                                    <StatTable rows={result.groupStats} title="Group" />
                                    <StatTable rows={result.groupRunStats} title="Group / run" />
                                </div>
                            </AssayOutputCard>

                            <AssayOutputCard title="Cleaned row ledger" description="Inspectable included/excluded rows.">
                                <div className="mb-3 flex justify-end">
                                    <button
                                        type="button"
                                        onClick={() => downloadText('bms_assay_cleaned_rows.csv', exportCleanedAssayRowsCsv(result.rows))}
                                        className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                                    >
                                        Export cleaned CSV
                                    </button>
                                </div>
                                <CleanedRowsTable rows={result.rows} />
                            </AssayOutputCard>
                        </>
                    ) : null}
                </div>
            </div>
        </div>
    );
}
