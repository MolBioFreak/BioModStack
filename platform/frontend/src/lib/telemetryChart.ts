export interface TelemetryChartGpuPoint {
    index: number;
    utilization: number | null;
    vram_gb: number | null;
    power_draw_w: number | null;
    temperature: number | null;
}

export interface TelemetryChartPoint {
    timestamp_ms: number;
    sample_count: number;
    cpu_utilization: number | null;
    cpu_frequency_current_mhz: number | null;
    cpu_power_watts: number | null;
    cpu_temperature: number | null;
    ram_used_gb: number | null;
    ram_available_gb: number | null;
    ram_utilization: number | null;
    ram_swap_percent: number | null;
    gpus: TelemetryChartGpuPoint[];
}

export interface TelemetryChartHistoryResponse {
    source: 'immutable_server_telemetry';
    database: 'dedicated_telemetry_store';
    resolution: 'server_bucketed_raw';
    start_ms: number;
    end_ms: number;
    effective_start_ms: number;
    bucket_ms: number;
    generated_at_ms: number;
    next_cursor_ms: number | null;
    points: TelemetryChartPoint[];
}

export function resolveTelemetryChartCursor(
    previous: TelemetryChartHistoryResponse | undefined,
    startMs: number,
    endMs: number,
    bucketMs: number,
): number | null {
    const cursor = previous?.next_cursor_ms;
    if (
        previous?.bucket_ms !== bucketMs
        || cursor == null
        || !Number.isFinite(cursor)
        || cursor < startMs
        || cursor >= endMs
    ) {
        return null;
    }
    return cursor;
}

export function mergeTelemetryChartHistory(
    previous: TelemetryChartHistoryResponse | undefined,
    incoming: TelemetryChartHistoryResponse,
    startMs: number,
    endMs: number,
): TelemetryChartHistoryResponse {
    const byTimestamp = new Map<number, TelemetryChartPoint>();
    const alignedStartMs = startMs - (startMs % incoming.bucket_ms);
    if (previous?.bucket_ms === incoming.bucket_ms) {
        for (const point of previous.points) {
            if (point.timestamp_ms >= alignedStartMs && point.timestamp_ms < endMs) {
                byTimestamp.set(point.timestamp_ms, point);
            }
        }
    }
    for (const point of incoming.points) {
        if (point.timestamp_ms >= alignedStartMs && point.timestamp_ms < endMs) {
            byTimestamp.set(point.timestamp_ms, point);
        }
    }
    return {
        ...incoming,
        start_ms: startMs,
        end_ms: endMs,
        points: [...byTimestamp.values()].sort(
            (left, right) => left.timestamp_ms - right.timestamp_ms,
        ),
    };
}

export function isRenderableTelemetryChartPoint(point: TelemetryChartPoint): boolean {
    const scalarValues = [
        point.cpu_utilization,
        point.cpu_frequency_current_mhz,
        point.ram_used_gb,
        point.ram_available_gb,
        point.ram_utilization,
        point.ram_swap_percent,
    ];
    if (scalarValues.some((value) => value == null || !Number.isFinite(value))) return false;
    return point.gpus.every((gpu) => [
        gpu.utilization,
        gpu.vram_gb,
        gpu.power_draw_w,
        gpu.temperature,
    ].every((value) => value != null && Number.isFinite(value)));
}
