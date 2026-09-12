import { useRef } from 'react';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchExecutionTargetRuntimeInventory, refreshExecutionTargetRuntimeInventory, provisionSelectionLabel, type ExecutionTarget } from '../../lib/api';

const buttonClass = 'rounded-lg border border-[var(--border-primary)] px-3 py-1.5 text-sm disabled:opacity-50';
export function ManagedRuntimeInventoryPanel({ target }: { target: ExecutionTarget }) {
  const binding = JSON.stringify([target.id, target.provider_instance_id, target.host, target.port, target.username, target.remote_root,
    target.host_key_sha256, target.active, target.state, target.activated_at, target.preload?.operation_id,
    target.preload?.updated_at, target.preload?.phase, target.preload?.recovery_required, target.preload?.source_revision, target.preload?.source_tree, target.progress?.operation_id]);
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
    && !target.preload?.recovery_required
    && !['checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked'].includes(target.preload?.phase ?? '');
  async function observe() {
    if (!allowed || lock.current || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    try { await refresh.mutateAsync(); } catch { /* Keep prior evidence and render failure. */ }
    finally { lock.current = false; }
  }
  const raw = saved.data;
  const invalid = raw != null && (raw.scope !== 'managed_independent_asset_releases'
    || !Array.isArray(raw.releases) || typeof raw.critical_runtime_ready !== 'boolean' || raw.scientific_ready !== false);
  const data = invalid ? undefined : raw;
  const error = refresh.error || saved.error || (invalid ? new Error('Unsupported managed inventory observation') : null);
  const message = isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail : error instanceof Error ? error.message : 'Inventory request failed';
  const fresh = data?.state === 'current' && !error && target.active && target.state === 'ready'
    && !target.preload?.recovery_required
    && !['checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked'].includes(target.preload?.phase ?? '');
  return <section aria-label="Managed runtime inventory" className="space-y-2 border-t border-[var(--border-primary)] pt-3 text-sm">
    <h5 className="font-medium">Managed asset-release inventory</h5>
    <p className="text-xs text-[var(--text-muted)]">Cumulative host-tracked model and image selections, not a whole-machine scan or every retained generation. Saved observations reload without contacting the worker. Explicit refresh rehashes installed assets over SSH; it does not download, provision, stage biological inputs, launch inference, or resume workflows.</p>
    <button type="button" className={buttonClass} disabled={!allowed} onClick={() => void observe()}>{refresh.isPending ? 'Observing installed assets…' : 'Refresh installed observation'}</button>
    {!allowed && !refresh.isPending && <p>Refresh requires an attached, ready, idle worker with no active preload.</p>}
    {saved.isPending && <p role="status">Loading saved installed observation…</p>}
    {error && <div role="alert"><p>{message}</p><button type="button" className={buttonClass} disabled={saved.isFetching} onClick={() => void saved.refetch()}>Reload saved observation</button></div>}
    {!saved.isPending && !data && <p>No saved managed observation. Installed asset state is unknown, not ready.</p>}
    <p>Critical runtime ready: {fresh ? String(data?.critical_runtime_ready) : 'unknown (no fresh evidence)'} · Scientific ready: false (not checked)</p>
    <p className="text-xs">Verified means installed bytes, modes and activation identity matched at observation, not scientific acceptance. Incompatible indicates a permission-mode or critical-runtime compatibility mismatch; unverified indicates absent or mismatched activation/manifest identity.</p>
    {data && <>
      <p>{fresh ? 'Fresh observation (current)' : 'Stale observation — refresh before relying on installed state'}</p>
      <p>Observed <time dateTime={data.observed_at}>{data.observed_at}</time></p>
      <p className="break-all text-xs">Boot identity {data.boot_id}</p>
      {!fresh && <p>Last observed critical readiness: {String(data.critical_runtime_ready)} — not current readiness.</p>}
      <ul aria-label="Inventory blockers">{data.blockers.map(blocker => <li key={blocker}>{blocker}</li>)}</ul>
      {data.releases.length === 0 && <p>No tracked releases in this observation. This is not readiness.</p>}
      <ul aria-label="Managed releases" className="space-y-3">
        {data.releases.map(release => <li key={`${release.selection.kind}:${release.release_sha256}`} className="space-y-1 break-all">
          <p className="font-medium">{release.selection.kind} · {provisionSelectionLabel(release.selection)} · Release state: {release.state}</p>
          <p className="text-xs font-mono">Release SHA256 {release.release_sha256}</p>
          <p>Bounded readiness: {fresh ? release.bounded_readiness ?? 'unknown (not checked)' : 'stale'} · Asset integrity and critical compatibility only, not inference acceptance.</p>
          <div aria-label="Bounded native preflight">
            <p>Native preflight state: {fresh ? release.native_readiness?.state ?? 'unverified (not checked)' : 'stale'} · Native runtime checks only, not full workflow or inference acceptance.</p>
            {release.native_readiness?.probe && <p>
              Last recorded native preflight: {release.native_readiness.probe.outcome}
              {release.native_readiness.probe.gpu_id != null && ` · GPU ${release.native_readiness.probe.gpu_id}`}
              {release.native_readiness.probe.gpu_uuid && ` · ${release.native_readiness.probe.gpu_uuid}`}
              {release.native_readiness.probe.observed_at && ` · ${release.native_readiness.probe.observed_at}`}
              {!fresh && ' (historical; not current readiness)'}
            </p>}
            {Array.isArray(release.native_readiness?.blockers) && release.native_readiness.blockers.map(reason => <p key={reason}>{reason}</p>)}
            {Array.isArray(release.native_readiness?.missing_authorities) && release.native_readiness.missing_authorities.map(reason => <p key={reason}>Not verified: {reason}</p>)}
          </div>
          <p className="text-xs font-mono">Source revision {release.source_revision} · Tree {release.source_tree}</p>
          {release.selection.kind === 'critical_runtime' && <div aria-label="Critical runtime compatibility">
            <p>Compatibility: {release.critical ? String(release.critical.compatible) : 'unknown — no compatibility evidence'}{!fresh && ' (stale)'}</p>
            {release.critical && <ul>{Array.from(new Set([...Object.keys(release.critical.requirements), ...Object.keys(release.critical.observed)])).map(key => <li key={key}>
              {key}: required {release.critical?.requirements[key] ?? 'not specified'} · observed {release.critical?.observed[key] ?? 'missing'}
            </li>)}</ul>}
          </div>}
          <ul aria-label={`${release.selection.kind} ${provisionSelectionLabel(release.selection)} installed artifacts`} className="max-h-64 space-y-1 overflow-auto text-xs">
            {release.artifacts.map(artifact => <li key={artifact.name}><p>{artifact.name} · Artifact state: {artifact.state}</p><p>{artifact.size_bytes.toLocaleString()} bytes · SHA256 {artifact.sha256}</p></li>)}
          </ul>
        </li>)}
      </ul>
    </>}
  </section>;
}
