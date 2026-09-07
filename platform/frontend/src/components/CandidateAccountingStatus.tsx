import type { Job } from '../lib/api';

export function CandidateAccountingStatus({ job }: { job: Job }) {
    const summary = job.result_summary;
    if (!summary || summary.state === 'unavailable') return null;
    const failed = job.status === 'failed' || summary.state === 'ingestion_failed' || summary.state === 'no_candidates';
    const validated = job.status === 'completed' && summary.state === 'validated' && !summary.partial;
    const fields = [
        ['Requested', summary.requested_count], ['Generated', summary.generated_count],
        ['Rejected', summary.rejected_count], ['Failed candidates', summary.failed_count],
        ['Unevaluable', summary.unevaluable_count], ['Expected publication', summary.expected_publication_count],
        ['Persisted', summary.persisted_count],
    ] as const;
    return <section aria-label="Candidate accounting" className="my-3 rounded border border-slate-700 p-3 text-sm">
        <div role={failed ? 'alert' : 'status'} className={failed ? 'text-red-400' : 'text-slate-300'}>
            {validated ? 'Publication validated' : `Publication: ${summary.state}`}
            {summary.partial && ' — retained partial results'}
            {summary.reason && <div>{summary.reason.code}: {summary.reason.message}</div>}
        </div>
        <div className="flex flex-wrap gap-4 text-slate-400">
            {fields.map(([label, count]) => <span key={label}>{label}: {count ?? 'unknown'}</span>)}
        </div>
        {summary.dispositions && <details className="text-slate-400">
            <summary>Candidate dispositions and reasons</summary>
            <ul>{summary.dispositions.map(item => <li key={item.candidate_id}>
                {item.candidate_id}: {item.disposition}{item.criterion && ` — ${item.criterion}`}{item.reason_code && `: ${item.reason_code}`}
            </li>)}</ul>
        </details>}
    </section>;
}
