/**
 * Empower 3 Import + SST Review
 */

import { useCallback, useMemo, useState } from 'react';
import {
    exportEmpowerPlasmidTracking,
    exportEmpowerSstMaster,
    importEmpowerFiles,
    listEmpowerSst,
    updateEmpowerInjection,
} from '../../api/client';

interface EmpowerInjection {
    id?: number;
    import_id?: number;
    sample_name: string;
    sample_type: string;
    injection_number?: string | null;
    method_name?: string | null;
    run_date?: string | null;
    source_file?: string | null;
    total_area?: number | null;
    primary_peak_area?: number | null;
    primary_peak_percent?: number | null;
    primary_peak_rt?: number | null;
    primary_peak_resolution?: number | null;
    is_excluded?: boolean;
    note?: string | null;
    flag?: string | null;
}

interface SstSummary {
    sst_group: string;
    n_injections: number;
    area_mean: number;
    area_rsd: number;
    percent_primary_mean: number;
    percent_primary_rsd: number;
    rt_mean: number;
    rt_rsd: number;
    resolution_mean: number;
    resolution_rsd: number;
}

export function EmpowerImport() {
    const [files, setFiles] = useState<File[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [importId, setImportId] = useState<number | null>(null);
    const [injections, setInjections] = useState<EmpowerInjection[]>([]);
    const [sstSummary, setSstSummary] = useState<SstSummary[]>([]);
    const [errors, setErrors] = useState<string[]>([]);

    const [peakProminence, setPeakProminence] = useState(100);
    const [baselineMethod, setBaselineMethod] = useState('snip');

    const totalInjections = useMemo(() => injections.length, [injections]);

    const handleFiles = (event: React.ChangeEvent<HTMLInputElement>) => {
        const selected = event.target.files ? Array.from(event.target.files) : [];
        setFiles(selected);
    };

    const handleImport = useCallback(async () => {
        if (!files.length) {
            setError('Select at least one .arw or .cdf file.');
            return;
        }

        setLoading(true);
        setError('');
        setErrors([]);

        try {
            const response = await importEmpowerFiles(files, {
                persist: true,
                baselineMethod,
                peakProminence,
            });
            setImportId(response.import_id ?? null);
            setInjections(response.injections ?? []);
            setSstSummary(response.sst_summary ?? []);
            setErrors(response.errors ?? []);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Import failed');
        } finally {
            setLoading(false);
        }
    }, [files, baselineMethod, peakProminence]);

    const handleRefreshSst = useCallback(async () => {
        if (!importId) return;
        const summary = await listEmpowerSst(importId);
        setSstSummary(summary);
    }, [importId]);

    const handleInjectionUpdate = async (idx: number) => {
        const inj = injections[idx];
        if (!inj.id) return;
        const payload = {
            sample_name: inj.sample_name,
            sample_type: inj.sample_type,
            injection_number: inj.injection_number,
            is_excluded: inj.is_excluded,
            note: inj.note,
            flag: inj.flag,
        };
        await updateEmpowerInjection(inj.id, payload);
        await handleRefreshSst();
    };

    const handleExport = async (type: 'sst' | 'plasmid') => {
        if (!importId) return;
        const blob = type === 'sst'
            ? await exportEmpowerSstMaster(importId)
            : await exportEmpowerPlasmidTracking(importId);
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = type === 'sst' ? 'sst_master.csv' : 'plasmid_tracking.csv';
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    };

    const sampleTypeOptions = ['SST', 'STANDARD', 'SAMPLE', 'BLANK', 'UNKNOWN'];

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-semibold text-text-primary">Empower 3 Import + SST Review</h3>
                <p className="text-text-secondary text-sm">Upload .arw or .cdf exports, review injections, and export SST/plasmid logs.</p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <label className="block text-xs text-text-muted">Empower exports (.arw, .cdf)</label>
                        <input
                            type="file"
                            multiple
                            accept=".arw,.cdf"
                            onChange={handleFiles}
                            className="block w-full text-xs text-text-secondary"
                        />
                        {files.length > 0 && (
                            <div className="text-xs text-text-muted">{files.length} file(s) selected</div>
                        )}
                    </div>

                    <div className="border border-border-primary p-4 bg-bg-secondary space-y-3">
                        <h4 className="text-sm font-medium text-text-primary">Parsing Defaults</h4>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Baseline Method</label>
                            <select
                                value={baselineMethod}
                                onChange={(e) => setBaselineMethod(e.target.value)}
                                className="w-full bg-bg-tertiary text-text-primary border border-border-primary px-2 py-1 text-sm"
                            >
                                <option value="snip">SNIP</option>
                                <option value="als">ALS</option>
                                <option value="linear">Linear</option>
                                <option value="none">None</option>
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs text-text-muted mb-1">Peak Prominence: {peakProminence}</label>
                            <input
                                type="range"
                                min="10"
                                max="500"
                                step="10"
                                value={peakProminence}
                                onChange={(e) => setPeakProminence(parseInt(e.target.value, 10))}
                                className="w-full accent-accent-primary"
                            />
                        </div>
                    </div>

                    <button
                        onClick={handleImport}
                        disabled={loading}
                        className="w-full bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium disabled:opacity-50"
                    >
                        {loading ? 'Importing...' : 'Import Empower Files'}
                    </button>

                    {error && <div className="p-3 bg-error/20 border border-error text-error text-sm">{error}</div>}
                    {errors.length > 0 && (
                        <div className="p-3 bg-warning/20 border border-warning text-warning text-xs space-y-1">
                            {errors.map((msg, idx) => (
                                <div key={idx}>{msg}</div>
                            ))}
                        </div>
                    )}
                </div>

                <div className="lg:col-span-2 space-y-4">
                    <div className="border border-border-primary p-4 bg-bg-secondary">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                                <h4 className="text-sm font-medium text-text-primary">Import Summary</h4>
                                <p className="text-xs text-text-muted">Total injections: {totalInjections}</p>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => handleExport('sst')}
                                    disabled={!importId}
                                    className="px-3 py-1 bg-bg-tertiary border border-border-primary text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"
                                >
                                    Export SST Master
                                </button>
                                <button
                                    onClick={() => handleExport('plasmid')}
                                    disabled={!importId}
                                    className="px-3 py-1 bg-bg-tertiary border border-border-primary text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"
                                >
                                    Export Plasmid Log
                                </button>
                            </div>
                        </div>
                    </div>

                    {sstSummary.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h4 className="text-sm font-medium text-text-primary">SST Summary</h4>
                                <button
                                    onClick={handleRefreshSst}
                                    className="px-2 py-1 text-xs border border-border-primary text-text-secondary"
                                >
                                    Refresh
                                </button>
                            </div>
                            <div className="overflow-auto">
                                <table className="w-full text-xs">
                                    <thead className="bg-bg-tertiary">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Group</th>
                                            <th className="px-3 py-2 text-left">n</th>
                                            <th className="px-3 py-2 text-left">Area Mean</th>
                                            <th className="px-3 py-2 text-left">Area %RSD</th>
                                            <th className="px-3 py-2 text-left">%Primary Mean</th>
                                            <th className="px-3 py-2 text-left">%Primary %RSD</th>
                                            <th className="px-3 py-2 text-left">RT Mean</th>
                                            <th className="px-3 py-2 text-left">RT %RSD</th>
                                            <th className="px-3 py-2 text-left">Res Mean</th>
                                            <th className="px-3 py-2 text-left">Res %RSD</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sstSummary.map((row) => (
                                            <tr key={row.sst_group} className="border-t border-border-primary">
                                                <td className="px-3 py-2 text-text-primary">{row.sst_group}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.n_injections}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.area_mean.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.area_rsd.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.percent_primary_mean.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.percent_primary_rsd.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.rt_mean.toFixed(3)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.rt_rsd.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.resolution_mean.toFixed(2)}</td>
                                                <td className="px-3 py-2 text-text-secondary">{row.resolution_rsd.toFixed(2)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {injections.length > 0 && (
                        <div className="border border-border-primary bg-bg-secondary p-4">
                            <h4 className="text-sm font-medium text-text-primary mb-3">Injection Review</h4>
                            <div className="overflow-auto max-h-[420px]">
                                <table className="w-full text-xs">
                                    <thead className="bg-bg-tertiary">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Sample</th>
                                            <th className="px-3 py-2 text-left">Type</th>
                                            <th className="px-3 py-2 text-left">Inj #</th>
                                            <th className="px-3 py-2 text-left">Primary Area</th>
                                            <th className="px-3 py-2 text-left">% Primary</th>
                                            <th className="px-3 py-2 text-left">RT</th>
                                            <th className="px-3 py-2 text-left">Res</th>
                                            <th className="px-3 py-2 text-left">Note</th>
                                            <th className="px-3 py-2 text-left">Exclude</th>
                                            <th className="px-3 py-2 text-left"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {injections.map((inj, idx) => (
                                            <tr key={idx} className="border-t border-border-primary">
                                                <td className="px-3 py-2 text-text-primary">
                                                    <input
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs w-40"
                                                        value={inj.sample_name}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, sample_name: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2">
                                                    <select
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs"
                                                        value={inj.sample_type}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, sample_type: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    >
                                                        {sampleTypeOptions.map((option) => (
                                                            <option key={option} value={option}>{option}</option>
                                                        ))}
                                                    </select>
                                                </td>
                                                <td className="px-3 py-2">
                                                    <input
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs w-20"
                                                        value={inj.injection_number || ''}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, injection_number: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_area?.toFixed(2) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_percent?.toFixed(2) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_rt?.toFixed(3) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2 text-text-secondary">
                                                    {inj.primary_peak_resolution?.toFixed(2) ?? '--'}
                                                </td>
                                                <td className="px-3 py-2">
                                                    <input
                                                        className="bg-bg-tertiary border border-border-primary px-2 py-1 text-xs w-32"
                                                        value={inj.note || ''}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, note: e.target.value };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-center">
                                                    <input
                                                        type="checkbox"
                                                        checked={inj.is_excluded ?? false}
                                                        onChange={(e) => {
                                                            const next = [...injections];
                                                            next[idx] = { ...inj, is_excluded: e.target.checked };
                                                            setInjections(next);
                                                        }}
                                                    />
                                                </td>
                                                <td className="px-3 py-2">
                                                    <button
                                                        onClick={() => handleInjectionUpdate(idx)}
                                                        className="px-2 py-1 text-xs border border-border-primary text-text-secondary hover:text-text-primary"
                                                    >
                                                        Save
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {injections.length === 0 && !loading && (
                        <div className="border border-border-primary p-8 bg-bg-secondary text-center text-text-muted">
                            Import Empower exports to review injections
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
