import { lazy, Suspense, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, buildFileDownloadUrl, continueComponentCheckpoint, prepareJobSubmission, type Job } from '../lib/api';
import { initialExecutionPolicy } from '../lib/executionPolicy';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { ExecutionPolicyControl } from './ExecutionPolicyControl';

const StructureWorkbench = lazy(() => import('../structureViewer/StructureWorkbench').then(module => ({ default: module.StructureWorkbench })));
interface ReviewFile { relative_path: string; path: string; name: string; type: string; sha256: string; schema: string }
interface ReviewSet { checkpoint_id: string; checkpoint_sha256: string; structures: ReviewFile[]; artifacts: ReviewFile[] }

/** Completed-stage artifact selection; placement never implies tensor/process migration. */
export function CheckpointReview({ job, launchContextId }: { job: Job; launchContextId?: string | null }) {
    const checkpoint = job.awaiting_payload?.component_checkpoint as { checkpoint_id: string; checkpoint_sha256: string };
    const queryClient = useQueryClient();
    const [selected, setSelected] = useState<string[]>([]);
    const [inspected, setInspected] = useState<string>();
    const [target, setTarget] = useState<string | null | undefined>(undefined);
    const [policy, setPolicy] = useState(() => job.execution_policy ?? initialExecutionPolicy());
    const review = useQuery({ queryKey: ['checkpoint-review', job.id, checkpoint.checkpoint_sha256],
        queryFn: async ({ signal }) => (await api.get<ReviewSet>(`/api/jobs/${encodeURIComponent(job.id)}/structure-files`, {
            signal, params: { checkpoint_sha256: checkpoint.checkpoint_sha256 },
        })).data, retry: false });
    const continuation = useMutation({ mutationFn: () => continueComponentCheckpoint(job.id, {
        checkpoint_id: checkpoint.checkpoint_id, checkpoint_sha256: checkpoint.checkpoint_sha256,
        checkpoint_decision: { selected_artifacts: [...selected] },
        ...(target !== undefined ? prepareJobSubmission({ execution_target_id: target, execution_policy: policy,
            ...(launchContextId ? { launch_context_id: launchContextId } : {}) }) : {}),
    }), onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['jobs'] }); } });
    const file = review.data?.structures.find(item => item.relative_path === inspected);
    const successor = continuation.data?.data.new_job_id ?? continuation.data?.data.job_id;
    return <section aria-label="Completed stage continuation" className="space-y-3">
        <h3>Review completed stage</h3>
        <p>Keep the current execution target to resume its checkpoint. Choosing Local or a different worker creates a successor from these retained outputs; it does not move a running process.</p>
        {review.isLoading && <p role="status">Loading checkpoint artifacts…</p>}
        {review.error && <p role="alert">{review.error.message}</p>}
        {review.data?.structures.map(item => <div key={item.relative_path}>
            <label><input type="checkbox" aria-label={`Continue with ${item.relative_path}`} checked={selected.includes(item.relative_path)} disabled={continuation.isPending}
                onChange={event => setSelected(previous => event.target.checked ? [...previous, item.relative_path] : previous.filter(path => path !== item.relative_path))} />{item.name}</label>
            <button type="button" onClick={() => setInspected(item.relative_path)}>Inspect {item.name}</button>
            <a href={buildFileDownloadUrl(item.path)} download>Download {item.name}</a>
        </div>)}
        {file && <Suspense fallback={<p>Loading structure viewer…</p>}><StructureWorkbench key={file.relative_path} structureUrl={buildFileDownloadUrl(file.path)} format={file.type === 'cif' ? 'cif' : 'pdb'} height="500px" /></Suspense>}
        <ExecutionTargetPicker value={target === undefined ? job.execution_target_id ?? '' : target ?? ''} onChange={setTarget} disabled={continuation.isPending} />
        {target === undefined ? <p>Default: keep the current execution target.</p> : <button type="button" disabled={continuation.isPending} onClick={() => setTarget(undefined)}>Keep current execution target</button>}
        {target !== undefined && target !== (job.execution_target_id ?? null) && <ExecutionPolicyControl initialPolicy={policy} onChange={setPolicy} />}
        <button type="button" disabled={!selected.length || continuation.isPending} onClick={() => continuation.mutate()}>Continue selected stage outputs</button>
        {continuation.error && <p role="alert">{continuation.error.message}</p>}
        {continuation.isSuccess && <p role="status">Continuation submitted.{successor && <> <Link to={`/designs/${encodeURIComponent(successor)}`}>Open continued Job</Link></>}</p>}
    </section>;
}
