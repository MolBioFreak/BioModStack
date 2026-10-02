import { useState } from 'react';
import { NativeBinderGenerationResults, type Props as WorkbenchProps } from './NativeBinderGenerationResults';
import { BindCraft2Trajectory, BindCraft2OutcomeSummary } from './BindCraft2Trajectory';
import { bc2Page, bc2Label, fetchBC2Page } from '../lib/bindcraft2Results';
import { BinderPredictionEvidence } from './BinderPredictionEvidence';
import { isAxiosError } from 'axios';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { nativeActionDefaults, submitBindCraft2Lifecycle, type BC2ActionField, type BC2Actions } from '../lib/bindcraft2Lifecycle';
import { useQuery } from '@tanstack/react-query';
import { BindCraft2SettingsReadback, type BindCraft2NativePage, type BindCraft2Stage } from './BindCraft2NativeResults';

interface CampaignSettings {
  requested_settings: Record<string, unknown>;
  effective_settings: Record<string, unknown>;
  request_sha256: string;
  effective_sha256: string;
  sweep_budget: unknown;
  native_action?: { operation: string; source_job_id: string; options: Record<string, unknown> };
}

/** Render the model-owned action schema without a duplicate settings contract. */
function NativeActionField({ name, field, value, onChange, page, required = false }: {
  name: string; field: BC2ActionField; value: unknown; onChange: (value: unknown) => void;
  page?: BindCraft2NativePage; required?: boolean;
}) {
  if (field.type === 'array') {
    const rows = Array.isArray(value) ? value : [];
    return <fieldset className="space-y-2 border border-slate-700 p-2"><legend>{name}</legend>
      {rows.map((row, index) => <div key={index} className="flex flex-wrap gap-2">
        <NativeActionField name={`${name} ${index + 1}`} field={field.items!} value={row} required page={page}
          onChange={next => onChange(rows.map((item, i) => i === index ? next : item))} />
        <button type="button" onClick={() => onChange(rows.filter((_, i) => i !== index))}>Remove {name} {index + 1}</button>
      </div>)}
      <button type="button" onClick={() => onChange([...rows, field.items?.type === 'object' ? {} : ''])}>Add {name}</button>
    </fieldset>;
  }
  if (field.type === 'object') {
    const item = value && typeof value === 'object' ? value as Record<string, unknown> : {};
    return <fieldset className="flex flex-wrap gap-3"><legend>{name}</legend>{Object.entries(field.properties ?? {}).map(([key, child]) =>
      <NativeActionField key={key} name={`${name} ${key}`} field={child} value={item[key]} page={page}
        required={field.required?.includes(key)} onChange={next => onChange({ ...item, [key]: next })} />)}</fieldset>;
  }
  if (field.type === 'boolean') return <label className="flex items-center gap-2"><input type="checkbox" aria-label={name} checked={value === true} onChange={event => onChange(event.target.checked)} />{name}</label>;
  const choices = field.control === 'campaign-arm' ? ['.', ...(page?.arms.flatMap(arm => arm.name ? [arm.name] : []) ?? [])]
    : field.control === 'structure' ? (page?.artifacts?.filter(file => /\.(?:cif|mmcif|pdb|ent)$/i.test(file.path)).map(file => file.path) ?? [])
      : field.enum;
  if (choices) return <label>{name}<select aria-label={name} required={required} value={String(value ?? '')} onChange={event => onChange(event.target.value)}>
    {!choices.includes(String(value ?? '')) && <option value="">Choose {name}</option>}
    {choices.map(choice => <option key={choice} value={choice}>{choice}</option>)}
  </select></label>;
  const numeric = field.type === 'integer' || field.type === 'number';
  return <label>{name}<input aria-label={name} type={numeric ? 'number' : 'text'} step={field.type === 'integer' ? 1 : 'any'}
    required={required} value={value === undefined ? '' : String(value)} onChange={event => onChange(numeric && event.target.value !== '' ? Number(event.target.value) : event.target.value)} /></label>;
}

export function BindCraft2NativeActions({ jobId, page, launchContextId }: { jobId: string; page?: BindCraft2NativePage; launchContextId?: string | null }) {
  const [open, setOpen] = useState(false);
  const [operation, setOperation] = useState('');
  const [saved, setSaved] = useState<Record<string, Record<string, unknown>>>({});
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [child, setChild] = useState<string | null>(null);
  const discovery = useQuery<BC2Actions>({
    queryKey: ['bindcraft2-native-actions'], enabled: open,
    queryFn: async () => {
      const response = await fetch('/api/models/bindcraft2/native-settings');
      if (!response.ok) throw new Error('Native action metadata unavailable');
      const data = await response.json();
      return data.settings.native_actions;
    },
  });
  const descriptor = discovery.data?.[operation];
  const options = saved[operation] ?? (descriptor ? nativeActionDefaults(descriptor) : {});
  const run = async () => {
    setBusy(true); setError(null); setChild(null);
    try { setChild((await submitBindCraft2Lifecycle(jobId, operation, options, target, launchContextId)).id); }
    catch (reason) {
      const detail = isAxiosError(reason) ? reason.response?.data?.detail : null;
      setError(typeof detail === 'string' ? detail : reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  };
  return <section aria-label="BindCraft2 native lifecycle" className="space-y-3">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>Native campaign actions</button>
    {open && <>
      <p>Resume continues a snapshot of this campaign in a new Job. Rank, filter, summaries, archive and score reuse native evidence; they are not a fresh generation or selected refinement round. Published parents remain unchanged.</p>
      {discovery.isLoading && <p>Loading native action controls...</p>}
      {discovery.isError && <p role="status">Native action controls could not be loaded.</p>}
      <form onSubmit={event => { event.preventDefault(); void run(); }} className="space-y-3">
        <label>Native operation <select aria-label="Native operation" required value={operation} onChange={event => setOperation(event.target.value)}>
          <option value="">Choose an operation</option>{Object.keys(discovery.data ?? {}).map(action => <option value={action} key={action}>{action}</option>)}
        </select></label>
        {descriptor && <div className="flex flex-wrap gap-3">{Object.entries(descriptor.properties).map(([name, field]) =>
          <NativeActionField key={`${operation}:${name}`} name={name} field={field} value={options[name]} page={page}
            required={descriptor.required?.includes(name)} onChange={value => setSaved(previous => ({ ...previous, [operation]: { ...options, [name]: value } }))} />)}</div>}
        <ExecutionTargetPicker value={target} onChange={setTarget} disabled={busy} />
        <button type="submit" disabled={busy || !operation}>Run native operation</button>
      </form>
      {busy && <p role="status">Submitting native operation...</p>}
      {error && <p role="alert">{error}</p>}
      {child && <p role="status">Created child Job. <a href={`/jobs/${encodeURIComponent(child)}`} className="text-accent underline">Open native action Job</a></p>}
    </>}
  </section>;
}

/** Native evidence supplements the existing selectable Design workbench. */
export function BindCraft2JobResults({ jobId, resultsAvailable = true, launchContextId, ...workbench }: WorkbenchProps & { resultsAvailable?: boolean }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [query, setQuery] = useState<{ arm: string | null; stage: BindCraft2Stage; offset: number; limit: number }>(() => {
    try {
      const saved = JSON.parse(new URLSearchParams(window.location.search).get('native_scope') ?? 'null');
      if (Array.isArray(saved) && saved[0] === 'bindcraft2' && ['trajectory', 'draw', 'retained', 'document', 'attempt'].includes(saved[2])) return { arm: saved[1], stage: saved[2], offset: 0, limit: 100 };
    } catch { /* ordinary results route */ }
    return { arm: null, stage: workbench.selectedDesignId || workbench.artifactId ? 'retained' : 'trajectory', offset: 0, limit: 100 };
  });
  const settings = useQuery<CampaignSettings>({
    queryKey: ['bindcraft2-campaign-settings', jobId], enabled: settingsOpen,
    queryFn: async () => {
      const response = await fetch(`/api/models/bindcraft2/campaign/jobs/${encodeURIComponent(jobId)}/settings`);
      if (!response.ok) throw new Error('Native compilation settings unavailable');
      return response.json();
    },
  });
  const { data, isLoading, isError, refetch } = useQuery<BindCraft2NativePage>({
    queryKey: ['bindcraft2-native-results', jobId, query],
    enabled: resultsAvailable,
    queryFn: ({ signal }) => fetchBC2Page(jobId, query.arm, query.stage, 0, signal),
    retry: false, refetchOnWindowFocus: false,
  });
  return <div className="space-y-2 min-w-0">
    {data && <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        {data.arms.length > 1 && <label>Campaign arm <select className="rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2" aria-label="Campaign arm" value={data.arm ?? ''} onChange={event => setQuery({ ...query, arm: event.target.value || null })}>
          {data.arms.map(arm => <option key={arm.name ?? ''} value={arm.name ?? ''}>{arm.name ?? 'Main campaign'}</option>)}
        </select></label>}
        <label>Show <select className="rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2" aria-label="Native records" value={query.stage} onChange={event => setQuery({ ...query, arm: data.arm, stage: event.target.value as BindCraft2Stage })}>
          <optgroup label="Results"><option value="trajectory">Design attempts</option><option value="draw">Scored sequences</option><option value="retained">Retained sequences</option><option value="document">Structures</option></optgroup>
          <optgroup label="Run details"><option value="attempt">Attempt settings</option></optgroup>
        </select></label>
      </div>
      <p className="text-[var(--text-secondary)]">{data.accounting.emitted_trajectories ?? 'Unknown'} attempts · {data.accounting.scored_draws ?? 'Unknown'} scored sequences · {data.accounting.retained_sequences ?? 'Unknown'} retained sequences</p>
    </div>}
    {!resultsAvailable ? <p>Native results will appear after publication.</p> : isLoading ? <p>Loading BindCraft2 native records...</p>
      : isError || !data ? <p role="status">BindCraft2 native records are not available for this job. <button type="button" onClick={() => void refetch()}>Retry native readback</button></p>
      : <NativeBinderGenerationResults {...workbench} jobId={jobId} launchContextId={launchContextId} adapter={{
          key: JSON.stringify(['bindcraft2', data.arm, query.stage]), title: 'Campaign overview', compact: true,
          recordLabel: ({ trajectory: 'Design attempts', draw: 'Scored sequences', retained: 'Retained sequences', attempt: 'Attempt settings', document: 'Structures' })[query.stage],
          initialMetrics: keys => {
            // Exact per-target confidence only: iptm_loss is not iPTM.
            const screen = keys.find(key => /^screen · .+\.iptm · last recorded$/.test(key));
            const refine = (screen && keys.includes(screen.replace(/^screen/, 'refine')) ? screen.replace(/^screen/, 'refine') : undefined)
              ?? keys.find(key => /^refine · .+\.iptm · last recorded$/.test(key));
            return { x: screen, y: refine ?? screen, distribution: refine ?? screen, headline: [refine ?? screen, 'duration_seconds'].filter((key): key is string => !!key) };
          },
          fetchPage: async (offset, signal) => bc2Page(offset === 0 ? data : await fetchBC2Page(jobId, data.arm, query.stage, offset, signal)),
          label: bc2Label, preferredColumns: ['seq_length', 'duration_seconds', 'terminated', ...Object.keys(bc2Page(data).records[0]?.metrics ?? {}).filter(key => /^(?:refine|screen) · .+\.iptm · last recorded$/.test(key)).sort((a, b) => Number(b.startsWith('refine · ')) - Number(a.startsWith('refine · '))).slice(0, 1), ...(query.stage === 'trajectory' ? [] : ['outcome', 'rank'])],
          inspect: row => <BindCraft2Trajectory key={row.candidate_key} jobId={jobId} row={row} arm={data.arm} />,
          summary: rows => <><BindCraft2OutcomeSummary rows={rows} />{data.analytics?.complete === false && <p role="status">Trajectory analytics are incomplete. Plots cover available recorded observations only.</p>}</>,
        }} />}
    {!!data?.analytics?.warnings?.length && <details><summary className="cursor-pointer text-sm">Recorded measurement notes</summary>{data.analytics.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</details>}
    <details onToggle={event => setSettingsOpen(event.currentTarget.open)}><summary className="cursor-pointer font-semibold">Native campaign settings</summary>
    <section aria-label="BindCraft2 campaign settings">
      <h3 className="font-semibold">Native campaign settings</h3>
      {settings.isLoading ? <p>Loading compiled settings...</p> : settings.isError || !settings.data
        ? <p role="status">Native compilation settings are not available for this job. Native result evidence remains separate.</p>
        : <>
          <div className="grid gap-4 lg:grid-cols-2">
            <details><summary>Requested settings</summary><BindCraft2SettingsReadback value={settings.data.requested_settings} /></details>
            <details><summary>Compiled effective settings</summary><BindCraft2SettingsReadback value={settings.data.effective_settings} /></details>
          </div>
          {settings.data.native_action && <details><summary>Saved native action: {settings.data.native_action.operation}</summary>
            <a className="text-accent underline" href={`/jobs/${encodeURIComponent(settings.data.native_action.source_job_id)}`}>Source campaign Job</a>
            <BindCraft2SettingsReadback value={settings.data.native_action.options} />
          </details>}
          <details><summary>Compilation identity and sweep budget</summary>
            <p className="break-all">Request SHA-256: {settings.data.request_sha256}</p>
            <p className="break-all">Effective SHA-256: {settings.data.effective_sha256}</p>
            <BindCraft2SettingsReadback value={settings.data.sweep_budget} />
          </details>
        </>}
    </section></details>
    <details onToggle={event => setEvidenceOpen(event.currentTarget.open)}><summary className="cursor-pointer">Prediction and follow-on evidence</summary>{evidenceOpen && <BinderPredictionEvidence jobId={jobId} launchContextId={launchContextId} />}</details>
    <BindCraft2NativeActions key={jobId} jobId={jobId} page={data} launchContextId={launchContextId} />

  </div>;
}
