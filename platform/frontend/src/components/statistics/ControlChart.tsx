/**
 * Control Chart (SPC) - Statistical Process Control
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runControlChart } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';
import { AssayPrimaryButton } from '../assay/AssayWorkbenchPrimitives';

export function ControlChart() {
    const [dataText, setDataText] = useState('');
    const [subgroupSize, setSubgroupSize] = useState(1);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const plotlyLayout = useThemePlotlyLayout();

    const handleRunAnalysis = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const data = dataText
                .trim()
                .split('\n')
                .filter(line => line.trim())
                .map(line => parseFloat(line.trim()));

            if (data.length < 10) {
                setError('Need at least 10 data points');
                setLoading(false);
                return;
            }

            const response = await runControlChart(data, subgroupSize);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [dataText, subgroupSize]);

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Control Chart Analysis</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Left - Input */}
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Paste real process data (one value per line)</label>
                        <textarea
                            value={dataText}
                            onChange={(e) => setDataText(e.target.value)}
                            rows={12}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">
                            Subgroup Size: {subgroupSize} {subgroupSize === 1 ? '(I-MR Chart)' : '(X̄-R Chart)'}
                        </label>
                        <input
                            type="range"
                            min="1"
                            max="10"
                            value={subgroupSize}
                            onChange={(e) => setSubgroupSize(parseInt(e.target.value))}
                            className="w-full accent-accent-primary"
                        />
                    </div>

                    <AssayPrimaryButton onClick={handleRunAnalysis} disabled={loading} className="w-full">
                        {loading ? 'Analyzing...' : 'Create Control Chart'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                {/* Right - Results */}
                <div className="lg:col-span-2 space-y-4">
                    {result && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary">
                                <h4 className="text-sm font-medium text-text-primary mb-2">Analysis Results</h4>
                                <pre className="text-xs text-text-secondary whitespace-pre-wrap">
                                    {`[${(result as { chart_type?: string }).chart_type ?? 'Control'} Chart]

Center Line: ${((result as { center_line?: number }).center_line as number)?.toFixed(4) ?? 'N/A'}
UCL: ${((result as { ucl?: number }).ucl as number)?.toFixed(4) ?? 'N/A'}
LCL: ${((result as { lcl?: number }).lcl as number)?.toFixed(4) ?? 'N/A'}

Violations: ${(result as { violation_count?: number }).violation_count ?? 0}`}
                                </pre>
                            </div>

                            {(result as { plotly_json?: object }).plotly_json && (
                                <div className="border border-border-primary bg-bg-secondary">
                                    <Plot
                                        data={(result as { plotly_json: { data: Plotly.Data[] } }).plotly_json.data}
                                        layout={{
                                            ...plotlyLayout,
                                            ...(result as { plotly_json: { layout: Partial<Plotly.Layout> } }).plotly_json.layout,
                                            autosize: true,
                                            height: 400,
                                        }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}
                        </>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Enter data and click "Create Control Chart" to analyze
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
