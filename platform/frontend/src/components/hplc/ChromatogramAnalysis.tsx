/**
 * HPLC Chromatogram Analysis
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runHplcAnalysis, runHplcCalibration } from '../../api/client';
import { API_URL } from '../../api/config';
import { useThemePlotlyLayout } from '../useThemeColors';

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
    const [baselineMethod, setBaselineMethod] = useState('snip');
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
                                <option value="snip">SNIP (recommended)</option>
                                <option value="als">ALS</option>
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

                    <button
                        onClick={handleRunAnalysis}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Analyzing...' : 'Analyze Chromatogram'}
                    </button>

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

                    <button
                        onClick={handleRun}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Fitting...' : 'Fit Calibration Curve'}
                    </button>

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
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Sample Quantification</h3>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Calibration Conc.</label>
                            <textarea
                                value={calConcText}
                                onChange={(e) => setCalConcText(e.target.value)}
                                rows={5}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Calibration Areas</label>
                            <textarea
                                value={calAreaText}
                                onChange={(e) => setCalAreaText(e.target.value)}
                                rows={5}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Sample Areas</label>
                            <textarea
                                value={sampleAreaText}
                                onChange={(e) => setSampleAreaText(e.target.value)}
                                rows={5}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Sample IDs</label>
                            <textarea
                                value={sampleIdsText}
                                onChange={(e) => setSampleIdsText(e.target.value)}
                                rows={5}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Unit</label>
                        <input
                            value={unit}
                            onChange={(e) => setUnit(e.target.value)}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                        />
                    </div>

                    <button
                        onClick={handleRun}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Quantifying...' : 'Quantify Samples'}
                    </button>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="space-y-4">
                    {r && (
                        <>
                            {r.curve_stats && (
                                <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                                    <h4 className="font-medium text-text-primary mb-2">Calibration Stats</h4>
                                    <div className="grid grid-cols-3 gap-2">
                                        <div><span className="text-text-muted">Slope:</span> {r.curve_stats.slope.toFixed(2)}</div>
                                        <div><span className="text-text-muted">Intercept:</span> {r.curve_stats.intercept.toFixed(2)}</div>
                                        <div><span className="text-text-muted">R²:</span> {r.curve_stats.r_squared.toFixed(4)}</div>
                                    </div>
                                </div>
                            )}

                            {r.samples && (
                                <div className="border border-border-primary p-4 bg-bg-secondary">
                                    <h4 className="text-sm font-medium text-text-primary mb-2">Results</h4>
                                    <table className="w-full text-sm">
                                        <thead className="bg-bg-tertiary">
                                            <tr>
                                                <th className="px-3 py-2 text-left">Sample</th>
                                                <th className="px-3 py-2 text-left">Area</th>
                                                <th className="px-3 py-2 text-left">Conc. ({unit})</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {r.samples.map((s, i) => (
                                                <tr key={i} className="border-t border-border-primary">
                                                    <td className="px-3 py-2 text-text-primary">{s.id}</td>
                                                    <td className="px-3 py-2 text-text-secondary">{s.area.toFixed(0)}</td>
                                                    <td className="px-3 py-2 text-text-secondary">{s.concentration.toFixed(2)}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter calibration and sample data to quantify
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
