import { useRef } from 'react';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchExecutionTargetRuntimeInventory, refreshExecutionTargetRuntimeInventory, type ExecutionTarget } from '../../lib/api';

const buttonClass = 'rounded-lg border border-[var(--border-primary)] px-3 py-1.5 text-sm disabled:opacity-50';
export function ManagedRuntimeInventoryPanel({ target }: { target: ExecutionTarget }) {
  const binding = JSON.stringify([target.id, target.host, target.port, target.username, target.remote_root,
    target.host_key_sha256, target.active, target.state, target.activated_at, target.preload?.operation_id,
    target.preload?.updated_at, target.progress?.operation_id]);
  return <InventoryObservation key={binding} target={target} binding={binding} />;
}
function InventoryObservation({ target, binding }: { target: ExecutionTarget; binding: string }) {
  const client = useQueryClient();
  const queryKey = ['managed-runtime-inventory', target.id, binding];
  const mutationKey = ['remote-preload', target.id];
  const active = useIsMutating({ mutationKey });
  const lock = useRef(false);
  const saved = useQuery({ queryKey, queryFn: () => fetchExecutionTargetRuntimeInventory(target.id),
    retry: false, staleTime: 0, refetchOnMount: 'always', refetchInterval: 30_000 });
  const refresh = useMutation({ mutationKey, retry: false,
    mutationFn: () => refreshExecutionTargetRuntimeInventory(target.id),
    onSuccess: data => { client.setQueryData(queryKey, data); },
    // Failure invalidates freshness on the server; reload that saved evidence, not another SSH request.
    onSettled: async () => { await client.invalidateQueries({ queryKey }); },
  });
  const allowed = target.active && target.state === 'ready' && !target.progress && active === 0
    && !['checking', 'transferring', 'verifying'].includes(target.preload?.phase ?? '');
  async function observe() {
    if (!allowed || lock.current || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    try { await refresh.mutateAsync(); } catch { /* Keep prior evidence and render failure. */ }
    finally { lock.current = false; }
  }
  const raw = saved.data;
  const invalid = raw != null && (raw.scope !== 'managed_independent_asset_releases'
    || !Array.isArray(raw.releases) || raw.critical_runtime_ready !== false || raw.scientific_ready !== false);
  const data = invalid ? undefined : raw;
  const error = refresh.error || saved.error || (invalid ? new Error('Unsupported managed inventory observation') : null);
  const message = isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail : error instanceof Error ? error.message : 'Inventory request failed';
  const fresh = data?.state === 'current' && !error;
  return <section aria-label="Managed runtime inventory" className="space-y-2 border-t border-[var(--border-primary)] pt-3 text-sm">
    <h5 className="font-medium">Managed asset-release inventory</h5>
    <p className="text-xs text-[var(--text-muted)]">Cumulative host-tracked model and image selections, not a whole-machine scan or every retained generation. Saved observations reload without contacting the worker. Explicit refresh rehashes installed assets over SSH; it does not download, provision, stage biological inputs, launch inference, or resume workflows.</p>
    <button type="button" className={buttonClass} disabled={!allowed} onClick={() => void observe()}>{refresh.isPending ? 'Observing installed assets…' : 'Refresh installed observation'}</button>
    {!allowed && !refresh.isPending && <p>Refresh requires an attached, ready, idle worker with no active preload.</p>}
    {saved.isPending && <p role="status">Loading saved installed observation…</p>}
    {error && <div role="alert"><p>{message}</p><button type="button" className={buttonClass} disabled={saved.isFetching} onClick={() => void saved.refetch()}>Reload saved observation</button></div>}
    {!saved.isPending && !data && <p>No saved managed observation. Installed asset state is unknown, not ready.</p>}
    <p>Critical runtime ready: false · Scientific ready: false</p>
    <p className="text-xs">Verified means installed bytes, modes and activation identity matched at observation, not compatibility or scientific acceptance. Incompatible indicates a permission-mode mismatch; unverified indicates absent or mismatched activation/manifest identity.</p>
    {data && <>
      <p>{fresh ? 'Fresh observation (current)' : 'Stale observation — refresh before relying on installed state'}</p>
      <p>Observed <time dateTime={data.observed_at}>{data.observed_at}</time></p>
      <p className="break-all text-xs">Boot identity {data.boot_id}</p>
      {data.releases.length === 0 && <p>No tracked releases in this observation. This is not readiness.</p>}
      <ul aria-label="Managed releases" className="space-y-3">
        {data.releases.map(release => <li key={`${release.selection.kind}:${release.selection.model_id}:${release.release_sha256}`} className="space-y-1 break-all">
          <p className="font-medium">{release.selection.kind} · {release.selection.model_id} · Release state: {release.state}</p>
          <p className="text-xs font-mono">Release SHA256 {release.release_sha256}</p>
          <p className="text-xs font-mono">Source revision {release.source_revision} · Tree {release.source_tree}</p>
          <ul aria-label={`${release.selection.kind} ${release.selection.model_id} installed artifacts`} className="max-h-64 space-y-1 overflow-auto text-xs">
            {release.artifacts.map(artifact => <li key={artifact.name}><p>{artifact.name} · Artifact state: {artifact.state}</p><p>{artifact.size_bytes.toLocaleString()} bytes · SHA256 {artifact.sha256}</p></li>)}
          </ul>
        </li>)}
      </ul>
    </>}
  </section>;
}
