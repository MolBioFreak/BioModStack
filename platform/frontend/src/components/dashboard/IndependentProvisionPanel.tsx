import { ArtifactDetails } from './ArtifactDetails';
import { PagedArtifactDetails } from './PagedArtifactDetails';
import { launcherWorkflowTemplates, launcherExperimentalTemplates, visibleLauncherTemplates } from '../../lib/launcherCatalog';
import { useEffect, useRef, useState } from 'react';
import { ManagedRuntimeInventoryPanel } from './ManagedRuntimeInventoryPanel';
import { formatBinaryBytes } from '../telemetryCards';
import { getModelDisplayName } from '../../constants/displayNames';
import { isAxiosError } from 'axios';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchModels, fetchTemplates, fetchProvisionCatalog, fetchHuggingFaceAssetStatus, checkHuggingFaceAssetConnection, previewExecutionTargetProvision, provisionExecutionTarget,
  cancelExecutionTargetProvision, retryExecutionTargetProvision,
  provisionSelectionLabel, type WorkflowProvisionRequest, type CatalogProvisionSelection, type WorkflowPackSelection,
  type CachedArtifactReceipt, type ExecutionTarget, type ProvisionSelection,
} from '../../lib/api';

interface Props {
  target: ExecutionTarget;
  onChanged: () => void | Promise<unknown>;
  inventoryVisible?: boolean;
}
const buttonClass = 'rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-1.5 text-sm text-[var(--text-primary)] hover:bg-[var(--card-hover)] disabled:opacity-50';
const selectClass = 'mt-1 block w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 text-[var(--text-primary)]';
// One readiness statement for the whole section, kept in a tooltip.
const PREPARATION_CAVEAT = 'Preparation downloads missing assets, unpacks archives and publishes shared weight layouts on this worker. It does not launch a job, run inference or establish scientific readiness.';
// Workflow templates that own a whole-workflow pack on the server.
const WORKFLOW_PACKS: Record<string, WorkflowPackSelection['workflow_id']> = {
  structure_prediction: 'structure_prediction',
  antibody_denovo: 'antibody_denovo',
  protein_modification_experimental: 'de_novo_design',
};
const SCOPE_HINTS: Record<string, string> = {
  workflow: 'Every model, image and weight set the workflow can use.',
  model: 'One model: its container image and its managed weights.',
  image: 'One container image only. Weights are not downloaded.',
};
const ACTIVE_PHASES = ['checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked'];
const PHASE_LABELS: Record<string, string> = {
  checking: 'Looking up cached assets',
  transferring: 'Downloading assets',
  verifying: 'Publishing downloads and layouts',
  cancelling: 'Cancelling',
  recovery_blocked: 'Needs attention — transport not proven stopped',
  failed: 'Failed',
  cancelled: 'Cancelled',
  source_download_ready: 'Downloads complete',
};
// Runtime identity already exposed by the attachment owner; never bind a full manifest.
function capabilityIdentity(target: ExecutionTarget) {
  const binding = target.capabilities.critical_runtime_binding as Record<string, unknown> | undefined;
  const runtime = target.capabilities.critical_runtime as Record<string, unknown> | undefined;
  const environment = binding?.environment as Record<string, unknown> | undefined;
  return [target.capabilities.container_backend, binding?.release_sha256,
    environment?.BMS_CONTAINER_BACKEND, runtime?.release_sha256,
    runtime?.source_revision, runtime?.source_tree];
}
// Strictly accept the download contract and its historical wire spelling.
function isDownloadPreview(scope: unknown) {
  return scope === 'download_only' || scope === 'managed_asset_activation';
}
function errorText(error: unknown) {
  return isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail : error instanceof Error ? error.message : 'Provisioning request failed';
}
function bytes(n: number) {
  return formatBinaryBytes(n);
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
  const { target } = props;
  // The target API exposes last operation source identity, not the live host source.
  // Backend digest admission binds the selected declarations and destination.
  const binding = JSON.stringify([target.id, target.provider_instance_id, target.host, target.port,
    target.username, target.remote_root, target.host_key_sha256, target.active, target.state,
    target.activated_at, capabilityIdentity(target), target.preload?.operation_id,
    target.preload?.source_revision, target.preload?.source_tree, target.preload?.recovery_required]);
  return <ProvisionChooser key={binding} {...props} />;
}
function HuggingFaceSource() {
  const status = useQuery({ queryKey: ['hf-asset-link'], queryFn: ({ signal }) => fetchHuggingFaceAssetStatus(signal), retry: false, staleTime: 60_000 });
  const check = useMutation({ mutationFn: checkHuggingFaceAssetConnection, retry: false });
  const value = check.data ?? status.data;
  if (status.isPending && !check.data) return null;
  const label = !value ? 'Download source unknown'
    : !value.configured ? 'Source: controller over SSH (no Hugging Face bucket configured)'
    : `Source: Hugging Face ${value.bucket ?? 'bucket'}${value.mode === 'hf_preferred' ? ' (large files), controller for the rest' : ''}`;
  return <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-secondary)]" aria-label="Download source">
    <span>{label}</span>
    {value?.configured && <span className={value.available ? 'text-emerald-300' : 'text-[var(--text-muted)]'} title={value.message}>
      {value.available ? 'connected' : check.isSuccess ? 'not reachable' : 'not checked'}
    </span>}
    {value?.configured && <button type="button" className="underline decoration-dotted disabled:opacity-50" disabled={check.isPending} onClick={() => check.mutate()}>
      {check.isPending ? 'Checking…' : 'Check'}
    </button>}
    {(status.error || check.error) && <span role="alert" className="text-[var(--error)]">{errorText(check.error ?? status.error)}</span>}
  </div>;
}

function ProvisionChooser({ target, onChanged, inventoryVisible = true }: Props) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [kind, setKind] = useState<CatalogProvisionSelection['kind'] | 'workflow'>('workflow');
  const [modelId, setModelId] = useState('');
  const catalog = useQuery({ queryKey: ['remote-provision-catalog'], queryFn: fetchProvisionCatalog, retry: false });
  const models = useQuery({ queryKey: ['models'], queryFn: ({ signal }) => fetchModels(undefined, signal), retry: false });
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => fetchTemplates(), retry: false });
  const selections = catalog.isError ? [] : (catalog.data ?? []).filter((item): item is CatalogProvisionSelection => item.kind === kind);
  const modelNames = new Map((models.isError ? [] : models.data?.data ?? []).map(item => [item.id, item.name]));
  // Only list what this scope can actually prepare; names come from the model registry.
  const named = selections.map(item => {
    const name = modelNames.get(item.model_id) ?? getModelDisplayName(item.model_id);
    // Registry ids without a display name read better as words.
    return { id: item.model_id, name: name === item.model_id ? item.model_id.split('_').map(w => w[0].toUpperCase() + w.slice(1)).join(' ') : name };
  });
  // Distinct models can share a registry name (e.g. stable and experimental builds).
  const modelEntries = named.map(item => named.filter(other => other.name === item.name).length > 1
    ? { ...item, name: `${item.name} (${item.id.includes('experimental') ? 'experimental' : item.id})` } : item)
    .sort((a, b) => a.name.localeCompare(b.name));
  const workflows = [...new Map([
    ...visibleLauncherTemplates(templates.isError ? [] : templates.data?.data ?? [], Boolean((window as Window & { __DEBUG_MODE__?: boolean }).__DEBUG_MODE__)),
    ...launcherWorkflowTemplates, ...launcherExperimentalTemplates,
  ].map(item => [item.id, item])).values()];
  const packIds = new Set((catalog.data ?? []).flatMap(item => item.kind === 'workflow_pack' ? [item.workflow_id] : []));
  const packWorkflows = workflows.filter(item => WORKFLOW_PACKS[item.id] && packIds.has(WORKFLOW_PACKS[item.id]));
  const formWorkflows = workflows.filter(item => !packWorkflows.includes(item));
  const valid = kind !== 'workflow' && selections.some(item => item.model_id === modelId);
  const selectedWorkflow = kind === 'workflow' ? workflows.find(item => item.id === modelId) : undefined;
  const pack = catalog.data?.find((item): item is WorkflowPackSelection =>
    item.kind === 'workflow_pack' && item.workflow_id === (WORKFLOW_PACKS[modelId] ?? modelId));
  const operation = target.preload;
  const live = Boolean(operation) && (ACTIVE_PHASES.includes(operation!.phase) || Boolean(operation!.recovery_required));
  return <section aria-label="Independent worker provisioning" className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-[var(--text-primary)]">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <span className="text-xs text-[var(--text-muted)]" title={PREPARATION_CAVEAT}>Downloads only; nothing is launched ⓘ</span>
      <HuggingFaceSource />
    </div>
    {live && <PreparationStatus target={target} onChanged={onChanged} />}
    <div className="grid gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
      <label className="text-sm">Prepare<select aria-label="Provision scope" className={selectClass} value={kind} onChange={event => { setKind(event.target.value as CatalogProvisionSelection['kind'] | 'workflow'); setModelId(''); }}>
        <option value="workflow">Whole workflow</option><option value="model">Model (image + weights)</option><option value="image">Container image only</option>
      </select></label>
      <label className="text-sm">{kind === 'workflow' ? 'Workflow' : kind === 'model' ? 'Model' : 'Image'}<select aria-label={kind === 'workflow' ? 'Preparation workflow' : 'Provision model'} className={selectClass} value={modelId} onChange={event => setModelId(event.target.value)} disabled={kind !== 'workflow' && catalog.isPending}>
        <option value="">{kind === 'workflow' ? 'Select a workflow' : 'Select a model'}</option>
        {kind === 'workflow' ? <>
          {packWorkflows.length > 0 && <optgroup label="Prepare everything here">
            {packWorkflows.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </optgroup>}
          {formWorkflows.length > 0 && <optgroup label="Prepare from the launch form">
            {formWorkflows.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </optgroup>}
        </> : modelEntries.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
    </div>
    <p className="text-xs text-[var(--text-muted)]">{SCOPE_HINTS[kind]}</p>
    {catalog.isPending && <p role="status">Loading provisioning catalog…</p>}
    {catalog.isError && <div role="alert"><p>{errorText(catalog.error)}</p><button type="button" className={buttonClass} onClick={() => void catalog.refetch()}>Retry catalog</button></div>}
    {kind !== 'workflow' && !catalog.isPending && !catalog.isError && selections.length === 0 && <p>No reviewed selections available.</p>}
    {[models, templates].map((query, index) => query.isError && <div role="alert" key={index}>
      <p>{index === 0 ? 'Model registry' : 'Workflow catalog'}: {errorText(query.error)}</p>
      <button type="button" className={buttonClass} onClick={() => void query.refetch()}>Retry {index === 0 ? 'models' : 'workflows'}</button>
    </div>)}
    {selectedWorkflow && <div className="space-y-2 text-sm">
      {pack ? <>
        <p className="text-[var(--text-secondary)]">{pack.workflow_id === 'de_novo_design'
          ? 'All supported De Novo generator, sequence, packing, prediction and MD base assets. Optional native oracles, corpora and custom checkpoints remain selected-request additions.'
          : pack.workflow_id === 'antibody_denovo'
          ? 'The binder generators, sequence designers, validators and optional operation assets. Jobs keep their own settings and biological inputs.'
          : 'The workflow’s supported local predictors and shared assets, including optional analysis assets. Inputs and MSA requests stay with each job.'}</p>
        <ProvisionActions key={JSON.stringify(pack)} target={target} onChanged={onChanged} selection={pack} prepareInOneClick />
      </> : <>
      <p className="text-[var(--text-secondary)]">{selectedWorkflow.name} prepares its dependencies from its launch form: choose settings and this worker there, then use “Preview artifact downloads”. Nothing is launched.</p>
      <a className={buttonClass} href={`${import.meta.env.BASE_URL}submit?template=${encodeURIComponent(selectedWorkflow.id)}`}>Open {selectedWorkflow.name} form</a>
      </>}
    </div>}
    {kind !== 'workflow' && <ProvisionActions key={JSON.stringify([kind, modelId, valid])} target={target} onChanged={onChanged} selection={valid ? { kind, model_id: modelId } : null} />}
    {!live && <PreparationStatus target={target} onChanged={onChanged} />}
    <details onToggle={event => setEvidenceOpen(event.currentTarget.open)} className="border-t border-[var(--border-primary)] pt-3 text-sm">
      <summary className="cursor-pointer font-medium">What is already on this worker</summary>
      <div aria-label="Last independent provision receipt" className="space-y-2 pt-2">
        <h5 className="font-medium">Last preparation</h5>
        {target.artifact_inventory ? <>
          <p title="Cache downloads only, and separate from the installed inventory below. Not scientific readiness or runtime activation.">{target.artifact_inventory.state === 'download_verified' ? 'Cache downloads verified at observation' : 'Historical cache observation — not a current audit'}</p>
          <p>{target.artifact_inventory.selection.kind} · {provisionSelectionLabel(target.artifact_inventory.selection)} · Observed {target.artifact_inventory.observed_at}</p>
          <PagedArtifactDetails targetId={target.id} operationId={target.artifact_inventory.operation_id} collection="inventory" count={target.artifact_inventory.artifact_count} />
        </> : <p>No preparation recorded. Installed artifacts are unknown.</p>}
        {inventoryVisible && evidenceOpen && <ManagedRuntimeInventoryPanel target={target} />}
      </div>
    </details>
  </section>;
}
export function WorkflowProvisionPanel({ target, onChanged, workflowRequest }: Props & { workflowRequest: WorkflowProvisionRequest }) {
  const selection: ProvisionSelection = { kind: 'workflow', workflow_request: workflowRequest };
  const binding = JSON.stringify([selection, target.id, target.provider_instance_id, target.host, target.port, target.username, target.remote_root,
    target.host_key_sha256, target.active, target.state, target.activated_at, capabilityIdentity(target),
    target.preload?.operation_id, target.preload?.source_revision, target.preload?.source_tree, target.preload?.phase, target.preload?.recovery_required]);
  return <section aria-label="Unsaved workflow provisioning" className="mt-3 space-y-3">
    <h4 title="Uses the current typed workflow request. No saved Job, biological input staging, MSA service request or inference is created by provisioning. Scientific launch restrictions remain separate.">Prepare this workflow's dependencies (no launch)</h4>
    <ProvisionActions key={binding} target={target} onChanged={onChanged} selection={selection} />
    <PreparationStatus target={target} onChanged={onChanged} />
  </section>;
}
export const DE_NOVO_PRELOAD_SELECTION: WorkflowPackSelection = { kind: 'workflow_pack', workflow_id: 'de_novo_design' };

type ProvisionActionsProps = Props & { selection: ProvisionSelection | null; retryOperationId?: string; prepareInOneClick?: boolean };

// Keep invalidation at the shared action boundary, including for independent callers.
export function ProvisionActions(props: ProvisionActionsProps) {
  const { target, selection, retryOperationId } = props;
  const binding = JSON.stringify([selection, retryOperationId, target.id, target.provider_instance_id,
    target.host, target.port, target.username, target.remote_root, target.host_key_sha256,
    target.active, target.state, target.activated_at, capabilityIdentity(target), target.preload?.operation_id,
    target.preload?.source_revision, target.preload?.source_tree, target.preload?.phase,
    target.preload?.recovery_required]);
  return <BoundProvisionActions key={binding} {...props} />;
}

export function CatalogProvisionPanel({ target, onChanged, selection, showStatus = true }: Props & { selection: CatalogProvisionSelection | WorkflowPackSelection; showStatus?: boolean }) {
  return <section aria-label="Independent dependency preparation" className="mt-3 space-y-3">
    <h4>{selection.kind === 'workflow_pack' && selection.workflow_id === DE_NOVO_PRELOAD_SELECTION.workflow_id ? 'All De Novo dependencies' : 'Independent dependency preparation'}</h4>
    <p className="text-xs" title={PREPARATION_CAVEAT}>Prepare the full dependency set, independently of the current draft.</p>
    <ProvisionActions target={target} onChanged={onChanged} selection={selection} />
    {showStatus && <PreparationStatus target={target} onChanged={onChanged} />}
  </section>;
}

function BoundProvisionActions({ target, onChanged, selection, retryOperationId, prepareInOneClick = false }: ProvisionActionsProps) {
  const client = useQueryClient();
  // Shared with saved-Job preloading: neither can enqueue over the other.
  const mutationKey = ['remote-preload', target.id];
  const active = useIsMutating({ mutationKey });
  const lock = useRef(false);
  const [consumed, setConsumed] = useState(false);
  const current = useRef(true);
  useEffect(() => { current.current = true; return () => { current.current = false; }; }, []);
  const preview = useMutation({ mutationFn: () => previewExecutionTargetProvision(target.id, selection!), retry: false });
  const provision = useMutation({
    mutationKey, mutationFn: (digest: string) => retryOperationId
      ? retryExecutionTargetProvision(target.id, retryOperationId, { ...selection!, preview_sha256: digest })
      : provisionExecutionTarget(target.id, { ...selection!, preview_sha256: digest }), retry: false,
    onSettled: () => onChanged(),
  });
  const busy = active > 0 || target.preload?.recovery_required || ACTIVE_PHASES.includes(target.preload?.phase ?? '');
  const allowed = target.active && target.state === 'ready' && !busy;
  // The keyed boundary binds the entire request, including every scientific setting.
  // Server-normalized workflow selections may contain additional schema defaults.
  function matchesSelection(observed?: ProvisionSelection) {
    if (!selection || !observed || observed.kind !== selection.kind) return false;
    if (selection.kind === 'workflow_pack') return observed.kind === 'workflow_pack' && observed.workflow_id === selection.workflow_id;
    if (selection.kind === 'workflow') return true;
    return 'model_id' in observed && observed.model_id === selection.model_id;
  }
  const data = !consumed && preview.data && isDownloadPreview(preview.data.scope) && matchesSelection(preview.data.selection) ? preview.data : undefined;
  async function requestPreview() {
    if (lock.current || !allowed || !selection || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    setConsumed(false);
    preview.reset();
    provision.reset();
    try { await preview.mutateAsync(); } catch { /* Render the API error; retry is explicit. */ }
    finally { lock.current = false; }
  }
  async function prepareWorkflow() {
    if (lock.current || !allowed || !selection || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    setConsumed(false);
    preview.reset();
    provision.reset();
    try {
      const fresh = await preview.mutateAsync();
      // One explicit click keeps the existing preview-bound start. Switching
      // worker/selection or beginning another operation must not start late.
      if (!current.current || client.isMutating({ mutationKey }) > 0
        || !isDownloadPreview(fresh.scope) || !matchesSelection(fresh.selection) || fresh.blockers?.length) return;
      setConsumed(true);
      await provision.mutateAsync(fresh.preview_sha256);
    } catch { /* Existing error and dependency details remain visible. */ }
    finally { lock.current = false; }
  }
  async function start() {
    if (lock.current || !allowed || !data || data.blockers?.length || consumed || client.isMutating({ mutationKey }) > 0) return;
    lock.current = true;
    setConsumed(true); // Even a rejected start requires a new selection-bound preview.
    try { await provision.mutateAsync(data.preview_sha256); } catch { /* Render the API error. */ }
    finally { lock.current = false; }
  }
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      {prepareInOneClick ? <button type="button" className={buttonClass}
        disabled={!allowed || !selection || preview.isPending || provision.isPending || (consumed && provision.isSuccess)}
        onClick={() => void prepareWorkflow()}>{preview.isPending ? 'Reading workflow download metadata…' : provision.isPending ? 'Starting preparation…' : 'Prepare entire workflow'}</button> : <>
      <button type="button" className={buttonClass} disabled={!allowed || !selection || preview.isPending} onClick={() => void requestPreview()}>{preview.isPending ? 'Reading download metadata…' : 'Preview artifact downloads'}</button>
      <button type="button" className={buttonClass} disabled={!allowed || !data || !!data.blockers?.length || preview.isPending} onClick={() => void start()}>{provision.isPending ? 'Starting provision…' : retryOperationId ? 'Retry provision with fresh preview' : 'Start provision'}</button>
      </>}
    </div>
    {!allowed && <p className="text-xs text-[var(--text-muted)]">Needs an attached, ready worker with no preparation already running.</p>}
    {(preview.error || provision.error) && <p role="alert" className="text-sm text-[var(--error)]">{errorText(preview.error || provision.error)}</p>}
    {provision.isSuccess && <p role="status">Preparation started. Progress appears below.</p>}
    {data && <div aria-label="Provision preview" className="space-y-2 text-sm">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4" title="Reuses cached assets and downloads missing files; archives are unpacked and shared weight layouts published. These figures are not a free-space check or storage reservation.">
        <div className="rounded-lg bg-[var(--bg-tertiary)] px-3 py-2"><dt className="text-xs text-[var(--text-muted)]">To download</dt><dd className="font-semibold tabular-nums">{data.estimates_complete === false || data.transfer_bytes == null ? 'unknown' : bytes(data.transfer_bytes)}</dd></div>
        <div className="rounded-lg bg-[var(--bg-tertiary)] px-3 py-2"><dt className="text-xs text-[var(--text-muted)]">Disk needed</dt><dd className="font-semibold tabular-nums">{data.estimates_complete === false || data.storage_bytes == null ? 'unknown' : bytes(data.storage_bytes)}</dd></div>
        <div className="rounded-lg bg-[var(--bg-tertiary)] px-3 py-2"><dt className="text-xs text-[var(--text-muted)]">Total set</dt><dd className="font-semibold tabular-nums">{data.estimates_complete === false ? 'unknown' : bytes(data.total_bytes)}</dd></div>
        <div className="rounded-lg bg-[var(--bg-tertiary)] px-3 py-2"><dt className="text-xs text-[var(--text-muted)]">Files</dt><dd className="font-semibold tabular-nums">{data.artifacts.length.toLocaleString()}</dd></div>
      </dl>
      <p className="text-xs text-[var(--text-muted)]">To {data.destination ? `${data.destination.target_id} · ${data.destination.remote_root}` : 'destination not reported'} · worker inventory {data.inventory_state ?? 'unobserved'}</p>
      {!!data.blockers?.length && <ul aria-label="Provision blockers" className="space-y-1 text-[var(--warning)]">{data.blockers.map(blocker => <li key={blocker}>{blocker}</li>)}</ul>}
      {!!data.dependencies?.length && <details className="text-xs"><summary className="cursor-pointer">Components ({data.dependencies.length})</summary>
        <ul aria-label={data.estimates_complete === false ? 'Selected dependencies (bytes unverified)' : 'Selected dependencies'} className="mt-1 space-y-0.5">{data.dependencies.map((dependency, index) => <li key={`${dependency.name}-${index}`} className="break-all">{dependency.name} · {dependency.kind}{data.estimates_complete === false ? ' · size and SHA256 unknown' : ''}</li>)}</ul>
      </details>}
      {data.asset_states && <ArtifactDetails label="Exact dependency states" count={data.asset_states.length}>{() => <ul aria-label="Exact dependency states">{data.asset_states?.map(asset => <li key={asset.name} className="break-all">{asset.name} · {asset.state} · {bytes(asset.size_bytes)} · SHA256 {asset.sha256}</li>)}</ul>}</ArtifactDetails>}
      {data.effective_params && <details><summary>Effective workflow settings (read only)</summary><SettingValues value={data.effective_params} /></details>}
      <ArtifactList artifacts={data.artifacts} />
      <p className="break-all text-[10px] font-mono text-[var(--text-muted)]">Preview {data.preview_sha256}{data.plan_sha256 ? ` · plan ${data.plan_sha256}` : ''}</p>
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
  const { total_count: totalCount, total_bytes: declared, verified_bytes: verified, verified_count: verifiedCount } = operation.artifact_summary;
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
    <p title="Counts completed files only; live transfer bytes and rate are not reported.">{verifiedCount} of {totalCount} artifacts complete or cached{declared > 0 ? ` · ${bytes(verified)} of ${bytes(declared)}` : ''}</p>
    <p className="text-xs text-[var(--text-muted)]">{operation.message}{operation.sequence != null ? ` · Sequence ${operation.sequence}` : ''} · Started {operation.started_at} · Last worker update {operation.updated_at}</p>
    <PagedArtifactDetails targetId={target.id} operationId={operation.operation_id} collection="progress" count={totalCount} sequence={operation.sequence} />
    <PagedArtifactDetails targetId={target.id} operationId={operation.operation_id} collection="cached" count={operation.cached_artifact_count} sequence={operation.sequence} />
    {(operation.cancel_requested || operation.recovery_required) && <p role="status">Cancellation requested or recovery required. Ownership is not released until the server proves underlying transport stopped. No automatic retry.</p>}
    {cancellable && <button type="button" className={buttonClass} disabled={active > 0} onClick={() => void requestCancel()}>{cancel.isPending ? 'Requesting cancellation…' : operation.phase === 'recovery_blocked' ? 'Recheck cancellation quiescence' : 'Cancel provision'}</button>}
    {cancel.error && <p role="alert">{errorText(cancel.error)}</p>}
    {['failed', 'cancelled', 'recovery_blocked'].includes(operation.phase) && <ProvisionActions
      key={JSON.stringify([operation.operation_id, operation.selection, operation.phase, target.id, target.host, target.port,
        target.username, target.remote_root, target.host_key_sha256, target.activated_at, target.active, target.state,
        capabilityIdentity(target), operation.source_revision, operation.source_tree, operation.recovery_required, target.provider_instance_id])}
      target={target} onChanged={onChanged} selection={operation.selection} retryOperationId={operation.operation_id} />}
  </section>;
}
