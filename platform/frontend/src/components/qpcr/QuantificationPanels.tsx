/**
 * qPCR Standard Curve and Absolute Quantification Panels
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { useThemePlotlyLayout } from '../useThemeColors';
import { API_URL } from '../../api/config';
import { AssayPrimaryButton } from '../assay/AssayWorkbenchPrimitives';

export function StandardCurvePanel() {
    const [concText, setConcText] = useState('');
    const [cqText, setCqText] = useState('');
    const [gene, setGene] = useState('');
    const [unit, setUnit] = useState('copies/uL');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const response = await fetch(`${API_URL}/analysis/qpcr/standard-curve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    concentrations: concText.trim().split('\n').map(v => parseFloat(v.trim())),
                    cq_values: cqText.trim().split('\n').map(v => parseFloat(v.trim())),
                    gene,
                    unit,
                }),
                signal: AbortSignal.timeout(30000),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            setResult(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [concText, cqText, gene, unit]);

    const r = result as {
        slope?: number;
        intercept?: number;
        r_squared?: number;
        efficiency?: number;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Standard Curve</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Concentrations (one per line)</label>
                        <textarea
                            value={concText}
                            onChange={(e) => setConcText(e.target.value)}
                            rows={6}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Cq Values (one per line)</label>
                        <textarea
                            value={cqText}
                            onChange={(e) => setCqText(e.target.value)}
                            rows={6}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Gene Name</label>
                            <input
                                value={gene}
                                onChange={(e) => setGene(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Unit</label>
                            <input
                                value={unit}
                                onChange={(e) => setUnit(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                    </div>

                    <AssayPrimaryButton onClick={handleRun} disabled={loading} className="w-full">
                        {loading ? 'Fitting...' : 'Fit Standard Curve'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    {r && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary">
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                    <div>
                                        <div className="text-text-muted text-xs">Slope</div>
                                        <div className="text-text-primary font-medium">{r.slope?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Intercept</div>
                                        <div className="text-text-primary font-medium">{r.intercept?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">R²</div>
                                        <div className="text-text-primary font-medium">{r.r_squared?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Efficiency</div>
                                        <div className="text-text-primary font-medium">{r.efficiency?.toFixed(1)}%</div>
                                    </div>
                                </div>
                            </div>

                            {r.plotly_json && (
                                <div className="border border-border-primary bg-bg-secondary">
                                    <Plot
                                        data={r.plotly_json.data}
                                        layout={{ ...plotlyLayout, ...r.plotly_json.layout, autosize: true, height: 350 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter concentration and Cq values to fit standard curve
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function QuantificationPanel() {
    const [stdConcText, setStdConcText] = useState('');
    const [stdCqText, setStdCqText] = useState('');
    const [sampleCqText, setSampleCqText] = useState('');
    const [sampleIdsText, setSampleIdsText] = useState('');
    const [unit, setUnit] = useState('copies/uL');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const response = await fetch(`${API_URL}/analysis/qpcr/quantify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    std_concentrations: stdConcText.trim().split('\n').map(v => parseFloat(v.trim())),
                    std_cq_values: stdCqText.trim().split('\n').map(v => parseFloat(v.trim())),
                    sample_cq_values: sampleCqText.trim().split('\n').map(v => parseFloat(v.trim())),
                    sample_ids: sampleIdsText.trim().split('\n').map(v => v.trim()),
                    unit,
                }),
                signal: AbortSignal.timeout(30000),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            setResult(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Quantification failed');
        } finally {
            setLoading(false);
        }
    }, [stdConcText, stdCqText, sampleCqText, sampleIdsText, unit]);

    const r = result as {
        standard_curve?: { slope: number; intercept: number; r_squared: number; efficiency_percent: number };
        quantities?: {
            sample_id: string;
            cq_value: number;
            quantity: number;
            quantity_formatted?: string;
            unit?: string;
            within_range?: boolean;
            extrapolated?: boolean;
        }[];
        summary?: string;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Absolute Quantification</h3>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Std Concentrations</label>
                            <textarea
                                value={stdConcText}
                                onChange={(e) => setStdConcText(e.target.value)}
                                rows={5}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Std Cq Values</label>
                            <textarea
                                value={stdCqText}
                                onChange={(e) => setStdCqText(e.target.value)}
                                rows={5}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Sample Cq Values</label>
                            <textarea
                                value={sampleCqText}
                                onChange={(e) => setSampleCqText(e.target.value)}
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

                    <AssayPrimaryButton onClick={handleRun} disabled={loading} className="w-full">
                        {loading ? 'Quantifying...' : 'Quantify Samples'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="space-y-4">
                    {r && (
                        <>
                            {r.standard_curve && (
                                <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                                    <h4 className="font-medium text-text-primary mb-2">Curve Statistics</h4>
                                    <div className="grid grid-cols-2 gap-2">
                                        <div><span className="text-text-muted">Slope:</span> {r.standard_curve.slope.toFixed(4)}</div>
                                        <div><span className="text-text-muted">Intercept:</span> {r.standard_curve.intercept.toFixed(4)}</div>
                                        <div><span className="text-text-muted">R²:</span> {r.standard_curve.r_squared.toFixed(4)}</div>
                                        <div><span className="text-text-muted">Efficiency:</span> {r.standard_curve.efficiency_percent.toFixed(1)}%</div>
                                    </div>
                                </div>
                            )}

                            {r.summary && (
                                <div className="border border-border-primary p-4 bg-bg-secondary text-xs text-text-secondary whitespace-pre-wrap">
                                    {r.summary}
                                </div>
                            )}

                            {r.quantities && (
                                <div className="border border-border-primary p-4 bg-bg-secondary">
                                    <h4 className="text-sm font-medium text-text-primary mb-2">Results</h4>
                                    <table className="w-full text-sm">
                                        <thead className="bg-bg-tertiary">
                                            <tr>
                                                <th className="px-3 py-2 text-left">Sample</th>
                                                <th className="px-3 py-2 text-left">Cq</th>
                                                <th className="px-3 py-2 text-left">Concentration ({unit})</th>
                                                <th className="px-3 py-2 text-left">QC</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {r.quantities.map((s, i) => (
                                                <tr key={i} className="border-t border-border-primary">
                                                    <td className="px-3 py-2 text-text-primary">{s.sample_id}</td>
                                                    <td className="px-3 py-2 text-text-secondary">{s.cq_value.toFixed(2)}</td>
                                                    <td className="px-3 py-2 text-text-secondary">{s.quantity_formatted || s.quantity.toExponential(2)}</td>
                                                    <td className={`px-3 py-2 ${s.within_range ? 'text-success' : 'text-warning'}`}>
                                                        {s.extrapolated ? 'Extrapolated' : 'In range'}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {r.plotly_json && (
                                <div className="border border-border-primary bg-bg-secondary">
                                    <Plot
                                        data={r.plotly_json.data}
                                        layout={{ ...plotlyLayout, ...r.plotly_json.layout, autosize: true, height: 350 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter standard curve and sample data to quantify
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function AnovaDunnettPanel() {
    const [groupsJson, setGroupsJson] = useState('');
    const [groupNames, setGroupNames] = useState('');
    const [controlGroup, setControlGroup] = useState('');
    const [alpha, setAlpha] = useState(0.05);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const groups = JSON.parse(groupsJson);
            const names = groupNames.split(',').map(n => n.trim()).filter(Boolean);
            const selectedControl = controlGroup.trim();
            if (!selectedControl) {
                throw new Error('Control group name is required');
            }
            const response = await fetch(`${API_URL}/analysis/qpcr/anova-dunnett`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    groups,
                    group_names: names,
                    control_group: selectedControl,
                    alpha,
                }),
                signal: AbortSignal.timeout(30000),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            setResult(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [groupsJson, groupNames, controlGroup, alpha]);

    const r = result as {
        anova_f?: number;
        anova_p?: number;
        summary?: string;
        comparisons?: { group: string; difference: number; diff?: number; p_value: number; significant: boolean }[];
        dunnett_results?: { group: string; difference?: number; diff?: number; p_value: number; significant: boolean }[];
    } | null;
    const comparisons = r?.comparisons ?? r?.dunnett_results;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">ANOVA + Dunnett Post-Hoc</h3>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Groups (JSON array of arrays)</label>
                        <textarea
                            value={groupsJson}
                            onChange={(e) => setGroupsJson(e.target.value)}
                            rows={4}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Group Names (comma-separated; one per group)</label>
                        <input
                            value={groupNames}
                            onChange={(e) => setGroupNames(e.target.value)}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Control Group Name</label>
                        <input
                            value={controlGroup}
                            onChange={(e) => setControlGroup(e.target.value)}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Alpha: {alpha}</label>
                        <input
                            type="range"
                            min="0.01"
                            max="0.10"
                            step="0.01"
                            value={alpha}
                            onChange={(e) => setAlpha(parseFloat(e.target.value))}
                            className="w-full accent-accent-primary"
                        />
                    </div>

                    <AssayPrimaryButton onClick={handleRun} disabled={loading} className="w-full">
                        {loading ? 'Running...' : 'Run ANOVA + Dunnett'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="space-y-4">
                    {r && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                                <div className="grid grid-cols-2 gap-4 mb-3">
                                    <div><span className="text-text-muted">F-statistic:</span> <span className="text-text-primary font-medium">{r.anova_f?.toFixed(4)}</span></div>
                                    <div><span className="text-text-muted">p-value:</span> <span className="text-text-primary font-medium">{r.anova_p?.toExponential(2)}</span></div>
                                </div>
                                {r.summary && <div className="whitespace-pre-wrap text-text-secondary">{r.summary}</div>}
                            </div>

                            {comparisons && (
                                <div className="border border-border-primary p-4 bg-bg-secondary">
                                    <h4 className="text-sm font-medium text-text-primary mb-2">Dunnett Comparisons (vs Control)</h4>
                                    <table className="w-full text-sm">
                                        <thead className="bg-bg-tertiary">
                                            <tr>
                                                <th className="px-3 py-2 text-left">Group</th>
                                                <th className="px-3 py-2 text-left">Diff</th>
                                                <th className="px-3 py-2 text-left">p-value</th>
                                                <th className="px-3 py-2 text-left">Sig</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {comparisons.map((d, i) => (
                                                <tr key={i} className="border-t border-border-primary">
                                                    <td className="px-3 py-2 text-text-primary">{d.group}</td>
                                                    <td className="px-3 py-2 text-text-secondary">{(d.difference ?? d.diff ?? 0).toFixed(4)}</td>
                                                    <td className="px-3 py-2 text-text-secondary">{d.p_value.toFixed(4)}</td>
                                                    <td className={`px-3 py-2 ${d.significant ? 'text-success' : 'text-text-muted'}`}>
                                                        {d.significant ? 'Yes' : 'No'}
                                                    </td>
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
                            Enter group data to run ANOVA with Dunnett post-hoc
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
