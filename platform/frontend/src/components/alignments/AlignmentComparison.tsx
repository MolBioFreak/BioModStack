import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { searchProjects, listGlobalExperiments, listDomainExperiments } from '../../lib/projectManager';

type Receipt = {receipt_id: string; entity_kind: string; availability: string};
type Artifact = {id: string; role: string; logical_path?: string | null};
type Inputs = {left_receipt_id: string; left_artifact_id: string; right_receipt_id: string; right_artifact_id: string};
type Agreement = {method: string; matched_rows: number; shared_mapped_residues: number; left_unmapped_residues: number; right_unmapped_residues: number; rows_with_unknown_mapping: number; left_only_mapped_residues_known: number; right_only_mapped_residues_known: number; left_aligned_pairs: number; right_aligned_pairs: number; shared_aligned_pairs: number; union_aligned_pairs: number; jaccard: number | null};
type ComparedRow = {source_member_id: string; occurrence_id: string; left_row_id: string; right_row_id: string; positions: {source: number; left_column: number; right_column: number}[]; left_only_positions: number[]; right_only_positions: number[]; mapping_unknown: boolean; left_unmapped_columns: number[]; right_unmapped_columns: number[]};
type Result = {method: string; report: {agreement: Agreement; left_excluded: number; right_excluded: number; rows?: ComparedRow[]; left_unknown_identity_rows?: string[]; right_unknown_identity_rows?: string[]; coordinate_convention?: string}};
type Saved = {id: string; revision_id: string; state: {comparisons: (Result & {id: string; name: string; left_artifact_id: string; right_artifact_id: string; provenance: Partial<Inputs>})[]}};

function Report({result}: {result: Result}) {
  const a = result.report.agreement;
  const [rowIndex, setRowIndex] = useState('');
  const [offset, setOffset] = useState(0);
  const row = rowIndex ? result.report.rows?.[Number(rowIndex) - 1] : undefined;
  const positions = (values: number[]) => values.slice(0, 100).map(p => p + 1).join(', ') || 'none';
  return <section aria-label="Comparison report"><h3>Source-coordinate agreement</h3>
    <p>Method: {result.method} / {a.method}. Observed agreement is not biological accuracy.</p>
    <p>Aligned-pair intersection / union: {a.shared_aligned_pairs} / {a.union_aligned_pairs}; Jaccard: {a.jaccard === null ? 'undefined (no comparable pairs)' : a.jaccard.toFixed(4)}.</p>
    <p>Left aligned pairs: {a.left_aligned_pairs}; right aligned pairs: {a.right_aligned_pairs}.</p>
    <p>Matched rows: {a.matched_rows}; shared mapped residues: {a.shared_mapped_residues}; excluded rows: left {result.report.left_excluded}, right {result.report.right_excluded}.</p>
    <p>Rows with unknown mapping: {a.rows_with_unknown_mapping}; unmapped residues: left {a.left_unmapped_residues}, right {a.right_unmapped_residues}; known mapped residues outside shared scope: left {a.left_only_mapped_residues_known}, right {a.right_only_mapped_residues_known}.</p>
    <details><summary>Inspect source correspondence and unmapped positions</summary>
      <p>Displayed coordinates are 1-based; retained report coordinates are zero-based. Different column numbers show placement changes, not biological correctness. Open each result to inspect its gaps.</p>
      <label>Mapped occurrence <select value={rowIndex} onChange={e => {setRowIndex(e.target.value); setOffset(0);}}><option value="">Choose an occurrence</option>{result.report.rows?.map((r, i) => <option key={i} value={i + 1}>{r.source_member_id} / {r.occurrence_id} ({r.left_row_id} → {r.right_row_id})</option>)}</select></label>
      {row && <><p>Source mapping: {row.mapping_unknown ? 'partly unknown' : 'known'}; shared positions {row.positions.length}.</p>
        <p>Left unmapped columns ({row.left_unmapped_columns.length}; first 100): {positions(row.left_unmapped_columns)}. Right unmapped columns ({row.right_unmapped_columns.length}; first 100): {positions(row.right_unmapped_columns)}.</p>
        <p>Known left-only source positions ({row.left_only_positions.length}; first 100): {positions(row.left_only_positions)}. Known right-only source positions ({row.right_only_positions.length}; first 100): {positions(row.right_only_positions)}.</p>
        <table><caption>Shared source positions {row.positions.length ? offset + 1 : 0}–{Math.min(offset + 100, row.positions.length)} of {row.positions.length}</caption><thead><tr><th>Source position</th><th>Left column</th><th>Right column</th><th>Placement</th></tr></thead><tbody>{row.positions.slice(offset, offset + 100).map(p => <tr key={p.source}><td>{p.source + 1}</td><td>{p.left_column + 1}</td><td>{p.right_column + 1}</td><td>{p.left_column === p.right_column ? 'same column' : 'shifted'}</td></tr>)}</tbody></table>
        <button disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 100))}>Previous positions</button><button disabled={offset + 100 >= row.positions.length} onClick={() => setOffset(n => n + 100)}>Next positions</button>
      </>}
      <p>Rows with unknown source identity: left {result.report.left_unknown_identity_rows?.length ?? 'not reported'}; right {result.report.right_unknown_identity_rows?.length ?? 'not reported'}.</p>
    </details>
  </section>;
}

function ProjectComparison({project, savedState, operationJob}: {project: string; savedState?: string | null; operationJob?: string | null}) {
  const prefix = `/api/family-msa/projects/${encodeURIComponent(project)}`;
  const [inputs, setInputs] = useState<Inputs>({left_receipt_id: '', left_artifact_id: '', right_receipt_id: '', right_artifact_id: ''});
  const [result, setResult] = useState<Result>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [domain, setDomain] = useState('');
  const [name, setName] = useState('Alignment comparison');
  const [saved, setSaved] = useState<Saved>();
  const [molecule, setMolecule] = useState<'protein' | 'dna' | 'rna'>('protein');
  const [jobId, setJobId] = useState(operationJob || '');
  const operation = useQuery({queryKey: ['alignment-comparison-operation', project, jobId], enabled: Boolean(jobId), queryFn: async ({signal}) => (await api.get<{job_id: string; status: string; error_message: string | null; artifact_url: string | null}>(`${prefix}/operations/${encodeURIComponent(jobId)}`, {signal})).data,
    refetchInterval: q => !q.state.data || ['queued', 'running', 'pending'].includes(q.state.data.status) ? 3000 : false, refetchIntervalInBackground: false});
  const computed = useQuery({queryKey: ['alignment-comparison-report', project, jobId, operation.data?.artifact_url], enabled: Boolean(jobId && operation.data?.artifact_url), queryFn: async ({signal}) => (await api.get<Result>(`${prefix}/operations/${encodeURIComponent(jobId)}/artifact`, {signal})).data});
  useEffect(() => { if (computed.data) setResult(computed.data); }, [computed.data]);
  const running = busy || Boolean(jobId && (!operation.data || ['queued', 'running', 'pending'].includes(operation.data.status)));

  const receipts = useQuery({queryKey: ['alignment-comparison-receipts', project], queryFn: async ({signal}) => {
    const items: Receipt[] = []; let after: string | null = null;
    do { const {data}: {data: {items: Receipt[]; next_cursor: string | null}} = await api.get(`${prefix}/receipts`, {params: {after: after || undefined}, signal}); items.push(...data.items); after = data.next_cursor; } while (after);
    return items;
  }});
  const left = useQuery({queryKey: ['alignment-comparison-artifacts', project, inputs.left_receipt_id], enabled: Boolean(inputs.left_receipt_id), queryFn: async ({signal}) => (await api.get<{artifacts: Artifact[]}>(`${prefix}/receipts/${encodeURIComponent(inputs.left_receipt_id)}`, {signal})).data.artifacts.filter(a => a.role === 'alignment')});
  const right = useQuery({queryKey: ['alignment-comparison-artifacts', project, inputs.right_receipt_id], enabled: Boolean(inputs.right_receipt_id), queryFn: async ({signal}) => (await api.get<{artifacts: Artifact[]}>(`${prefix}/receipts/${encodeURIComponent(inputs.right_receipt_id)}`, {signal})).data.artifacts.filter(a => a.role === 'alignment')});
  const domains = useQuery({queryKey: ['alignment-comparison-domains', project], queryFn: async () => {
    const experiments = await listGlobalExperiments(project);
    return (await Promise.all(experiments.map(async e => (await listDomainExperiments(project, e.id)).map(d => ({id: d.id, label: `${e.name} / ${d.name}`}))))).flat();
  }});
  const reopened = useQuery({queryKey: ['alignment-comparison-saved', project, savedState], enabled: Boolean(savedState), queryFn: async ({signal}) => (await api.get<Saved>(`${prefix}/shared-state/${encodeURIComponent(savedState!)}`, {signal})).data});
  const failure = receipts.error || left.error || right.error || domains.error || reopened.error || operation.error || computed.error;
  function change(side: 'left' | 'right', kind: 'receipt' | 'artifact', value: string) {
    setResult(undefined); setSaved(undefined); setError(''); setJobId('');
    setInputs(old => kind === 'receipt' ? {...old, [`${side}_receipt_id`]: value, [`${side}_artifact_id`]: ''} : {...old, [`${side}_artifact_id`]: value});
  }
  async function compare() {
    setBusy(true); setError(''); setResult(undefined); setSaved(undefined);
    try { setJobId((await api.post<{job_id: string}>(`${prefix}/operations`, {operation: 'compare', molecule, domain_experiment_id: domain, name, inputs})).data.job_id); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!result) return;
    setBusy(true); setError('');
    try {
      const {data} = await api.post<Saved>(`${prefix}/shared-state`, {name, domain_experiment_id: domain, state: {
        schema: 'bms.family-msa.shared-state.v1', marks: [], views: [], comparisons: [{id: crypto.randomUUID(), name, ...result, left_artifact_id: inputs.left_artifact_id, right_artifact_id: inputs.right_artifact_id, provenance: {...inputs}}],
      }});
      setSaved((await api.get<Saved>(`${prefix}/shared-state/${encodeURIComponent(data.id)}`, {params: {revision_id: data.revision_id}})).data);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    {(error || failure) && <p role="alert">Comparison unavailable: {error || String(failure)}</p>}
    {receipts.isLoading && <p role="status">Loading retained results…</p>}
    {receipts.data?.length === 0 && <p>No retained alignment results in this Project.</p>}
    <fieldset disabled={running}><legend>Comparison inputs</legend>
      <label>Molecule <select value={molecule} onChange={e => setMolecule(e.target.value as typeof molecule)}><option value="protein">Protein</option><option value="dna">DNA</option><option value="rna">RNA</option></select></label>
      <label>Destination domain <select value={domain} onChange={e => setDomain(e.target.value)}><option value="">Choose a domain</option>{domains.data?.map(d => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
      {(['left', 'right'] as const).map(side => <div key={side}>
        <label>{side === 'left' ? 'Left' : 'Right'} result <select aria-label={`${side} result`} value={inputs[`${side}_receipt_id`]} onChange={e => change(side, 'receipt', e.target.value)}>
          <option value="">Choose a retained result</option>{receipts.data?.map((r, i) => <option key={r.receipt_id} value={r.receipt_id}>{r.entity_kind === 'family_msa_import' ? 'Imported alignment' : 'Alignment result'} {i + 1} · {r.availability} ({r.receipt_id})</option>)}
        </select></label>
        <label>{side === 'left' ? 'Left' : 'Right'} member <select aria-label={`${side} member`} value={inputs[`${side}_artifact_id`]} onChange={e => change(side, 'artifact', e.target.value)}>
          <option value="">Choose an alignment member</option>{(side === 'left' ? left.data : right.data)?.map(a => <option key={a.id} value={a.id}>{a.logical_path || a.id}</option>)}
        </select></label>
        {inputs[`${side}_receipt_id`] && <Link to={`/alignments/results/${encodeURIComponent(inputs[`${side}_receipt_id`])}?project_id=${encodeURIComponent(project)}`}>Open {side} result</Link>}
      </div>)}
      <button type="button" disabled={!domain || !Object.values(inputs).every(Boolean)} onClick={compare}>Compare alignments</button>
    </fieldset>
    {jobId && <p role="status">Managed comparison Job {jobId}: {operation.data?.status || 'submitted'}. <Link to={`/jobs/${encodeURIComponent(jobId)}`}>Open Job</Link> · <a href={`/alignments/compare?project_id=${encodeURIComponent(project)}&operation_job_id=${encodeURIComponent(jobId)}`}>Resume comparison</a> {operation.data?.artifact_url && <> · <a href={`${prefix}/operations/${encodeURIComponent(jobId)}/artifact`} download>Download report</a></>}</p>}
    {operation.data?.error_message && <p role="alert">{operation.data.error_message}</p>}
    {result && <><Report result={result}/><fieldset disabled={busy}><legend>Save comparison to Project</legend>
      <label>Comparison name <input value={name} onChange={e => setName(e.target.value)}/></label>
      <button type="button" disabled={!domain || !Object.values(inputs).every(Boolean) || !name.trim() || Boolean(saved)} onClick={save}>Save comparison</button>
    </fieldset></>}
    {saved && <p role="status">Saved revision {saved.revision_id}. <Link to={`/alignments/compare?project_id=${encodeURIComponent(project)}&shared_state_id=${encodeURIComponent(saved.id)}`}>Reopen saved comparison</Link></p>}
    {reopened.data?.state.comparisons.map(c => <section key={c.id}><h3>{c.name}</h3><p>Saved revision {reopened.data.revision_id}; left artifact {c.left_artifact_id}; right artifact {c.right_artifact_id}.</p><Report result={c}/></section>)}
  </>;
}

/** Explicit comparison through retained Project receipts, never inferred row identity. */
export default function AlignmentComparison({initialProject, savedState, operationJob}: {initialProject?: string | null; savedState?: string | null; operationJob?: string | null}) {
  const [project, setProject] = useState(initialProject || '');
  const projects = useQuery({queryKey: ['alignment-comparison-projects'], queryFn: async () => {
    const items = []; let cursor: string | undefined;
    do { const page = await searchProjects({cursor, archive: 'all'}); items.push(...page.items); cursor = page.next_cursor || undefined; } while (cursor);
    return items;
  }});
  return <section aria-label="Alignment comparison"><h1>Compare alignments</h1>
    <p>Select exact retained results and members. Comparison uses verified source occurrences and coordinates, not matching labels or same-numbered columns. Missing provenance remains unknown.</p>
    <label>Comparison Project <select value={project} onChange={e => setProject(e.target.value)}><option value="">Choose a Project</option>{projects.data?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {projects.error && <p role="alert">Projects unavailable: {String(projects.error)}</p>}
    {project && <ProjectComparison key={`${project}:${operationJob || ''}`} project={project} savedState={project === initialProject ? savedState : undefined} operationJob={project === initialProject ? operationJob : undefined}/>}
  </section>;
}
