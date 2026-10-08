import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    ScatterChart,
    Scatter
} from 'recharts';
import type { MetricDistribution } from '../lib/api';

interface HistogramProps {
    data: MetricDistribution;
    title: string;
    color?: string;
    height?: number;
}

export function Histogram({ data, title, color = "#8884d8", height = 300 }: HistogramProps) {
    // Transform API histogram data to Recharts format
    const chartData = data.histogram_bins.slice(0, -1).map((bin, i) => ({
        bin: bin.toFixed(1),
        count: data.histogram_counts[i],
        range: `${bin.toFixed(1)} - ${data.histogram_bins[i + 1].toFixed(1)}`
    }));

    return (
        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/40 backdrop-blur-sm">
            <h3 className="text-slate-300 text-sm font-semibold tracking-wide mb-6 flex items-center gap-2">
                <span className="w-1 h-4 bg-slate-500 rounded-full"></span>
                {title}
            </h3>
            <div style={{ height }}>
                <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={chartData} margin={{ top: 10, right: 10, bottom: 20, left: -10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} vertical={false} />
                        <XAxis
                            dataKey="bin"
                            stroke="#64748b"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            dy={10}
                        />
                        <YAxis
                            stroke="#64748b"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                        />
                        <Tooltip
                            contentStyle={{
                                backgroundColor: 'rgba(15, 23, 42, 0.95)',
                                border: '1px solid rgba(51, 65, 85, 0.5)',
                                borderRadius: '12px',
                                color: '#f8fafc',
                                padding: '12px',
                                boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
                                backdropFilter: 'blur(8px)'
                            }}
                            cursor={{ fill: '#334155', opacity: 0.2 }}
                            itemStyle={{ color: color }}
                        />
                        <Bar
                            dataKey="count"
                            fill={color}
                            radius={[6, 6, 0, 0]}
                            name="Designs"
                            animationDuration={1500}
                        />
                    </BarChart>
                </ResponsiveContainer>
            </div>
            <div className="flex justify-between mt-4 text-xs font-medium text-slate-500 px-2 border-t border-slate-700/30 pt-4">
                <div className="flex gap-2">
                    <span>Avg: <span className="text-slate-300">{data.avg.toFixed(2)}</span></span>
                    <span className="text-slate-700">|</span>
                    <span>Median: <span className="text-slate-300">{data.median.toFixed(2)}</span></span>
                </div>
                <div>Max: <span className="text-slate-300">{data.max.toFixed(2)}</span></div>
            </div>
        </div>
    );
}

interface ScatterProps {
    data: Array<{ x: number; y: number; id: string }>;
    xLabel: string;
    yLabel: string;
    title: string;
    height?: number;
}

export function MetricScatter({ data, xLabel, yLabel, title, height = 300 }: ScatterProps) {
    return (
        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/40 backdrop-blur-sm">
            <h3 className="text-slate-300 text-sm font-semibold tracking-wide mb-6 flex items-center gap-2">
                <span className="w-1 h-4 bg-slate-500 rounded-full"></span>
                {title}
            </h3>
            <div style={{ height }}>
                <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart margin={{ top: 10, right: 10, bottom: 25, left: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
                        <XAxis
                            type="number"
                            dataKey="x"
                            name={xLabel}
                            stroke="#64748b"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            dy={10}
                            domain={['auto', 'auto']}
                            label={{ value: xLabel, position: 'bottom', fill: '#64748b', fontSize: 11, dy: 20 }}
                        />
                        <YAxis
                            type="number"
                            dataKey="y"
                            name={yLabel}
                            stroke="#64748b"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            dx={-10}
                            domain={['auto', 'auto']}
                            label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 11 }}
                        />
                        <Tooltip
                            cursor={{ strokeDasharray: '3 3', stroke: '#cbd5e1' }}
                            contentStyle={{
                                backgroundColor: 'rgba(15, 23, 42, 0.95)',
                                border: '1px solid rgba(51, 65, 85, 0.5)',
                                borderRadius: '12px',
                                color: '#f8fafc',
                                padding: '12px',
                                backdropFilter: 'blur(8px)'
                            }}
                        />
                        <Scatter
                            name="Designs"
                            data={data}
                            fill="#8b5cf6"
                            shape="circle"
                            fillOpacity={0.6}
                            animationDuration={1000}
                        />
                    </ScatterChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}
