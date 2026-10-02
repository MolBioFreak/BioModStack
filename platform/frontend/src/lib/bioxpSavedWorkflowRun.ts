import type { SavedWorkflowSnapshot } from './bioxpWorkflowPlan';

export type PendingWorkflowRun = {
    version: 1; key: string; jobId: string; generation: number;
    saved: SavedWorkflowSnapshot; document: Record<string, unknown>;
};
export const pendingWorkflowRunStorageKey = 'bms.bioxp.saved-workflow-runs.v1';
// A bounded browser association, not a second job ledger. Robot history remains authoritative.
const maxEntries = 8;
const maxBytes = 2_000_000;
export const copyWorkflowValue = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
export async function canonicalWorkflowJobId(key: string): Promise<string> {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key.trim()));
    return `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
}
export function readPendingWorkflowRuns(): { runs: PendingWorkflowRun[]; warning: string | null } {
    try {
        const text = localStorage.getItem(pendingWorkflowRunStorageKey);
        if (!text) return { runs: [], warning: null };
        if (text.length > maxBytes) throw new Error('Oversize association');
        const runs: PendingWorkflowRun[] = JSON.parse(text);
        if (!Array.isArray(runs) || runs.length > maxEntries || runs.some(run => run.version !== 1
            || typeof run.key !== 'string' || !/^protocol-live-[a-f0-9]{64}$/.test(run.jobId)
            || !Number.isInteger(run.generation) || !run.saved?.draft || !run.document)) throw new Error('Invalid association');
        return { runs, warning: null };
    } catch {
        return { runs: [], warning: 'Browser run retention unavailable. Recover original job from robot history; do not resubmit uncertain work.' };
    }
}
export function retainPendingWorkflowRun(run: PendingWorkflowRun): string | null {
    try {
        const previous = readPendingWorkflowRuns();
        const runs = [...previous.runs.filter(item => item.jobId !== run.jobId), run];
        let evicted = false;
        while (runs.length > maxEntries || (JSON.stringify(runs).length > maxBytes && runs.length > 1)) {
            runs.shift(); evicted = true;
        }
        const text = JSON.stringify(runs);
        if (text.length > maxBytes) throw new Error('Snapshot exceeds browser retention limit');
        localStorage.setItem(pendingWorkflowRunStorageKey, text);
        return previous.warning ?? (evicted ? 'Older browser run associations were evicted by the retention limit; robot history remains authoritative.' : null);
    } catch {
        return 'Browser run retention failed. This session retains the original job, but reload recovery may be unavailable; keep its identity. Submission behavior is unchanged.';
    }
}
