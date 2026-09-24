import { nativeCandidateRoute } from '../lib/nativeBinderResults';
import React from 'react';

export type BindCraft2Stage = 'trajectory' | 'draw' | 'retained' | 'attempt' | 'document';
export interface BindCraft2NativePage {
  schema: 'bindcraft2.native-readback.v1';
  arm: string | null;
  stage: BindCraft2Stage;
  offset: number;
  limit: number;
  total: number;
  accounting: Record<string, number | null>;
  arms: { name: string | null; accounting: Record<string, number | null> }[];
  metadata: Record<string, unknown> | null;
  rows: Record<string, unknown>[];
  artifacts?: { path: string; media_type: string; bytes: number; download_url?: string | null }[];
}

const missing = 'Unknown / not emitted';
const scalar = (value: unknown): string => value === null || value === undefined || value === '' ? missing : String(value);
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Read-only nested settings, retaining false, zero, null and empty collections. */
export function BindCraft2SettingsReadback({ value }: { value: unknown }) {
  if (value === null) return <span>Explicit null</span>;
  if (Array.isArray(value)) return value.length ? <ol className="space-y-1">{value.map((item, index) => <li key={index}><BindCraft2SettingsReadback value={item} /></li>)}</ol> : <span>Empty list</span>;
  if (typeof value !== 'object') return <span>{scalar(value)}</span>;
  const entries = Object.entries(object(value));
  return entries.length ? <dl className="grid gap-2 sm:grid-cols-[minmax(10rem,1fr)_minmax(0,2fr)]">{entries.map(([key, entry]) =>
    <React.Fragment key={key}><dt className="font-medium break-words">{key}</dt><dd className="min-w-0 break-words"><BindCraft2SettingsReadback value={entry} /></dd></React.Fragment>)}</dl> : <span>Empty object</span>;
}

/** Presentation only: uses native page order/ranks and existing verified handles. */
export function BindCraft2NativeResults({ page, onPage, jobId, launchContextId }: {
  page: BindCraft2NativePage;
  onPage: (query: { arm: string | null; stage: BindCraft2Stage; offset: number; limit: number }) => void;
  jobId?: string;
  launchContextId?: string | null;
}) {
  const change = (arm: string | null, stage: BindCraft2Stage, offset = 0, limit = page.limit) =>
    onPage({ arm, stage, offset, limit });
  const records = ['trajectory', 'draw', 'retained'].includes(page.stage);
  const metricKeys = [...new Set(page.rows.flatMap(row => Object.keys(object(row.values))))]
    .filter(key => !['design', 'rank', 'outcome', 'failed_filters', 'Binder_Sequence'].includes(key));
  const labels: Record<BindCraft2Stage, string> = { trajectory: 'Emitted trajectories', draw: 'Scored draws', retained: 'Retained sequences', attempt: 'Attempt settings', document: 'Structure states' };
  return <section aria-label="BindCraft2 native results" className="space-y-3 min-w-0 text-sm">
    <h2 className="font-semibold">BindCraft2 native campaign</h2>
    <p>Native order and ranks are preserved. Missing associations are evidence, not invented selectable structures. Computational filters do not establish experimental binding.</p>
    <div className="flex flex-wrap gap-4">
      <label>Campaign arm <select aria-label="Campaign arm" value={page.arm ?? ''} onChange={e => change(e.target.value || null, page.stage)}>
        {page.arms.map(a => <option key={a.name ?? ''} value={a.name ?? ''}>{a.name ?? 'Main campaign'}</option>)}
      </select></label>
      <label>Native records <select aria-label="Native records" value={page.stage} onChange={e => change(page.arm, e.target.value as BindCraft2Stage)}>
        {(Object.keys(labels) as BindCraft2Stage[]).map(stage => <option key={stage} value={stage}>{labels[stage]}</option>)}
      </select></label>
      <label>Rows per page <select aria-label="Rows per page" value={page.limit} onChange={e => change(page.arm, page.stage, 0, Number(e.target.value))}>
        {[25, 50, 100].map(limit => <option key={limit} value={limit}>{limit}</option>)}
      </select></label>
    </div>
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">{Object.entries(page.accounting).map(([key, value]) =>
      <React.Fragment key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{scalar(value)}</dd></React.Fragment>)}</dl>
    <p>{page.total} records; showing {page.rows.length ? page.offset + 1 : 0}–{page.offset + page.rows.length}</p>
    <div className="max-w-full overflow-x-auto"><table className="w-full text-left border-collapse"><thead><tr>
      <th scope="col" className="p-2">Identity</th>
      {records ? <><th scope="col" className="p-2">Native rank</th><th scope="col" className="p-2">Outcome / filters</th><th scope="col" className="p-2">Sequence</th>{metricKeys.map(key => <th scope="col" className="p-2" key={key}>{key}</th>)}<th scope="col" className="p-2">Associations / states</th></>
        : page.stage === 'attempt' ? <><th scope="col" className="p-2">Claimed trajectory</th><th scope="col" className="p-2">Effective / adaptive settings</th></>
          : <><th scope="col" className="p-2">Target state / variant</th><th scope="col" className="p-2">Chain roles</th><th scope="col" className="p-2">Association / native file</th></>}
    </tr></thead><tbody>{page.rows.map((row, index) => <tr className="border-t border-slate-700 align-top" key={`${page.arm ?? ''}:${page.stage}:${page.offset + index}`}>
      <th scope="row" className="p-2 break-words">{scalar(row.design ?? row.path)}</th>
      {records ? <>
        <td className="p-2">{scalar(row.rank)}</td>
        <td className="p-2">{scalar(row.outcome)}{Array.isArray(row.failed_filters) && row.failed_filters.length > 0 && <ul>{row.failed_filters.map(filter => <li key={String(filter)}>{String(filter)}</li>)}</ul>}</td>
        <td className="p-2 font-mono break-all">{scalar(row.sequence)}</td>
        {metricKeys.map(key => <td className="p-2" key={key}>{Object.keys(object(object(row.target_readings)[key])).length
          ? <dl>{Object.entries(object(object(row.target_readings)[key])).map(([state, value]) => <React.Fragment key={state}><dt>{state}</dt><dd>{scalar(value)}</dd></React.Fragment>)}</dl>
          : scalar(object(row.values)[key])}</td>)}
        <td className="p-2"><dl><dt>Trajectory</dt><dd>{scalar(row.trajectory_design)}</dd><dt>Scored draw</dt><dd>{scalar(row.scored_design)}</dd></dl>
          {Array.isArray(row.structures) && row.structures.map((item, i) => { const structure = object(item); return <p key={String(structure.artifact_id ?? i)}>{scalar(structure.target_state)} · {scalar(structure.variant)}{structure.primary ? ' · primary' : ''} · binder {scalar(structure.binder_chains)} / target {scalar(structure.target_chains)} {typeof structure.download_url === 'string' && <a className="text-accent underline" href={structure.download_url} download>Native structure</a>} {jobId && typeof row.design_id === 'string' && typeof structure.artifact_id === 'string' && <a className="text-accent underline" href={nativeCandidateRoute(jobId, row.design_id, { artifact_id: structure.artifact_id, target_state: typeof structure.target_state === 'string' ? structure.target_state : undefined }, launchContextId)}>Select exact native document</a>}</p>; })}
          {jobId && typeof row.design_id === 'string' && <a className="text-accent underline" href={nativeCandidateRoute(jobId, row.design_id, undefined, launchContextId)}>Candidate workbench</a>}
        </td>
      </> : page.stage === 'attempt' ? <>
        <td className="p-2">{scalar(row.trajectory)}</td><td className="p-2"><details><summary>Effective settings</summary><BindCraft2SettingsReadback value={row.effective_settings} /></details><details><summary>Drawn / adaptive choices</summary><BindCraft2SettingsReadback value={row.drawn} /></details><p className="break-all">Attempt SHA-256: {scalar(row.sha256)}</p></td>
      </> : <>
        <td className="p-2">{scalar(row.target_state)} · {scalar(row.structure_variant)}<p>Primary state: {scalar(row.primary_target_state)}</p></td>
        <td className="p-2">Binder: {scalar(row.binder_chains)}<br />Target: {scalar(row.target_chains)}</td>
        <td className="p-2">{scalar(row.retained_design)}{typeof row.download_url === 'string' && <p><a className="text-accent underline" href={row.download_url} download>Download native structure</a></p>}</td>
      </>}
    </tr>)}</tbody></table></div>
    <nav aria-label="Native result pagination" className="flex gap-3">
      <button type="button" disabled={page.offset === 0} onClick={() => change(page.arm, page.stage, Math.max(0, page.offset - page.limit))}>Previous</button>
      <button type="button" disabled={page.offset + page.limit >= page.total} onClick={() => change(page.arm, page.stage, page.offset + page.limit)}>Next</button>
    </nav>
    {!!page.artifacts?.length && <details><summary>Native table exports and campaign files</summary><ul className="space-y-1">{page.artifacts.map(file => <li key={file.path} className="break-all">{file.download_url ? <a className="text-accent underline" href={file.download_url} download>{file.path}</a> : file.path} ({file.bytes} bytes)</li>)}</ul></details>}
    {page.metadata && <details><summary>Native campaign metadata</summary><BindCraft2SettingsReadback value={page.metadata} /></details>}
  </section>;
}
