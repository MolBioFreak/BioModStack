import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    ScatterChart,
    Scatter,
    AreaChart,
    Area,
    ReferenceLine,
    ReferenceArea
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


// Per-residue pLDDT area chart with professional styling
interface ResidueChartProps {
    residueNumbers: number[];
    plddt: number[];
    designName: string;
    height?: number;
}

export function ResidueLineChart({ residueNumbers, plddt, designName, height = 350 }: ResidueChartProps) {
    // Transform to Recharts format
    const chartData = residueNumbers.map((res, i) => ({
        residue: res,
        plddt: plddt[i],
    }));

    // Calculate stats
    const avg = plddt.reduce((a, b) => a + b, 0) / plddt.length;
    const min = Math.min(...plddt);
    const max = Math.max(...plddt);

    // Count confidence regions
    const highCount = plddt.filter(v => v >= 80).length;
    const medCount = plddt.filter(v => v >= 60 && v < 80).length;
    const lowCount = plddt.filter(v => v < 60).length;

    // Calculate tick interval for cleaner x-axis (every ~50 residues, or at least 10 ticks)
    const tickInterval = Math.max(1, Math.floor(plddt.length / 10));

    return (
        <div className="bg-gradient-to-br from-slate-800/80 to-slate-900/90 p-6 rounded-2xl border border-slate-700/60 backdrop-blur-xl shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between mb-5">
                <div>
                    <h3 className="text-white text-lg font-bold flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
                            <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
                            </svg>
                        </div>
                        Per-Residue Confidence
                    </h3>
                    <p className="text-sm text-slate-400 mt-1 ml-[52px] truncate max-w-lg font-medium">{designName}</p>
                </div>

                {/* Confidence breakdown pills */}
                <div className="flex gap-3">
                    <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-emerald-500/15 border border-emerald-500/30 shadow-sm">
                        <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-lg shadow-emerald-400/50"></div>
                        <span className="text-sm font-bold text-emerald-400">{highCount}</span>
                        <span className="text-xs text-emerald-400/70">high</span>
                    </div>
                    <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-amber-500/15 border border-amber-500/30 shadow-sm">
                        <div className="w-2.5 h-2.5 rounded-full bg-amber-400 shadow-lg shadow-amber-400/50"></div>
                        <span className="text-sm font-bold text-amber-400">{medCount}</span>
                        <span className="text-xs text-amber-400/70">med</span>
                    </div>
                    <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-red-500/15 border border-red-500/30 shadow-sm">
                        <div className="w-2.5 h-2.5 rounded-full bg-red-400 shadow-lg shadow-red-400/50"></div>
                        <span className="text-sm font-bold text-red-400">{lowCount}</span>
                        <span className="text-xs text-red-400/70">low</span>
                    </div>
                </div>
            </div>

            {/* Chart */}
            <div style={{ height }} className="relative">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 20, right: 20, bottom: 35, left: 50 }}>
                        <defs>
                            {/* Main area gradient - blue/purple theme */}
                            <linearGradient id="plddtAreaGradient" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor="#818cf8" stopOpacity={0.6} />
                                <stop offset="30%" stopColor="#60a5fa" stopOpacity={0.4} />
                                <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.05} />
                            </linearGradient>
                            {/* Line gradient */}
                            <linearGradient id="plddtLineGradient" x1="0" y1="0" x2="1" y2="0">
                                <stop offset="0%" stopColor="#818cf8" />
                                <stop offset="50%" stopColor="#60a5fa" />
                                <stop offset="100%" stopColor="#38bdf8" />
                            </linearGradient>
                        </defs>

                        {/* Confidence zone bands */}
                        <ReferenceArea y1={80} y2={100} fill="#34d399" fillOpacity={0.08} />
                        <ReferenceArea y1={60} y2={80} fill="#fbbf24" fillOpacity={0.06} />
                        <ReferenceArea y1={0} y2={60} fill="#f87171" fillOpacity={0.04} />

                        <CartesianGrid
                            strokeDasharray="3 3"
                            stroke="#334155"
                            opacity={0.5}
                            vertical={false}
                        />

                        <XAxis
                            dataKey="residue"
                            stroke="#64748b"
                            fontSize={10}
                            tickLine={false}
                            axisLine={{ stroke: '#475569', strokeWidth: 1 }}
                            tick={{ fill: '#94a3b8', fontSize: 10 }}
                            interval={tickInterval}
                            label={{
                                value: 'Residue Position',
                                position: 'bottom',
                                fill: '#94a3b8',
                                fontSize: 12,
                                dy: 18,
                                fontWeight: 600
                            }}
                        />

                        <YAxis
                            domain={[0, 100]}
                            stroke="#64748b"
                            fontSize={10}
                            tickLine={false}
                            axisLine={{ stroke: '#475569', strokeWidth: 1 }}
                            tick={{ fill: '#94a3b8', fontSize: 10 }}
                            ticks={[0, 20, 40, 60, 80, 100]}
                            label={{
                                value: 'pLDDT Score',
                                angle: -90,
                                position: 'insideLeft',
                                fill: '#94a3b8',
                                fontSize: 12,
                                dx: -10,
                                fontWeight: 600
                            }}
                        />

                        <Tooltip
                            contentStyle={{
                                backgroundColor: 'rgba(15, 23, 42, 0.98)',
                                border: '1px solid rgba(99, 102, 241, 0.4)',
                                borderRadius: '12px',
                                color: '#f8fafc',
                                padding: '12px 16px',
                                boxShadow: '0 20px 40px rgba(0,0,0,0.4)',
                            }}
                            formatter={(value: number) => {
                                const color = value >= 80 ? '#34d399' : value >= 60 ? '#fbbf24' : '#f87171';
                                const label = value >= 80 ? 'High' : value >= 60 ? 'Medium' : 'Low';
                                return [
                                    <span style={{ color, fontWeight: 700, fontSize: '16px' }}>
                                        {value.toFixed(1)} <span style={{ fontSize: '11px', opacity: 0.8 }}>({label})</span>
                                    </span>,
                                    'pLDDT'
                                ];
                            }}
                            labelFormatter={(label) => (
                                <span className="text-slate-300 font-semibold">Residue {label}</span>
                            )}
                            cursor={{ stroke: '#818cf8', strokeWidth: 2, strokeDasharray: '4 4' }}
                        />

                        {/* Confidence threshold reference lines */}
                        <ReferenceLine
                            y={80}
                            stroke="#34d399"
                            strokeDasharray="6 4"
                            strokeWidth={1.5}
                            opacity={0.7}
                            label={{ value: '80', position: 'right', fill: '#34d399', fontSize: 10 }}
                        />
                        <ReferenceLine
                            y={60}
                            stroke="#fbbf24"
                            strokeDasharray="6 4"
                            strokeWidth={1.5}
                            opacity={0.7}
                            label={{ value: '60', position: 'right', fill: '#fbbf24', fontSize: 10 }}
                        />

                        {/* Main area with gradient fill */}
                        <Area
                            type="monotone"
                            dataKey="plddt"
                            stroke="url(#plddtLineGradient)"
                            strokeWidth={2.5}
                            fill="url(#plddtAreaGradient)"
                            activeDot={{
                                r: 7,
                                fill: '#818cf8',
                                stroke: '#1e1b4b',
                                strokeWidth: 3
                            }}
                            animationDuration={1200}
                            animationEasing="ease-out"
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </div>

            {/* Stats footer - card style */}
            <div className="flex items-center justify-between mt-5 pt-5 border-t border-slate-700/50">
                <div className="flex items-center gap-4">
                    <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl px-4 py-2 text-center">
                        <div className="text-xl font-bold text-blue-400">{avg.toFixed(1)}</div>
                        <div className="text-[10px] text-blue-400/70 uppercase tracking-wider font-medium">Average</div>
                    </div>
                    <div className="bg-slate-700/30 border border-slate-600/30 rounded-xl px-4 py-2 text-center">
                        <div className="text-xl font-bold text-slate-300">{min.toFixed(1)}</div>
                        <div className="text-[10px] text-slate-500 uppercase tracking-wider font-medium">Minimum</div>
                    </div>
                    <div className="bg-slate-700/30 border border-slate-600/30 rounded-xl px-4 py-2 text-center">
                        <div className="text-xl font-bold text-slate-300">{max.toFixed(1)}</div>
                        <div className="text-[10px] text-slate-500 uppercase tracking-wider font-medium">Maximum</div>
                    </div>
                </div>
                <div className="bg-purple-500/10 border border-purple-500/30 rounded-xl px-4 py-2 text-center">
                    <div className="text-xl font-bold text-purple-400">{plddt.length}</div>
                    <div className="text-[10px] text-purple-400/70 uppercase tracking-wider font-medium">Residues</div>
                </div>
            </div>
        </div>
    );
}
