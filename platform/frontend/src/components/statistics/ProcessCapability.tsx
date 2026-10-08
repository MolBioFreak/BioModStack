/**
 * Process Capability Analysis
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { runCapability } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';
import { AssayPrimaryButton } from '../assay/AssayWorkbenchPrimitives';

export function ProcessCapability() {
    const [dataText, setDataText] = useState('');
    const [usl, setUsl] = useState(25.10);
    const [lsl, setLsl] = useState(24.90);
    const [target, setTarget] = useState(25.00);
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

            if (data.length < 30) {
                setError('Need at least 30 data points');
                setLoading(false);
                return;
            }

            if (usl <= lsl) {
                setError('USL must be greater than LSL');
                setLoading(false);
                return;
            }

            const response = await runCapability(data, usl, lsl, target, subgroupSize);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Analysis failed');
        } finally {
            setLoading(false);
        }
    }, [dataText, usl, lsl, target, subgroupSize]);

    const r = result as {
        is_capable?: boolean;
        is_centered?: boolean;
        cp?: number;
        cpk?: number;
        pp?: number;
        ppk?: number;
        mean?: number;
        std_within?: number;
        std_overall?: number;
        ppm_total?: number;
        plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Process Capability Analysis</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Left - Input */}
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-xs text-text-muted mb-1">Data (one value per line)</label>
                        <textarea
                            value={dataText}
                            onChange={(e) => setDataText(e.target.value)}
                            rows={10}
                            className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                        />
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <h4 className="text-sm font-medium text-text-primary">Specification Limits</h4>

                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-xs text-text-muted mb-1">LSL</label>
                                <input
                                    type="number"
                                    step="0.01"
                                    value={lsl}
                                    onChange={(e) => setLsl(parseFloat(e.target.value))}
                                    className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                                />
                            </div>
                            <div>
                                <label className="block text-xs text-text-muted mb-1">USL</label>
                                <input
                                    type="number"
                                    step="0.01"
                                    value={usl}
                                    onChange={(e) => setUsl(parseFloat(e.target.value))}
                                    className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="block text-xs text-text-muted mb-1">Target</label>
                            <input
                                type="number"
                                step="0.01"
                                value={target}
                                onChange={(e) => setTarget(parseFloat(e.target.value))}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            />
                        </div>

                        <div>
                            <label className="block text-xs text-text-muted mb-1">Subgroup Size: {subgroupSize}</label>
                            <input
                                type="range"
                                min="1"
                                max="10"
                                value={subgroupSize}
                                onChange={(e) => setSubgroupSize(parseInt(e.target.value))}
                                className="w-full accent-accent-primary"
                            />
                        </div>
                    </div>

                    <AssayPrimaryButton onClick={handleRunAnalysis} disabled={loading} className="w-full">
                        {loading ? 'Analyzing...' : 'Analyze Capability'}
                    </AssayPrimaryButton>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                </div>

                {/* Right - Results */}
                <div className="lg:col-span-2 space-y-4">
                    {r && (
                        <>
                            <div className="border border-border-primary p-4 bg-bg-secondary">
                                <div className="flex gap-4 mb-4">
                                    <span className={`px-3 py-1 text-sm font-medium ${r.is_capable ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
                                        {r.is_capable ? 'CAPABLE' : 'NOT CAPABLE'}
                                    </span>
                                    <span className={`px-3 py-1 text-sm font-medium ${r.is_centered ? 'bg-success/20 text-success' : 'bg-warning/20 text-warning'}`}>
                                        {r.is_centered ? 'CENTERED' : 'OFF-CENTER'}
                                    </span>
                                </div>

                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                    <div>
                                        <div className="text-text-muted text-xs">Cp (Potential)</div>
                                        <div className="text-text-primary font-medium">{r.cp?.toFixed(3)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Cpk (Actual)</div>
                                        <div className="text-text-primary font-medium">{r.cpk?.toFixed(3)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Pp (Overall)</div>
                                        <div className="text-text-primary font-medium">{r.pp?.toFixed(3)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Ppk (Overall)</div>
                                        <div className="text-text-primary font-medium">{r.ppk?.toFixed(3)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Mean</div>
                                        <div className="text-text-primary font-medium">{r.mean?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Std (Within)</div>
                                        <div className="text-text-primary font-medium">{r.std_within?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">Std (Overall)</div>
                                        <div className="text-text-primary font-medium">{r.std_overall?.toFixed(4)}</div>
                                    </div>
                                    <div>
                                        <div className="text-text-muted text-xs">PPM Total</div>
                                        <div className="text-text-primary font-medium">{r.ppm_total?.toFixed(0)}</div>
                                    </div>
                                </div>
                            </div>

                            {r.plotly_json && (
                                <div className="border border-border-primary bg-bg-secondary">
                                    <Plot
                                        data={r.plotly_json.data}
                                        layout={{
                                            ...plotlyLayout,
                                            ...r.plotly_json.layout,
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
                            Enter data and specification limits to analyze process capability
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
