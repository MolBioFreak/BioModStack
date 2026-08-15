import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    discoverHardware,
    fetchFanControl,
    fetchPowerControl,
    fetchSchedulerConfig,
    fetchSystemStatus,
    fetchTelemetryHistory,
    setFanControl,
    setPowerControlManual,
    toggleGpuDisabled,
} from '../lib/api';
import type { GPUStatus, PerGpuFanStatus, SystemStatus } from '../lib/api';
import { resolveCpuFrequencyScaleMhz, resolveCpuPowerScaleWatts } from './infraTelemetryScaling';
import {
    downsampleTelemetryTail,
    isValidPollPreset,
    loadPersistedTelemetryPreferences,
    mergeMinuteHistoryWithRawTail,
    parseTelemetryTimestampMs,
    persistTelemetryPreferences,
    resolveTelemetryDisplayIntervalMs,
    resolveTelemetryGapBreakMs,
    resolveTelemetryWindowBounds,
} from './infraTelemetryHistory';
import type {
    LiveSample,
    PollPreset,
    WindowPreset,
} from './infraTelemetryHistory';
import { jobPollingInterval } from '../lib/queryPolling';

const SHARED_CONTROL_POLL_INTERVAL_MS = 10000;
const MINUTE_LIVE_TAIL_MS = 120_000;
const SHARED_SYSTEM_QUERY_KEY = ['system'];
const SHARED_POWER_CONTROL_QUERY_KEY = ['powerControl'];
const SHARED_FAN_CONTROL_QUERY_KEY = ['fanControl'];
const SHARED_SCHEDULER_CONFIG_QUERY_KEY = ['schedulerConfig'];
const INFRA_LIVE_SHARED_QUERY_KEY = ['infra-live-shared'];

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
    { value: 15, label: '15m' },
    { value: 30, label: '30m' },
    { value: 60, label: '1h' },
];


interface AxisConfig {
    title: string;
    color: string;
    range?: [number, number];
    suffix?: string;
    decimals?: number;
}

interface TimeSeriesLine {
    x?: Array<string | number | null>;
    y?: Array<number | null>;
    customdata?: unknown[];
    name?: string;
    mode?: string;
    line?: {
        color?: string;
        width?: number;
        shape?: string;
        simplify?: boolean;
    };
    hovertemplate?: string;
}

interface TimeSeriesPlotProps {
    height: number;
    samples: LiveSample[];
    yAxis: AxisConfig;
    series: TimeSeriesLine[];
    showXAxisLabels?: boolean;
    traceType?: 'scatter' | 'scattergl';
    compact?: boolean;
    redrawKey?: string | number;
    xDomain?: [number, number];
}


export interface InfraLiveTelemetryProps {
    showXAxisLabels?: boolean;
    defaultPollIntervalMs?: PollPreset;
    defaultWindowMinutes?: WindowPreset;
    variant?: 'infra' | 'dashboard';
    dashboardSize?: 'micro' | 'compact' | 'standard' | 'large' | 'xlarge';
}

interface DashboardSizingConfig {
    compactFrame: boolean;
    controlsGapClass: string;
    layoutGapClass: string;
    outerSpacingClass: string;
    headerGapClass: string;
    cpuPanelHeight: number;
    cpuPlotHeight: number;
    ramPanelHeight: number;
    ramPlotHeight: number;
    gpuPanelHeight: number;
    gpuPlotHeight: number;
}

const DASHBOARD_SIZING: Record<NonNullable<InfraLiveTelemetryProps['dashboardSize']>, DashboardSizingConfig> = {
    micro: {
        compactFrame: true,
        controlsGapClass: 'gap-1',
        layoutGapClass: 'gap-1',
        outerSpacingClass: 'space-y-1',
        headerGapClass: 'gap-2',
        cpuPanelHeight: 118,
        cpuPlotHeight: 118,
        ramPanelHeight: 118,
        ramPlotHeight: 118,
        gpuPanelHeight: 96,
        gpuPlotHeight: 96,
    },
    compact: {
        compactFrame: true,
        controlsGapClass: 'gap-1.5',
        layoutGapClass: 'gap-1.5',
        outerSpacingClass: 'space-y-1.5',
        headerGapClass: 'gap-2.5',
        cpuPanelHeight: 168,
        cpuPlotHeight: 168,
        ramPanelHeight: 168,
        ramPlotHeight: 168,
        gpuPanelHeight: 132,
        gpuPlotHeight: 132,
    },
    standard: {
        compactFrame: true,
        controlsGapClass: 'gap-2',
        layoutGapClass: 'gap-2',
        outerSpacingClass: 'space-y-2',
        headerGapClass: 'gap-4',
        cpuPanelHeight: 228,
        cpuPlotHeight: 228,
        ramPanelHeight: 228,
        ramPlotHeight: 228,
        gpuPanelHeight: 196,
        gpuPlotHeight: 196,
    },
    large: {
        compactFrame: true,
        controlsGapClass: 'gap-2',
        layoutGapClass: 'gap-3',
        outerSpacingClass: 'space-y-3',
        headerGapClass: 'gap-4',
        cpuPanelHeight: 286,
        cpuPlotHeight: 286,
        ramPanelHeight: 286,
        ramPlotHeight: 286,
        gpuPanelHeight: 244,
        gpuPlotHeight: 244,
    },
    xlarge: {
        compactFrame: true,
        controlsGapClass: 'gap-2.5',
        layoutGapClass: 'gap-4',
        outerSpacingClass: 'space-y-4',
        headerGapClass: 'gap-4',
        cpuPanelHeight: 360,
        cpuPlotHeight: 360,
        ramPanelHeight: 360,
        ramPlotHeight: 360,
        gpuPanelHeight: 310,
        gpuPlotHeight: 310,
    },
};


function formatClock(timestamp: string): string {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

function PanelFrame({
    title,
    subtitle,
    children,
    compact = false,
}: {
    title: string;
    subtitle?: string;
    children: React.ReactNode;
    compact?: boolean;
}) {
    return (
        <section className={`rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/88 shadow-2xl shadow-black/10 ${compact ? 'p-3' : 'p-4'}`}>
            <div className={compact ? 'mb-2' : 'mb-4'}>
                <h2 className={`font-semibold text-[var(--text-primary)] ${compact ? 'text-base' : 'text-lg'}`}>{title}</h2>
                {subtitle ? <p className="mt-1 text-sm text-[var(--text-muted)]">{subtitle}</p> : null}
            </div>
            {children}
        </section>
    );
}

const PLOT_GRID = 'rgba(51, 65, 85, 0.42)';
const PLOT_FONT = '#cbd5e1';
const PLOT_TICK = '#94a3b8';
const PLOT_PANEL_BG = 'rgba(15, 23, 42, 0.22)';

const UI_ACCENT = 'var(--accent-primary)';
const UI_SUCCESS = 'var(--success)';
const UI_WARNING = 'var(--warning)';
const UI_LINK = 'var(--link)';

function TotalPowerBar({
    payload,
    currentLimits,
    compact = false,
}: {
    payload: SystemStatus;
    currentLimits: Record<number, number>;
    compact?: boolean;
}) {
    const gpuDraw = payload.gpus.reduce((sum, gpu) => sum + gpu.power_draw_w, 0);
    const cpuDraw = payload.cpu.power_watts ?? 0;
    const totalDraw = gpuDraw + cpuDraw;
    const totalGpuPowerCap = payload.gpus.reduce(
        (sum, gpu) => sum + (currentLimits[gpu.index] ?? gpu.power_limit_w),
        0,
    );
    const cpuCap = resolveCpuPowerScaleWatts(payload.cpu, []) ?? 0;
    const totalCap = totalGpuPowerCap + cpuCap;
    const fillPercent = Math.max(0, Math.min(100, toPercent(totalDraw, totalCap)));
    const cpuPowerLabel = payload.cpu.power_watts != null
        ? ` + CPU ${cpuDraw.toFixed(0)}W`
        : ' + CPU unavailable';
    const capLabel = payload.cpu.power_watts != null
        ? `${totalCap.toFixed(0)}W cap`
        : `GPU cap ${totalGpuPowerCap.toFixed(0)}W`;

    return (
        <section className={`rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/88 shadow-2xl shadow-black/10 ${compact ? 'p-3' : 'p-4'}`}>
            <div className="mb-2 flex items-center justify-between gap-3">
                <div>
                    <h2 className={`font-semibold text-[var(--text-primary)] ${compact ? 'text-sm' : 'text-base'}`}>Total Power Draw</h2>
                    <p className={`text-[var(--text-muted)] ${compact ? 'text-[11px]' : 'text-sm'}`}>
                        GPU {gpuDraw.toFixed(0)}W{cpuPowerLabel} of {capLabel}
                    </p>
                </div>
                <div className="text-right">
                    <div className={`font-semibold text-[var(--text-primary)] ${compact ? 'text-base' : 'text-lg'}`}>
                        {totalDraw.toFixed(0)}W
                    </div>
                    <div className={`text-[var(--text-muted)] ${compact ? 'text-[11px]' : 'text-xs'}`}>
                        {fillPercent.toFixed(0)}%
                    </div>
                </div>
            </div>
            <div className={`overflow-hidden rounded-full border border-[var(--border-primary)] bg-[var(--bg-primary)] ${compact ? 'h-2.5' : 'h-3'}`}>
                <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{
                        width: `${fillPercent}%`,
                        background: `linear-gradient(90deg, ${UI_SUCCESS} 0%, ${UI_WARNING} 72%, #ef4444 100%)`,
                    }}
                />
            </div>
        </section>
    );
}

function SegmentedControl<T extends string | number>({
    label,
    value,
    options,
    onChange,
    compact = false,
}: {
    label: string;
    value: T;
    options: ReadonlyArray<{ value: T; label: string }>;
    onChange: (value: T) => void;
    compact?: boolean;
}) {
    return (
        <div>
            <div
                className={`font-semibold uppercase text-[var(--text-muted)] ${compact ? 'mb-1 text-[9px]' : 'mb-2 text-xs'}`}
                style={{ letterSpacing: '0.16em' }}
            >
                {label}
            </div>
            <div className={`inline-flex border border-[var(--border-primary)] bg-[var(--bg-primary)] ${compact ? 'rounded-lg p-0.5' : 'rounded-xl p-1'}`}>
                {options.map((option) => {
                    const active = option.value === value;
                    return (
                        <button
                            key={String(option.value)}
                            type="button"
                            onClick={() => onChange(option.value)}
                            className={`border font-medium transition-colors ${
                                compact ? 'rounded-md px-2 py-1 text-[10px]' : 'rounded-lg px-3 py-2 text-sm'
                            } ${
                                active
                                    ? 'text-[var(--accent-primary)]'
                                    : 'border-transparent text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]'
                            }`}
                            style={active ? {
                                backgroundColor: 'color-mix(in srgb, var(--accent-primary) 14%, var(--bg-tertiary))',
                                borderColor: 'color-mix(in srgb, var(--accent-primary) 34%, var(--border-primary))',
                            } : undefined}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

function clamp01(value: number): number {
    if (Number.isNaN(value)) return 0;
    return Math.max(0, Math.min(1, value));
}

function sampleGapAllowance(previous: LiveSample, current: LiveSample, defaultGapBreakMs: number): number {
    const previousPoll = isValidPollPreset(previous.pollIntervalMs) ? previous.pollIntervalMs : defaultGapBreakMs;
    const currentPoll = isValidPollPreset(current.pollIntervalMs) ? current.pollIntervalMs : defaultGapBreakMs;
    return Math.max(defaultGapBreakMs, Math.round(previousPoll * 6), Math.round(currentPoll * 6));
}

function shouldBreakBetweenSamples(previous: LiveSample, current: LiveSample, defaultGapBreakMs: number): boolean {
    return current.timestampMs - previous.timestampMs > sampleGapAllowance(previous, current, defaultGapBreakMs);
}

function buildGapAwareTraceData<T = number>(
    samples: LiveSample[],
    gapBreakMs: number,
    valueForSample: (sample: LiveSample) => number | null,
    customForSample?: (sample: LiveSample) => T | null,
): { x: string[]; y: Array<number | null>; customdata?: Array<T | null> } {
    const x: string[] = [];
    const y: Array<number | null> = [];
    const customdata: Array<T | null> = [];

    for (let index = 0; index < samples.length; index += 1) {
        const sample = samples[index];
        const previous = index > 0 ? samples[index - 1] : null;
        if (previous && shouldBreakBetweenSamples(previous, sample, gapBreakMs)) {
            x.push(sample.timestamp);
            y.push(null);
            if (customForSample) customdata.push(null);
        }

        x.push(sample.timestamp);
        y.push(valueForSample(sample));
        if (customForSample) {
            customdata.push(customForSample(sample));
        }
    }

    return customForSample ? { x, y, customdata } : { x, y };
}

function getTempBandColor(temp: number | null): string {
    if (temp == null) return UI_LINK;
    if (temp < 35) return '#3b82f6';
    if (temp < 50) return '#22d3ee';
    if (temp < 65) return '#22c55e';
    if (temp < 75) return '#eab308';
    if (temp < 85) return '#f97316';
    return '#ef4444';
}

function toPercent(value: number, maxValue: number): number {
    if (!Number.isFinite(value) || !Number.isFinite(maxValue) || maxValue <= 0) return 0;
    return clamp01(value / maxValue) * 100;
}

function legendName(label: string, value: string): string {
    return `${label} ${value}`;
}

function formatGpuProcessMemory(memoryMb: number): string {
    if (memoryMb >= 1024) {
        return `${(memoryMb / 1024).toFixed(1)} GB`;
    }
    return `${memoryMb} MB`;
}

function getEffectiveFanMode(fan: PerGpuFanStatus | null | undefined): 'auto' | 'manual' {
    if (!fan) return 'auto';
    return fan.mode === 'manual' || fan.profile_mode === 'manual' ? 'manual' : 'auto';
}

function getEffectiveFanTarget(fan: PerGpuFanStatus | null | undefined): number {
    if (!fan) return 35;
    return fan.target_percent ?? fan.profile_target_percent ?? fan.current_percent ?? fan.min_percent ?? 35;
}

function GpuProcessList({ gpu, compact = false }: { gpu: GPUStatus; compact?: boolean }) {
    const processes = [...gpu.processes].sort((a, b) => b.memory_mb - a.memory_mb);

    if (processes.length === 0) {
        return compact ? null : (
            <div className="mb-4 rounded-xl border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)]/55 px-3 py-3 text-sm text-[var(--text-muted)]">
                No active GPU processes.
            </div>
        );
    }

    if (compact) {
        return (
            <div className="mb-2.5 flex flex-wrap items-center gap-1.5 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/70 px-2.5 py-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
                    Processes
                </span>
                {processes.map((process) => (
                    <div
                        key={`${process.pid}-${process.name}`}
                        className="inline-flex min-w-0 items-center gap-1.5 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/70 px-2 py-1"
                    >
                        <span className="max-w-[170px] truncate text-[11px] font-medium text-[var(--text-primary)]">
                            {process.name}
                        </span>
                        <span className="inline-flex min-w-[3.1rem] items-center justify-center rounded-md border border-accent/20 bg-accent/10 px-1.5 py-0.5 text-center text-[10px] font-medium text-accent">
                            {formatGpuProcessMemory(process.memory_mb)}
                        </span>
                    </div>
                ))}
            </div>
        );
    }

    return (
        <div className="mb-4">
            <div className="mb-2 flex items-center justify-between">
                <div className="text-xs font-semibold uppercase text-[var(--text-muted)]" style={{ letterSpacing: '0.16em' }}>
                    Active Processes
                </div>
                <div className="inline-flex min-w-[2rem] items-center justify-center rounded-md border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)]">
                    {processes.length}
                </div>
            </div>
            <div className={`flex flex-wrap ${compact ? 'gap-1.5' : 'gap-2'}`}>
                {processes.map((process) => (
                    <div
                        key={`${process.pid}-${process.name}`}
                        className={`flex-1 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/70 ${compact ? 'min-w-[135px] px-2.5 py-2' : 'min-w-[180px] px-3 py-3'}`}
                    >
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className={`truncate font-medium text-[var(--text-primary)] ${compact ? 'text-xs' : 'text-sm'}`}>{process.name}</div>
                            </div>
                            <div className={`inline-flex min-w-[3.25rem] items-center justify-center rounded-md border border-accent/20 bg-accent/10 text-center font-medium text-accent ${compact ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-xs'}`}>
                                {formatGpuProcessMemory(process.memory_mb)}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function GpuCompactProcessChips({ gpu }: { gpu: GPUStatus }) {
    const processes = [...gpu.processes].sort((a, b) => b.memory_mb - a.memory_mb);
    if (processes.length === 0) {
        return null;
    }

    const visibleProcesses = processes.slice(0, 5);
    const hiddenCount = Math.max(0, processes.length - visibleProcesses.length);

    return (
        <div className="ml-auto flex min-w-0 flex-1 flex-wrap items-center justify-end gap-1.5">
            {visibleProcesses.map((process) => (
                <div
                    key={`${process.pid}-${process.name}`}
                    className="inline-flex min-w-0 max-w-[132px] items-center gap-1.5 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/65 px-2 py-1"
                >
                    <span className="truncate text-[10px] font-medium text-[var(--text-primary)]">
                        {process.name}
                    </span>
                    <span
                        className="inline-flex min-w-[3rem] items-center justify-center rounded-md px-1.5 py-0.5 text-center text-[9px] font-medium"
                        style={{
                            color: 'var(--accent-primary)',
                            backgroundColor: 'color-mix(in srgb, var(--accent-primary) 12%, transparent)',
                            border: '1px solid color-mix(in srgb, var(--accent-primary) 24%, var(--border-primary))',
                        }}
                    >
                        {formatGpuProcessMemory(process.memory_mb)}
                    </span>
                </div>
            ))}
            {hiddenCount > 0 ? (
                <div className="inline-flex min-w-[2.1rem] items-center justify-center rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1 text-[10px] font-medium text-[var(--text-secondary)]">
                    +{hiddenCount}
                </div>
            ) : null}
        </div>
    );
}

interface GpuInlinePowerControlProps {
    gpu: GPUStatus;
    currentLimit: number;
    disabled: boolean;
    isPending: boolean;
    onSetLimit: (watts: number) => void;
    onToggleDisable: () => void;
    fan?: PerGpuFanStatus | null;
    onSetFanMode?: (mode: 'auto' | 'manual', targetPercent?: number) => void;
    onSetFanTarget?: (targetPercent: number) => void;
    compact?: boolean;
}

function GpuInlinePowerControl({
    gpu,
    currentLimit,
    disabled,
    isPending,
    onSetLimit,
    onToggleDisable,
    fan,
    onSetFanMode,
    onSetFanTarget,
    compact = false,
}: GpuInlinePowerControlProps) {
    const [inputValue, setInputValue] = useState(String(Math.round(currentLimit)));
    const [fanInputValue, setFanInputValue] = useState(String(Math.round(getEffectiveFanTarget(fan))));

    useEffect(() => {
        setInputValue(String(Math.round(currentLimit)));
    }, [currentLimit]);

    useEffect(() => {
        setFanInputValue(String(Math.round(getEffectiveFanTarget(fan))));
    }, [fan]);

    const parsedValue = parseInt(inputValue, 10);
    const isOutOfRange =
        !Number.isNaN(parsedValue) && (parsedValue < gpu.min_power_watts || parsedValue > gpu.max_power_watts);
    const isDirty = parsedValue !== currentLimit;
    const fanMode = getEffectiveFanMode(fan);
    const fanTarget = getEffectiveFanTarget(fan);
    const parsedFanValue = parseInt(fanInputValue, 10);
    const fanMin = fan?.min_percent ?? 30;
    const fanMax = fan?.max_percent ?? 100;
    const fanIsOutOfRange =
        !Number.isNaN(parsedFanValue) && (parsedFanValue < fanMin || parsedFanValue > fanMax);
    const fanIsDirty = !Number.isNaN(parsedFanValue) && parsedFanValue !== fanTarget;
    const powerDraftValue = Number.isNaN(parsedValue) ? currentLimit : parsedValue;
    const fanDraftValue = Number.isNaN(parsedFanValue) ? fanTarget : parsedFanValue;

    const handleApply = () => {
        if (!Number.isNaN(parsedValue) && parsedValue >= gpu.min_power_watts && parsedValue <= gpu.max_power_watts) {
            onSetLimit(parsedValue);
        }
    };

    const stepValue = (delta: number) => {
        const current = Number.isNaN(parsedValue) ? currentLimit : parsedValue;
        const next = Math.min(gpu.max_power_watts, Math.max(gpu.min_power_watts, current + delta));
        setInputValue(String(next));
    };

    const stepFanValue = (delta: number) => {
        const current = Number.isNaN(parsedFanValue) ? fanTarget : parsedFanValue;
        const next = Math.min(fanMax, Math.max(fanMin, current + delta));
        setFanInputValue(String(next));
    };

    const handleApplyFan = () => {
        if (!fan || !onSetFanTarget || Number.isNaN(parsedFanValue) || fanIsOutOfRange) return;
        onSetFanTarget(parsedFanValue);
    };

    const commitPowerSlider = () => {
        if (isPending || isOutOfRange || powerDraftValue === currentLimit) return;
        onSetLimit(powerDraftValue);
    };

    const commitFanSlider = () => {
        if (!fan || !onSetFanTarget || isPending || fanIsOutOfRange || fanDraftValue === fanTarget) return;
        onSetFanTarget(fanDraftValue);
    };

    if (compact) {
        return (
            <div className="mb-1 flex flex-wrap items-center gap-2 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/70 px-2 py-1.5">
                <div className="flex min-w-[16rem] flex-[1.25] items-center gap-2">
                    <span className="inline-flex min-w-[3.2rem] items-center justify-center rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1 text-center text-[10px] font-medium text-[var(--text-secondary)]">
                        {gpu.power_draw_w.toFixed(1)}W
                    </span>
                    <input
                        type="range"
                        min={gpu.min_power_watts}
                        max={gpu.max_power_watts}
                        step={1}
                        value={powerDraftValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onMouseUp={commitPowerSlider}
                        onTouchEnd={commitPowerSlider}
                        onKeyUp={commitPowerSlider}
                        disabled={isPending}
                        className="h-2 w-full min-w-0"
                        style={{ accentColor: 'var(--warning)' }}
                    />
                    <span className="w-9 text-right text-[10px] font-medium text-[var(--text-primary)]">
                        {powerDraftValue}
                    </span>
                    <span className="text-[10px] text-[var(--text-muted)]">cap</span>
                </div>

                {fan ? (
                    <div className="flex min-w-[18rem] flex-[1.4] items-center gap-1.5">
                        <span className="inline-flex min-w-[3.9rem] items-center justify-center rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1 text-center text-[10px] font-medium text-[var(--text-secondary)]">
                            Fan {fan.current_percent != null ? `${fan.current_percent}%` : 'n/a'}
                        </span>
                        <button
                            type="button"
                            onClick={() => onSetFanMode?.('auto', fanTarget)}
                            disabled={isPending || !fan.writable}
                            className={`rounded border px-2 py-1 text-[10px] font-medium transition-colors disabled:opacity-50 ${
                                fanMode === 'auto'
                                    ? 'border-emerald-500/40 bg-emerald-500/12 text-emerald-300'
                                    : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'
                            }`}
                        >
                            Auto
                        </button>
                        <button
                            type="button"
                            onClick={() => onSetFanMode?.('manual', fanDraftValue)}
                            disabled={isPending || !fan.writable}
                            className={`rounded border px-2 py-1 text-[10px] font-medium transition-colors disabled:opacity-50 ${
                                fanMode === 'manual'
                                    ? 'border-amber-500/40 bg-amber-500/12 text-amber-300'
                                    : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'
                            }`}
                        >
                            Manual
                        </button>
                        <input
                            type="range"
                            min={fanMin}
                            max={fanMax}
                            step={1}
                            value={fanDraftValue}
                            onChange={(e) => setFanInputValue(e.target.value)}
                            onMouseUp={commitFanSlider}
                            onTouchEnd={commitFanSlider}
                            onKeyUp={commitFanSlider}
                            disabled={isPending || !fan.writable}
                            className="h-2 w-full min-w-0 disabled:opacity-50"
                            style={{ accentColor: 'var(--accent-primary)' }}
                        />
                        <span className="w-7 text-right text-[10px] font-medium text-[var(--text-primary)]">
                            {fanDraftValue}
                        </span>
                        <span className="text-[10px] text-[var(--text-muted)]">fan</span>
                    </div>
                ) : null}

                <GpuCompactProcessChips gpu={gpu} />
                <button
                    type="button"
                    onClick={onToggleDisable}
                    className="rounded px-2 py-1 text-[10px] font-medium transition-colors"
                    style={disabled ? {
                        color: 'var(--success)',
                        backgroundColor: 'color-mix(in srgb, var(--success) 14%, transparent)',
                    } : {
                        color: 'var(--error)',
                        backgroundColor: 'color-mix(in srgb, var(--error) 14%, transparent)',
                    }}
                >
                    {disabled ? 'Enable' : 'Disable'}
                </button>
            </div>
        );
    }

    return (
        <div className={`flex flex-wrap items-center rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/70 ${compact ? 'mb-1 gap-1.5 px-2 py-1' : 'mb-2.5 gap-1.5 px-2.5 py-2'}`}>
            <span className={`inline-flex min-w-[3.4rem] items-center justify-center rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1 text-center font-medium text-[var(--text-secondary)] ${compact ? 'text-[10px]' : 'text-[11px]'}`}>
                {gpu.power_draw_w.toFixed(1)}W
            </span>
            <button
                type="button"
                onClick={() => stepValue(-5)}
                className={`rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)] ${compact ? 'h-[1.375rem] w-[1.375rem] text-[10px]' : 'h-6 w-6 text-[11px]'}`}
            >
                −
            </button>
            <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value.replace(/[^0-9]/g, ''))}
                className={`rounded border bg-[var(--bg-tertiary)] px-2 py-1 text-center text-[var(--text-primary)] ${compact ? 'w-12 text-[10px]' : 'w-[3.25rem] text-[11px]'} ${
                    isOutOfRange ? 'border-red-500' : isDirty ? 'border-yellow-500' : 'border-[var(--border-primary)]'
                }`}
            />
            <button
                type="button"
                onClick={() => stepValue(5)}
                className={`rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)] ${compact ? 'h-[1.375rem] w-[1.375rem] text-[10px]' : 'h-6 w-6 text-[11px]'}`}
            >
                +
            </button>
            <span className={`text-[var(--text-muted)] ${compact ? 'text-[10px]' : 'text-[11px]'}`}>cap</span>
            {isDirty ? (
                <button
                    type="button"
                    onClick={handleApply}
                    disabled={isPending || isOutOfRange}
                    className={`rounded px-2 py-1 font-medium disabled:opacity-50 ${compact ? 'text-[10px]' : 'text-[11px]'}`}
                    style={{
                        color: 'var(--accent-primary)',
                        backgroundColor: 'color-mix(in srgb, var(--accent-primary) 14%, transparent)',
                    }}
                >
                    Apply
                </button>
            ) : null}
            {fan ? (
                <>
                    <span className={`inline-flex min-w-[4rem] items-center justify-center rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1 text-center font-medium text-[var(--text-secondary)] ${compact ? 'text-[10px]' : 'text-[11px]'}`}>
                        Fan {fan.current_percent != null ? `${fan.current_percent}%` : 'n/a'}
                    </span>
                    <button
                        type="button"
                        onClick={() => onSetFanMode?.('auto', fanTarget)}
                        disabled={isPending || !fan.writable}
                        className={`rounded border px-2 py-1 font-medium transition-colors disabled:opacity-50 ${compact ? 'text-[10px]' : 'text-[11px]'} ${
                            fanMode === 'auto'
                                ? 'border-emerald-500/40 bg-emerald-500/12 text-emerald-300'
                                : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'
                        }`}
                    >
                        Auto
                    </button>
                    <button
                        type="button"
                        onClick={() => onSetFanMode?.('manual', parsedFanValue || fanTarget)}
                        disabled={isPending || !fan.writable}
                        className={`rounded border px-2 py-1 font-medium transition-colors disabled:opacity-50 ${compact ? 'text-[10px]' : 'text-[11px]'} ${
                            fanMode === 'manual'
                                ? 'border-amber-500/40 bg-amber-500/12 text-amber-300'
                                : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'
                        }`}
                    >
                        Manual
                    </button>
                    <button
                        type="button"
                        onClick={() => stepFanValue(-5)}
                        disabled={isPending || !fan.writable}
                        className={`rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)] disabled:opacity-50 ${compact ? 'h-[1.375rem] w-[1.375rem] text-[10px]' : 'h-6 w-6 text-[11px]'}`}
                    >
                        −
                    </button>
                    <input
                        type="text"
                        inputMode="numeric"
                        pattern="[0-9]*"
                        value={fanInputValue}
                        onChange={(e) => setFanInputValue(e.target.value.replace(/[^0-9]/g, ''))}
                        disabled={isPending || !fan.writable}
                        className={`rounded border bg-[var(--bg-tertiary)] px-2 py-1 text-center text-[var(--text-primary)] disabled:opacity-50 ${compact ? 'w-10 text-[10px]' : 'w-[3rem] text-[11px]'} ${
                            fanIsOutOfRange ? 'border-red-500' : fanIsDirty ? 'border-yellow-500' : 'border-[var(--border-primary)]'
                        }`}
                    />
                    <button
                        type="button"
                        onClick={() => stepFanValue(5)}
                        disabled={isPending || !fan.writable}
                        className={`rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)] disabled:opacity-50 ${compact ? 'h-[1.375rem] w-[1.375rem] text-[10px]' : 'h-6 w-6 text-[11px]'}`}
                    >
                        +
                    </button>
                    <span className={`text-[var(--text-muted)] ${compact ? 'text-[10px]' : 'text-[11px]'}`}>fan</span>
                    {fanIsDirty ? (
                        <button
                            type="button"
                            onClick={handleApplyFan}
                            disabled={isPending || fanIsOutOfRange || !fan.writable}
                            className={`rounded px-2 py-1 font-medium disabled:opacity-50 ${compact ? 'text-[10px]' : 'text-[11px]'}`}
                            style={{
                                color: 'var(--accent-primary)',
                                backgroundColor: 'color-mix(in srgb, var(--accent-primary) 14%, transparent)',
                            }}
                        >
                            Apply
                        </button>
                    ) : null}
                </>
            ) : null}
            {compact ? <GpuCompactProcessChips gpu={gpu} /> : null}
            <button
                type="button"
                onClick={onToggleDisable}
                className={`rounded px-2 py-1 font-medium transition-colors ${compact ? 'text-[10px]' : 'text-[11px]'}`}
                style={disabled ? {
                    color: 'var(--success)',
                    backgroundColor: 'color-mix(in srgb, var(--success) 14%, transparent)',
                } : {
                    color: 'var(--error)',
                    backgroundColor: 'color-mix(in srgb, var(--error) 14%, transparent)',
                }}
            >
                {disabled ? 'Enable' : 'Disable'}
            </button>
        </div>
    );
}

function parseSeriesTimestamp(value: string | number | null | undefined): number | null {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string') return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function buildTelemetrySvgPath(
    line: TimeSeriesLine,
    xMin: number,
    xMax: number,
    yMin: number,
    yMax: number,
): string {
    const xValues = line.x ?? [];
    const yValues = line.y ?? [];
    const xRange = Math.max(1, xMax - xMin);
    const yRange = Math.max(Number.EPSILON, yMax - yMin);
    let path = '';
    let drawing = false;

    for (let index = 0; index < Math.min(xValues.length, yValues.length); index += 1) {
        const timestampMs = parseSeriesTimestamp(xValues[index]);
        const value = yValues[index];
        if (timestampMs == null || value == null || !Number.isFinite(value)) {
            drawing = false;
            continue;
        }

        const x = Math.max(0, Math.min(1000, ((timestampMs - xMin) / xRange) * 1000));
        const y = Math.max(0, Math.min(100, 100 - ((value - yMin) / yRange) * 100));
        path += `${drawing ? ' L' : ' M'} ${x.toFixed(2)} ${y.toFixed(2)}`;
        drawing = true;
    }

    return path;
}

function formatAxisValue(value: number, axis: AxisConfig): string {
    const decimals = axis.decimals ?? (Number.isInteger(value) ? 0 : 1);
    return `${value.toFixed(decimals)}${axis.suffix ?? ''}`;
}

function TimeSeriesPlot({
    height,
    samples,
    yAxis,
    series,
    showXAxisLabels = true,
    compact = false,
    xDomain,
}: TimeSeriesPlotProps) {
    const firstTimestampMs = samples[0]?.timestampMs ?? Date.now() - 1;
    const lastTimestampMs = samples[samples.length - 1]?.timestampMs ?? firstTimestampMs + 1;
    const xMin = xDomain?.[0] ?? Math.min(firstTimestampMs, lastTimestampMs - 1);
    const xMax = xDomain?.[1] ?? Math.max(lastTimestampMs, firstTimestampMs + 1);
    const finiteValues = series.flatMap((line) => (line.y ?? []).filter(
        (value): value is number => value != null && Number.isFinite(value),
    ));
    const yMin = yAxis.range?.[0] ?? (finiteValues.length > 0 ? Math.min(...finiteValues) : 0);
    const computedYMax = yAxis.range?.[1] ?? (finiteValues.length > 0 ? Math.max(...finiteValues) : 1);
    const yMax = computedYMax > yMin ? computedYMax : yMin + 1;
    const middleY = yMin + (yMax - yMin) / 2;
    const legendHeight = compact ? 22 : 26;
    const xLabelHeight = showXAxisLabels ? 18 : 4;

    return (
        <div
            className="relative w-full overflow-hidden rounded-lg"
            style={{ height, background: PLOT_PANEL_BG }}
            role="img"
            aria-label={`${yAxis.title} telemetry history`}
        >
            <div
                className={`absolute left-11 right-2 top-1 z-10 flex min-w-0 items-center overflow-hidden whitespace-nowrap ${compact ? 'gap-2 text-[9px]' : 'gap-3 text-[10px]'}`}
                style={{ color: PLOT_FONT }}
            >
                {series.map((line, index) => (
                    <span key={`${line.name ?? 'series'}:${index}`} className="inline-flex min-w-0 items-center gap-1">
                        <span
                            className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                            style={{ background: line.line?.color ?? UI_LINK }}
                        />
                        <span className="truncate">{line.name ?? `Series ${index + 1}`}</span>
                    </span>
                ))}
            </div>

            <div
                className="absolute bottom-1 left-1 top-6 flex w-9 flex-col justify-between text-right text-[9px] tabular-nums"
                style={{ color: PLOT_TICK }}
                aria-hidden="true"
            >
                <span>{formatAxisValue(yMax, yAxis)}</span>
                <span>{formatAxisValue(middleY, yAxis)}</span>
                <span>{formatAxisValue(yMin, yAxis)}</span>
            </div>

            <div
                className="absolute left-11 right-2"
                style={{ top: legendHeight, bottom: xLabelHeight }}
            >
                <svg
                    className="h-full w-full"
                    viewBox="0 0 1000 100"
                    preserveAspectRatio="none"
                    aria-hidden="true"
                >
                    {[0, 25, 50, 75, 100].map((y) => (
                        <line
                            key={`h:${y}`}
                            x1="0"
                            x2="1000"
                            y1={y}
                            y2={y}
                            stroke={PLOT_GRID}
                            strokeWidth="1"
                            vectorEffect="non-scaling-stroke"
                        />
                    ))}
                    {[0, 200, 400, 600, 800, 1000].map((x) => (
                        <line
                            key={`v:${x}`}
                            x1={x}
                            x2={x}
                            y1="0"
                            y2="100"
                            stroke={PLOT_GRID}
                            strokeWidth="1"
                            vectorEffect="non-scaling-stroke"
                        />
                    ))}
                    {series.map((line, index) => {
                        const path = buildTelemetrySvgPath(line, xMin, xMax, yMin, yMax);
                        return path ? (
                            <path
                                key={`${line.name ?? 'series'}:${index}`}
                                d={path}
                                fill="none"
                                stroke={line.line?.color ?? UI_LINK}
                                strokeWidth={line.line?.width ?? 1.5}
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                vectorEffect="non-scaling-stroke"
                            />
                        ) : null;
                    })}
                </svg>
            </div>

            {showXAxisLabels ? (
                <div
                    className="absolute bottom-0 left-11 right-2 flex justify-between text-[9px] tabular-nums"
                    style={{ color: PLOT_TICK }}
                    aria-hidden="true"
                >
                    <span>{samples[0]?.clock ?? '--:--:--'}</span>
                    <span>{samples[samples.length - 1]?.clock ?? '--:--:--'}</span>
                </div>
            ) : null}
        </div>
    );
}

function CpuPanel({
    current,
    samples,
    showXAxisLabels,
    compact = false,
    panelHeight,
    plotHeight,
    traceType = 'scatter',
    gapBreakMs,
    redrawKey,
    xDomain,
}: {
    current: SystemStatus['cpu'];
    samples: LiveSample[];
    showXAxisLabels: boolean;
    compact?: boolean;
    panelHeight?: number;
    plotHeight?: number;
    traceType?: 'scatter' | 'scattergl';
    gapBreakMs: number;
    redrawKey: string | number;
    xDomain: [number, number];
}) {
    const powerScale = resolveCpuPowerScaleWatts(current, samples);
    const frequencyScale = resolveCpuFrequencyScaleMhz(current, samples);
    const tempColor = getTempBandColor(current.temperature);
    const cpuPowerTelemetry = current.power_telemetry;
    const scaleSubtitle = [
        powerScale != null ? `Power scale ${powerScale.toFixed(0)}W` : null,
        `Freq scale ${(frequencyScale / 1000).toFixed(2)}GHz`,
    ].filter(Boolean).join(' · ');
    const cpuPowerSubtitle = cpuPowerTelemetry && !cpuPowerTelemetry.available
        ? `CPU power n/a: ${cpuPowerTelemetry.message}`
        : scaleSubtitle;
    const cpuUtilTrace = buildGapAwareTraceData(samples, gapBreakMs, (sample) => sample.cpuUtil);
    const cpuFreqTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => toPercent(sample.cpuFreqMhz, frequencyScale),
        (sample) => sample.cpuFreqMhz / 1000,
    );
    const cpuPowerTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => sample.cpuPower == null || powerScale == null ? null : toPercent(sample.cpuPower, powerScale),
        (sample) => sample.cpuPower == null ? null : sample.cpuPower,
    );
    const cpuTempTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => sample.cpuTemp ?? null,
        (sample) => sample.cpuTemp ?? null,
    );

    return (
        <PanelFrame title={current.name} subtitle={cpuPowerSubtitle} compact={compact}>
            <div style={{ height: panelHeight ?? (compact ? 270 : 288) }}>
                <TimeSeriesPlot
                    height={plotHeight ?? (compact ? 270 : 288)}
                    samples={samples}
                    yAxis={{ title: 'Scale %', color: PLOT_TICK, range: [0, 100], suffix: '%' }}
                    compact={compact}
                    redrawKey={redrawKey}
                    xDomain={xDomain}
                    series={[
                        {
                            x: cpuUtilTrace.x,
                            y: cpuUtilTrace.y,
                            mode: 'lines',
                            name: legendName('Util', `${current.utilization.toFixed(1)}%`),
                            line: { color: UI_SUCCESS, width: 1.55, shape: 'linear', simplify: false },
                            hovertemplate: 'CPU %{y:.1f}%<extra></extra>',
                        },
                        {
                            x: cpuFreqTrace.x,
                            y: cpuFreqTrace.y,
                            customdata: cpuFreqTrace.customdata,
                            mode: 'lines',
                            name: legendName('Freq', `${(current.frequency_current_mhz / 1000).toFixed(2)} GHz`),
                            line: { color: UI_LINK, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Freq %{customdata:.2f} GHz<extra></extra>',
                        },
                        {
                            x: cpuPowerTrace.x,
                            y: cpuPowerTrace.y,
                            customdata: cpuPowerTrace.customdata,
                            mode: 'lines',
                            name: legendName('Power', current.power_watts != null ? `${current.power_watts.toFixed(0)}W` : 'n/a'),
                            line: { color: UI_WARNING, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Package %{customdata:.0f} W<extra></extra>',
                        },
                        {
                            x: cpuTempTrace.x,
                            y: cpuTempTrace.y,
                            customdata: cpuTempTrace.customdata,
                            mode: 'lines',
                            name: legendName('Temp', current.temperature != null ? `${current.temperature.toFixed(1)}C` : 'n/a'),
                            line: { color: tempColor, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Temp %{customdata:.1f} C<extra></extra>',
                        },
                    ]}
                    showXAxisLabels={showXAxisLabels}
                    traceType={traceType}
                />
            </div>
        </PanelFrame>
    );
}

function RamPanel({
    current,
    samples,
    showXAxisLabels,
    compact = false,
    panelHeight,
    plotHeight,
    traceType = 'scatter',
    gapBreakMs,
    redrawKey,
    xDomain,
}: {
    current: SystemStatus['ram'];
    samples: LiveSample[];
    showXAxisLabels: boolean;
    compact?: boolean;
    panelHeight?: number;
    plotHeight?: number;
    traceType?: 'scatter' | 'scattergl';
    gapBreakMs: number;
    redrawKey: string | number;
    xDomain: [number, number];
}) {
    const ramUsedTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => toPercent(sample.ramUsed, Math.max(current.total_gb, 1)),
        (sample) => sample.ramUsed,
    );
    const ramFreeTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => toPercent(sample.ramFree, Math.max(current.total_gb, 1)),
        (sample) => sample.ramFree,
    );
    const ramUtilTrace = buildGapAwareTraceData(samples, gapBreakMs, (sample) => sample.ramUtil);
    const ramSwapTrace = buildGapAwareTraceData(samples, gapBreakMs, (sample) => sample.ramSwap);

    return (
        <PanelFrame title="System Memory" compact={compact}>
            <div style={{ height: panelHeight ?? (compact ? 270 : 288) }}>
                <TimeSeriesPlot
                    height={plotHeight ?? (compact ? 270 : 288)}
                    samples={samples}
                    yAxis={{ title: 'Scale %', color: PLOT_TICK, range: [0, 100], suffix: '%' }}
                    compact={compact}
                    redrawKey={redrawKey}
                    xDomain={xDomain}
                    series={[
                        {
                            x: ramUsedTrace.x,
                            y: ramUsedTrace.y,
                            customdata: ramUsedTrace.customdata,
                            mode: 'lines',
                            name: legendName('Used', `${current.used_gb.toFixed(1)} GB`),
                            line: { color: UI_LINK, width: 1.55, shape: 'linear', simplify: false },
                            hovertemplate: 'Used %{customdata:.1f} GB<extra></extra>',
                        },
                        {
                            x: ramFreeTrace.x,
                            y: ramFreeTrace.y,
                            customdata: ramFreeTrace.customdata,
                            mode: 'lines',
                            name: legendName('Free', `${current.available_gb.toFixed(1)} GB`),
                            line: { color: UI_SUCCESS, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Free %{customdata:.1f} GB<extra></extra>',
                        },
                        {
                            x: ramUtilTrace.x,
                            y: ramUtilTrace.y,
                            mode: 'lines',
                            name: legendName('Util', `${current.utilization.toFixed(1)}%`),
                            line: { color: UI_WARNING, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'RAM %{y:.1f}%<extra></extra>',
                        },
                        {
                            x: ramSwapTrace.x,
                            y: ramSwapTrace.y,
                            mode: 'lines',
                            name: legendName('Swap', `${current.swap_percent.toFixed(1)}%`),
                            line: { color: UI_ACCENT, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Swap %{y:.1f}%<extra></extra>',
                        },
                    ]}
                    showXAxisLabels={showXAxisLabels}
                    traceType={traceType}
                />
            </div>
        </PanelFrame>
    );
}

function GpuPanel({
    gpu,
    samples,
    showXAxisLabels,
    compact = false,
    panelHeight,
    plotHeight,
    traceType = 'scatter',
    powerControls,
    gapBreakMs,
    redrawKey,
    xDomain,
}: {
    gpu: GPUStatus;
    samples: LiveSample[];
    showXAxisLabels: boolean;
    compact?: boolean;
    panelHeight?: number;
    plotHeight?: number;
    traceType?: 'scatter' | 'scattergl';
    powerControls?: GpuInlinePowerControlProps;
    gapBreakMs: number;
    redrawKey: string | number;
    xDomain: [number, number];
}) {
    const totalGb = gpu.memory_total_mb / 1024;
    const currentVramGb = (gpu.memory_used_mb + gpu.reserved_memory_mb) / 1024;
    const powerLimit = powerControls?.currentLimit ?? (gpu.power_limit_w > 0 ? gpu.power_limit_w : Math.max(gpu.max_power_watts, 1));
    const tempColor = getTempBandColor(gpu.temperature);
    const gpuUtilTrace = buildGapAwareTraceData(samples, gapBreakMs, (sample) => sample.gpu[gpu.index]?.util ?? null);
    const gpuVramTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => toPercent(sample.gpu[gpu.index]?.vram ?? 0, Math.max(totalGb, 1)),
        (sample) => sample.gpu[gpu.index]?.vram ?? null,
    );
    const gpuPowerTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => toPercent(sample.gpu[gpu.index]?.power ?? 0, powerLimit),
        (sample) => sample.gpu[gpu.index]?.power ?? null,
    );
    const gpuTempTrace = buildGapAwareTraceData(
        samples,
        gapBreakMs,
        (sample) => sample.gpu[gpu.index]?.temp ?? null,
        (sample) => sample.gpu[gpu.index]?.temp ?? null,
    );

    return (
        <PanelFrame title={gpu.name} compact={compact}>
            {powerControls ? <GpuInlinePowerControl {...powerControls} compact={compact} /> : null}
            {!compact ? <GpuProcessList gpu={gpu} compact={compact} /> : null}
            {compact && !powerControls ? <GpuProcessList gpu={gpu} compact /> : null}

            <div style={{ height: panelHeight ?? (compact ? 240 : 256) }}>
                <TimeSeriesPlot
                    height={plotHeight ?? (compact ? 240 : 256)}
                    samples={samples}
                    yAxis={{ title: 'Scale %', color: PLOT_TICK, range: [0, 100], suffix: '%' }}
                    compact={compact}
                    redrawKey={redrawKey}
                    xDomain={xDomain}
                    series={[
                        {
                            x: gpuUtilTrace.x,
                            y: gpuUtilTrace.y,
                            mode: 'lines',
                            name: legendName('Util', `${gpu.utilization.toFixed(0)}%`),
                            line: { color: UI_SUCCESS, width: 1.55, shape: 'linear', simplify: false },
                            hovertemplate: 'GPU %{y:.0f}%<extra></extra>',
                        },
                        {
                            x: gpuVramTrace.x,
                            y: gpuVramTrace.y,
                            customdata: gpuVramTrace.customdata,
                            mode: 'lines',
                            name: legendName('VRAM', `${currentVramGb.toFixed(1)} / ${totalGb.toFixed(0)} GB`),
                            line: { color: UI_LINK, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'VRAM %{customdata:.1f} GB<extra></extra>',
                        },
                        {
                            x: gpuPowerTrace.x,
                            y: gpuPowerTrace.y,
                            customdata: gpuPowerTrace.customdata,
                            mode: 'lines',
                            name: legendName('Power', `${gpu.power_draw_w.toFixed(1)}W`),
                            line: { color: UI_WARNING, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Power %{customdata:.1f} W<extra></extra>',
                        },
                        {
                            x: gpuTempTrace.x,
                            y: gpuTempTrace.y,
                            customdata: gpuTempTrace.customdata,
                            mode: 'lines',
                            name: legendName('Temp', `${gpu.temperature.toFixed(0)}C`),
                            line: { color: tempColor, width: 1.4, shape: 'linear', simplify: false },
                            hovertemplate: 'Temp %{customdata:.0f} C<extra></extra>',
                        },
                    ]}
                    showXAxisLabels={showXAxisLabels}
                    traceType={traceType}
                />
            </div>
        </PanelFrame>
    );
}

function buildSample(payload: SystemStatus, pollIntervalMs: PollPreset, authoritativeTimestampMs?: number): LiveSample {
    const gpu: LiveSample['gpu'] = {};
    const timestampMs = authoritativeTimestampMs ?? parseTelemetryTimestampMs(payload.timestamp);
    payload.gpus.forEach((item) => {
        gpu[item.index] = {
            util: item.utilization,
            vram: Number(((item.memory_used_mb + item.reserved_memory_mb) / 1024).toFixed(3)),
            power: item.power_draw_w,
            temp: item.temperature,
        };
    });

    return {
        timestamp: new Date(timestampMs).toISOString(),
        timestampMs,
        pollIntervalMs,
        clock: formatClock(new Date(timestampMs).toISOString()),
        cpuUtil: payload.cpu.utilization,
        cpuFreqMhz: payload.cpu.frequency_current_mhz,
        cpuPower: payload.cpu.power_watts,
        cpuTemp: payload.cpu.temperature,
        ramUsed: payload.ram.used_gb,
        ramFree: payload.ram.available_gb,
        ramUtil: payload.ram.utilization,
        ramSwap: payload.ram.swap_percent,
        gpu,
    };
}

export function InfraControlStateCollector() {
    useQuery({
        queryKey: SHARED_POWER_CONTROL_QUERY_KEY,
        queryFn: fetchPowerControl,
        refetchInterval: (query) => jobPollingInterval(SHARED_CONTROL_POLL_INTERVAL_MS, query),
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    useQuery({
        queryKey: SHARED_FAN_CONTROL_QUERY_KEY,
        queryFn: fetchFanControl,
        refetchInterval: (query) => jobPollingInterval(SHARED_CONTROL_POLL_INTERVAL_MS, query),
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    useQuery({
        queryKey: SHARED_SCHEDULER_CONFIG_QUERY_KEY,
        queryFn: fetchSchedulerConfig,
        refetchInterval: (query) => jobPollingInterval(SHARED_CONTROL_POLL_INTERVAL_MS, query),
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    return null;
}

export function InfraLiveTelemetry({
    showXAxisLabels = true,
    defaultPollIntervalMs = 1000,
    defaultWindowMinutes = 3,
    variant = 'infra',
    dashboardSize = 'standard',
}: InfraLiveTelemetryProps = {}) {
    const compact = variant === 'dashboard';
    const dashboardSizing = DASHBOARD_SIZING[dashboardSize];
    // Use SVG scatter everywhere here. The dashboard/infra charts are modest in size,
    // and avoiding Plotly's WebGL path is materially more stable under heavy browser load.
    const traceType: 'scatter' | 'scattergl' = 'scatter';
    const queryClient = useQueryClient();
    const [restoredState] = useState(() =>
        loadPersistedTelemetryPreferences(defaultPollIntervalMs, defaultWindowMinutes),
    );
    const [pollIntervalMs, setPollIntervalMs] = useState<PollPreset>(restoredState.pollIntervalMs);
    const [windowMinutes, setWindowMinutes] = useState<WindowPreset>(restoredState.windowMinutes);
    const resolution = windowMinutes >= 10 ? 'minute' : 'raw';
    const usesRangeAwareDisplay = windowMinutes >= 15;
    const displayIntervalMs = resolveTelemetryDisplayIntervalMs(windowMinutes, pollIntervalMs);
    const historyQuery = useQuery({
        queryKey: ['immutable-telemetry-history', windowMinutes],
        queryFn: async () => {
            const nowMs = Date.now();
            const requestEndMs = nowMs + 1_000;
            const [stableStartMs, stableEndMs] = resolveTelemetryWindowBounds(
                nowMs,
                windowMinutes,
                displayIntervalMs,
            );
            const startMs = usesRangeAwareDisplay
                ? stableStartMs
                : requestEndMs - windowMinutes * 60_000;
            if (resolution === 'raw') {
                return fetchTelemetryHistory(startMs, requestEndMs, 'raw', 4000);
            }
            const rawTailStartMs = Math.max(startMs, requestEndMs - MINUTE_LIVE_TAIL_MS);
            const [minuteHistory, rawTail] = await Promise.all([
                fetchTelemetryHistory(startMs, requestEndMs, 'minute', 4000),
                fetchTelemetryHistory(rawTailStartMs, requestEndMs, 'raw', 4000),
            ]);
            const rawTailPoints = usesRangeAwareDisplay
                ? downsampleTelemetryTail(rawTail.data.points, displayIntervalMs)
                : rawTail.data.points;
            return {
                ...minuteHistory,
                data: {
                    ...minuteHistory.data,
                    start_ms: usesRangeAwareDisplay ? stableStartMs : minuteHistory.data.start_ms,
                    end_ms: usesRangeAwareDisplay ? stableEndMs : minuteHistory.data.end_ms,
                    generated_at_ms: rawTail.data.generated_at_ms,
                    points: mergeMinuteHistoryWithRawTail(minuteHistory.data.points, rawTailPoints),
                },
            };
        },
        refetchInterval: displayIntervalMs,
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: !usesRangeAwareDisplay,
    });
    const historyPoints = historyQuery.data?.data.points ?? [];
    const samples = historyPoints.map((point) => buildSample(point.payload, 1000, point.timestamp_ms));
    const latestPoint = historyPoints.at(-1);
    const payload = latestPoint?.payload;
    const staleAfterMs = windowMinutes >= 10 ? 120_000 : Math.max(10_000, pollIntervalMs * 5);
    const historyIsStale = Boolean(
        latestPoint && (historyQuery.data?.data.generated_at_ms ?? Date.now()) - latestPoint.timestamp_ms > staleAfterMs,
    );
    const xDomain: [number, number] = [
        historyQuery.data?.data.start_ms ?? Date.now() - windowMinutes * 60_000,
        historyQuery.data?.data.end_ms ?? Date.now(),
    ];

    const { data: powerControlData } = useQuery({
        queryKey: SHARED_POWER_CONTROL_QUERY_KEY,
        queryFn: fetchPowerControl,
        enabled: false,
        staleTime: Infinity,
    });

    const { data: fanControlData } = useQuery({
        queryKey: SHARED_FAN_CONTROL_QUERY_KEY,
        queryFn: fetchFanControl,
        enabled: false,
        staleTime: Infinity,
    });

    const { data: schedulerConfigData } = useQuery({
        queryKey: SHARED_SCHEDULER_CONFIG_QUERY_KEY,
        queryFn: fetchSchedulerConfig,
        enabled: false,
        staleTime: Infinity,
    });

    const manualMutation = useMutation({
        mutationFn: ({ gpuIndex, limitWatts }: { gpuIndex: number; limitWatts: number }) =>
            setPowerControlManual(gpuIndex, limitWatts),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: SHARED_POWER_CONTROL_QUERY_KEY });
            queryClient.invalidateQueries({ queryKey: SHARED_SYSTEM_QUERY_KEY });
        },
    });

    const toggleDisableMutation = useMutation({
        mutationFn: (gpuId: number) => toggleGpuDisabled(gpuId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: SHARED_SCHEDULER_CONFIG_QUERY_KEY });
        },
    });

    const fanMutation = useMutation({
        mutationFn: ({
            gpuIndex,
            mode,
            targetPercent,
        }: {
            gpuIndex: number;
            mode: 'auto' | 'manual';
            targetPercent?: number;
        }) => setFanControl(gpuIndex, mode, targetPercent),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: SHARED_FAN_CONTROL_QUERY_KEY });
        },
    });

    const discoverMutation = useMutation({
        mutationFn: async () => {
            const discovery = await discoverHardware();
            const system = await fetchSystemStatus();
            return { discovery, system };
        },
        onSuccess: ({ discovery, system }) => {
            queryClient.setQueryData(INFRA_LIVE_SHARED_QUERY_KEY, system);
            queryClient.setQueryData(SHARED_SYSTEM_QUERY_KEY, system);
            queryClient.setQueryData(SHARED_POWER_CONTROL_QUERY_KEY, { data: discovery.data.power_control });
            queryClient.setQueryData(SHARED_FAN_CONTROL_QUERY_KEY, { data: discovery.data.fan_control });
            queryClient.invalidateQueries({ queryKey: INFRA_LIVE_SHARED_QUERY_KEY });
            queryClient.invalidateQueries({ queryKey: SHARED_SYSTEM_QUERY_KEY });
            queryClient.invalidateQueries({ queryKey: SHARED_POWER_CONTROL_QUERY_KEY });
            queryClient.invalidateQueries({ queryKey: SHARED_FAN_CONTROL_QUERY_KEY });
        },
    });

    useEffect(() => {
        if (typeof window === 'undefined') return undefined;
        persistTelemetryPreferences(pollIntervalMs, windowMinutes);
        return undefined;
    }, [pollIntervalMs, windowMinutes]);

    const latestTimestampMs = samples.length > 0 ? samples[samples.length - 1].timestampMs : NaN;
    const visibleSamples =
        Number.isNaN(latestTimestampMs)
            ? samples
            : samples.filter((sample) => sample.timestampMs >= latestTimestampMs - windowMinutes * 60 * 1000);
    const plotRedrawKey = `${variant}:${traceType}:${showXAxisLabels ? 'x' : 'nx'}:${windowMinutes}`;
    const gapBreakMs = resolveTelemetryGapBreakMs(resolution, pollIntervalMs);
    const currentLimits = powerControlData?.data.limits ?? {};
    const currentFanControls = fanControlData?.data.gpus ?? {};
    const gpuOverrides = schedulerConfigData?.data?.overrides ?? {};

    return (
        <section className={variant === 'infra'
            ? 'mb-6 rounded-3xl border border-[var(--border-primary)] bg-[var(--bg-primary)]/96 p-5 shadow-2xl shadow-black/10'
            : dashboardSizing.outerSpacingClass
        }>
            <div className={variant === 'infra'
                ? 'mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between'
                : `flex flex-col ${dashboardSizing.headerGapClass} lg:flex-row lg:items-center lg:justify-between`
            }>
                {variant === 'infra' ? (
                    <div>
                        <h2 className="text-3xl font-bold text-[var(--text-primary)]">Live BMS Telemetry</h2>
                        <p className="mt-2 max-w-4xl text-sm text-[var(--text-muted)]">
                            Live CPU, RAM, and per-GPU charts rendered directly from the BMS telemetry API with no external monitor in the loop.
                        </p>
                    </div>
                ) : null}

                <div className={`flex flex-wrap items-start justify-end ${dashboardSizing.controlsGapClass}`}>
                    <SegmentedControl
                        label={usesRangeAwareDisplay ? 'Base poll' : 'Poll'}
                        value={pollIntervalMs}
                        options={POLL_PRESETS}
                        onChange={setPollIntervalMs}
                        compact={compact}
                    />
                    <SegmentedControl
                        label="Window"
                        value={windowMinutes}
                        options={WINDOW_PRESETS}
                        onChange={setWindowMinutes}
                        compact={compact}
                    />
                    {usesRangeAwareDisplay ? (
                        <div title="Long-window charts fetch and repaint at a range-aware cadence while host telemetry collection remains live.">
                            <div className={`font-semibold uppercase text-[var(--text-muted)] ${compact ? 'mb-1 text-[9px]' : 'mb-2 text-xs'}`}>
                                Display
                            </div>
                            <div className={`rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] font-semibold text-[var(--text-primary)] ${compact ? 'px-2.5 py-1.5 text-[10px]' : 'px-3 py-2 text-sm'}`}>
                                {displayIntervalMs / 1000}s
                            </div>
                        </div>
                    ) : null}
                    <div>
                        <div
                            className={`font-semibold uppercase text-[var(--text-muted)] ${compact ? 'mb-1 text-[9px]' : 'mb-2 text-xs'}`}
                            style={{ letterSpacing: '0.16em' }}
                        >
                            Discovery
                        </div>
                        <button
                            type="button"
                            onClick={() => discoverMutation.mutate()}
                            disabled={discoverMutation.isPending}
                            className={`rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] font-medium text-[var(--text-secondary)] transition-colors hover:border-accent/40 hover:text-[var(--text-primary)] disabled:cursor-wait disabled:opacity-60 ${compact ? 'px-2.5 py-1.5 text-[10px]' : 'px-3 py-2 text-sm'}`}
                            title="Refresh GPU, fan, power, and CPU RAPL capability discovery from the live host"
                        >
                            {discoverMutation.isPending ? 'Discovering...' : 'Discover hardware'}
                        </button>
                    </div>
                </div>
            </div>

            {discoverMutation.isError && (
                <div className="mb-3 rounded-2xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-200">
                    Hardware discovery failed: {discoverMutation.error instanceof Error ? discoverMutation.error.message : 'unknown error'}
                </div>
            )}

            {!payload && historyQuery.isPending && (
                <div className="rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/70 p-5 text-sm text-[var(--text-secondary)]">
                    Loading live telemetry...
                </div>
            )}

            {historyQuery.isError && (
                <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-5 text-sm text-red-200">
                    Failed to fetch live telemetry from the BMS API. Persisted chart data may be stale.
                </div>
            )}

            {!historyQuery.isPending && !historyQuery.isError && !payload && (
                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 p-5 text-sm text-amber-100">
                    No telemetry samples are available for this window.
                </div>
            )}

            {historyIsStale && (
                <div className="mb-3 rounded-2xl border border-amber-500/20 bg-amber-500/10 p-3 text-sm text-amber-100">
                    Telemetry collection is stale. The charts show the last persisted samples and preserve the collection gap.
                </div>
            )}

            {payload && (
                <div className={compact ? dashboardSizing.outerSpacingClass : 'space-y-6'}>
                    {compact && (
                        <TotalPowerBar
                            payload={payload}
                            currentLimits={currentLimits}
                            compact={compact}
                        />
                    )}
                    <div className={`grid xl:grid-cols-2 ${compact ? dashboardSizing.layoutGapClass : 'gap-6'}`}>
                        <CpuPanel
                            current={payload.cpu}
                            samples={visibleSamples}
                            showXAxisLabels={showXAxisLabels}
                            compact={compact && dashboardSizing.compactFrame}
                            panelHeight={compact ? dashboardSizing.cpuPanelHeight : undefined}
                            plotHeight={compact ? dashboardSizing.cpuPlotHeight : undefined}
                            traceType={traceType}
                            gapBreakMs={gapBreakMs}
                            redrawKey={`${plotRedrawKey}:cpu`}
                            xDomain={xDomain}
                        />
                        <RamPanel
                            current={payload.ram}
                            samples={visibleSamples}
                            showXAxisLabels={showXAxisLabels}
                            compact={compact && dashboardSizing.compactFrame}
                            panelHeight={compact ? dashboardSizing.ramPanelHeight : undefined}
                            plotHeight={compact ? dashboardSizing.ramPlotHeight : undefined}
                            traceType={traceType}
                            gapBreakMs={gapBreakMs}
                            redrawKey={`${plotRedrawKey}:ram`}
                            xDomain={xDomain}
                        />
                    </div>

                    <div className={`grid xl:grid-cols-2 ${compact ? dashboardSizing.layoutGapClass : 'gap-6'}`}>
                        {payload.gpus.map((gpu) => (
                            <GpuPanel
                                key={gpu.index}
                                gpu={gpu}
                                samples={visibleSamples}
                                showXAxisLabels={showXAxisLabels}
                                compact={compact && dashboardSizing.compactFrame}
                                panelHeight={compact ? dashboardSizing.gpuPanelHeight : undefined}
                                plotHeight={compact ? dashboardSizing.gpuPlotHeight : undefined}
                                traceType={traceType}
                                gapBreakMs={gapBreakMs}
                                redrawKey={`${plotRedrawKey}:gpu:${gpu.index}`}
                                xDomain={xDomain}
                                powerControls={compact ? {
                                    gpu,
                                    currentLimit: currentLimits[gpu.index] ?? gpu.power_limit_w,
                                    disabled: gpuOverrides[String(gpu.index)]?.disabled ?? false,
                                    isPending: manualMutation.isPending || toggleDisableMutation.isPending || fanMutation.isPending,
                                    fan: currentFanControls[String(gpu.index)] ?? null,
                                    onSetLimit: (watts) => manualMutation.mutate({ gpuIndex: gpu.index, limitWatts: watts }),
                                    onSetFanMode: (mode, targetPercent) => fanMutation.mutate({ gpuIndex: gpu.index, mode, targetPercent }),
                                    onSetFanTarget: (targetPercent) => fanMutation.mutate({ gpuIndex: gpu.index, mode: 'manual', targetPercent }),
                                    onToggleDisable: () => toggleDisableMutation.mutate(gpu.index),
                                } : undefined}
                            />
                        ))}
                    </div>
                </div>
            )}
        </section>
    );
}

export default InfraLiveTelemetry;
