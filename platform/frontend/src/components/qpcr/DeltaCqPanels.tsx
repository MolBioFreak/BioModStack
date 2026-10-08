/**
 * qPCR ΔCq and ΔΔCq Analysis Panels
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runDeltaCq, runDeltaDeltaCq } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';

export function DeltaCqPanel() {
    const [dataText, setDataText] = useState('');
    const [refGenes, setRefGenes] = useState('');
    const [targetGenes, setTargetGenes] = useState('');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const parseData = (text: string) => {
        const lines = text.trim().split('\n');
        const headers = lines[0].split(',').map(h => h.trim().toLowerCase());

        return lines.slice(1).map(line => {
            const values = line.split(',').map(v => v.trim());
            return {
                sample: values[headers.indexOf('sample')] || '',
                gene: values[headers.indexOf('gene')] || '',
                cq: parseFloat(values[headers.indexOf('cq')]) || 0,
                group: values[headers.indexOf('group')] || '',
            };
        });
    };

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const data = parseData(dataText);
            const refs = refGenes.split(',').map(g => g.trim()).filter(Boolean);
            const targets = targetGenes.split(',').map(g => g.trim()).filter(Boolean);

            const response = await runDeltaCq(data, refs, targets);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [dataText, refGenes, targetGenes]);

    const r = result as {
        summary?: string;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">ΔCq Analysis</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Data (CSV: Sample,Gene,Cq,Group)</label>
                        <textarea
                            value={dataText}
                            onChange={(e) => setDataText(e.target.value)}
                            rows={10}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Reference Genes (comma-separated)</label>
                            <input
                                type="text"
                                value={refGenes}
                                onChange={(e) => setRefGenes(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Target Genes (comma-separated)</label>
                            <input
                                type="text"
                                value={targetGenes}
                                onChange={(e) => setTargetGenes(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                    </div>

                    <button
                        onClick={handleRun}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Analyzing...' : 'Calculate ΔCq'}
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
                            Enter qPCR data and run ΔCq analysis
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function DeltaDeltaCqPanel() {
    const [dataText, setDataText] = useState('');
    const [refGenes, setRefGenes] = useState('');
    const [targetGenes, setTargetGenes] = useState('');
    const [controlGroup, setControlGroup] = useState('');
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const plotlyLayout = useThemePlotlyLayout();

    const parseData = (text: string) => {
        const lines = text.trim().split('\n');
        const headers = lines[0].split(',').map(h => h.trim().toLowerCase());

        return lines.slice(1).map(line => {
            const values = line.split(',').map(v => v.trim());
            return {
                sample: values[headers.indexOf('sample')] || '',
                gene: values[headers.indexOf('gene')] || '',
                cq: parseFloat(values[headers.indexOf('cq')]) || 0,
                group: values[headers.indexOf('group')] || '',
            };
        });
    };

    const handleRun = useCallback(async () => {
        setLoading(true);
        setError('');
        setResult(null);

        try {
            const data = parseData(dataText);
            const refs = refGenes.split(',').map(g => g.trim()).filter(Boolean);
            const targets = targetGenes.split(',').map(g => g.trim()).filter(Boolean);

            const response = await runDeltaDeltaCq(data, refs, targets, controlGroup);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [dataText, refGenes, targetGenes, controlGroup]);

    const r = result as {
        summary?: string;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">ΔΔCq & Fold Change</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Data (CSV: Sample,Gene,Cq,Group)</label>
                        <textarea
                            value={dataText}
                            onChange={(e) => setDataText(e.target.value)}
                            rows={8}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Reference Genes</label>
                            <input
                                type="text"
                                value={refGenes}
                                onChange={(e) => setRefGenes(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Target Genes</label>
                            <input
                                type="text"
                                value={targetGenes}
                                onChange={(e) => setTargetGenes(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Control Group Name</label>
                            <input
                                type="text"
                                value={controlGroup}
                                onChange={(e) => setControlGroup(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>
                    </div>

                    <button
                        onClick={handleRun}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Analyzing...' : 'Calculate ΔΔCq'}
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
                            Enter qPCR data and run ΔΔCq fold-change analysis
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
