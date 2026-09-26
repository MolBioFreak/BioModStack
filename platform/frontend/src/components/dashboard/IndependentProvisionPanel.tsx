import { ArtifactDetails } from './ArtifactDetails';
import { launcherWorkflowTemplates, launcherExperimentalTemplates, visibleLauncherTemplates } from '../../lib/launcherCatalog';
import { useEffect, useRef, useState } from 'react';
import { ManagedRuntimeInventoryPanel } from './ManagedRuntimeInventoryPanel';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchModels, fetchTemplates, fetchProvisionCatalog, previewExecutionTargetProvision, provisionExecutionTarget,
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
// One readiness statement for the whole section; individual figures no longer repeat it.
const PREPARATION_CAVEAT = 'Preparation downloads and verifies assets on this worker. It does not launch a job, run inference or establish scientific readiness.';
const ACTIVE_PHASES = ['checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked'];
const PHASE_LABELS: Record<string, string> = {
  checking: 'Checking worker cache',
  transferring: 'Downloading assets',
  verifying: 'Verifying downloads',
  cancelling: 'Cancelling',
  recovery_blocked: 'Needs attention — transport not proven stopped',
  failed: 'Failed',
  cancelled: 'Cancelled',
  source_download_ready: 'Assets ready',
};
const ARTIFACT_STATE_LABELS: Record<string, string> = {
  pending: 'queued', transferring: 'downloading', verifying: 'verifying', verified: 'verified', interrupted: 'interrupted',
};
function errorText(error: unknown) {
  return isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail : error instanceof Error ? error.message : 'Provisioning request failed';
}
function bytes(n: number) {
  return `${n.toLocaleString()} bytes`;
}
function elapsedLabel(startedAt: string, updatedAt?: string) {
  const start = Date.parse(startedAt);
  const end = updatedAt ? Date.parse(updatedAt) : NaN;
  if (!Number.isFinite(start)) return null;
  const seconds = Math.max(0, ((Number.isFinite(end) ? end : Date.now()) - start) / 1000);
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
function ArtifactList({ artifacts }: { artifacts: CachedArtifactReceipt[] }) {
  return <ArtifactDetails label="Cache artifacts" count={artifacts.length}>{() => <ul className="space-y-2 text-xs" aria-label="Cache artifacts">
    {artifacts.map(artifact => <li key={artifact.name} className="break-all">
      <p className="font-mono">{artifact.name}</p>
      <p>{bytes(artifact.size_bytes)} · SHA256 <span className="font-mono">{artifact.sha256}</span></p>
    </li>)}
  </ul>}</ArtifactDetails>;
}

/** All POSTs require clicks. Keyed boundaries discard stale/in-flight previews. */
export function IndependentProvisionPanel(props: Props) {
  const { target, onChanged } = props;
  const client = useQueryClient();
  // The target API exposes last operation source identity, not the live host source.
  // Backend digest admission additionally rejects unobserved source/file changes.
  const binding = JSON.stringify([target.id, target.provider_instance_id, target.host, target.port,
    target.username, target.remote_root, target.host_key_sha256, target.active, target.state,
    target.activated_at, target.capabilities, target.preload?.operation_id,
    target.preload?.source_revision, target.preload?.source_tree, target.preload?.phase, target.preload?.recovery_required, target.progress?.operation_id]);
  // Refresh installed evidence once as a preparation settles: the section is at its stalest
  // exactly when the operator wants the result. This lives outside the keyed child, because a
  // phase change re-keys and remounts that child and would lose the transition.
  const operation = target.preload;
  const operationId = operation?.operation_id ?? null;
  const artifacts = operation?.artifact_progress ?? [];
  const settled = Boolean(operation) && ((artifacts.length > 0 && artifacts.every(artifact => artifact.state === 'verified'))
    || ['failed', 'cancelled', 'recovery_blocked'].includes(operation!.phase));
  const observed = useRef<{ id: string; settled: boolean } | null>(null);
  useEffect(() => {
    const previous = observed.current;
    observed.current = operationId ? { id: operationId, settled } : null;
    if (!operationId || !settled || !previous || previous.id !== operationId || previous.settled) return;
    void onChanged();
    void client.invalidateQueries({ queryKey: ['managed-runtime-inventory', target.id] });
  }, [operationId, settled, onChanged, client, target.id]);
  return <ProvisionChooser key={binding} {...props} />;
}
function ProvisionChooser({ target, onChanged }: Props) {
  const [kind, setKind] = useState<CatalogProvisionSelection['kind'] | 'workflow'>('workflow');
  const [modelId, setModelId] = useState('');
  const catalog = useQuery({ queryKey: ['remote-provision-catalog'], queryFn: fetchProvisionCatalog, retry: false });
  const models = useQuery({ queryKey: ['models'], queryFn: () => fetchModels(), retry: false });
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => fetchTemplates(), retry: false });
  const selections = catalog.isError ? [] : (catalog.data ?? []).filter(item => item.kind === kind);
  const modelEntries = models.isError ? [] : (models.data?.data ?? []);
  const workflows = [...new Map([
    ...visibleLauncherTemplates(templates.isError ? [] : templates.data?.data ?? [], Boolean((window as Window & { __DEBUG_MODE__?: boolean }).__DEBUG_MODE__)),
    ...launcherWorkflowTemplates, ...launcherExperimentalTemplates,
  ].map(item => [item.id, item])).values()];
  const entries = kind === 'workflow' ? workflows : modelEntries;
  const valid = kind !== 'workflow' && !models.isError && modelEntries.some(item => item.id === modelId)
    && selections.some(item => item.model_id === modelId);
  const selectedWorkflow = kind === 'workflow' ? workflows.find(item => item.id === modelId) : undefined;
  const familySelection = !catalog.isError && selectedWorkflow?.id === DE_NOVO_PRELOAD_SELECTION.model_id
    ? catalog.data?.find(item => item.kind === 'model' && item.model_id === selectedWorkflow.id) : undefined;
  const operation = target.preload;
  const live = Boolean(operation) && (ACTIVE_PHASES.includes(operation!.phase) || Boolean(operation!.recovery_required));
  return <section aria-label="Independent worker provisioning" className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-[var(--text-primary)]">
    <h4 className="font-medium">Worker preparation</h4>
    <p className="text-xs text-[var(--text-muted)]">{PREPARATION_CAVEAT} Configure a workflow for exact dependencies; model and image scopes prepare only the reviewed assets listed for that scope.</p>
    {live && <PreparationStatus target={target} onChanged={onChanged} />}
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm">Provision scope<select aria-label="Provision scope" className={selectClass} value={kind} onChange={event => { setKind(event.target.value as CatalogProvisionSelection['kind'] | 'workflow'); setModelId(''); }}>
        <option value="workflow">Workflow</option><option value="model">Model (reviewed managed assets only)</option><option value="image">Container image only (not weights)</option>
      </select></label>
      <label className="text-sm">{kind === 'workflow' ? 'Preparation workflow' : 'Provision model'}<select aria-label={kind === 'workflow' ? 'Preparation workflow' : 'Provision model'} className={selectClass} value={modelId} onChange={event => setModelId(event.target.value)} disabled={kind !== 'workflow' && (models.isPending || models.isError)}>
        <option value="">Select a model or workflow</option>
        {entries.map(item => <option key={item.id} value={item.id}>{item.name}{kind !== 'workflow' && !selections.some(selection => selection.model_id === item.id) ? ' (not available in this scope)' : ''}</option>)}
      </select></label>
    </div>
    {catalog.isPending && <p role="status">Loading provisioning catalog…</p>}
    {catalog.isError && <div role="alert"><p>{errorText(catalog.error)}</p><button type="button" className={buttonClass} onClick={() => void catalog.refetch()}>Retry catalog</button></div>}
    {kind !== 'workflow' && !catalog.isPending && !catalog.isError && selections.length === 0 && <p>No reviewed selections available.</p>}
    {[models, templates].map((query, index) => query.isError && <div role="alert" key={index}>
      <p>{index === 0 ? 'Model registry' : 'Workflow catalog'}: {errorText(query.error)}</p>
      <button type="button" className={buttonClass} onClick={() => void query.refetch()}>Retry {index === 0 ? 'models' : 'workflows'}</button>
    </div>)}
    {kind !== 'workflow' && modelId && !valid && <p role="status">Model scope needs reviewed managed assets for this model in this deployment, and {modelId} has none. Use Container image only, or a configured workflow's dependency preview.</p>}
    {selectedWorkflow && <div className="space-y-2 text-sm">
      <p>{selectedWorkflow.description}</p>
      {familySelection && <CatalogProvisionPanel target={target} onChanged={onChanged} selection={familySelection} showStatus={false} />}
      <p>For current-request dependencies, open the existing workflow configuration to choose scientific settings and this worker. Unsaved preparation is available only where that form has “Preview artifact downloads”. Otherwise, the saved-Job preload below can use an existing job as its dependency recipe without rerunning it. Opening the launcher does not prepare assets or launch a Job.</p>
      <a className={buttonClass} href={`${import.meta.env.BASE_URL}submit?template=${encodeURIComponent(selectedWorkflow.id)}`}>Configure {selectedWorkflow.name}</a>
    </div>}
    {kind !== 'workflow' && <ProvisionActions key={JSON.stringify([kind, modelId, valid])} target={target} onChanged={onChanged} selection={valid ? { kind, model_id: modelId } : null} />}
    {!live && <PreparationStatus target={target} onChanged={onChanged} />}
    <details className="border-t border-[var(--border-primary)] pt-3 text-sm">
      <summary className="cursor-pointer font-medium">Evidence — last preparation receipt and worker asset inventory</summary>
      <div aria-label="Last independent provision receipt" className="space-y-2 pt-2">
        <h5 className="font-medium">Last preparation receipt</h5>
        <p className="text-xs text-[var(--text-muted)]">Cache downloads only, and separate from the installed inventory below. Not scientific readiness or runtime activation.</p>
        {target.artifact_inventory ? <>
          <p>{target.artifact_inventory.state === 'download_verified' ? 'Cache downloads verified at observation' : 'Stale cache observation — provision again to re-verify'}</p>
          <p>{target.artifact_inventory.selection.kind} · {provisionSelectionLabel(target.artifact_inventory.selection)} · Observed {target.artifact_inventory.observed_at}</p>
          <p className="break-all text-xs">Operation {target.artifact_inventory.operation_id}</p>
          <ArtifactList artifacts={target.artifact_inventory.artifacts} />
        </> : <p>No independent provision observation. Installed artifacts are unknown.</p>}
        <ManagedRuntimeInventoryPanel target={target} />
      </div>
    </details>
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
    <PreparationStatus target={target} onChanged={onChanged} />
  </section>;
}
export const DE_NOVO_PRELOAD_SELECTION: CatalogProvisionSelection = { kind: 'model', model_id: 'protein_modification_experimental' };

type ProvisionActionsProps = Props & { selection: ProvisionSelection | null; retryOperationId?: string };

// Keep invalidation at the shared action boundary, including for independent callers.
export function ProvisionActions(props: ProvisionActionsProps) {
  const { target, selection, retryOperationId } = props;
  const binding = JSON.stringify([selection, retryOperationId, target.id, target.provider_instance_id,
    target.host, target.port, target.username, target.remote_root, target.host_key_sha256,
    target.active, target.state, target.activated_at, target.capabilities, target.preload?.operation_id,
    target.preload?.source_revision, target.preload?.source_tree, target.preload?.phase,
    target.preload?.recovery_required, target.progress?.operation_id]);
  return <BoundProvisionActions key={binding} {...props} />;
}

export function CatalogProvisionPanel({ target, onChanged, selection, showStatus = true }: Props & { selection: CatalogProvisionSelection; showStatus?: boolean }) {
  return <section aria-label="Independent dependency preparation" className="mt-3 space-y-3">
    <h4>{selection.model_id === DE_NOVO_PRELOAD_SELECTION.model_id ? 'All De Novo dependencies' : 'Independent dependency preparation'}</h4>
    <p className="text-xs">Prepare the catalog's full dependency set, independently of the current scientific draft. {PREPARATION_CAVEAT}</p>
    <ProvisionActions target={target} onChanged={onChanged} selection={selection} />
    {showStatus && <PreparationStatus target={target} onChanged={onChanged} />}
  </section>;
}

function BoundProvisionActions({ target, onChanged, selection, retryOperationId }: ProvisionActionsProps) {
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
  const busy = active > 0 || target.preload?.recovery_required || ACTIVE_PHASES.includes(target.preload?.phase ?? '');
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
    {provision.isSuccess && <p role="status">Provision request accepted. Completion is reported by worker progress; installed evidence is shown separately from the last-preparation receipt.</p>}
    {data && <div aria-label="Provision preview" className="space-y-2 text-sm">
      <p>Dependency bytes {data.estimates_complete === false ? 'unknown' : bytes(data.total_bytes)}</p>
      <p>Provisioning makes an additional installed copy separate from cache and retains prior release generations. This is not a missing-byte transfer estimate, a free-space check or a storage reservation.</p>
      <p>Destination: {data.destination ? `${data.destination.target_id} · ${data.destination.remote_root}` : 'not reported'}</p>
      <p>Worker asset inventory: {data.inventory_state ?? 'unobserved'}</p>
      <p>Download size: {data.estimates_complete === false ? 'unknown' : data.transfer_bytes?.toLocaleString() ?? 'unknown'} bytes · Storage needed: {data.estimates_complete === false ? 'unknown' : data.storage_bytes?.toLocaleString() ?? 'unknown'} bytes</p>
      <ul aria-label="Provision blockers">{data.blockers?.map(blocker => <li key={blocker}>{blocker}</li>)}</ul>
      {data.estimates_complete === false && <ul aria-label="Selected dependencies (bytes unverified)">{data.dependencies?.map((dependency, index) => <li key={`${dependency.name}-${index}`}>{dependency.name} · {dependency.kind} · size and SHA256 unknown</li>)}</ul>}
      {data.plan_sha256 && <p className="break-all font-mono">Plan SHA256 {data.plan_sha256}</p>}
      {data.asset_states && <ArtifactDetails label="Exact dependency states" count={data.asset_states.length}>{() => <ul aria-label="Exact dependency states">{data.asset_states?.map(asset => <li key={asset.name} className="break-all">{asset.name} · {asset.state} · {bytes(asset.size_bytes)} · SHA256 {asset.sha256}</li>)}</ul>}</ArtifactDetails>}
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

/** Live preparation state first: what is happening, how far along, and how to stop it. */
function PreparationStatus({ target, onChanged }: Props) {
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
  const cancellable = operation.recovery_required || ACTIVE_PHASES.includes(operation.phase);
  const artifacts = operation.artifact_progress ?? [];
  const declared = artifacts.reduce((sum, artifact) => sum + (artifact.size_bytes || 0), 0);
  const verified = artifacts.filter(artifact => artifact.state === 'verified').reduce((sum, artifact) => sum + (artifact.size_bytes || 0), 0);
  const verifiedCount = artifacts.filter(artifact => artifact.state === 'verified').length;
  // Receipts only report completed objects, not live transfer bytes or throughput.
  // Poll-driven rerenders advance active elapsed time; settled receipts end at their last update.
  const elapsed = elapsedLabel(operation.started_at, ACTIVE_PHASES.includes(operation.phase) ? undefined : operation.updated_at);
  async function requestCancel() {
    if (lock.current || !cancellable || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    try { await cancel.mutateAsync(); } catch { /* Server owns quiescence and recovery. */ }
    finally { lock.current = false; }
  }
  return <section aria-label="Provision operation" className="space-y-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-sm">
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <p className="font-medium">{PHASE_LABELS[operation.phase] ?? operation.phase}{operation.artifact ? ` — ${operation.artifact}` : ''}</p>
      <p className="text-xs text-[var(--text-muted)]">{ACTIVE_PHASES.includes(operation.phase) ? 'Running' : 'Last preparation'}{elapsed ? ` · ${elapsed} elapsed` : ''}</p>
    </div>
    <p>{verifiedCount} of {artifacts.length} artifacts verified{declared > 0 ? ` · ${bytes(verified)} verified of ${bytes(declared)} declared` : ''}. Transfer progress and rate are not reported.</p>
    <p className="text-xs text-[var(--text-muted)]">{operation.message}{operation.sequence != null ? ` · Sequence ${operation.sequence}` : ''} · Started {operation.started_at} · Last worker update {operation.updated_at}</p>
    {artifacts.length > 0 && <ArtifactDetails label="Artifact progress" count={artifacts.length}>{() => <ul aria-label="Artifact progress">{artifacts.map(artifact => <li key={artifact.name} className="break-all">{artifact.name} · {ARTIFACT_STATE_LABELS[artifact.state] ?? artifact.state} · {bytes(artifact.size_bytes)} declared · SHA256 {artifact.sha256}</li>)}</ul>}</ArtifactDetails>}
    <p className="text-xs text-[var(--text-muted)]">States are reported worker activity; completed verified objects are retained for retry.</p>
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
