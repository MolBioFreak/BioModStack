import type { ProjectRun } from '../../lib/projectManager';
import { displayLabel, valueText } from './projectManagerState';

interface RunPanelProps {
    runs: ProjectRun[];
    selectedNodeKey?: string;
    onSelect: (kind: 'workflow' | 'workflow_run', id: string, run: ProjectRun) => void;
    onAction: (action: string, run: ProjectRun) => void;
    onLoadMore?: () => void;
}

function progressText(run: ProjectRun): string {
    if (run.progress.kind === 'indeterminate' || run.progress.value === null) return 'Indeterminate';
    return `${displayLabel(run.progress.kind)} ${run.progress.value}`;
}

export function RunPanel({ runs, selectedNodeKey, onSelect, onAction, onLoadMore }: RunPanelProps) {
    return (
        <section aria-label="Workflow runs" className="border-t border-border-primary bg-surface-secondary">
            <header className="flex items-center justify-between gap-3 px-4 py-3">
                <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Execution evidence</p>
                    <h2 className="mt-0.5 text-sm font-semibold text-content">Workflow runs</h2>
                </div>
                <span className="rounded-full border border-border-primary px-2 py-1 text-[10px] text-content-secondary">{runs.length} bounded runs</span>
            </header>
            <div className="max-h-80 overflow-auto border-t border-border-primary">
                {!runs.length ? (
                    <p className="px-4 py-8 text-center text-xs text-content-muted">Select an experiment to inspect its bounded canonical run page.</p>
                ) : runs.map((run) => (
                    <article key={run.run_id} className="border-b border-border-primary px-4 py-3 last:border-b-0">
                        <button
                            type="button"
                            aria-label={`Inspect run ${run.run_id}`}
                            aria-pressed={selectedNodeKey === `workflow_run:${run.run_id}`}
                            onClick={() => onSelect('workflow_run', run.run_id, run)}
                            onKeyDown={(event) => {
                                if (event.key === 'Enter' || event.key === ' ') {
                                    event.preventDefault();
                                    onSelect('workflow_run', run.run_id, run);
                                }
                            }}
                            className="w-full rounded-lg text-left outline-none focus:ring-2 focus:ring-accent"
                        >
                            <span className="flex flex-wrap items-start justify-between gap-2">
                                <span>
                                    <span className="block text-xs font-semibold text-content">{run.target_label}</span>
                                    <span className="mt-0.5 block font-mono text-[10px] text-content-muted">
                                        {run.run_id} · {run.batch_or_run_group_id ? `group ${run.batch_or_run_group_id}` : 'no run group'}
                                    </span>
                                </span>
                                <span className="flex flex-wrap items-center justify-end gap-2">
                                    {run.replica_index !== null && (
                                        <span className="rounded-full border border-border-primary px-2 py-0.5 text-[10px] text-content-secondary">Scientific replica {run.replica_index}</span>
                                    )}
                                    <span className="rounded-full border border-border-primary px-2 py-0.5 text-[10px] text-content-secondary">Canonical {displayLabel(run.canonical_state)}</span>
                                    <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold text-accent">Global {displayLabel(run.normalized_state)}</span>
                                </span>
                            </span>
                        </button>

                        <dl className="mt-3 grid grid-cols-2 gap-2 text-[10px] md:grid-cols-4">
                            <div><dt className="text-content-muted">Workflow</dt><dd className="truncate text-content-secondary">{displayLabel(run.workflow_type)}</dd></div>
                            <div><dt className="text-content-muted">Canonical job</dt><dd className="truncate font-mono text-content-secondary">{run.canonical_job_id ?? 'Not materialized'}</dd></div>
                            <div><dt className="text-content-muted">Stage</dt><dd className="truncate text-content-secondary">{run.stage ? displayLabel(run.stage) : 'Unavailable'}</dd></div>
                            <div><dt className="text-content-muted">Progress</dt><dd className="truncate text-content-secondary">{progressText(run)}</dd></div>
                            <div><dt className="text-content-muted">Started</dt><dd className="truncate text-content-secondary">{run.started_at ?? 'Unavailable'}</dd></div>
                            <div><dt className="text-content-muted">Elapsed</dt><dd className="truncate text-content-secondary">{run.elapsed_seconds} seconds</dd></div>
                            <div><dt className="text-content-muted">Outputs</dt><dd className="truncate text-content-secondary">{run.output_count}</dd></div>
                            <div><dt className="text-content-muted">Adapter</dt><dd className="truncate font-mono text-content-secondary">{run.adapter_id ?? 'Unavailable'}</dd></div>
                        </dl>

                        {run.condition.severity !== 'none' && (
                            <p role="status" className="mt-3 rounded-lg border border-border-primary bg-surface px-3 py-2 text-[10px] text-content-secondary">
                                {displayLabel(run.condition.severity)}{run.condition.code ? ` · ${run.condition.code}` : ''}{run.condition.message ? ` · ${run.condition.message}` : ''}
                            </p>
                        )}

                        {run.available_actions.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2" aria-label={`Actions for run ${run.run_id}`}>
                                {run.available_actions.map((action) => (
                                    <button key={action} type="button" onClick={() => onAction(action, run)} className="rounded-md border border-accent px-2.5 py-1.5 text-[10px] font-semibold text-accent focus:ring-2 focus:ring-accent">
                                        {displayLabel(action)}
                                    </button>
                                ))}
                            </div>
                        )}

                        {run.attempts.length > 0 && (
                            <section aria-label={`Attempts for run ${run.run_id}`} className="mt-3">
                                <h3 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-content-muted">Attempt provenance</h3>
                                <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                                    {run.attempts.map((attempt) => (
                                        <div key={attempt.attempt_id} className="rounded-lg border border-border-primary bg-surface p-3">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-[11px] font-semibold text-content">Attempt {attempt.attempt_number}</span>
                                                <span className="text-[10px] text-content-secondary">{displayLabel(attempt.canonical_state)}</span>
                                            </div>
                                            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[10px]">
                                                <dt className="text-content-muted">Attempt ID</dt><dd className="truncate font-mono text-content-secondary">{attempt.attempt_id}</dd>
                                                <dt className="text-content-muted">Canonical job</dt><dd className="truncate font-mono text-content-secondary">{attempt.canonical_job_id}</dd>
                                                <dt className="text-content-muted">Binding</dt><dd className="truncate text-content-secondary">{valueText(attempt.binding_receipt)}</dd>
                                                <dt className="text-content-muted">Runtime</dt><dd className="truncate text-content-secondary">{valueText(attempt.runtime_identity)}</dd>
                                                <dt className="text-content-muted">Terminal</dt><dd className="truncate text-content-secondary">{valueText(attempt.terminal_receipt)}</dd>
                                            </dl>
                                        </div>
                                    ))}
                                </div>
                            </section>
                        )}
                    </article>
                ))}
            </div>
            {onLoadMore && <div className="border-t border-border-primary p-2 text-center"><button type="button" onClick={onLoadMore} className="rounded-md border border-border-primary px-3 py-1.5 text-xs font-semibold text-content-secondary focus:ring-2 focus:ring-accent">Load next run page</button></div>}
        </section>
    );
}
