import type { ExecutionStage, Job } from './api';
import { getModelDisplayName, getStageDisplayName } from '../constants/displayNames';

export type StageHistory = Pick<Job, 'execution_stages' | 'all_stages' | 'current_stage' | 'completed_stages'>;
export type StageJob = StageHistory & Pick<Job, 'model_id' | 'status'>;

const globalStates = new Set([
    'complete', 'completed', 'done', 'success', 'succeeded', 'failed', 'failure',
    'cancelled', 'canceled', 'queued', 'pending', 'running', 'awaiting_input',
]);
function stageId(value: string | null | undefined): string | null {
    const id = value?.trim();
    return id && !globalStates.has(id.toLowerCase().replace(/[ -]/g, '_')) ? id : null;
}

/** Presentation only: neither workflow mode nor default parameters establish execution. */
export function getJobExecutionStages(job: StageJob, payload?: StageHistory | null): ExecutionStage[] {
    // An explicitly empty projection is authoritative too. Older endpoint payloads
    // must not overwrite a projection supplied by the job reader.
    if (payload?.execution_stages !== undefined) return payload.execution_stages;
    if (job.execution_stages !== undefined) return job.execution_stages;
    const history = payload ?? job;
    const planned = new Set((history.all_stages ?? []).map(stageId).filter((id): id is string => !!id));
    const completed = new Set((history.completed_stages ?? []).map(stageId).filter((id): id is string => !!id));
    const current = stageId(history.current_stage);
    const ids = new Set([...planned, ...completed, ...(current ? [current] : [])]);
    if (!ids.size) return [{ id: job.model_id, label: getModelDisplayName(job.model_id), state: 'unknown', source: 'model' }];
    return [...ids].map(id => {
        let state: ExecutionStage['state'] = planned.has(id) && !['completed', 'failed', 'cancelled'].includes(job.status)
            ? 'planned' : 'unknown';
        if (completed.has(id)) state = 'completed';
        else if (id === current) {
            state = ['running', 'awaiting_input', 'failed', 'cancelled'].includes(job.status)
                ? job.status as ExecutionStage['state']
                : job.status === 'queued' ? 'planned' : 'unknown';
        }
        return { id, label: getStageDisplayName(id), state, source: completed.has(id) || current === id ? 'recorded' : 'plan' };
    });
}
