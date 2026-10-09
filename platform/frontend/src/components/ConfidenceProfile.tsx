// Shared owner extracted from AnalyticsDashboard's working pLDDT chart.
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import type { AtomRef } from '../structureViewer/contracts/structureIdentity';
import type { ChainMetric } from '../lib/api';
const AXIS_COLOR = '#94a3b8';
const GRID_COLOR = '#334155';
const DEFAULT_PLOT_CONFIG = { responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg' as const } };
const makeLayout = (overrides: Partial<Layout>): Partial<Layout> => ({ paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a', font: { color: '#e2e8f0', size: 11 }, ...overrides });
export type ConfidenceChainMetric = ChainMetric & { positionLabels?: string[]; identities?: AtomRef[]; label?: string };
export function ConfidenceProfile({ chainMetrics, onInspect }: { chainMetrics: Record<string, ConfidenceChainMetric>; onInspect?: (ref: AtomRef) => void }) {
    return (<Plot
                            data={Object.entries(chainMetrics || {})
                                .filter(([, metric]) => metric.type !== 'ligand')
                                .sort(([leftId, leftMetric], [rightId, rightMetric]) => {
                                    const order: Record<string, number> = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                                    return (order[leftMetric.type] ?? 4) - (order[rightMetric.type] ?? 4) || leftId.localeCompare(rightId);
                                })
                                .map(([chainId, metric], index) => ({
                                    type: 'scatter' as const,
                                    mode: 'lines',
                                    x: metric.positionLabels ?? metric.residue_numbers ?? Array.from({ length: metric.length }, (_, value) => value + 1),
                                    y: metric.plddt,
                                    customdata: metric.identities,
                                    name: metric.label ?? `Chain ${chainId} (${metric.type}, avg ${metric.avg_plddt?.toFixed(1) ?? 'n/a'})`,
                                    line: {
                                        width: 2.4,
                                        color: ['#60a5fa', '#2dd4bf', '#f59e0b', '#a78bfa'][index % 4],
                                        shape: 'spline' as const,
                                    },
                                    hovertemplate: `<b>Chain ${chainId}</b><br>Residue %{x}<br>pLDDT: %{y:.1f}<extra></extra>`,
                                })) as Data[]}
                            layout={{
                                ...makeLayout({
                                    xaxis: {
                                        ...(Object.values(chainMetrics).some(m => m.positionLabels?.some((label, i) => label !== String(m.residue_numbers[i]))) ? { type: 'category' as const } : {}),
                                        title: { text: 'Residue Number', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                        zeroline: false,
                                    },
                                    yaxis: {
                                        title: { text: 'pLDDT', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                        range: [0, 100],
                                        dtick: 20,
                                        zeroline: false,
                                    },
                                    margin: { l: 60, r: 36, t: 20, b: 60 },
                                    legend: {
                                        orientation: 'h',
                                        y: -0.18,
                                        x: 0.5,
                                        xanchor: 'center',
                                        font: { size: 11, color: '#cbd5e1' },
                                    },
                                    shapes: [
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 100, fillcolor: '#1d4ed820', line: { width: 0 } },
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 90, fillcolor: '#0d948820', line: { width: 0 } },
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 70, fillcolor: '#ca8a0420', line: { width: 0 } },
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 50, fillcolor: '#dc262620', line: { width: 0 } },
                                    ],
                                }),
                                hovermode: 'x unified',
                            }}
                            onClick={event => {
                                const ref = event.points[0]?.customdata as unknown as AtomRef | undefined;
                                if (ref?.documentId) onInspect?.(ref);
                            }}
                            config={DEFAULT_PLOT_CONFIG}
                            style={{ width: '100%', height: '380px' }}
                        />
    );
}
