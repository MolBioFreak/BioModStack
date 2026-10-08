/**
 * Hypothesis Testing Panel
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runHypothesisTest } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';
import { AssayPrimaryButton } from '../assay/AssayWorkbenchPrimitives';

export function HypothesisTesting() {
    const [testType, setTestType] = useState('two_sample');
    const [group1Text, setGroup1Text] = useState('');
    const [group2Text, setGroup2Text] = useState('');
    const [alpha, setAlpha] = useState(0.05);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const handleRunTest = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const group1 = group1Text.trim().split('\n').filter(l => l.trim()).map(l => parseFloat(l.trim()));

            let group2: number[] | number;
            if (testType === 'one_sample') {
                group2 = parseFloat(group2Text.trim().split('\n')[0]);
            } else {
                group2 = group2Text.trim().split('\n').filter(l => l.trim()).map(l => parseFloat(l.trim()));
            }

            const response = await runHypothesisTest(testType, group1, group2, alpha);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Test failed');
        } finally {
            setLoading(false);
        }
    }, [testType, group1Text, group2Text, alpha]);

    const r = result as {
        test_type?: string;
        statistic?: number;
        p_value?: number;
        alpha?: number;
        degrees_of_freedom?: number;
        reject_null?: boolean;
        effect_size?: number;
        summary?: string;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Hypothesis Testing</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Test Type</label>
                        <select
                            value={testType}
                            onChange={(e) => setTestType(e.target.value)}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                        >
                            <option value="two_sample">Two-Sample t-Test</option>
                            <option value="one_sample">One-Sample t-Test</option>
                            <option value="paired">Paired t-Test</option>
                            <option value="anova">One-Way ANOVA</option>
                        </select>
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">
                            {testType === 'one_sample' ? 'Sample Data' : 'Group 1 / Before'}
                        </label>
                        <textarea
                            value={group1Text}
                            onChange={(e) => setGroup1Text(e.target.value)}
                            rows={6}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">
                            {testType === 'one_sample' ? 'Population Mean' : 'Group 2 / After'}
                        </label>
                        <textarea
                            value={group2Text}
                            onChange={(e) => setGroup2Text(e.target.value)}
                            rows={6}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">α (Significance Level): {alpha}</label>
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

                    <AssayPrimaryButton onClick={handleRunTest} disabled={loading} className="w-full">
                        {loading ? 'Running...' : 'Run Test'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    {r && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary">
                                <div className={`inline-block px-3 py-1 text-sm font-medium mb-4 ${r.reject_null ? 'bg-error/20 text-error' : 'bg-success/20 text-success'}`}>
                                    {r.reject_null ? 'REJECT H₀' : 'FAIL TO REJECT H₀'}
                                </div>

                                <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
                                    <div>
                                        <div className="text-text-muted text-xs">Test Type</div>
                                        <div className="text-text-primary font-medium">{r.test_type}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Statistic</div>
                                        <div className="text-text-primary font-medium">{r.statistic?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">p-value</div>
                                        <div className="text-text-primary font-medium">{r.p_value?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">α</div>
                                        <div className="text-text-primary font-medium">{r.alpha}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">df</div>
                                        <div className="text-text-primary font-medium">{r.degrees_of_freedom?.toFixed(1)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Effect Size</div>
                                        <div className="text-text-primary font-medium">{r.effect_size?.toFixed(4) || 'N/A'}</div>
                                    </div>
                                </div>

                                {r.summary && (
                                    <div className="mt-4 p-3 bg-bg-tertiary text-text-secondary text-sm">{r.summary}</div>
                                )}
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
                            Select a test type and enter data to perform hypothesis testing
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
