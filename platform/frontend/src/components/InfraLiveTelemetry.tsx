import { startTransition, useDeferredValue, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Config, Data, Layout } from 'plotly.js';
import { fetchSystemStatus } from '../lib/api';
import type { GPUStatus, SystemStatus } from '../lib/api';

const MAX_WINDOW_RETENTION_MS = 15 * 60 * 1000;

type PollPreset = 1000 | 2000 | 5000;
type WindowPreset = 1 | 3 | 5 | 10;
const POLL_PRESETS: ReadonlyArray<{ value: PollPreset; label: string }> = [
    { value: 1000, label: '1s' },
    { value: 2000, label: '2s' },
    { value: 5000, label: '5s' },
];
const WINDOW_PRESETS: ReadonlyArray<{ value: WindowPreset; label: string }> = [
    { value: 1, label: '1m' },
    { value: 3, label: '3m' },
    { value: 5, label: '5m' },
    { value: 10, label: '10m' },
];
interface SummaryItem {
    key: string;
    label: string;
    value: string;
    accent: string;
    progress?: number | null;
    detail?: string;
    compactValue?: boolean;
}
interface LiveSample {
    timestamp: string;
    timestampMs: number;
    clock: string;
    cpuUtil: number;
    cpuPower: number | null;
    cpuTemp: number | null;
    ramUsed: number;
    ramUtil: number;
    gpu: Record<number, { util: number; vram: number; power: number; temp: number }>;
}

interface AxisConfig {
    title: string;
    color: string;
    range?: [number, number];
    suffix?: string;
    decimals?: number;
}

interface TimeSeriesPlotProps {
    height: number;
    samples: LiveSample[];
    leftAxis: AxisConfig;
    rightAxis: AxisConfig;
    leftSeries: Data;
    rightSeries: Data;
}

function formatClock(timestamp: string): string {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

function PanelFrame({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
    return (
        <section className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4 shadow-2xl shadow-black/20">
            <div className="mb-4">
                <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
                {subtitle ? <p className="mt-1 text-sm text-slate-400">{subtitle}</p> : null}
            </div>
            {children}
        </section>
    );
}

const PLOT_GRID = 'rgba(51, 65, 85, 0.42)';
const PLOT_FONT = '#cbd5e1';
const PLOT_TICK = '#94a3b8';
const PLOT_BG = 'rgba(15, 23, 42, 0)';
const PLOT_PANEL_BG = 'rgba(15, 23, 42, 0.22)';
const PLOT_CONFIG: Partial<Config> = {
    displayModeBar: false,
    responsive: true,
    scrollZoom: false,
};

function SegmentedControl<T extends string | number>({
    label,
    value,
    options,
    onChange,
}: {
    label: string;
    value: T;
    options: ReadonlyArray<{ value: T; label: string }>;
    onChange: (value: T) => void;
}) {
    return (
        <div>
            <div
                className="mb-2 text-xs font-semibold uppercase text-slate-400"
                style={{ letterSpacing: '0.16em' }}
            >
                {label}
            </div>
            <div className="inline-flex rounded-xl border border-slate-700 bg-slate-950 p-1">
                {options.map((option) => {
                    const active = option.value === value;
                    return (
                        <button
                            key={String(option.value)}
                            type="button"
                            onClick={() => onChange(option.value)}
                            className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                                active
                                    ? 'bg-cyan-500/15 text-cyan-200 shadow-[inset_0_0_0_1px_rgba(34,211,238,0.28)]'
                                    : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                            }`}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

function buildAxis(axis: AxisConfig, side: 'left' | 'right') {
    return {
        title: {
            text: axis.title,
            font: { color: side === 'left' ? axis.color : PLOT_TICK, size: 11 },
        },
        color: PLOT_TICK,
        tickfont: { color: PLOT_TICK, size: 10 },
        titlefont: { color: axis.color, size: 11 },
        gridcolor: side === 'left' ? PLOT_GRID : 'rgba(0,0,0,0)',
        zeroline: false,
        showline: false,
        tickformat: axis.decimals === 0 ? ',.0f' : axis.decimals === 1 ? ',.1f' : undefined,
        ticksuffix: axis.suffix,
        range: axis.range,
        fixedrange: true,
    };
}

function clamp01(value: number): number {
    if (Number.isNaN(value)) return 0;
    return Math.max(0, Math.min(1, value));
}

function buildCpuSummaryItems(cpu: SystemStatus['cpu']): SummaryItem[] {
    const currentGhz = cpu.frequency_current_mhz / 1000;
    const maxGhz = cpu.frequency_max_mhz > 0 ? cpu.frequency_max_mhz / 1000 : null;
    const frequencyProgress =
        cpu.frequency_max_mhz > 0 ? clamp01(cpu.frequency_current_mhz / cpu.frequency_max_mhz) : null;
    const powerScale = cpu.power_watts != null ? Math.max(200, Math.ceil(cpu.power_watts / 25) * 25) : null;

    return [
        {
            key: 'freq',
            label: 'Freq',
            value: `${currentGhz.toFixed(2)} GHz`,
            detail: maxGhz != null ? `max ${maxGhz.toFixed(2)} GHz` : undefined,
            accent: '#93c5fd',
            progress: frequencyProgress,
        },
        {
            key: 'util',
            label: 'Util',
            value: `${cpu.utilization.toFixed(1)}%`,
            accent: '#4ade80',
            progress: clamp01(cpu.utilization / 100),
        },
        {
            key: 'power',
            label: 'Package Power',
            value: cpu.power_watts != null ? `${cpu.power_watts.toFixed(0)} W` : 'n/a',
            detail: powerScale != null ? `scale ${powerScale} W` : undefined,
            accent: '#fb923c',
            progress: cpu.power_watts != null && powerScale != null ? clamp01(cpu.power_watts / powerScale) : null,
        },
        {
            key: 'temp',
            label: 'Temp',
            value: cpu.temperature != null ? `${cpu.temperature.toFixed(1)} C` : 'n/a',
            accent: '#f472b6',
            progress: cpu.temperature != null ? clamp01(cpu.temperature / 100) : null,
        },
    ];
}

function buildRamSummaryItems(ram: SystemStatus['ram']): SummaryItem[] {
    return [
        {
            key: 'used',
            label: 'Used',
            value: `${ram.used_gb.toFixed(1)} GB`,
            accent: '#38bdf8',
            progress: clamp01(ram.used_gb / Math.max(ram.total_gb, 1)),
        },
        {
            key: 'free',
            label: 'Free',
            value: `${ram.available_gb.toFixed(1)} GB`,
            accent: '#4ade80',
            progress: clamp01(ram.available_gb / Math.max(ram.total_gb, 1)),
        },
        {
            key: 'util',
            label: 'Util',
            value: `${ram.utilization.toFixed(1)}%`,
            accent: '#fb923c',
            progress: clamp01(ram.utilization / 100),
        },
        {
            key: 'swap',
            label: 'Swap',
            value: `${ram.swap_percent.toFixed(1)}%`,
            accent: '#94a3b8',
            progress: clamp01(ram.swap_percent / 100),
        },
    ];
}

function buildGpuSummaryItems(gpu: GPUStatus): SummaryItem[] {
    const vramUsedMb = gpu.memory_used_mb + gpu.reserved_memory_mb;
    const vramGb = vramUsedMb / 1024;
    const totalGb = gpu.memory_total_mb / 1024;
    const powerLimit = gpu.power_limit_w > 0 ? gpu.power_limit_w : Math.max(gpu.max_power_watts, 1);

    return [
        {
            key: 'util',
            label: 'Util',
            value: `${gpu.utilization.toFixed(0)}%`,
            accent: '#4ade80',
            progress: clamp01(gpu.utilization / 100),
        },
        {
            key: 'vram',
            label: 'VRAM',
            value: `${vramGb.toFixed(1)} / ${totalGb.toFixed(0)} GB`,
            accent: '#38bdf8',
            progress: clamp01(vramUsedMb / Math.max(gpu.memory_total_mb, 1)),
        },
        {
            key: 'power',
            label: 'Power',
            value: `${gpu.power_draw_w.toFixed(1)} W`,
            accent: '#fb923c',
            progress: clamp01(gpu.power_draw_w / powerLimit),
        },
        {
            key: 'temp',
            label: 'Temp',
            value: `${gpu.temperature.toFixed(0)} C`,
            accent: '#f472b6',
            progress: clamp01(gpu.temperature / 100),
        },
    ];
}

function formatGpuProcessMemory(memoryMb: number): string {
    if (memoryMb >= 1024) {
        return `${(memoryMb / 1024).toFixed(1)} GB`;
    }
    return `${memoryMb} MB`;
}

function GpuProcessList({ gpu }: { gpu: GPUStatus }) {
    const processes = [...gpu.processes].sort((a, b) => b.memory_mb - a.memory_mb);

    if (processes.length === 0) {
        return (
            <div className="mb-4 rounded-xl border border-dashed border-slate-800 bg-slate-950/45 px-3 py-3 text-sm text-slate-500">
                No active GPU processes.
            </div>
        );
    }

    return (
        <div className="mb-4">
            <div className="mb-2 flex items-center justify-between">
                <div className="text-xs font-semibold uppercase text-slate-500" style={{ letterSpacing: '0.16em' }}>
                    Active Processes
                </div>
                <div className="rounded-full border border-slate-700 bg-slate-950/80 px-2 py-1 text-xs font-medium text-slate-300">
                    {processes.length}
                </div>
            </div>
            <div className="flex flex-wrap gap-2">
                {processes.map((process) => (
                    <div
                        key={`${process.pid}-${process.name}`}
                        className="min-w-[180px] flex-1 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-3"
                    >
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="truncate text-sm font-medium text-slate-100">{process.name}</div>
                                <div className="mt-1 text-xs text-slate-500">PID {process.pid}</div>
                            </div>
                            <div className="rounded-full border border-cyan-500/20 bg-cyan-500/10 px-2 py-1 text-xs font-medium text-cyan-200">
                                {formatGpuProcessMemory(process.memory_mb)}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function SummaryStrip({ items }: { items: SummaryItem[] }) {
    return (
        <div className="mb-4 grid gap-3 md:grid-cols-4">
            {items.map((item) => (
                <div key={item.key} className="rounded-xl border border-slate-800 bg-slate-900/85 px-3 py-3">
                    <div className="flex items-start justify-between gap-3">
                        <span className="text-sm font-medium text-slate-300">{item.label}</span>
                        <span className={`font-semibold text-slate-100 text-right ${item.compactValue ? 'max-w-[72%] text-sm leading-snug' : 'text-base'}`}>
                            {item.value}
                        </span>
                    </div>
                    {item.detail && (
                        <div className="mt-2 text-xs text-slate-500">{item.detail}</div>
                    )}
                    {item.progress != null && (
                        <div className="mt-3 h-1.5 rounded-full bg-slate-800">
                            <div
                                className="h-1.5 rounded-full"
                                style={{
                                    width: `${(item.progress * 100).toFixed(1)}%`,
                                    backgroundColor: item.accent,
                                }}
                            />
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

function TimeSeriesPlot({
    height,
    samples,
    leftAxis,
    rightAxis,
    leftSeries,
    rightSeries,
}: TimeSeriesPlotProps) {
    const revision =
        samples.length === 0
            ? 0
            : samples[samples.length - 1].timestampMs + samples.length;
    const xValues = samples.map((sample) => sample.timestamp);
    const layout: Partial<Layout> = {
        height,
        margin: { l: 52, r: 54, t: 10, b: 34 },
        paper_bgcolor: PLOT_BG,
        plot_bgcolor: PLOT_PANEL_BG,
        font: { color: PLOT_FONT, size: 11 },
        hovermode: 'x unified',
        showlegend: true,
        legend: {
            orientation: 'h',
            x: 0,
            y: 1.12,
            xanchor: 'left',
            yanchor: 'bottom',
            font: { color: PLOT_FONT, size: 11 },
        },
        xaxis: {
            type: 'date',
            color: PLOT_TICK,
            tickfont: { color: PLOT_TICK, size: 10 },
            tickformat: '%I:%M:%S %p',
            showgrid: false,
            zeroline: false,
            fixedrange: true,
            range: xValues.length > 0 ? [xValues[0], xValues[xValues.length - 1]] : undefined,
        },
        yaxis: buildAxis(leftAxis, 'left'),
        yaxis2: {
            ...buildAxis(rightAxis, 'right'),
            overlaying: 'y',
            side: 'right',
        },
    };

    return (
        <Plot
            data={[leftSeries, rightSeries]}
            layout={layout}
            config={PLOT_CONFIG}
            style={{ width: '100%', height: '100%' }}
            useResizeHandler
            revision={revision}
            className="h-full w-full"
        />
    );
}

function CpuPanel({ current, samples }: { current: SystemStatus['cpu']; samples: LiveSample[] }) {
    const summaryItems = buildCpuSummaryItems(current);

    return (
        <PanelFrame
            title={current.name}
        >
            <SummaryStrip items={summaryItems} />

            <div className="h-72">
                <TimeSeriesPlot
                    height={288}
                    samples={samples}
                    leftAxis={{ title: 'CPU %', color: '#22c55e', range: [0, 100], suffix: '%' }}
                    rightAxis={{ title: 'Package W', color: '#f97316', decimals: 0 }}
                    leftSeries={{
                        x: samples.map((sample) => sample.timestamp),
                        y: samples.map((sample) => sample.cpuUtil),
                        type: 'scattergl',
                        mode: 'lines',
                        name: 'CPU %',
                        line: { color: '#22c55e', width: 1.15, shape: 'linear' },
                        hovertemplate: 'CPU %{y:.1f}%<extra></extra>',
                    }}
                    rightSeries={{
                        x: samples.map((sample) => sample.timestamp),
                        y: samples.map((sample) => sample.cpuPower),
                        type: 'scattergl',
                        mode: 'lines',
                        name: 'Package W',
                        yaxis: 'y2',
                        line: { color: '#f97316', width: 1.05, shape: 'linear' },
                        connectgaps: true,
                        hovertemplate: 'Package %{y:.0f} W<extra></extra>',
                    }}
                />
            </div>
        </PanelFrame>
    );
}

function RamPanel({ current, samples }: { current: SystemStatus['ram']; samples: LiveSample[] }) {
    const summaryItems = buildRamSummaryItems(current);

    return (
        <PanelFrame
            title="System Memory"
        >
            <SummaryStrip items={summaryItems} />

            <div className="h-72">
                <TimeSeriesPlot
                    height={288}
                    samples={samples}
                    leftAxis={{ title: 'Used GB', color: '#38bdf8', decimals: 1 }}
                    rightAxis={{ title: 'RAM %', color: '#c084fc', range: [0, 100], suffix: '%' }}
                    leftSeries={{
                        x: samples.map((sample) => sample.timestamp),
                        y: samples.map((sample) => sample.ramUsed),
                        type: 'scattergl',
                        mode: 'lines',
                        name: 'RAM Used GB',
                        line: { color: '#38bdf8', width: 1.15, shape: 'linear' },
                        hovertemplate: 'Used %{y:.1f} GB<extra></extra>',
                    }}
                    rightSeries={{
                        x: samples.map((sample) => sample.timestamp),
                        y: samples.map((sample) => sample.ramUtil),
                        type: 'scattergl',
                        mode: 'lines',
                        name: 'RAM %',
                        yaxis: 'y2',
                        line: { color: '#c084fc', width: 1.05, shape: 'linear' },
                        hovertemplate: 'RAM %{y:.1f}%<extra></extra>',
                    }}
                />
            </div>
        </PanelFrame>
    );
}

function GpuPanel({
    gpu,
    samples,
}: {
    gpu: GPUStatus;
    samples: LiveSample[];
}) {
    const summaryItems = buildGpuSummaryItems(gpu);

    return (
        <PanelFrame
            title={gpu.name}
        >
            <SummaryStrip items={summaryItems} />
            <GpuProcessList gpu={gpu} />

            <div className="h-64">
                <TimeSeriesPlot
                    height={256}
                    samples={samples}
                    leftAxis={{ title: 'GPU %', color: '#4ade80', range: [0, 100], suffix: '%' }}
                    rightAxis={{ title: 'VRAM GB', color: '#60a5fa', decimals: 1 }}
                    leftSeries={{
                        x: samples.map((sample) => sample.timestamp),
                        y: samples.map((sample) => sample.gpu[gpu.index]?.util ?? 0),
                        type: 'scattergl',
                        mode: 'lines',
                        name: 'GPU %',
                        line: { color: '#4ade80', width: 1.15, shape: 'linear' },
                        hovertemplate: 'GPU %{y:.0f}%<extra></extra>',
                    }}
                    rightSeries={{
                        x: samples.map((sample) => sample.timestamp),
                        y: samples.map((sample) => sample.gpu[gpu.index]?.vram ?? 0),
                        type: 'scattergl',
                        mode: 'lines',
                        name: 'VRAM GB',
                        yaxis: 'y2',
                        line: { color: '#60a5fa', width: 1.05, shape: 'linear' },
                        hovertemplate: 'VRAM %{y:.1f} GB<extra></extra>',
                    }}
                />
            </div>
        </PanelFrame>
    );
}

function buildSample(payload: SystemStatus): LiveSample {
    const gpu: LiveSample['gpu'] = {};
    payload.gpus.forEach((item) => {
        gpu[item.index] = {
            util: item.utilization,
            vram: Number(((item.memory_used_mb + item.reserved_memory_mb) / 1024).toFixed(3)),
            power: item.power_draw_w,
            temp: item.temperature,
        };
    });

    return {
        timestamp: payload.timestamp,
        timestampMs: Date.parse(payload.timestamp),
        clock: formatClock(payload.timestamp),
        cpuUtil: payload.cpu.utilization,
        cpuPower: payload.cpu.power_watts,
        cpuTemp: payload.cpu.temperature,
        ramUsed: payload.ram.used_gb,
        ramUtil: payload.ram.utilization,
        gpu,
    };
}

export function InfraLiveTelemetry() {
    const [pollIntervalMs, setPollIntervalMs] = useState<PollPreset>(1000);
    const [windowMinutes, setWindowMinutes] = useState<WindowPreset>(3);
    const [samples, setSamples] = useState<LiveSample[]>([]);

    const { data, isLoading, error } = useQuery({
        queryKey: ['infra-live-telemetry', pollIntervalMs],
        queryFn: () => fetchSystemStatus(),
        refetchInterval: pollIntervalMs,
        refetchOnWindowFocus: false,
        staleTime: 0,
    });

    const payload = data?.data;

    useEffect(() => {
        if (!payload) return;
        startTransition(() => {
            setSamples((prev) => {
                if (prev.length > 0 && prev[prev.length - 1]?.timestamp === payload.timestamp) {
                    return prev;
                }
                const next = [...prev, buildSample(payload)];
                const latestTime = next[next.length - 1]?.timestampMs ?? Date.parse(payload.timestamp);
                if (Number.isNaN(latestTime)) {
                    return next;
                }
                const cutoff = latestTime - MAX_WINDOW_RETENTION_MS;
                return next.filter((sample) => sample.timestampMs >= cutoff);
            });
        });
    }, [payload]);

    const latestTimestampMs = samples.length > 0 ? samples[samples.length - 1].timestampMs : NaN;
    const visibleSamples =
        Number.isNaN(latestTimestampMs)
            ? samples
            : samples.filter((sample) => sample.timestampMs >= latestTimestampMs - windowMinutes * 60 * 1000);
    const deferredSamples = useDeferredValue(visibleSamples);

    return (
        <section className="mb-6 rounded-3xl border border-cyan-500/20 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.14),transparent_34%),linear-gradient(180deg,rgba(15,23,42,0.95),rgba(2,6,23,0.98))] p-5 shadow-2xl shadow-black/30">
            <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                        <span
                            className="inline-flex rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-semibold uppercase text-cyan-200"
                            style={{ letterSpacing: '0.18em' }}
                        >
                            Live Overlay
                        </span>
                        <span className="inline-flex rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-200">
                            Live
                        </span>
                    </div>
                    <h2 className="text-3xl font-bold text-slate-100">Live BMS Telemetry</h2>
                    <p className="mt-2 max-w-4xl text-sm text-slate-400">
                        Live CPU, RAM, and per-GPU charts rendered directly from the BMS telemetry API with no external monitor in the loop.
                    </p>
                </div>

                <div className="flex flex-wrap items-start gap-4">
                    <SegmentedControl
                        label="Poll"
                        value={pollIntervalMs}
                        options={POLL_PRESETS}
                        onChange={setPollIntervalMs}
                    />
                    <SegmentedControl
                        label="Window"
                        value={windowMinutes}
                        options={WINDOW_PRESETS}
                        onChange={setWindowMinutes}
                    />
                </div>
            </div>

            {isLoading && (
                <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-5 text-sm text-slate-300">
                    Loading live telemetry...
                </div>
            )}

            {error && (
                <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-5 text-sm text-red-200">
                    Failed to fetch live telemetry from the BMS API.
                </div>
            )}

            {payload && (
                <div className="space-y-6">
                    <div className="grid gap-6 xl:grid-cols-2">
                        <CpuPanel current={payload.cpu} samples={deferredSamples} />
                        <RamPanel current={payload.ram} samples={deferredSamples} />
                    </div>

                    <div className="grid gap-6 xl:grid-cols-2">
                        {payload.gpus.map((gpu) => (
                            <GpuPanel key={gpu.index} gpu={gpu} samples={deferredSamples} />
                        ))}
                    </div>
                </div>
            )}
        </section>
    );
}

export default InfraLiveTelemetry;
