import { useState } from 'react';
import { IndependentProvisionPanel } from './IndependentProvisionPanel';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query';
import { preloadExecutionTarget, type ExecutionTarget } from '../../lib/api';

interface Props {
  target: ExecutionTarget;
  jobs: Array<{ id: string; model_id: string; name?: string | null }>;
  onChanged: () => void | Promise<unknown>;
}

/** Cache preparation is operator initiated; polling never sends a POST. */
export function RemotePreloadPanel({ target, jobs, onChanged }: Props) {
  const [jobId, setJobId] = useState('');
  const queryClient = useQueryClient();
  const mutationKey = ['remote-preload', target.id];
  const activePreloads = useIsMutating({ mutationKey });
  const mutation = useMutation({
    mutationKey,
    mutationFn: (recipeId: string) => preloadExecutionTarget(target.id, recipeId),
    retry: false, // Admission and retry always require an explicit operator click.
    onSettled: () => onChanged(),
  });
  const error = isAxiosError(mutation.error) && typeof mutation.error.response?.data?.detail === 'string'
    ? mutation.error.response.data.detail : mutation.error?.message;
  const preload = target.preload;
  const progress = target.progress;
  const busy = mutation.isPending || activePreloads > 0 || ['checking', 'transferring', 'verifying'].includes(preload?.phase ?? '');
  const validRecipe = jobs.some(job => job.id === jobId);
  const canPreload = target.active && target.state === 'ready' && !progress && !busy;
  function submit() {
    if (!validRecipe || !canPreload || queryClient.isMutating({ mutationKey }) > 0) return;
    mutation.mutate(jobId);
  }
  return <section aria-label="Remote preload and activity" className="space-y-3 rounded-lg border border-[var(--border-primary)] p-3">
    <IndependentProvisionPanel target={target} onChanged={onChanged} />
    <h4 className="font-medium">Preload source and runtime files</h4>
    <p className="text-xs text-[var(--text-muted)]">Use a saved Job as the exact dependency recipe. This does not submit a Job or transfer biological inputs, results, or secrets. Downloads ready does not mean scientific Ready.</p>
    <label className="block text-sm">Saved Job recipe
      <select aria-label="Saved Job recipe" value={jobId} disabled={busy} onChange={event => setJobId(event.target.value)} className="mt-1 block w-full rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2">
        <option value="">Select a saved Job</option>
        {jobs.map(job => <option key={job.id} value={job.id}>{job.name || job.id} — {job.model_id}</option>)}
      </select>
    </label>
    <button type="button" disabled={!canPreload || !validRecipe} onClick={() => void submit()} className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-sm disabled:opacity-50">
      {busy ? 'Preloading…' : preload?.phase === 'failed' ? 'Retry preload' : 'Preload selected worker'}
    </button>
    {error && <p role="alert" className="text-sm text-[var(--error)]">{error}</p>}
    {preload && <div role="status" aria-label="Preload progress" className="text-sm">
      {preload.phase === 'source_download_ready' && <p>Source/download ready — not scientific Ready</p>}
      <p>{preload.message}</p>
      {preload.artifact && <p className="break-all font-mono">{preload.artifact}</p>}
      <p className="text-xs text-[var(--text-muted)]">{preload.selection ? `${preload.selection.kind} ${preload.selection.model_id}` : `Recipe ${preload.job_id}`} · Source {preload.source_revision.slice(0, 12)} · Updated {preload.updated_at}</p>
    </div>}
    {progress && <div role="status" aria-label="Worker activity" className="text-sm">
      <p>{progress.message}</p>
      {progress.artifact && <p className="break-all font-mono">{progress.artifact}</p>}
      {progress.activity && <p>{progress.activity.stage}: {progress.activity.state}</p>}
      <p className="text-xs text-[var(--text-muted)]">Job {progress.job_id} · Updated {progress.updated_at}</p>
    </div>}
  </section>;
}
