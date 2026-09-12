import { useRef, useState } from 'react';
import { ManagedRuntimeInventoryPanel } from './ManagedRuntimeInventoryPanel';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchProvisionCatalog, previewExecutionTargetProvision, provisionExecutionTarget,
  cancelExecutionTargetProvision, retryExecutionTargetProvision,
  provisionSelectionLabel, type WorkflowProvisionRequest, type CatalogProvisionSelection,
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
    target.preload?.source_revision, target.preload?.source_tree, target.preload?.phase, target.preload?.recovery_required, target.progress?.operation_id]);
  return <ProvisionChooser key={binding} {...props} />;
}
function ProvisionChooser({ target, onChanged }: Props) {
  const [kind, setKind] = useState<CatalogProvisionSelection['kind']>('model');
  const [modelId, setModelId] = useState('');
  const catalog = useQuery({ queryKey: ['remote-provision-catalog'], queryFn: fetchProvisionCatalog, retry: false });
  const selections = catalog.isError ? [] : (catalog.data ?? []).filter(item => item.kind === kind && typeof item.model_id === 'string');
  const valid = selections.some(item => item.model_id === modelId);
  const inventory = target.artifact_inventory;
  return <section aria-label="Independent worker provisioning" className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-[var(--text-primary)]">
    <h4 className="font-medium">Provision model or image asset releases</h4>
    <p className="text-xs text-[var(--text-muted)]">No saved Job required. Model includes its reviewed runtime dependencies; image downloads only the container. Preview hashes managed source files without transferring them. Provisioning does not launch inference.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm">Provision scope<select aria-label="Provision scope" className={selectClass} value={kind} onChange={event => { setKind(event.target.value as CatalogProvisionSelection['kind']); setModelId(''); }}>
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
        <p>{inventory.selection.kind} · {provisionSelectionLabel(inventory.selection)} · Observed {inventory.observed_at}</p>
        <p className="break-all text-xs">Operation {inventory.operation_id}</p>
        <ArtifactList artifacts={inventory.artifacts} />
      </> : <p>No independent provision observation. Installed artifacts are unknown.</p>}
    </div>
    <ManagedRuntimeInventoryPanel target={target} />
    <ProvisionOperation target={target} onChanged={onChanged} />
  </section>;
}
export function WorkflowProvisionPanel({ target, onChanged, workflowRequest }: Props & { workflowRequest: WorkflowProvisionRequest }) {
  const selection: ProvisionSelection = { kind: 'workflow', workflow_request: workflowRequest };
  const binding = JSON.stringify([selection, target.id, target.provider_instance_id, target.host, target.port, target.username, target.remote_root,
    target.host_key_sha256, target.active, target.state, target.activated_at, target.capabilities,
    target.preload?.operation_id, target.preload?.source_revision, target.preload?.source_tree, target.preload?.phase, target.preload?.recovery_required, target.progress?.operation_id]);
  return <section aria-label="Unsaved workflow provisioning" className="mt-3 space-y-3">
    <h4>Provision this workflow's dependencies without launching</h4>
    <p className="text-xs">Uses the current typed workflow request. No saved Job, biological input staging, MSA service request or inference is created by provisioning. Scientific launch restrictions remain separate.</p>
    <ProvisionActions key={binding} target={target} onChanged={onChanged} selection={selection} />
    <ProvisionOperation target={target} onChanged={onChanged} />
  </section>;
}
function ProvisionActions({ target, onChanged, selection, retryOperationId }: Props & { selection: ProvisionSelection | null; retryOperationId?: string }) {
  const client = useQueryClient();
  // Shared with saved-Job preloading: neither can enqueue over the other.
  const mutationKey = ['remote-preload', target.id];
  const active = useIsMutating({ mutationKey });
  const lock = useRef(false);
  const [consumed, setConsumed] = useState(false);
  const preview = useMutation({ mutationFn: () => previewExecutionTargetProvision(target.id, selection!), retry: false });
  const provision = useMutation({
    mutationKey, mutationFn: (digest: string) => retryOperationId
      ? retryExecutionTargetProvision(target.id, retryOperationId, { ...selection!, preview_sha256: digest })
      : provisionExecutionTarget(target.id, { ...selection!, preview_sha256: digest }), retry: false,
    onSettled: () => onChanged(),
  });
  const busy = active > 0 || target.preload?.recovery_required || ['checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked'].includes(target.preload?.phase ?? '');
  const allowed = target.active && target.state === 'ready' && !target.progress && !busy;
  // The keyed boundary binds the entire request, including every scientific setting.
  // Server-normalized workflow selections may contain additional schema defaults.
  const matchesSelection = preview.data?.selection.kind === selection?.kind
    && (selection?.kind === 'workflow' || (preview.data?.selection.kind !== 'workflow' && preview.data?.selection.model_id === selection?.model_id));
  const data = !consumed && preview.data?.scope === 'managed_asset_activation' && matchesSelection ? preview.data : undefined;
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
    if (lock.current || !allowed || !data || data.blockers?.length || consumed || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    setConsumed(true); // Even a rejected start requires a new byte-bound preview.
    try { await provision.mutateAsync(data.preview_sha256); } catch { /* Render the API error. */ }
    finally { lock.current = false; }
  }
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      <button type="button" className={buttonClass} disabled={!allowed || !selection || preview.isPending} onClick={() => void requestPreview()}>{preview.isPending ? 'Hashing preview…' : 'Preview artifact downloads'}</button>
      <button type="button" className={buttonClass} disabled={!allowed || !data || !!data.blockers?.length || preview.isPending} onClick={() => void start()}>{provision.isPending ? 'Starting provision…' : retryOperationId ? 'Retry provision with fresh preview' : 'Start provision'}</button>
    </div>
    {!allowed && <p className="text-xs text-[var(--text-muted)]">Provisioning requires an attached, ready, idle worker with no active preload.</p>}
    {(preview.error || provision.error) && <p role="alert" className="text-sm text-[var(--error)]">{errorText(preview.error || provision.error)}</p>}
    {provision.isSuccess && <p role="status">Provision request accepted. Completion is reported by worker progress; installed evidence is shown separately from the last-cache receipt.</p>}
    {data && <div aria-label="Provision preview" className="space-y-2 text-sm">
      <p>{data.total_bytes.toLocaleString()} dependency bytes total · Managed asset activation — not scientific Ready</p>
      <p>Provisioning makes an additional installed copy separate from cache and retains prior release generations. This total is not a missing-byte transfer estimate, free-space check or storage reservation.</p>
      <p>Destination: {data.destination ? `${data.destination.target_id} · ${data.destination.remote_root}` : 'not reported'}</p>
      <p>Installed inventory evidence: {data.inventory_state ?? 'unobserved'}</p>
      <p>Transfer upper bound: {data.transfer_bytes?.toLocaleString() ?? 'unknown'} bytes · Selected storage: {data.storage_bytes?.toLocaleString() ?? 'unknown'} bytes. Neither is free disk capacity or an ETA.</p>
      <ul aria-label="Provision blockers">{data.blockers?.map(blocker => <li key={blocker}>{blocker}</li>)}</ul>
      {data.plan_sha256 && <p className="break-all font-mono">Plan SHA256 {data.plan_sha256}</p>}
      {data.asset_states && <ul aria-label="Exact dependency states">{data.asset_states.map(asset => <li key={asset.name} className="break-all">{asset.name} · {asset.state} · {asset.size_bytes.toLocaleString()} bytes · SHA256 {asset.sha256}</li>)}</ul>}
      {data.effective_params && <details><summary>Effective workflow settings (read only)</summary><SettingValues value={data.effective_params} /></details>}
      <p className="break-all text-xs font-mono">Preview SHA256 {data.preview_sha256}</p>
      <ArtifactList artifacts={data.artifacts} />
    </div>}
  </div>;
}

function SettingValues({ value }: { value: unknown }) {
  if (value === null || typeof value !== 'object') return <span>{String(value)}</span>;
  return <dl className="ml-3 space-y-1">{Object.entries(value).map(([key, item]) => <div key={key}><dt className="font-medium">{key}</dt><dd><SettingValues value={item} /></dd></div>)}</dl>;
}

function ProvisionOperation({ target, onChanged }: Props) {
  const client = useQueryClient();
  const mutationKey = ['remote-preload', target.id];
  const active = useIsMutating({ mutationKey });
  const lock = useRef(false);
  const operation = target.preload;
  const cancel = useMutation({ mutationKey, retry: false,
    mutationFn: () => cancelExecutionTargetProvision(target.id, operation!.operation_id),
    onSettled: () => onChanged(),
  });
  if (!operation?.selection) return null;
  const cancellable = operation.recovery_required || ['checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked'].includes(operation.phase);
  async function requestCancel() {
    if (lock.current || !cancellable || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    try { await cancel.mutateAsync(); } catch { /* Server owns quiescence and recovery. */ }
    finally { lock.current = false; }
  }
  return <section aria-label="Provision operation" className="space-y-2 border-t pt-3 text-sm">
    <h5>Provision operation {operation.operation_id}</h5>
    <p>{provisionSelectionLabel(operation.selection)} · {operation.phase} · Sequence {operation.sequence ?? 'not reported'}</p>
    <p>{operation.message} · Updated {operation.updated_at}</p>
    {operation.artifact && <p>Active artifact: {operation.artifact}</p>}
    <ul aria-label="Artifact progress">{operation.artifact_progress?.map(artifact => <li key={artifact.name} className="break-all">{artifact.name} · {artifact.state} · {artifact.size_bytes.toLocaleString()} declared bytes · SHA256 {artifact.sha256}</li>)}</ul>
    <p className="text-xs">Artifact states are reported activity, not invented byte percentages or scientific acceptance. Completed verified objects are retained for retry.</p>
    {(operation.cancel_requested || operation.recovery_required) && <p role="status">Cancellation requested or recovery required. Ownership is not released until the server proves underlying transport stopped. No automatic retry.</p>}
    {cancellable && <button type="button" className={buttonClass} disabled={active > 0} onClick={() => void requestCancel()}>{cancel.isPending ? 'Requesting cancellation…' : operation.phase === 'recovery_blocked' ? 'Recheck cancellation quiescence' : 'Cancel provision'}</button>}
    {cancel.error && <p role="alert">{errorText(cancel.error)}</p>}
    {['failed', 'cancelled', 'recovery_blocked'].includes(operation.phase) && <ProvisionActions
      key={JSON.stringify([operation.operation_id, operation.selection, operation.phase, target.id, target.host, target.port,
        target.username, target.remote_root, target.host_key_sha256, target.activated_at, target.active, target.state,
        target.capabilities, operation.source_revision, operation.source_tree, operation.recovery_required, target.provider_instance_id, target.progress?.operation_id])}
      target={target} onChanged={onChanged} selection={operation.selection} retryOperationId={operation.operation_id} />}
  </section>;
}
