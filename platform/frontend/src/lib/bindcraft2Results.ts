import type { BindCraft2NativePage, BindCraft2Stage } from '../components/BindCraft2NativeResults';
import type { NativeGenerationPage, NativeGenerationRecord } from './nativeBinderResults';

export const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
// Native CSV scalar decoding only. Empty cells are missing, never zero. JSON evidence is unchanged.
export function csvNumber(value: unknown): number | undefined {
    if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
    if (typeof value !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) return undefined;
    const number = Number(value); return Number.isFinite(number) ? number : undefined;
}
export async function fetchBC2Page(jobId: string, arm: string | null, stage: BindCraft2Stage, offset = 0, signal?: AbortSignal): Promise<BindCraft2NativePage> {
    const params = new URLSearchParams({ stage, offset: String(offset), limit: '100' });
    if (arm !== null) params.set('arm', arm);
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/bindcraft2-results?${params}`, { signal });
    if (!response.ok) throw new Error(`Native records unavailable (${response.status})`);
    return response.json();
}
export function bc2Record(row: Record<string, unknown>, page: Pick<BindCraft2NativePage, 'arm' | 'stage'>): NativeGenerationRecord {
    const native = object(row.values), analytics = object(row.analytics);
    const metrics: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(native)) {
        if (['design', 'Timing', 'hash', 'bms_attempt_sha256', 'Binder_Sequence', 'trajectory', 'length'].includes(key)) continue;
        const number = csvNumber(value);
        if (number !== undefined) metrics[key] = number;
    }
    // Multi-target CSV cells are not scalar aggregates. Preserve exact state identities.
    for (const [metric, states] of Object.entries(object(row.target_readings))) {
        for (const [state, value] of Object.entries(object(states))) {
            metrics[`${state} · ${metric}`] = value === null ? null : csvNumber(value);
        }
    }
    // These are measurements from recorded updates, not the fresh native prediction/verdict.
    for (const [phase, columns] of Object.entries(object(analytics.phase_metrics))) {
        for (const [column, summary] of Object.entries(object(columns))) {
            const stats = object(summary);
            metrics[`${phase} · ${column} · last recorded`] = stats.last;
            metrics[`${phase} · ${column} · peak recorded`] = stats.max;
        }
    }
    metrics.duration_seconds = analytics.duration_seconds;
    metrics.seq_length = csvNumber(native.length) ?? (typeof row.sequence === 'string' && row.sequence ? row.sequence.length : undefined);
    metrics.terminated = row.terminated;
    metrics.outcome = row.outcome;
    metrics.rank = csvNumber(row.rank);
    return { ...row, candidate_key: JSON.stringify([page.arm, page.stage, row.design ?? row.path ?? row.sha256 ?? row.trajectory]),
        design_id: typeof row.design_id === 'string' ? row.design_id : undefined,
        structures: Array.isArray(row.structures) ? row.structures as NativeGenerationRecord['structures'] : [],
        native_input_id: row.design ?? row.path, native_record: row, metrics };
}
export function bc2Page(page: BindCraft2NativePage): NativeGenerationPage {
    return { ...page, records: page.rows.map(row => bc2Record(row, page)), receipt: { accounting: page.accounting, analytics: page.analytics },
        publication: page.metadata ?? {}, artifacts: page.artifacts?.map(file => ({ ...file, download_url: file.download_url ?? undefined })) };
}
export function bc2Label(row: NativeGenerationRecord): string {
    const native = object(row.native_record), values = object(native.values);
    return values.trajectory ? `Trajectory ${values.trajectory}` : String(native.design ?? native.path ?? 'Native record');
}
export interface BC2TracePage {
    design: string; arm: string | null; offset: number; limit: number; total: number;
    rows: Record<string, unknown>[]; available: boolean; warnings: string[];
}
export async function fetchBC2Trace(jobId: string, design: string, arm: string | null, offset: number, signal?: AbortSignal): Promise<BC2TracePage> {
    const params = new URLSearchParams({ design, offset: String(offset), limit: '1000' });
    if (arm !== null) params.set('arm', arm);
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/bindcraft2-results/trajectory?${params}`, { signal });
    if (!response.ok) throw new Error(`Trajectory trace unavailable (${response.status})`);
    return response.json();
}
