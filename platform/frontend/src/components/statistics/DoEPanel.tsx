/**
 * Design of Experiments (DoE) Panel with RSM Analysis
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { generateDoeDesign, analyzeRsm } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';

export function DoEPanel() {
    const [activeTab, setActiveTab] = useState<'design' | 'rsm'>('design');

    // Design Generation State
    const [designType, setDesignType] = useState('full_factorial');
    const [nFactors, setNFactors] = useState(2);
    const [centerPoints, setCenterPoints] = useState(3);
    const [designResult, setDesignResult] = useState<Record<string, unknown> | null>(null);
    const [designLoading, setDesignLoading] = useState(false);
    const [designError, setDesignError] = useState('');

    // RSM Analysis State
    const [rsmDesign, setRsmDesign] = useState('');
    const [rsmResponse, setRsmResponse] = useState('');
    const [rsmResult, setRsmResult] = useState<Record<string, unknown> | null>(null);
    const [rsmLoading, setRsmLoading] = useState(false);
    const [rsmError, setRsmError] = useState('');
    const [rsmPlotTab, setRsmPlotTab] = useState<'contour' | 'surface'>('contour');

    const plotlyLayout = useThemePlotlyLayout();

    const handleGenerateDesign = useCallback(async () => {
        setDesignLoading(true);
        setDesignError('');
        setDesignResult(null);

        try {
            const response = await generateDoeDesign(designType, nFactors, centerPoints);
            setDesignResult(response);

            // Auto-populate RSM design text if available
            if (response.design_matrix) {
                const matrix = response.design_matrix as Record<string, number>[];
                const cols = Object.keys(matrix[0] || {});
                const header = cols.join(',');
                const rows = matrix.map(row => cols.map(c => row[c]).join(','));
                setRsmDesign([header, ...rows].join('\n'));
            }
        } catch (err) {
            setDesignError(err instanceof Error ? err.message : 'Design generation failed');
        } finally {
            setDesignLoading(false);
        }
    }, [designType, nFactors, centerPoints]);

    const handleAnalyzeRsm = useCallback(async () => {
        setRsmLoading(true);
        setRsmError('');
        setRsmResult(null);

        try {
            // Parse design matrix from text
            const lines = rsmDesign.trim().split('\n');
            const header = lines[0].split(',').map(h => h.trim());
            const matrix = lines.slice(1).map(line => {
                const values = line.split(',').map(v => parseFloat(v.trim()));
                const row: Record<string, number> = {};
                header.forEach((h, i) => row[h] = values[i]);
                return row;
            });

            // Parse response values
            const responseValues = rsmResponse.trim().split('\n').map(v => parseFloat(v.trim()));

            if (matrix.length !== responseValues.length) {
                setRsmError(`Design rows (${matrix.length}) must match response values (${responseValues.length})`);
                setRsmLoading(false);
                return;
            }

            const response = await analyzeRsm(matrix, responseValues);
            setRsmResult(response);
        } catch (err) {
            setRsmError(err instanceof Error ? err.message : 'RSM analysis failed');
        } finally {
            setRsmLoading(false);
        }
    }, [rsmDesign, rsmResponse]);

    const dr = designResult as {
        summary?: string;
        design_matrix?: Record<string, number>[];
        n_runs?: number;
        n_factors?: number;
    } | null;

    const rr = rsmResult as {
        summary?: string;
        coefficients?: Record<string, number>;
        r_squared?: number;
        adj_r_squared?: number;
        optimal_point?: Record<string, number>;
        predicted_optimum?: number;
        contour_plot?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
        surface_plot?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
    } | null;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Design of Experiments</h3>

            {/* Tab Selector */}
            <div className="flex gap-2 border-b border-border-primary">
                <button
                    onClick={() => setActiveTab('design')}
                    className={`px-4 py-2 text-sm font-medium ${activeTab === 'design' ? 'border-b-2 border-accent-primary text-accent-primary' : 'text-text-secondary'}`}
                >
                    Generate Design
                </button>
                <button
                    onClick={() => setActiveTab('rsm')}
                    className={`px-4 py-2 text-sm font-medium ${activeTab === 'rsm' ? 'border-b-2 border-accent-primary text-accent-primary' : 'text-text-secondary'}`}
                >
                    RSM Analysis
                </button>
            </div>

            {/* Design Generation Tab */}
            {activeTab === 'design' && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div className="space-y-4">
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Design Type</label>
                            <select
                                value={designType}
                                onChange={(e) => setDesignType(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            >
                                <option value="full_factorial">Full Factorial (2^k)</option>
                                <option value="fractional_factorial">Fractional Factorial</option>
                                <option value="plackett_burman">Plackett-Burman</option>
                                <option value="central_composite">Central Composite (CCD)</option>
                                <option value="box_behnken">Box-Behnken</option>
                            </select>
                        </div>

                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Number of Factors: {nFactors}</label>
                            <input
                                type="range"
                                min="2"
                                max="6"
                                value={nFactors}
                                onChange={(e) => setNFactors(parseInt(e.target.value))}
                                className="w-full accent-accent-primary"
                            />
                        </div>

                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Center Points: {centerPoints}</label>
                            <input
                                type="range"
                                min="0"
                                max="6"
                                value={centerPoints}
                                onChange={(e) => setCenterPoints(parseInt(e.target.value))}
                                className="w-full accent-accent-primary"
                            />
                        </div>

                        <button
                            onClick={handleGenerateDesign}
                            disabled={designLoading}
                            className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                        >
                            {designLoading ? 'Generating...' : 'Generate Design'}
                        </button>

                        {designError && <div className="p-3 bg-error/20 border border-error text-error text-sm">{designError}</div>}
                    </div>

                    <div className="lg:col-span-2 space-y-4">
                        {dr && (
                            <>
                                <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                                    <div className="mb-2"><strong>Runs:</strong> {dr.n_runs} | <strong>Factors:</strong> {dr.n_factors}</div>
                                    {dr.summary && <div className="whitespace-pre-wrap text-text-secondary">{dr.summary}</div>}
                                </div>

                                {dr.design_matrix && (
                                    <div className="border border-border-primary bg-bg-secondary p-4 max-h-80 overflow-auto">
                                        <h4 className="text-sm font-medium text-text-primary mb-2">Design Matrix</h4>
                                        <table className="w-full text-sm">
                                            <thead className="bg-bg-tertiary">
                                                <tr>
                                                    <th className="px-2 py-1 text-left">Run</th>
                                                    {Object.keys(dr.design_matrix[0] || {}).map(col => (
                                                        <th key={col} className="px-2 py-1 text-left">{col}</th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {dr.design_matrix.map((row, i) => (
                                                    <tr key={i} className="border-t border-border-primary">
                                                        <td className="px-2 py-1 text-text-primary">{i + 1}</td>
                                                        {Object.values(row).map((val, j) => (
                                                            <td key={j} className="px-2 py-1 text-text-secondary">{val}</td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </>
                        )}

                        {!designResult && !designLoading && (
                            <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                                Select design parameters and click Generate
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* RSM Analysis Tab */}
            {activeTab === 'rsm' && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div className="space-y-4">
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Design Matrix (CSV with header)</label>
                            <textarea
                                value={rsmDesign}
                                onChange={(e) => setRsmDesign(e.target.value)}
                                rows={8}
                                placeholder="Paste a real DOE design matrix CSV with factor columns"
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-xs"
                            />
                        </div>

                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-1">Response Values (one per line)</label>
                            <textarea
                                value={rsmResponse}
                                onChange={(e) => setRsmResponse(e.target.value)}
                                rows={6}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary p-2 font-mono text-sm"
                            />
                        </div>

                        <button
                            onClick={handleAnalyzeRsm}
                            disabled={rsmLoading || !rsmDesign}
                            className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                        >
                            {rsmLoading ? 'Analyzing...' : 'Analyze RSM'}
                        </button>

                        {rsmError && <div className="p-3 bg-error/20 border border-error text-error text-sm">{rsmError}</div>}
                    </div>

                    <div className="lg:col-span-2 space-y-4">
                        {rr && (
                            <>
                                <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                                    <div className="grid grid-cols-2 gap-4 mb-3">
                                        <div><span className="text-text-muted">R²:</span> <span className="text-text-primary font-medium">{rr.r_squared?.toFixed(4)}</span></div>
                                        <div><span className="text-text-muted">Adj R²:</span> <span className="text-text-primary font-medium">{rr.adj_r_squared?.toFixed(4)}</span></div>
                                    </div>
                                    {rr.optimal_point && (
                                        <div className="mb-2">
                                            <span className="text-text-muted">Optimal Point:</span>
                                            <span className="ml-2 text-text-primary">
                                                {Object.entries(rr.optimal_point).map(([k, v]) => `${k}=${(v as number).toFixed(3)}`).join(', ')}
                                            </span>
                                        </div>
                                    )}
                                    {rr.predicted_optimum !== undefined && (
                                        <div><span className="text-text-muted">Predicted Optimum:</span> <span className="text-text-primary font-medium">{rr.predicted_optimum.toFixed(4)}</span></div>
                                    )}
                                </div>

                                {/* Plot Tabs */}
                                <div className="border border-border-primary bg-bg-secondary">
                                    <div className="flex border-b border-border-primary">
                                        <button
                                            onClick={() => setRsmPlotTab('contour')}
                                            className={`px-4 py-2 text-sm ${rsmPlotTab === 'contour' ? 'bg-accent-primary text-white' : 'text-text-secondary'}`}
                                        >
                                            Contour Plot
                                        </button>
                                        <button
                                            onClick={() => setRsmPlotTab('surface')}
                                            className={`px-4 py-2 text-sm ${rsmPlotTab === 'surface' ? 'bg-accent-primary text-white' : 'text-text-secondary'}`}
                                        >
                                            3D Surface
                                        </button>
                                    </div>

                                    {rsmPlotTab === 'contour' && rr.contour_plot && (
                                        <Plot
                                            data={rr.contour_plot.data}
                                            layout={{ ...plotlyLayout, ...rr.contour_plot.layout, autosize: true, height: 400 }}
                                            useResizeHandler
                                            style={{ width: '100%' }}
                                        />
                                    )}

                                    {rsmPlotTab === 'surface' && rr.surface_plot && (
                                        <Plot
                                            data={rr.surface_plot.data}
                                            layout={{ ...plotlyLayout, ...rr.surface_plot.layout, autosize: true, height: 400 }}
                                            useResizeHandler
                                            style={{ width: '100%' }}
                                        />
                                    )}
                                </div>
                            </>
                        )}

                        {!rsmResult && !rsmLoading && (
                            <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                                Enter design matrix and response values to analyze
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
