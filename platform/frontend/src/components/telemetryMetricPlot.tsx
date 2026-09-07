import { useState } from 'react';
import { resolveTelemetryPlotX } from './infraTelemetryHistory';
import type { LiveSample } from './infraTelemetryHistory';

interface AxisConfig {
    title: string;
    color: string;
    range?: [number, number];
    suffix?: string;
    decimals?: number;
}
interface TimeSeriesLine {
    axis?: AxisConfig;
    x?: Array<string | number | null>;
    y?: Array<number | null>;
    customdata?: unknown[];
    name?: string;
    mode?: string;
    line?: { color?: string; width?: number; shape?: string; simplify?: boolean };
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

function parseSeriesTimestamp(value: string | number | null | undefined): number | null {
    const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) : NaN;
    return Number.isFinite(parsed) ? parsed : null;
}

export function buildTelemetrySvgPath(line: TimeSeriesLine, xMin: number, xMax: number, yMin: number, yMax: number): string {
    let path = '';
    let drawing = false;
    for (let index = 0; index < Math.min(line.x?.length ?? 0, line.y?.length ?? 0); index++) {
        const timestamp = parseSeriesTimestamp(line.x?.[index]);
        const value = line.y?.[index];
        const x = timestamp == null ? null : resolveTelemetryPlotX(timestamp, xMin, xMax);
        if (x == null || value == null || !Number.isFinite(value)) { drawing = false; continue; }
        const y = 100 - ((value - yMin) / Math.max(Number.EPSILON, yMax - yMin)) * 100;
        path += `${drawing ? ' L' : ' M'} ${x.toFixed(2)} ${y.toFixed(2)}`;
        drawing = true;
    }
    return path;
}

function formatValue(value: number, axis: AxisConfig): string {
    return `${Number(value.toFixed(axis.decimals ?? 2))}${axis.suffix ?? ''}`;
}
function clock(timestamp: number): string {
    return new Date(timestamp).toLocaleTimeString([], { hour12: false });
}

// Small multiples share time/geometry, never a dimensionless mixed-unit Y scale.
export function TimeSeriesPlot(props: TimeSeriesPlotProps) {
    const { series, height, showXAxisLabels = true } = props;
    if (series.length > 1 && series.some(line => line.axis)) {
        const stripHeight = Math.max(60, (height - (showXAxisLabels ? 18 : 0)) / series.length);
        return <div className="space-y-1" data-bms-telemetry-strips="true">
            {series.map((line, index) => <MetricStrip key={line.axis?.title ?? line.name ?? index}
                {...props} height={stripHeight + (index === series.length - 1 && showXAxisLabels ? 18 : 0)}
                series={[line]} yAxis={line.axis ?? props.yAxis}
                showXAxisLabels={index === series.length - 1 && showXAxisLabels} />)}
        </div>;
    }
    return <MetricStrip {...props} />;
}

function MetricStrip({ height, samples, yAxis, series, showXAxisLabels = true, xDomain }: TimeSeriesPlotProps) {
    const [selected, setSelected] = useState<number | null>(null);
    const xMin = xDomain?.[0] ?? samples[0]?.timestampMs ?? 0;
    const xMax = xDomain?.[1] ?? Math.max(xMin + 1, samples.at(-1)?.timestampMs ?? 1);
    const values = series.flatMap(line => (line.y ?? []).filter((v): v is number => v != null && Number.isFinite(v)));
    // Zero baseline; expand for measured extremes rather than clipping to a power cap.
    const yMin = Math.min(yAxis.range?.[0] ?? 0, ...values);
    const maximum = Math.max(yAxis.range?.[1] ?? 0, ...values, 1);
    const magnitude = 10 ** Math.floor(Math.log10(maximum));
    const yMax = yAxis.range?.[1] != null && maximum <= yAxis.range[1]
        ? yAxis.range[1] : Math.ceil(maximum / magnitude * 2) * magnitude / 2;
    const bottom = showXAxisLabels ? 20 : 4;
    const title = `${yAxis.title}${yAxis.suffix ? ` (${yAxis.suffix.trim()})` : ''}`;
    const timestamps = [...new Set(series.flatMap(line => (line.x ?? []).flatMap(value => {
        const t = parseSeriesTimestamp(value);
        return t != null && t >= xMin && t <= xMax ? [t] : [];
    })))].sort((a, b) => a - b);
    const selectedTime = selected == null ? null : timestamps[Math.min(selected, timestamps.length - 1)];
    const inspect = (fraction: number) => {
        const target = xMin + Math.max(0, Math.min(1, fraction)) * (xMax - xMin);
        let closest = 0;
        timestamps.forEach((t, i) => { if (Math.abs(t - target) < Math.abs(timestamps[closest] - target)) closest = i; });
        setSelected(closest);
    };
    return <div className="relative w-full overflow-hidden rounded-lg border border-[var(--border-primary)]"
        style={{ height, background: 'var(--surface-plot,var(--bg-secondary))' }}
        role="group" aria-label={`${title} telemetry history`} data-bms-telemetry-plot="true">
        <div className="absolute left-2 right-2 top-0.5 flex items-center justify-between gap-2 text-[11px]"
            data-bms-telemetry-legend="true">
            <span className="shrink-0 font-medium" style={{ color: series[0]?.line?.color }}>{title}</span>
            <span className="truncate text-[var(--text-secondary)]" title={series.map(line => line.name).join(' · ')}>
                {series.map((line, index) => <span key={index} style={{ color: line.line?.color }}>{index > 0 ? ' · ' : ''}{line.name}</span>)}
            </span>
        </div>
        <div className="absolute left-1 flex w-[4.5rem] flex-col justify-between text-right text-[10px] tabular-nums"
            style={{ top: 22, bottom, color: 'var(--chart-axis,var(--text-muted))' }}
            data-bms-telemetry-axis="true">
            <span>{formatValue(yMax, yAxis)}</span><span>{formatValue(yMin, yAxis)}</span>
        </div>
        <div className="absolute right-2 outline-offset-1" style={{ left: 82, top: 22, bottom }}
            tabIndex={0} role="group" aria-label={`${title}; use arrow keys to inspect samples`}
            data-bms-telemetry-inspector="true" data-bms-telemetry-lines="true"
            onPointerMove={event => { const rect = event.currentTarget.getBoundingClientRect(); inspect((event.clientX - rect.left) / rect.width); }}
            onPointerLeave={() => setSelected(null)} onBlur={() => setSelected(null)}
            onKeyDown={event => {
                if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
                event.preventDefault();
                setSelected(previous => Math.max(0, Math.min(timestamps.length - 1, previous == null ? 0 : previous + (event.key === 'ArrowRight' ? 1 : -1))));
            }}>
            <svg className="h-full w-full" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
                {[0, 50, 100].map(y => <line key={y} x1="0" x2="1000" y1={y} y2={y}
                    stroke="var(--chart-grid,var(--border-primary))" vectorEffect="non-scaling-stroke" />)}
                {series.map((line, i) => <path key={i} d={buildTelemetrySvgPath(line, xMin, xMax, yMin, yMax)}
                    fill="none" stroke={line.line?.color ?? 'var(--link)'} strokeWidth={line.line?.width ?? 1.5}
                    strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />)}
                {selectedTime != null && <line x1={resolveTelemetryPlotX(selectedTime, xMin, xMax)!}
                    x2={resolveTelemetryPlotX(selectedTime, xMin, xMax)!} y1="0" y2="100"
                    stroke="var(--text-secondary)" vectorEffect="non-scaling-stroke" />}
            </svg>
        </div>
        {values.length === 0 && <span className="absolute bottom-1 right-3 text-[10px] text-[var(--text-muted)]">No samples</span>}
        {selectedTime != null && <div role="status" className="pointer-events-none absolute left-1 right-1 top-0 z-20 break-words rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 text-[11px] text-[var(--text-primary)]">
            {clock(selectedTime)}{series.map((line, i) => {
                // A gap sentinel may share the resumed sample timestamp; prefer the actual reading.
                const indices = (line.x ?? []).flatMap((x, index) => parseSeriesTimestamp(x) === selectedTime ? [index] : []);
                const value = line.y?.[indices.at(-1) ?? -1];
                return <span key={i} style={{ color: line.line?.color }}> · {line.axis?.title ?? line.name}: {value == null || !Number.isFinite(value) ? 'n/a' : `${value}${yAxis.suffix ?? ''}`}</span>;
            })}
        </div>}
        {showXAxisLabels && <div className="absolute bottom-0 right-2 flex justify-between text-[10px] tabular-nums text-[var(--text-muted)]" style={{ left: 82 }}>
            <span>{clock(xMin)}</span><span>{clock(xMax)}</span>
        </div>}
    </div>;
}
