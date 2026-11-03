import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { jobPollingInterval } from '../../lib/queryPolling';
import { errorText, statusText } from './alignmentText';

/** Reopen managed analysis/export Jobs without treating their output as an MSA. */
export default function AlignmentOperationResult({jobId, projectId, operation}: {jobId: string; projectId: string; operation: string}) {
    const base = `/api/family-msa/projects/${encodeURIComponent(projectId)}/operations/${encodeURIComponent(jobId)}`;
    const status = useQuery({queryKey: ['alignment-operation-result', projectId, jobId],
        queryFn: async ({signal}) => (await api.get<{status: string; artifact_url: string | null; error_message: string | null}>(base, {signal})).data,
        retry: false, refetchInterval: q => !q.state.data || ['queued', 'pending', 'running'].includes(q.state.data.status) ? jobPollingInterval(3000, q) : false,
        refetchIntervalInBackground: false});
    return <section aria-label="Alignment job">
        <h1>{operation === 'export' ? 'Alignment export' : operation === 'compare' ? 'Alignment comparison' : 'Ensemble support'}</h1>
        <p title={jobId}>Status: {status.data ? statusText(status.data.status) : 'Loading…'}</p>
        {(status.error || status.data?.error_message) && <p role="alert">{status.error ? errorText(status.error) : status.data?.error_message}</p>}
        {status.data?.artifact_url && <a href={`${base}/artifact`} download>Download {operation === 'export' ? 'ZIP' : 'report'}</a>}
        {operation === 'compare' && <p><Link to={`/alignments/compare?project_id=${encodeURIComponent(projectId)}&operation_job_id=${encodeURIComponent(jobId)}`}>Open comparison</Link></p>}
        <p><Link to={`/alignments/library?project_id=${encodeURIComponent(projectId)}`}>Open project alignments</Link></p>
    </section>;
}
