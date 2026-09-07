import { useRef, useState } from 'react';
import { ManagedRuntimeInventoryPanel } from './ManagedRuntimeInventoryPanel';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchProvisionCatalog, previewExecutionTargetProvision, provisionExecutionTarget,
  type CachedArtifactReceipt, type ExecutionTarget, type ProvisionSelection,
} from '../../lib/api';

interface Props {
  target: ExecutionTarget;
  onChanged: () => void | Promise<unknown>;
}
const buttonClass = 'rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-1.5 text-sm text-[var(--text-primary)] hover:bg-[var(--card-hover)] disabled:opacity-50';
const selectClass = 'mt-1 block w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 text-[var(--text-primary)]';
function errorText(error: unknown) {
  return isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail : error instanceof Error ? error.message : 'Provisioning request failed';
}
function ArtifactList({ artifacts }: { artifacts: CachedArtifactReceipt[] }) {
  return <ul className="max-h-64 space-y-2 overflow-auto text-xs" aria-label="Cache artifacts">
    {artifacts.map(artifact => <li key={artifact.name} className="break-all">
      <p className="font-mono">{artifact.name}</p>
      <p>{artifact.size_bytes.toLocaleString()} bytes · SHA256 <span className="font-mono">{artifact.sha256}</span></p>
    </li>)}
  </ul>;
}

/** All POSTs require clicks. Keyed boundaries discard stale/in-flight previews. */
export function IndependentProvisionPanel(props: Props) {
  const { target } = props;
  // The target API exposes last operation source identity, not the live host source.
  // Backend digest admission additionally rejects unobserved source/file changes.
  const binding = JSON.stringify([target.id, target.provider_instance_id, target.host, target.port,
    target.username, target.remote_root, target.host_key_sha256, target.active, target.state,
    target.activated_at, target.capabilities, target.preload?.operation_id,
    target.preload?.source_revision, target.preload?.source_tree, target.progress?.operation_id]);
  return <ProvisionChooser key={binding} {...props} />;
}
function ProvisionChooser({ target, onChanged }: Props) {
  const [kind, setKind] = useState<ProvisionSelection['kind']>('model');
  const [modelId, setModelId] = useState('');
  const catalog = useQuery({ queryKey: ['remote-provision-catalog'], queryFn: fetchProvisionCatalog, retry: false });
  const selections = catalog.isError ? [] : (catalog.data ?? []).filter(item => item.kind === kind && typeof item.model_id === 'string');
  const valid = selections.some(item => item.model_id === modelId);
  const inventory = target.artifact_inventory;
  return <section aria-label="Independent worker provisioning" className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-[var(--text-primary)]">
    <h4 className="font-medium">Provision model or image asset releases</h4>
    <p className="text-xs text-[var(--text-muted)]">No saved Job required. Model includes its reviewed runtime dependencies; image downloads only the container. Preview hashes managed source files without transferring them. Provisioning does not launch inference.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm">Provision scope<select aria-label="Provision scope" className={selectClass} value={kind} onChange={event => { setKind(event.target.value as ProvisionSelection['kind']); setModelId(''); }}>
        <option value="model">Model and runtime dependencies</option><option value="image">Container image only</option>
      </select></label>
      <label className="text-sm">Provision model<select aria-label="Provision model" className={selectClass} value={modelId} onChange={event => setModelId(event.target.value)} disabled={catalog.isPending || catalog.isError}>
        <option value="">Select a reviewed model</option>
        {selections.map(item => <option key={item.model_id} value={item.model_id}>{item.model_id}</option>)}
      </select></label>
    </div>
    {catalog.isPending && <p role="status">Loading provisioning catalog…</p>}
    {catalog.isError && <div role="alert"><p>{errorText(catalog.error)}</p><button type="button" className={buttonClass} onClick={() => void catalog.refetch()}>Retry catalog</button></div>}
    {!catalog.isPending && !catalog.isError && selections.length === 0 && <p>No reviewed selections available.</p>}
    <ProvisionActions key={JSON.stringify([kind, modelId, valid])} target={target} onChanged={onChanged} selection={valid ? { kind, model_id: modelId } : null} />
    <div aria-label="Last independent provision receipt" className="space-y-2 border-t border-[var(--border-primary)] pt-3 text-sm">
      <h5 className="font-medium">Last independent provision — cache receipt</h5>
      <p className="text-xs text-[var(--text-muted)]">Not a full installed inventory. Cache download verification is not scientific readiness or runtime activation. This last-cache receipt remains separate from the installed observation below.</p>
      {inventory ? <>
        <p>{inventory.state === 'download_verified' ? 'Cache downloads verified at observation' : 'Stale cache observation — provision again to re-verify'}</p>
        <p>{inventory.selection.kind} · {inventory.selection.model_id} · Observed {inventory.observed_at}</p>
        <p className="break-all text-xs">Operation {inventory.operation_id}</p>
        <ArtifactList artifacts={inventory.artifacts} />
      </> : <p>No independent provision observation. Installed artifacts are unknown.</p>}
    </div>
    <ManagedRuntimeInventoryPanel target={target} />
  </section>;
}
function ProvisionActions({ target, onChanged, selection }: Props & { selection: ProvisionSelection | null }) {
  const client = useQueryClient();
  // Shared with saved-Job preloading: neither can enqueue over the other.
  const mutationKey = ['remote-preload', target.id];
  const active = useIsMutating({ mutationKey });
  const lock = useRef(false);
  const [consumed, setConsumed] = useState(false);
  const preview = useMutation({ mutationFn: () => previewExecutionTargetProvision(target.id, selection!), retry: false });
  const provision = useMutation({
    mutationKey, mutationFn: (digest: string) => provisionExecutionTarget(target.id, { ...selection!, preview_sha256: digest }), retry: false,
    onSettled: async () => { await client.invalidateQueries({ queryKey: ['execution-targets'] }); await onChanged(); },
  });
  const busy = active > 0 || ['checking', 'transferring', 'verifying'].includes(target.preload?.phase ?? '');
  const allowed = target.active && target.state === 'ready' && !target.progress && !busy;
  const data = !consumed && preview.data?.scope === 'managed_asset_activation' && preview.data?.selection.kind === selection?.kind && preview.data?.selection.model_id === selection?.model_id ? preview.data : undefined;
  async function requestPreview() {
    if (lock.current || !allowed || !selection || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    setConsumed(false);
    preview.reset();
    provision.reset();
    try { await preview.mutateAsync(); } catch { /* Render the API error; retry is explicit. */ }
    finally { lock.current = false; }
  }
  async function start() {
    if (lock.current || !allowed || !data || consumed || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    setConsumed(true); // Even a rejected start requires a new byte-bound preview.
    try { await provision.mutateAsync(data.preview_sha256); } catch { /* Render the API error. */ }
    finally { lock.current = false; }
  }
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      <button type="button" className={buttonClass} disabled={!allowed || !selection || preview.isPending} onClick={() => void requestPreview()}>{preview.isPending ? 'Hashing preview…' : 'Preview artifact downloads'}</button>
      <button type="button" className={buttonClass} disabled={!allowed || !data || preview.isPending} onClick={() => void start()}>{provision.isPending ? 'Starting provision…' : 'Start provision'}</button>
    </div>
    {!allowed && <p className="text-xs text-[var(--text-muted)]">Provisioning requires an attached, ready, idle worker with no active preload.</p>}
    {(preview.error || provision.error) && <p role="alert" className="text-sm text-[var(--error)]">{errorText(preview.error || provision.error)}</p>}
    {provision.isSuccess && <p role="status">Provision request accepted. Completion is reported by worker progress; installed evidence is shown separately from the last-cache receipt.</p>}
    {data && <div aria-label="Provision preview" className="space-y-2 text-sm">
      <p>{data.total_bytes.toLocaleString()} dependency bytes total · Managed asset activation — not scientific Ready</p>
      <p>Provisioning makes an additional installed copy separate from cache and retains prior release generations. This total is not a missing-byte transfer estimate, free-space check or storage reservation.</p>
      <p className="break-all text-xs font-mono">Preview SHA256 {data.preview_sha256}</p>
      <ArtifactList artifacts={data.artifacts} />
    </div>}
  </div>;
}
