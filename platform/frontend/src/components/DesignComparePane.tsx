import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchDesignResidueMetrics } from '../lib/api';
import { DesignMultiLineChart } from './MetricCharts';

interface Design {
    id: string;
    name: string;
    plddt_overall: number | null;
}

interface DesignComparePaneProps {
    designs: Design[];
    preSelectedId?: string;
    selectedDesignIds?: string[];
    onSelectedDesignIdsChange?: (ids: string[]) => void;
}

const COLORS = [
    '#3b82f6', // blue
    '#ef4444', // red
    '#10b981', // emerald
    '#f59e0b', // amber
    '#8b5cf6', // violet
    '#ec4899', // pink
    '#06b6d4', // cyan
    '#f97316', // orange
];

export function DesignComparePane({ designs, preSelectedId, selectedDesignIds, onSelectedDesignIdsChange }: DesignComparePaneProps) {
    const [localIds, setLocalIds] = useState<string[]>(
        preSelectedId ? [preSelectedId] : designs.slice(0, 3).map(d => d.id)
    );

    const selectedIds = (selectedDesignIds ?? localIds).filter(id => designs.some(d => d.id === id));
    const label = (id: string) => `${designs.find(d => d.id === id)?.name ?? id} (${id})`;

    const { data: metricsMap, isLoading } = useQuery({
        queryKey: ['multiResidueMetrics', [...selectedIds].sort().join(',')],
        queryFn: async () => {
            const results = await Promise.all(
                selectedIds.map(async (id) => {
                    try {
                        const res = await fetchDesignResidueMetrics(id);
                        return { id, data: res.data };
                    } catch (e) {
                        console.error(`Failed to fetch metrics for ${id}`, e);
                        return { id, data: null };
                    }
                })
            );
            return results;
        },
        enabled: selectedIds.length > 0,
        staleTime: 1000 * 60 * 5, // Cache for 5 mins
    });

    const toggleDesign = (id: string) => {
        const next = selectedIds.includes(id) ? selectedIds.filter(x => x !== id) : [...selectedIds, id];
        setLocalIds(next);
        onSelectedDesignIdsChange?.(next);
    };

    // Prepare Chart Data
    const chartData = useMemo(() => {
        if (!metricsMap) return [];

        // Use reported residue positions, never an inferred correspondence.
        const positions = [...new Set(metricsMap.flatMap(m => m.data?.residue_numbers ?? []))].sort((a, b) => a - b);
        return positions.map(residue => {
            const point: { residue: number; [key: string]: number } = { residue };
            metricsMap.forEach(m => {
                const index = m.data?.residue_numbers.indexOf(residue) ?? -1;
                const value = index < 0 ? undefined : m.data?.plddt[index];
                if (typeof value === 'number' && Number.isFinite(value)) point[`${designs.find(d => d.id === m.id)?.name ?? m.id} (${m.id})`] = value;
            });
            return point;
        });

    }, [metricsMap, designs]);

    const designNames = selectedIds.map(label);

    return (
        <div className="flex h-[800px] gap-6">
            {/* Sidebar: Design List */}
            <div className="w-80 border-r border-slate-800 bg-slate-900/30 flex flex-col">
                <div className="p-4 border-b border-slate-800">
                    <h3 className="font-semibold text-slate-200">Select Designs</h3>
                    <p className="text-xs text-slate-500 mt-1">Select designs to overlay</p>
                </div>
                <div className="flex-1 overflow-y-auto p-2">
                    {designs.map(design => (
                        <div
                            key={design.id}
                            onClick={() => toggleDesign(design.id)}
                            className={`p-3 rounded-lg mb-1 cursor-pointer transition-colors border flex items-center justify-between ${selectedIds.includes(design.id)
                                ? 'bg-blue-500/10 border-blue-500/50'
                                : 'bg-transparent border-transparent hover:bg-slate-800'
                                }`}
                        >
                            <div className="truncate pr-2">
                                <div className={`text-sm font-medium truncate ${selectedIds.includes(design.id) ? 'text-blue-400' : 'text-slate-300'}`}>
                                    {design.name}
                                </div>
                                <div className="text-xs text-slate-500">
                                    pLDDT: {design.plddt_overall?.toFixed(1) ?? '—'}
                                </div>
                            </div>
                            {selectedIds.includes(design.id) && (
                                <div
                                    className="w-3 h-3 rounded-full"
                                    style={{ backgroundColor: COLORS[selectedIds.indexOf(design.id) % COLORS.length] }}
                                />
                            )}
                        </div>
                    ))}
                </div>
            </div>

            {/* Main Content */}
            <div className="flex-1 p-6 overflow-y-auto">
                <div className="mb-6">
                    <h2 className="text-xl font-bold text-white mb-2">Confidence Overlay</h2>
                    <p className="text-slate-400 text-sm">Comparing reported per-residue pLDDT across {selectedIds.length} designs. Residue numbers are not a structural alignment; verify compatible chains and producer scales.</p>
                </div>

                {isLoading ? (
                    <div className="h-64 flex items-center justify-center">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
                    </div>
                ) : chartData.length > 0 ? (
                    <DesignMultiLineChart
                        data={chartData}
                        designNames={designNames}
                        colors={COLORS}
                        height={500}
                    />
                ) : (
                    <div className="text-center text-slate-500 mt-20">
                        Select designs to view overlay chart.
                    </div>
                )}

                {/* Stats Table */}
                {metricsMap && metricsMap.length > 0 && (
                    <div className="mt-8 bg-slate-800/50 rounded-xl overflow-hidden border border-slate-700/50">
                        <table className="w-full text-sm text-left">
                            <thead className="bg-slate-800 text-slate-400">
                                <tr>
                                    <th className="px-4 py-3">Design</th>
                                    <th className="px-4 py-3">Avg pLDDT</th>
                                    <th className="px-4 py-3">Min</th>
                                    <th className="px-4 py-3">Max</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-700/50">
                                {selectedIds.map((id, idx) => {
                                    const dm = metricsMap.find(m => m.id === id)?.data;
                                    const d = designs.find(d => d.id === id);
                                    if (!dm || !dm.plddt) return null;

                                    const vals = dm.plddt.filter(Number.isFinite);
                                    if (!vals.length) return null;
                                    const min = Math.min(...vals);
                                    const max = Math.max(...vals);

                                    return (
                                        <tr key={id} className="hover:bg-slate-700/20">
                                            <td className="px-4 py-3 font-medium flex items-center gap-2">
                                                <div
                                                    className="w-2 h-2 rounded-full"
                                                    style={{ backgroundColor: COLORS[idx % COLORS.length] }}
                                                />
                                                <span className="text-slate-200">{d?.name}</span>
                                            </td>
                                            <td className="px-4 py-3 text-slate-300">{d?.plddt_overall?.toFixed(1)}</td>
                                            <td className="px-4 py-3 text-slate-400">{min.toFixed(1)}</td>
                                            <td className="px-4 py-3 text-slate-400">{max.toFixed(1)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
