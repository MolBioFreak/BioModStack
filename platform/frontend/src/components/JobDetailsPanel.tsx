/** Dashboard row expansion: job-level summary only; candidate review lives in Results. */
import { Link, useLocation } from 'react-router-dom';
import type { Job } from '../lib/api';
import { getModeDisplayName, getModelDisplayName } from '../constants/displayNames';
import { isFrustraMpnnOutputJob } from '../lib/jobOutputSummary';
import { CandidateAccountingStatus } from './CandidateAccountingStatus';
import { RemoteResultsPrompt } from './RemoteResultsPrompt';
import { RemoteDiagnosticsPrompt } from './RemoteDiagnosticsPrompt';

interface JobDetailsPanelProps {
    job: Job;
    onClose: () => void;
}

export function JobDetailsPanel({ job, onClose }: JobDetailsPanelProps) {
    const launchContextId = new URLSearchParams(useLocation().search).get('launch_context_id');
    const contextSearch = launchContextId ? `?${new URLSearchParams({ launch_context_id: launchContextId })}` : '';
    const isMolecularDynamicsJob = job.model_id === 'molecular_dynamics' ||
        job.mode === 'molecular_dynamics' || job.mode === 'md';
    const frustra = isFrustraMpnnOutputJob(job);
    const summary = job.result_summary;
    const hasAccounting = !!summary && summary.state !== 'unavailable';
    const fields = [
        ['Mode', getModeDisplayName(job.mode)],
        ['Status', job.status],
        // Requested work and stored results are different quantities. Never substitute one for the other.
        ...(!hasAccounting && typeof job.requested_design_count === 'number' ? [['Requested', job.requested_design_count]] : []),
        [frustra ? 'FrustraMPNN results' : 'Stored designs', frustra ? job.frustrampnn_result_count ?? 0 : job.design_count],
        ...(job.execution_target_id ? [['Execution target', job.execution_target_id]] : []),
        ...(job.execution_policy ? [['Result return', job.execution_policy.remote_result_policy]] : []),
    ];

    return <tr>
        <td colSpan={6} className="border-b border-[var(--border-color)] bg-[var(--bg-secondary)]">
            <section aria-label="Job quick summary" className="min-w-0 space-y-3 p-4 text-[var(--text-primary)]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                        <h3 className="break-words text-sm font-semibold">{job.name}</h3>
                        <p className="text-xs text-[var(--text-secondary)]">{getModelDisplayName(job.model_id)}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-xs">
                        <Link to={`/jobs/${encodeURIComponent(job.id)}${contextSearch}`} className="text-accent hover:underline">Job details</Link>
                        <Link to={`/designs/${encodeURIComponent(job.id)}${contextSearch}`} className="text-accent hover:underline">
                            {isMolecularDynamicsJob ? 'MD Operations →' : 'Open in Results Viewer →'}
                        </Link>
                        <button type="button" aria-label="Close job summary" onClick={onClose} className="px-2 py-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">✕</button>
                    </div>
                </div>
                <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
                    {fields.map(([label, value]) => <div key={label} className="min-w-0">
                        <dt className="text-[var(--text-secondary)]">{label}</dt>
                        <dd className="break-words font-medium">{value ?? 'Not reported'}</dd>
                    </div>)}
                </dl>
                <CandidateAccountingStatus job={job} compact />
                <RemoteResultsPrompt job={job} />
                <RemoteDiagnosticsPrompt job={job} />
            </section>
        </td>
    </tr>;
}

export default JobDetailsPanel;
