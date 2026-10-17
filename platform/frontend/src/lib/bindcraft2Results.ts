import type { BindCraft2NativePage, BindCraft2Stage } from '../components/BindCraft2NativeResults';
import { uniqueBinderComplex, type NativeGenerationPage, type NativeGenerationRecord } from './nativeBinderResults';

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
        structures: Array.isArray(row.structures) ? row.structures as NativeGenerationRecord['structures'] : page.stage === 'document' && typeof row.artifact_id === 'string' ? [{ artifact_id: row.artifact_id, logical_path: typeof row.path === 'string' ? row.path : undefined, download_url: typeof row.download_url === 'string' ? row.download_url : undefined, target_state: typeof row.target_state === 'string' ? row.target_state : undefined }] : [],
        native_input_id: row.design ?? row.path, native_record: row, metrics };
}
export function bc2Page(page: BindCraft2NativePage): NativeGenerationPage {
    return { ...page, records: page.rows.map(row => bc2Record(row, page)), receipt: { accounting: page.accounting, analytics: page.analytics },
        publication: page.metadata ?? {}, artifacts: page.artifacts?.map(file => ({ ...file, download_url: file.download_url ?? undefined })) };
}
export function bc2PreferredColumns(page: BindCraft2NativePage): string[] {
    const records = page.rows.map(row => bc2Record(row, page));
    const available = [...new Set(records.flatMap(row => Object.keys(object(row.metrics))))];
    const present = (key: string) => records.some(row => object(row.metrics)[key] !== null && object(row.metrics)[key] !== undefined);
    const confidence = ['i_pTM', 'pLDDT', 'pTM', 'i_pAE', 'Binder_RMSD'];
    const phaseConfidence = available.filter(key => /^(?:refine|screen) · .+\.iptm · last recorded$/.test(key));
    const preferred = page.stage === 'trajectory'
        ? ['seq_length', ...phaseConfidence, 'duration_seconds', 'terminated', 'outcome']
        : ['seq_length', ...confidence, ...available.filter(key => / · (?:i_pTM|pLDDT)$/.test(key)), 'outcome', 'rank'];
    return preferred.filter(key => available.includes(key) && present(key));
}
export function bc2MetricLabel(key: string): string | undefined {
    return ({ i_pTM: 'Interface pTM (0–1)', pLDDT: 'pLDDT (0–1)', pTM: 'pTM (0–1)',
        i_pAE: 'Interface PAE loss (normalized)', i_pDAE: 'Interface pDAE', Binder_RMSD: 'Binder RMSD (Å)',
        Unbound_Binder_pLDDT: 'Binder-only pLDDT (0–1)', Target_pLDDT: 'Target pLDDT (0–1)' } as Record<string, string>)[key];
}
export const bc2PreferredDocument = (row: NativeGenerationRecord) => uniqueBinderComplex(row.structures ?? []);

export async function fetchBC2TrajectoryNames(jobId: string, arm: string | null, signal?: AbortSignal): Promise<Record<string, string>> {
    const names: Record<string, string> = {};
    let offset = 0, total = Infinity;
    while (offset < total) {
        const page = await fetchBC2Page(jobId, arm, 'trajectory', offset, signal);
        total = page.total;
        if (!page.rows.length && offset < total) throw new Error('Design names are partially available; refresh to retry.');
        for (const row of page.rows) {
            const number = object(row.values).trajectory;
            if (row.design && csvNumber(number) !== undefined) names[String(row.design)] = `Design ${number}`;
        }
        offset += page.rows.length;
    }
    return names;
}
export function bc2Label(row: NativeGenerationRecord, names: Record<string, string> = {}): string {
    const native = object(row.native_record), values = object(native.values);
    const design = String(native.scored_design ?? native.design ?? '');
    const family = String(native.trajectory_design ?? design.replace(/_(?:candidate|seq)\d+$/, ''));
    const variant = /_candidate(\d+)$/.exec(design)?.[1];
    const name = names[family] ?? (values.trajectory ? `Design ${values.trajectory}` : 'Candidate');
    const length = csvNumber(values.length) ?? (typeof native.sequence === 'string' ? native.sequence.length : undefined);
    return [name, variant ? `Variant ${variant}` : undefined, length ? `${length} aa` : undefined].filter(Boolean).join(' · ');
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
