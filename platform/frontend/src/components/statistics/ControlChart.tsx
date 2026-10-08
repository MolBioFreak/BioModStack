/**
 * Control Chart (SPC) - Statistical Process Control
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runControlChart, seedDatasets, getDatasets, getDataset } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';

interface Dataset {
    id: number;
    name: string;
    data_points: number;
    is_builtin?: boolean;
}

export function ControlChart() {
    const [dataText, setDataText] = useState('');
    const [subgroupSize, setSubgroupSize] = useState(1);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [datasets, setDatasets] = useState<Dataset[]>([]);
    const [seedStatus, setSeedStatus] = useState('');

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

    const handleSeedDatasets = async () => {
        setSeedStatus('Seeding...');
        try {
            const response = await seedDatasets();
            setSeedStatus(response.message || 'Done');
            // Refresh dataset list
            const ds = await getDatasets('spc');
            setDatasets(ds);
        } catch {
            setSeedStatus('Error seeding');
        }
    };

    const handleLoadDataset = async (id: number) => {
        if (!id) return;
        try {
            const ds = await getDataset(id);
            setDataText(ds.data.join('\n'));
        } catch {
            setError('Failed to load dataset');
        }
    };

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Control Chart Analysis</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Left - Input */}
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-sm font-medium text-text-primary mb-2">Data Source</label>

                        <div className="flex gap-2 mb-3">
                            <button onClick={handleSeedDatasets} className="text-xs px-2 py-1 bg-bg-tertiary hover:bg-card-hover border border-border-primary text-text-primary">
                                Seed Datasets
                            </button>
                            {seedStatus && <span className="text-xs text-text-muted">{seedStatus}</span>}
                        </div>

                        {datasets.length > 0 && (
                            <select
                                onChange={(e) => handleLoadDataset(parseInt(e.target.value))}
                                className="w-full mb-3 bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            >
                                <option value="">-- Select Dataset --</option>
                                {datasets.map(ds => (
                                    <option key={ds.id} value={ds.id}>
                                        {ds.is_builtin ? '[Built-in] ' : ''}{ds.name} ({ds.data_points} pts)
                                    </option>
                                ))}
                            </select>
                        )}

                        <label className="block text-xs text-text-muted mb-1">Data (one value per line)</label>
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

                    <button
                        onClick={handleRunAnalysis}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Analyzing...' : 'Create Control Chart'}
                    </button>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                {/* Right - Results */}
                <div className="lg:col-span-2 space-y-4">
                    {result && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary">
                                <h4 className="text-sm font-medium text-text-primary mb-2">Analysis Results</h4>
                                <pre className="text-xs text-text-secondary whitespace-pre-wrap">
                                    {`[${(result as { chart_type?: string }).chart_type || 'Control'} Chart]

Center Line: ${((result as { center_line?: number }).center_line as number)?.toFixed(4) || 'N/A'}
UCL: ${((result as { ucl?: number }).ucl as number)?.toFixed(4) || 'N/A'}
LCL: ${((result as { lcl?: number }).lcl as number)?.toFixed(4) || 'N/A'}

Violations: ${(result as { violation_count?: number }).violation_count || 0}`}
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
