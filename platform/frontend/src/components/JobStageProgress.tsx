import type { ExecutionStage } from '../lib/api';
import { getJobExecutionStages, type StageHistory, type StageJob } from '../lib/jobStageProgress';
import { getModelDisplayName, getStageDisplayName } from '../constants/displayNames';

const neutral = 'bg-[var(--bg-tertiary)] border-[var(--border-primary)] text-[var(--text-secondary)]';
const styles: Record<ExecutionStage['state'], string> = {
    planned: neutral,
    unknown: neutral,
    completed: 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400',
    running: 'bg-blue-500/20 border-blue-500/30 text-blue-400',
    awaiting_input: 'bg-amber-500/20 border-amber-500/30 text-amber-400',
    failed: 'bg-red-500/20 border-red-500/30 text-red-400',
    cancelled: 'bg-orange-500/20 border-orange-500/30 text-orange-400',
};

export function JobStageProgress({ job, stagePayload, showState = false }: {
    job: StageJob;
    stagePayload?: StageHistory | null;
    showState?: boolean;
}) {
    const stages = getJobExecutionStages(job, stagePayload);
    if (!stages.length) return null;
    return (
        <div className="mt-1 flex flex-wrap items-center gap-1 pb-1" role="list" aria-label="Job execution stages">
            {stages.map((stage, index) => {
                const state = stage.state.replace('_', ' ');
                // A native key is identity, not necessarily human-readable copy.
                // Preserve an explicit server label and never change stage identity/state.
                const label = stage.label !== stage.id ? stage.label
                    : stage.source === 'model' ? getModelDisplayName(stage.id) : getStageDisplayName(stage.id);
                const detail = stage.state === 'unknown' ? 'Execution history unavailable; completion is unknown.'
                    : stage.state === 'planned' ? 'Planned stage; execution is not confirmed.'
                        : `Stage state: ${state}.`;
                return (
                    <span key={`${stage.id}:${index}`} role="listitem"
                        data-stage-id={stage.id} data-stage-state={stage.state} data-stage-source={stage.source}
                        aria-label={`${label}: ${state}`}
                        title={`${label}: ${state}. ${detail} Source: ${stage.source}.`}
                        className={`rounded-[3px] border px-1.5 py-0.5 text-[10px] font-semibold ${styles[stage.state]}`}>
                        {label}{showState ? ` · ${state}` : ''}
                    </span>
                );
            })}
        </div>
    );
}
