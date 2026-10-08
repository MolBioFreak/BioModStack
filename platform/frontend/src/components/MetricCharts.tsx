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
    ReferenceArea,
    LineChart,
    Line,
    Legend
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
                        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-accent-secondary flex items-center justify-center shadow-lg shadow-blue-500/20">
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
                            formatter={(value: number | undefined) => {
                                if (value === undefined) return ['N/A', 'pLDDT'];
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
                <div className="bg-accent/10 border border-accent/30 rounded-xl px-4 py-2 text-center">
                    <div className="text-xl font-bold text-accent">{plddt.length}</div>
                    <div className="text-[10px] text-accent/70 uppercase tracking-wider font-medium">Residues</div>
                </div>
            </div>
        </div>
    );
}

interface MultiLineChartProps {
    data: Array<{
        residue: number;
        [key: string]: number; // dynamic keys for each design
    }>;
    designNames: string[];
    colors: string[];
    height?: number;
}

export function DesignMultiLineChart({ data, designNames, colors, height = 400 }: MultiLineChartProps) {
    return (
        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/40 backdrop-blur-sm">
            <h3 className="text-slate-300 text-sm font-semibold tracking-wide mb-6 flex items-center gap-2">
                <span className="w-1 h-4 bg-blue-500 rounded-full"></span>
                pLDDT Comparison Overlay
            </h3>
            <div style={{ height }}>
                <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={data} margin={{ top: 10, right: 30, bottom: 20, left: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} vertical={false} />
                        <XAxis
                            dataKey="residue"
                            stroke="#64748b"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            dy={10}
                            label={{ value: 'Residue Position', position: 'bottom', fill: '#64748b', fontSize: 11, dy: 20 }}
                        />
                        <YAxis
                            domain={[0, 100]}
                            stroke="#64748b"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            ticks={[0, 20, 40, 60, 80, 100]}
                            label={{ value: 'pLDDT Score', angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 11 }}
                        />
                        <Tooltip
                            contentStyle={{
                                backgroundColor: 'rgba(15, 23, 42, 0.95)',
                                border: '1px solid rgba(51, 65, 85, 0.5)',
                                borderRadius: '12px',
                                color: '#f8fafc',
                                padding: '12px',
                                backdropFilter: 'blur(8px)'
                            }}
                            cursor={{ stroke: '#818cf8', strokeWidth: 1, strokeDasharray: '4 4' }}
                        />
                        <Legend wrapperStyle={{ paddingTop: '20px' }} />

                        {/* Render lines for each design */}
                        {designNames.map((name, index) => (
                            <Line
                                key={name}
                                type="monotone"
                                dataKey={name}
                                stroke={colors[index % colors.length]}
                                strokeWidth={2}
                                dot={false}
                                activeDot={{ r: 6 }}
                                animationDuration={1000}
                            />
                        ))}
                    </LineChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}

/**
 * Minimal sparkline chart for compact overlay displays
 * Just shows the line with basic hover tooltip - no headers, legends, or margins
 */
interface SparklineProps {
    data: number[];
    width?: number;
    height?: number;
    color?: string;
    thresholds?: { low: number; high: number };
}

export function SparklineChart({
    data,
    width = 200,
    height = 60,
    color = '#60a5fa',
    thresholds = { low: 60, high: 80 }
}: SparklineProps) {
    const chartData = data.map((value, index) => ({ index, value }));
    const avg = data.reduce((a, b) => a + b, 0) / data.length;

    return (
        <div style={{ width, height }} className="relative">
            <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
                    <defs>
                        <linearGradient id="sparkGradient" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                            <stop offset="100%" stopColor={color} stopOpacity={0.05} />
                        </linearGradient>
                    </defs>

                    {/* Threshold zone coloring */}
                    <ReferenceArea y1={thresholds.high} y2={100} fill="#34d399" fillOpacity={0.1} />
                    <ReferenceArea y1={thresholds.low} y2={thresholds.high} fill="#fbbf24" fillOpacity={0.08} />
                    <ReferenceArea y1={0} y2={thresholds.low} fill="#f87171" fillOpacity={0.08} />

                    {/* Average line */}
                    <ReferenceLine y={avg} stroke="#94a3b8" strokeDasharray="2 2" strokeWidth={1} />

                    <YAxis domain={[0, 100]} hide />
                    <XAxis dataKey="index" hide />

                    <Tooltip
                        contentStyle={{
                            backgroundColor: 'rgba(15, 23, 42, 0.95)',
                            border: '1px solid rgba(51, 65, 85, 0.6)',
                            borderRadius: '6px',
                            padding: '4px 8px',
                            fontSize: '11px',
                        }}
                        formatter={(value: number | undefined) => value !== undefined ? [`${value.toFixed(1)}`, 'pLDDT'] : ['N/A', 'pLDDT']}
                        labelFormatter={(idx) => `Residue ${idx + 1}`}
                    />

                    <Area
                        type="monotone"
                        dataKey="value"
                        stroke={color}
                        strokeWidth={1.5}
                        fill="url(#sparkGradient)"
                        dot={false}
                        activeDot={{ r: 3, fill: color }}
                    />
                </AreaChart>
            </ResponsiveContainer>

            {/* Compact stats overlay */}
            <div className="absolute bottom-0 left-0 right-0 flex justify-between text-[9px] text-slate-400 px-1">
                <span>avg: {avg.toFixed(0)}</span>
                <span>{data.length} res</span>
            </div>
        </div>
    );
}


/**
 * IPTMHeatmap - Displays the chain-chain ipTM scores matrix
 * 
 * Shows interface pTM scores between all pairs of chains,
 * using a viridis-like color scale (purple → blue → green → yellow).
 */
interface IPTMHeatmapProps {
    data: Record<string, Record<string, number>>; // {"0": {"0": 0.76, "1": 0.5}, ...}
    title?: string;
    width?: number;
    height?: number;
}

export function IPTMHeatmap({ data, title = "ipTM Pairs", width = 200, height = 200 }: IPTMHeatmapProps) {
    if (!data || Object.keys(data).length === 0) {
        return (
            <div className="flex items-center justify-center text-slate-500 text-xs h-full">
                No chain interface data
            </div>
        );
    }

    // Convert chain keys to sorted array
    const chainIds = Object.keys(data).sort((a, b) => parseInt(a) - parseInt(b));
    const n = chainIds.length;

    // Viridis-like color scale (blue → teal → yellow)
    const getColor = (value: number): string => {
        if (value === undefined || value === null) return '#1e1b4b'; // dark bg for missing

        // Clamp to 0-1
        const v = Math.max(0, Math.min(1, value));

        // Gradient stops: 0 = deep purple, 0.5 = teal, 1 = yellow
        if (v < 0.5) {
            // Purple (68, 1, 84) → Teal (32, 144, 140)
            const t = v * 2;
            const r = Math.round(68 + t * (32 - 68));
            const g = Math.round(1 + t * (144 - 1));
            const b = Math.round(84 + t * (140 - 84));
            return `rgb(${r}, ${g}, ${b})`;
        } else {
            // Teal (32, 144, 140) → Yellow (253, 231, 37)
            const t = (v - 0.5) * 2;
            const r = Math.round(32 + t * (253 - 32));
            const g = Math.round(144 + t * (231 - 144));
            const b = Math.round(140 + t * (37 - 140));
            return `rgb(${r}, ${g}, ${b})`;
        }
    };

    const cellSize = Math.min((width - 40) / n, (height - 40) / n, 50);

    return (
        <div className="flex flex-col" style={{ width, height }}>
            {title && (
                <div className="text-xs text-slate-300 font-medium mb-2 text-center">{title}</div>
            )}

            <div className="flex-1 flex items-center justify-center">
                <div className="relative">
                    {/* Y-axis labels */}
                    <div className="absolute -left-5 top-0 flex flex-col">
                        {chainIds.map((id) => (
                            <div
                                key={`y-${id}`}
                                className="text-[9px] text-slate-400 text-right pr-1"
                                style={{ height: cellSize, lineHeight: `${cellSize}px` }}
                            >
                                {String.fromCharCode(65 + parseInt(id))}
                            </div>
                        ))}
                    </div>

                    {/* Matrix */}
                    <div className="flex flex-col">
                        {chainIds.map((rowId) => (
                            <div key={`row-${rowId}`} className="flex">
                                {chainIds.map((colId) => {
                                    const value = data[rowId]?.[colId];
                                    return (
                                        <div
                                            key={`cell-${rowId}-${colId}`}
                                            style={{
                                                width: cellSize,
                                                height: cellSize,
                                                backgroundColor: getColor(value),
                                            }}
                                            className="cursor-pointer transition-opacity hover:opacity-80 border border-slate-800/20"
                                            title={`${String.fromCharCode(65 + parseInt(rowId))} ↔ ${String.fromCharCode(65 + parseInt(colId))}: ${value?.toFixed(2) ?? 'N/A'}`}
                                        />
                                    );
                                })}
                            </div>
                        ))}
                    </div>

                    {/* X-axis labels */}
                    <div className="flex mt-1">
                        {chainIds.map((id) => (
                            <div
                                key={`x-${id}`}
                                className="text-[9px] text-slate-400 text-center"
                                style={{ width: cellSize }}
                            >
                                {String.fromCharCode(65 + parseInt(id))}
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Color legend */}
            <div className="flex items-center justify-center gap-1 mt-2">
                <span className="text-[8px] text-slate-500">0</span>
                <div
                    className="h-2 w-16 rounded-sm"
                    style={{
                        background: 'linear-gradient(to right, rgb(68, 1, 84), rgb(32, 144, 140), rgb(253, 231, 37))'
                    }}
                />
                <span className="text-[8px] text-slate-500">1</span>
            </div>
        </div>
    );
}

export interface StabilityHeatmapProps {
    data: Record<string, Record<string, number>>; // {"A": {"1": -0.5, "2": 1.2}, ...}
    title?: string;
    width?: number;
    height?: number;
}

export function StabilityHeatmap({ data, title = "Stability (ddG)", width = 300, height: _height = 300 }: StabilityHeatmapProps) {
    if (!data || Object.keys(data).length === 0) {
        return (
            <div className="flex items-center justify-center text-slate-500 text-xs h-full bg-slate-800/20 rounded-xl">
                No stability data
            </div>
        );
    }

    // Extract chains and residues
    // Data structure: chain -> residue_index -> ddG
    const chains = Object.keys(data).sort();

    // Flatten to a grid for visualization
    // We'll visualize one chain at a time or stack them? 
    // For now, let's just take the first chain if multiple, or allow selection?
    // Simpler: Just visualize the first chain found.
    const chainId = chains[0];
    // residueMap and residueIndices not used in current implementation

    // We need 20 amino acids on Y axis (mutations) and Residue Index on X axis (positions)
    // Wait, ThermoMPNN output is usually (position, mutant_aa) -> ddG
    // Let's assume the data passed here is already processed into:
    // { "position_1": { "A": 0.0, "C": -0.5, ... }, ... }

    // If the data is raw ddG per position:
    // We need to know the specific structure of `stability_data` stored in DB.
    // Assuming it's a matrix of [Position x 20AA]

    // Let's assume input `data` is: { "1": {"A": 0.1, "R": -0.2...}, "2": ... } (Residue Index -> Mutation AA -> Score)

    const positions = Object.keys(data).sort((a, b) => parseInt(a) - parseInt(b));
    const aminoAcids = ['A', 'R', 'N', 'D', 'C', 'Q', 'E', 'G', 'H', 'I', 'L', 'K', 'M', 'F', 'P', 'S', 'T', 'W', 'Y', 'V'];

    const cellSizeX = Math.max(10, (width - 60) / positions.length);
    // cellSizeY not used - keeping cellSizeX only for now

    // Color scale: Blue (Stabilizing, < 0) -> White (0) -> Red (Destabilizing, > 0)
    const getColor = (val: number) => {
        if (val === undefined || val === null) return '#1e293b';

        // Clamp -2.0 to +2.0 roughly
        if (val < 0) {
            // Blue scale
            const intensity = Math.min(1, Math.abs(val) / 2.0);
            // White (255,255,255) to Blue (59, 130, 246)
            const r = Math.round(255 + intensity * (59 - 255));
            const g = Math.round(255 + intensity * (130 - 255));
            const b = Math.round(255 + intensity * (246 - 255));
            return `rgb(${r},${g},${b})`;
        } else {
            // Red scale
            const intensity = Math.min(1, val / 2.0);
            // White to Red (239, 68, 68)
            const r = Math.round(255 + intensity * (239 - 255));
            const g = Math.round(255 + intensity * (68 - 255));
            const b = Math.round(255 + intensity * (68 - 255));
            return `rgb(${r},${g},${b})`;
        }
    };

    return (
        <div className="flex flex-col bg-slate-800/40 p-4 rounded-xl border border-slate-700/40" style={{ width: '100%', maxWidth: width }}>
            {title && (
                <div className="text-xs text-slate-300 font-medium mb-3 flex justify-between items-center">
                    <span>{title}</span>
                    <span className="text-[10px] text-slate-500">Chain {chainId}</span>
                </div>
            )}

            <div className="overflow-x-auto">
                <div className="flex">
                    {/* Y-axis labels (AAs) */}
                    <div className="flex flex-col mr-2 pt-6">
                        {aminoAcids.map(aa => (
                            <div key={aa} className="text-[9px] text-slate-400 h-[14px] leading-[14px] text-right">
                                {aa}
                            </div>
                        ))}
                    </div>

                    {/* Heatmap */}
                    <div className="flex flex-col">
                        {/* X-axis labels (every 10th) */}
                        <div className="flex h-6 mb-0.5">
                            {positions.map((pos, i) => (
                                <div key={pos} className="text-[9px] text-slate-500 text-center relative" style={{ width: cellSizeX }}>
                                    {(i % 5 === 0) ? pos : ''}
                                </div>
                            ))}
                        </div>

                        {/* Grid */}
                        <div className="flex">
                            {positions.map(pos => (
                                <div key={pos} className="flex flex-col">
                                    {aminoAcids.map(aa => {
                                        const val = (data as any)[pos]?.[aa];
                                        return (
                                            <div
                                                key={`${pos}-${aa}`}
                                                className="h-[14px] border-[0.5px] border-slate-900/10 hover:border-slate-400 z-0 hover:z-10 relative"
                                                style={{ width: cellSizeX, backgroundColor: getColor(val) }}
                                                title={`Pos ${pos} -> ${aa}: ${val?.toFixed(2)}`}
                                            />
                                        );
                                    })}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {/* Legend */}
            <div className="flex items-center justify-center gap-2 mt-3 text-[10px] text-slate-400">
                <span>Stabilizing (&lt;0)</span>
                <div className="w-24 h-2 rounded bg-gradient-to-r from-blue-500 via-white to-red-500"></div>
                <span>Destabilizing (&gt;0)</span>
            </div>
        </div>
    );
}
