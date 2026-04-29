/**
 * HPLC Chromatogram Analysis
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runHplcAnalysis, runHplcCalibration } from '../../api/client';
import { API_URL } from '../../api/config';
import { useThemePlotlyLayout } from '../useThemeColors';
import {
    AssayEmptyState,
    AssayErrorNotice,
    AssayFieldLabel,
    AssayInputCard,
    AssayOutputCard,
    AssayPanel,
    AssayPrimaryButton,
} from '../assay/AssayWorkbenchPrimitives';

interface Peak {
    peak_id: number;
    retention_time: number;
    height: number;
    area: number;
    width: number;
    r_squared?: number;
}

export function ChromatogramAnalysis() {
    const [dataText, setDataText] = useState('');
    const [baselineMethod, setBaselineMethod] = useState('mocca2_flatfit');
    const [prominence, setProminence] = useState(100);
    const [fitModel, setFitModel] = useState('skew_normal');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRunAnalysis = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const lines = dataText.trim().split('\n');
            const time: number[] = [];
            const signal: number[] = [];

            for (const line of lines) {
                const parts = line.split(',');
                if (parts.length >= 2) {
                    const t = parseFloat(parts[0]);
                    const s = parseFloat(parts[1]);
                    if (!isNaN(t) && !isNaN(s)) {
                        time.push(t);
                        signal.push(s);
                    }
                }
            }

            if (time.length < 10) {
                setError('Need at least 10 data points');
                setLoading(false);
                return;
            }

            const response = await runHplcAnalysis(time, signal, baselineMethod, prominence, fitModel);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [dataText, baselineMethod, prominence, fitModel]);

    const r = result as {
        summary?: string;
        peaks?: Peak[];
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Chromatogram Analysis</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Data (time,signal per line)</label>
                        <textarea
                            value={dataText}
                            onChange={(e) => setDataText(e.target.value)}
                            rows={10}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <h4 className="text-sm font-medium text-text-primary">Analysis Parameters</h4>

                        <div>
                            <label className="block text-xs text-text-muted mb-1">Baseline Correction</label>
                            <select
                                value={baselineMethod}
                                onChange={(e) => setBaselineMethod(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            >
                                <option value="mocca2_flatfit">MOCCA2 flatfit (recommended)</option>
                                <option value="mocca2_arpls">MOCCA2 arPLS</option>
                                <option value="mocca2_asls">MOCCA2 asLS</option>
                                <option value="linear">Linear</option>
                                <option value="none">None</option>
                            </select>
                        </div>

                        <div>
                            <label className="block text-xs text-text-muted mb-1">Peak Prominence: {prominence}</label>
                            <input
                                type="range"
                                min="10"
                                max="500"
                                step="10"
                                value={prominence}
                                onChange={(e) => setProminence(parseInt(e.target.value))}
                                className="w-full accent-accent-primary"
                            />
                        </div>

                        <div>
                            <label className="block text-xs text-text-muted mb-1">Peak Fitting Model</label>
                            <select
                                value={fitModel}
                                onChange={(e) => setFitModel(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            >
                                <option value="skew_normal">Skew-Normal (asymmetric)</option>
                                <option value="gaussian">Gaussian (symmetric)</option>
                                <option value="emg">EMG (tailing)</option>
                            </select>
                        </div>
                    </div>

                    <AssayPrimaryButton onClick={handleRunAnalysis} disabled={loading} className="w-full">
                        {loading ? 'Analyzing...' : 'Analyze Chromatogram'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    {r && (
                        <>
                            {r.summary && (
                                <div className="border border-border-primary p-4 bg-bg-secondary text-sm whitespace-pre-wrap">
                                    {r.summary}
                                </div>
                            )}

                            {r.plotly_json && (
                                <div className="border border-border-primary bg-bg-secondary">
                                    <Plot
                                        data={r.plotly_json.data}
                                        layout={{ ...plotlyLayout, ...r.plotly_json.layout, autosize: true, height: 400 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}

                            {r.peaks && r.peaks.length > 0 && (
                                <div className="border border-border-primary bg-bg-secondary p-4">
                                    <h4 className="text-sm font-medium text-text-primary mb-3">Peak Table</h4>
                                    <div className="overflow-auto max-h-48">
                                        <table className="w-full text-sm">
                                            <thead className="bg-bg-tertiary">
                                                <tr>
                                                    <th className="px-3 py-2 text-left text-text-primary">Peak</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">RT</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Height</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Area</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Width</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">R²</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {r.peaks.map((peak) => (
                                                    <tr key={peak.peak_id} className="border-t border-border-primary">
                                                        <td className="px-3 py-2 text-text-primary">{peak.peak_id}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{peak.retention_time?.toFixed(3)}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{peak.height?.toFixed(0)}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{peak.area?.toFixed(0)}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{peak.width?.toFixed(3)}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{peak.r_squared?.toFixed(4)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            )}
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter chromatogram data and run analysis
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function CalibrationCurve() {
    const [concText, setConcText] = useState('');
    const [areaText, setAreaText] = useState('');
    const [analyteName, setAnalyteName] = useState('');
    const [unit, setUnit] = useState('µg/mL');
    const [forceOrigin, setForceOrigin] = useState(false);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const concs = concText.trim().split('\n').filter(l => l.trim()).map(l => parseFloat(l.trim()));
            const areas = areaText.trim().split('\n').filter(l => l.trim()).map(l => parseFloat(l.trim()));

            if (concs.length < 3) {
                setError('Need at least 3 calibration points');
                setLoading(false);
                return;
            }

            if (concs.length !== areas.length) {
                setError('Concentrations and areas must have same count');
                setLoading(false);
                return;
            }

            const points = concs.map((c, i) => ({ concentration: c, area: areas[i] }));
            const response = await runHplcCalibration(points, analyteName, unit, forceOrigin);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Calibration failed');
        } finally {
            setLoading(false);
        }
    }, [concText, areaText, analyteName, unit, forceOrigin]);

    const r = result as {
        summary?: string;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Calibration Curve</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Concentrations (one per line)</label>
                        <textarea
                            value={concText}
                            onChange={(e) => setConcText(e.target.value)}
                            rows={5}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Peak Areas (one per line)</label>
                        <textarea
                            value={areaText}
                            onChange={(e) => setAreaText(e.target.value)}
                            rows={5}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Analyte Name</label>
                            <input
                                type="text"
                                value={analyteName}
                                onChange={(e) => setAnalyteName(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Unit</label>
                            <input
                                type="text"
                                value={unit}
                                onChange={(e) => setUnit(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                        <div className="flex items-center">
                            <input
                                type="checkbox"
                                id="forceOrigin"
                                checked={forceOrigin}
                                onChange={(e) => setForceOrigin(e.target.checked)}
                                className="mr-2 accent-accent-primary"
                            />
                            <label htmlFor="forceOrigin" className="text-sm text-text-primary">Force through origin</label>
                        </div>
                    </div>

                    <AssayPrimaryButton onClick={handleRun} disabled={loading} className="w-full">
                        {loading ? 'Fitting...' : 'Fit Calibration Curve'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    {r && (
                        <>
                            {r.summary && (
                                <div className="border border-border-primary p-4 bg-bg-secondary text-sm whitespace-pre-wrap">
                                    {r.summary}
                                </div>
                            )}
                            {r.plotly_json && (
                                <div className="border border-border-primary bg-bg-secondary">
                                    <Plot
                                        data={r.plotly_json.data}
                                        layout={{ ...plotlyLayout, ...r.plotly_json.layout, autosize: true, height: 400 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter calibration data to fit a standard curve
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function HplcQuantification() {
    const [calConcText, setCalConcText] = useState('');
    const [calAreaText, setCalAreaText] = useState('');
    const [sampleAreaText, setSampleAreaText] = useState('');
    const [sampleIdsText, setSampleIdsText] = useState('');
    const [unit, setUnit] = useState('µg/mL');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const response = await fetch(`${API_URL}/analysis/hplc/quantify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    cal_concentrations: calConcText.trim().split('\n').map(v => parseFloat(v.trim())),
                    cal_areas: calAreaText.trim().split('\n').map(v => parseFloat(v.trim())),
                    sample_areas: sampleAreaText.trim().split('\n').map(v => parseFloat(v.trim())),
                    sample_ids: sampleIdsText.trim().split('\n').map(v => v.trim()),
                    unit,
                }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            setResult(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Quantification failed');
        } finally {
            setLoading(false);
        }
    }, [calConcText, calAreaText, sampleAreaText, sampleIdsText, unit]);

    const r = result as {
        samples?: { id: string; area: number; concentration: number }[];
        curve_stats?: { slope: number; intercept: number; r_squared: number };
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-lg font-semibold text-[var(--text-primary)]">Sample Quantification</h3>
                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    Quantify real unknown peak areas against explicit calibration standards; every sample row must carry a real identifier.
                </p>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] gap-6">
                <AssayPanel className="p-4">
                    <div className="space-y-5">
                        <AssayInputCard
                            title="Calibration standards"
                            description="Paste matched concentration and peak-area values, one value per line. Keep row order aligned across both fields."
                        >
                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                <div>
                                    <AssayFieldLabel
                                        label="Calibration concentrations"
                                        helper="One standard concentration per line."
                                    />
                                    <textarea
                                        value={calConcText}
                                        onChange={(e) => setCalConcText(e.target.value)}
                                        rows={6}
                                        className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 font-mono text-xs text-[var(--text-primary)]"
                                    />
                                </div>
                                <div>
                                    <AssayFieldLabel
                                        label="Calibration peak areas"
                                        helper="One integrated area per matching standard."
                                    />
                                    <textarea
                                        value={calAreaText}
                                        onChange={(e) => setCalAreaText(e.target.value)}
                                        rows={6}
                                        className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 font-mono text-xs text-[var(--text-primary)]"
                                    />
                                </div>
                            </div>
                        </AssayInputCard>

                        <AssayInputCard
                            title="Unknown samples"
                            description="Paste sample peak areas and real sample identifiers in the same row order."
                        >
                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                <div>
                                    <AssayFieldLabel
                                        label="Sample peak areas"
                                        helper="One unknown peak area per line."
                                    />
                                    <textarea
                                        value={sampleAreaText}
                                        onChange={(e) => setSampleAreaText(e.target.value)}
                                        rows={6}
                                        className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 font-mono text-xs text-[var(--text-primary)]"
                                    />
                                </div>
                                <div>
                                    <AssayFieldLabel
                                        label="Sample IDs"
                                        helper="One real sample identifier per line."
                                    />
                                    <textarea
                                        value={sampleIdsText}
                                        onChange={(e) => setSampleIdsText(e.target.value)}
                                        rows={6}
                                        className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 font-mono text-xs text-[var(--text-primary)]"
                                    />
                                </div>
                            </div>
                        </AssayInputCard>

                        <AssayInputCard title="Output unit">
                            <div className="max-w-xs">
                                <AssayFieldLabel label="Unit" helper="Displayed with quantified concentrations." />
                                <input
                                    value={unit}
                                    onChange={(e) => setUnit(e.target.value)}
                                    className="mt-2 w-full rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                                />
                            </div>
                        </AssayInputCard>

                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
                            <AssayPrimaryButton onClick={handleRun} disabled={loading} className="w-full sm:w-auto">
                                {loading ? 'Quantifying...' : 'Quantify Samples'}
                            </AssayPrimaryButton>
                        </div>

                        {error && <AssayErrorNotice message={error} />}
                    </div>
                </AssayPanel>

                <AssayOutputCard
                    title="Quantification Results"
                    description="Calibration fit metrics and quantified unknowns appear here after a successful run."
                    className="min-h-[360px]"
                >
                    {r && (
                        <div className="space-y-4">
                            {r.plotly_json && (
                                <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)]">
                                    <Plot
                                        data={r.plotly_json.data}
                                        layout={{ ...plotlyLayout, ...r.plotly_json.layout, autosize: true, height: 320 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}

                            {r.curve_stats && (
                                <div>
                                    <h4 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Calibration Stats</h4>
                                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                                        <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-sm">
                                            <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-muted)]">Slope</div>
                                            <div className="mt-1 font-semibold text-[var(--text-primary)]">{r.curve_stats.slope.toFixed(2)}</div>
                                        </div>
                                        <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-sm">
                                            <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-muted)]">Intercept</div>
                                            <div className="mt-1 font-semibold text-[var(--text-primary)]">{r.curve_stats.intercept.toFixed(2)}</div>
                                        </div>
                                        <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-sm">
                                            <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-muted)]">R²</div>
                                            <div className="mt-1 font-semibold text-[var(--text-primary)]">{r.curve_stats.r_squared.toFixed(4)}</div>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {r.samples && (
                                <div>
                                    <h4 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Quantified Samples</h4>
                                    <div className="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
                                        <table className="w-full text-sm">
                                            <thead className="bg-[var(--bg-tertiary)] text-[var(--text-primary)]">
                                                <tr>
                                                    <th className="px-3 py-2 text-left">Sample</th>
                                                    <th className="px-3 py-2 text-left">Area</th>
                                                    <th className="px-3 py-2 text-left">Conc. ({unit})</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {r.samples.map((s, i) => (
                                                    <tr key={i} className="border-t border-[var(--border-primary)]">
                                                        <td className="px-3 py-2 text-[var(--text-primary)]">{s.id}</td>
                                                        <td className="px-3 py-2 text-[var(--text-secondary)]">{s.area.toFixed(0)}</td>
                                                        <td className="px-3 py-2 text-[var(--text-secondary)]">{s.concentration.toFixed(2)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {!result && !loading && (
                        <AssayEmptyState
                            title="No quantification run yet"
                            description="Enter matched calibration concentrations/areas and sample IDs to quantify real samples."
                        />
                    )}

                    {loading && (
                        <AssayEmptyState
                            title="Quantifying samples"
                            description="BMS is fitting the calibration series and applying it to the unknown peak areas."
                        />
                    )}
                </AssayOutputCard>
            </div>
        </div>
    );
}
