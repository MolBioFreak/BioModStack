/**
 * Regression Analysis Panel
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runRegression } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';
import {
    AssayPrimaryButton,
    AssaySegmentedTabs,
    type AssaySegmentedTabItem,
} from '../assay/AssayWorkbenchPrimitives';

type RegressionPlotTab = 'scatter' | 'diagnostics';

const regressionPlotTabs: Array<AssaySegmentedTabItem<RegressionPlotTab>> = [
    { id: 'scatter', label: 'Scatter Plot' },
    { id: 'diagnostics', label: 'Diagnostics' },
];

export function RegressionAnalysis() {
    const [xText, setXText] = useState('');
    const [yText, setYText] = useState('');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<RegressionPlotTab>('scatter');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRunAnalysis = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const x = xText.trim().split('\n').filter(l => l.trim()).map(l => parseFloat(l.trim()));
            const y = yText.trim().split('\n').filter(l => l.trim()).map(l => parseFloat(l.trim()));

            if (x.length !== y.length) {
                setError('X and Y must have same length');
                setLoading(false);
                return;
            }

            if (x.length < 3) {
                setError('Need at least 3 data points');
                setLoading(false);
                return;
            }

            const response = await runRegression(x, y);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [xText, yText]);

    const r = result as {
        coefficients?: { intercept?: number; X?: number };
        r_squared?: number;
        adj_r_squared?: number;
        f_statistic?: number;
        f_pvalue?: number;
        n_obs?: number;
        p_values?: { intercept?: number; X?: number };
        scatter_plot?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
        diagnostics_plot?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Regression Analysis</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">X (Predictor)</label>
                        <textarea
                            value={xText}
                            onChange={(e) => setXText(e.target.value)}
                            rows={8}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Y (Response)</label>
                        <textarea
                            value={yText}
                            onChange={(e) => setYText(e.target.value)}
                            rows={8}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <AssayPrimaryButton onClick={handleRunAnalysis} disabled={loading} className="w-full">
                        {loading ? 'Fitting...' : 'Fit Model'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    {r && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary">
                                <h4 className="font-medium text-text-primary mb-3">Regression Results</h4>

                                <div className="bg-bg-tertiary p-3 mb-4 font-mono text-sm text-text-primary">
                                    Y = {r.coefficients?.intercept?.toFixed(4)} + {r.coefficients?.X?.toFixed(4)} × X
                                </div>

                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                    <div>
                                        <div className="text-text-muted text-xs">R²</div>
                                        <div className="text-text-primary font-medium">{r.r_squared?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Adj R²</div>
                                        <div className="text-text-primary font-medium">{r.adj_r_squared?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">F-statistic</div>
                                        <div className="text-text-primary font-medium">{r.f_statistic?.toFixed(2)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">F p-value</div>
                                        <div className="text-text-primary font-medium">{r.f_pvalue?.toExponential(2)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Observations</div>
                                        <div className="text-text-primary font-medium">{r.n_obs}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Intercept p</div>
                                        <div className="text-text-primary font-medium">{r.p_values?.intercept?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Slope p</div>
                                        <div className="text-text-primary font-medium">{r.p_values?.X?.toExponential(2)}</div>
                                    </div>
                                </div>
                            </div>

                            <div className="border border-border-primary bg-bg-secondary">
                                <AssaySegmentedTabs
                                    items={regressionPlotTabs}
                                    activeId={activeTab}
                                    onChange={setActiveTab}
                                    ariaLabel="Regression plot views"
                                />

                                {activeTab === 'scatter' && r.scatter_plot && (
                                    <Plot
                                        data={r.scatter_plot.data}
                                        layout={{ ...plotlyLayout, ...r.scatter_plot.layout, autosize: true, height: 350 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                )}

                                {activeTab === 'diagnostics' && r.diagnostics_plot && (
                                    <Plot
                                        data={r.diagnostics_plot.data}
                                        layout={{ ...plotlyLayout, ...r.diagnostics_plot.layout, autosize: true, height: 350 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                )}
                            </div>
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter X and Y data to fit a linear regression model
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
