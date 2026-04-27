/**
 * qPCR Raw Data Import - File upload and plate visualization
 */

import { useState, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { uploadQpcrFile } from '../../api/client';
import { useThemePlotlyLayout } from '../useThemeColors';

interface Well {
    well_position: string;
    sample_name: string;
    target_name: string;
    task: string;
    ct: number | null;
    quantity?: number;
}

export function RawDataImport() {
    const [file, setFile] = useState<File | null>(null);
    const [result, setResult] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [ctMin, setCtMin] = useState(10);
    const [ctMax, setCtMax] = useState(35);
    const [activeTab, setActiveTab] = useState<'heatmap' | 'curves' | 'table' | 'stdcurve'>('heatmap');

    const plotlyLayout = useThemePlotlyLayout();

    const handleUpload = useCallback(async () => {
        if (!file) {
            setError('Please select a file');
            return;
        }

        setLoading(true);
        setError('');
        setResult(null);

        try {
            const response = await uploadQpcrFile(file);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Upload failed');
        } finally {
            setLoading(false);
        }
    }, [file]);

    const r = result as {
        filename?: string;
        n_wells?: number;
        targets?: string[];
        samples?: string[];
        wells?: Well[];
        results_plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
        amplification_plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
        standard_curve_plotly_json?: { data: Plotly.Data[]; layout: Partial<Plotly.Layout> };
        standard_curve_stats?: {
            target_name?: string;
            slope?: number;
            intercept?: number;
            r_squared?: number;
            efficiency?: number;
            n_points?: number;
        };
        assay_summary?: {
            standard_curves?: Record<string, { r_squared?: number; efficiency_percent?: number; is_valid?: boolean; flags?: string[] }>;
            quantities?: unknown[];
            replicate_qc?: unknown[];
            ntc_qc?: unknown[];
            spike_recovery?: unknown[];
            flag_counts?: Record<string, number>;
        };
    } | null;

    const assayFlags = r?.assay_summary?.flag_counts ? Object.entries(r.assay_summary.flag_counts) : [];

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Raw Data Import</h3>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Left - Input */}
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <label className="block text-sm font-medium text-text-primary mb-2">QS5/Applied Biosystems Data</label>
                        <p className="text-xs text-text-muted mb-3">Upload EDS, Excel, or CSV file</p>

                        <input
                            type="file"
                            accept=".eds,.xlsx,.xls,.csv"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
                            className="block w-full text-sm text-text-secondary file:mr-4 file:py-2 file:px-4 file:border-0 file:text-sm file:font-medium file:bg-accent-primary file:text-white"
                        />

                        {file && (
                            <div className="mt-2 text-xs text-text-secondary">
                                Selected: {file.name} ({(file.size / 1024).toFixed(1)} KB)
                            </div>
                        )}
                    </div>

                    <button
                        onClick={handleUpload}
                        disabled={loading || !file}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Uploading...' : 'Upload & Parse'}
                    </button>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}

                    {/* Summary */}
                    {r && (
                        <div className="border border-border-primary p-4 bg-bg-secondary text-sm">
                            <div className="mb-2"><strong>File:</strong> {r.filename}</div>
                            <div className="mb-2"><strong>Wells:</strong> {r.n_wells}</div>
                            <div className="mb-2"><strong>Targets:</strong> {r.targets?.join(', ')}</div>
                            <div className="text-xs text-text-muted">
                                Samples: {r.samples?.slice(0, 5).join(', ')}{r.samples && r.samples.length > 5 ? '...' : ''}
                            </div>
                            {r.assay_summary && (
                                <div className="mt-3 border-t border-border-primary pt-3 text-xs text-text-secondary">
                                    <div className="font-medium text-text-primary mb-1">Assay QC</div>
                                    <div>Standard curves: {Object.keys(r.assay_summary.standard_curves || {}).length}</div>
                                    <div>Quantified unknowns: {r.assay_summary.quantities?.length || 0}</div>
                                    <div>Replicate QC groups: {r.assay_summary.replicate_qc?.length || 0}</div>
                                    <div>NTCs: {r.assay_summary.ntc_qc?.length || 0}</div>
                                    <div>Spike recoveries: {r.assay_summary.spike_recovery?.length || 0}</div>
                                    {assayFlags.length > 0 && (
                                        <div className="mt-1 text-warning">
                                            Flags: {assayFlags.map(([flag, count]) => `${flag} (${count})`).join(', ')}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Ct Range Control */}
                    {r?.results_plotly_json && (
                        <div className="border border-border-primary p-4 bg-bg-secondary">
                            <label className="block text-xs text-text-muted mb-2">Ct Color Range</label>
                            <div className="grid grid-cols-2 gap-2">
                                <div>
                                    <label className="text-xs text-text-muted">Min</label>
                                    <input
                                        type="number"
                                        value={ctMin}
                                        onChange={(e) => setCtMin(parseInt(e.target.value))}
                                        className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs text-text-muted">Max</label>
                                    <input
                                        type="number"
                                        value={ctMax}
                                        onChange={(e) => setCtMax(parseInt(e.target.value))}
                                        className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                                    />
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Right - Results */}
                <div className="lg:col-span-2">
                    {r && (
                        <div className="border border-border-primary bg-bg-secondary">
                            {/* Tab Nav */}
                            <div className="flex border-b border-border-primary">
                                {(['heatmap', 'curves', 'table', 'stdcurve'] as const).map(tab => (
                                    <button
                                        key={tab}
                                        onClick={() => setActiveTab(tab)}
                                        className={`px-4 py-2 text-sm ${activeTab === tab ? 'bg-accent-primary text-white' : 'text-text-secondary'}`}
                                    >
                                        {tab === 'heatmap' && 'Plate Heatmap'}
                                        {tab === 'curves' && 'Amplification'}
                                        {tab === 'table' && 'Results Table'}
                                        {tab === 'stdcurve' && 'Std Curve'}
                                    </button>
                                ))}
                            </div>

                            {/* Tab Content */}
                            <div className="p-4">
                                {activeTab === 'heatmap' && r.results_plotly_json && (
                                    <Plot
                                        data={r.results_plotly_json.data.map(trace => ({
                                            ...trace,
                                            zmin: ctMin,
                                            zmax: ctMax,
                                        }))}
                                        layout={{ ...plotlyLayout, ...r.results_plotly_json.layout, autosize: true, height: 450 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                )}

                                {activeTab === 'curves' && r.amplification_plotly_json && (
                                    <Plot
                                        data={r.amplification_plotly_json.data}
                                        layout={{ ...plotlyLayout, ...r.amplification_plotly_json.layout, autosize: true, height: 450 }}
                                        useResizeHandler
                                        style={{ width: '100%' }}
                                    />
                                )}

                                {activeTab === 'table' && r.wells && (
                                    <div className="max-h-96 overflow-auto">
                                        <table className="w-full text-sm">
                                            <thead className="bg-bg-tertiary sticky top-0">
                                                <tr>
                                                    <th className="px-3 py-2 text-left text-text-primary">Well</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Sample</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Target</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Task</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Ct</th>
                                                    <th className="px-3 py-2 text-left text-text-primary">Quantity</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {r.wells.map((well, i) => (
                                                    <tr key={i} className="border-t border-border-primary">
                                                        <td className="px-3 py-2 text-text-primary">{well.well_position}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{well.sample_name}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{well.target_name}</td>
                                                        <td className="px-3 py-2 text-text-muted">{well.task}</td>
                                                        <td className="px-3 py-2 text-text-primary">{well.ct ?? 'Undet.'}</td>
                                                        <td className="px-3 py-2 text-text-secondary">{well.quantity || ''}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}

                                {activeTab === 'stdcurve' && (
                                    <div className="space-y-4">
                                        {r.standard_curve_plotly_json ? (
                                            <>
                                                <Plot
                                                    data={r.standard_curve_plotly_json.data}
                                                    layout={{ ...plotlyLayout, ...r.standard_curve_plotly_json.layout, autosize: true, height: 350 }}
                                                    useResizeHandler
                                                    style={{ width: '100%' }}
                                                />
                                                {r.standard_curve_stats && (
                                                    <div className="bg-bg-tertiary p-4 text-sm">
                                                        <div className="font-medium text-text-primary mb-2">
                                                            Standard Curve: {r.standard_curve_stats.target_name}
                                                        </div>
                                                        <div className="grid grid-cols-2 gap-2 text-xs">
                                                            <div><span className="text-text-muted">Slope:</span> {r.standard_curve_stats.slope?.toFixed(4)}</div>
                                                            <div><span className="text-text-muted">Y-intercept:</span> {r.standard_curve_stats.intercept?.toFixed(4)}</div>
                                                            <div><span className="text-text-muted">R²:</span> {r.standard_curve_stats.r_squared?.toFixed(4)}</div>
                                                            <div><span className="text-text-muted">Efficiency:</span> {r.standard_curve_stats.efficiency?.toFixed(1)}%</div>
                                                        </div>
                                                    </div>
                                                )}
                                            </>
                                        ) : (
                                            <div className="text-center text-text-muted py-8">
                                                No STANDARD wells detected. Standard curve requires wells with Task=STANDARD and known Quantity.
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {!result && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Upload an EDS, Excel, or CSV file to view plate data
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
