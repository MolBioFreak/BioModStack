import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import AlignmentResultList, { type AlignmentReceipt } from './AlignmentResultList';
import { api } from '../../lib/api';
import { searchProjects, listGlobalExperiments, listDomainExperiments, type ProjectListItem } from '../../lib/projectManager';
import { errorText } from './alignmentText';

type Receipt = AlignmentReceipt;
type Page = {items: Receipt[]; next_cursor: string | null};

/** Project-owned receipts only: imports do not create compute Jobs. */
export default function AlignmentLibrary({ initialProject }: {initialProject?: string | null}) {
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [project, setProject] = useState(initialProject || '');
  const [rows, setRows] = useState<Receipt[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [domains, setDomains] = useState<{id: string; label: string}[]>([]);
  const [domain, setDomain] = useState('');
  const [replayKey, setReplayKey] = useState<string>(() => crypto.randomUUID());
  const [alignmentKey, setAlignmentKey] = useState<string>(() => crypto.randomUUID());
  const [alignment, setAlignment] = useState<File | null>(null);
  const [format, setFormat] = useState('fasta');
  const [molecule, setMolecule] = useState('protein');
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState('');
  const [receiptsLoading, setReceiptsLoading] = useState(false);
  const [receiptsError, setReceiptsError] = useState('');
  const [reload, setReload] = useState(0);
  const [imported, setImported] = useState<{project: string; receipt: string} | null>(null);
  useEffect(() => { let live = true; setProjectsLoading(true); setProjectsError(''); (async () => {
    try { let cursor: string | undefined; const all: ProjectListItem[] = [];
      do { const page = await searchProjects({cursor, archive: 'all'}); all.push(...page.items); cursor = page.next_cursor || undefined; } while (cursor);
      if (live) setProjects(all);
    } catch (e) { if (live) setProjectsError(errorText(e)); }
    finally { if (live) setProjectsLoading(false); }
  })(); return () => { live = false; }; }, [reload]);
  useEffect(() => { let live = true; setRows([]); setCursor(null); setError(''); setReceiptsError(''); setReceiptsLoading(Boolean(project));
    if (project) api.get<Page>(`/api/family-msa/projects/${encodeURIComponent(project)}/receipts`).then(({data}) => { if (live) { setRows(data.items); setCursor(data.next_cursor); } }).catch(e => { if (live) setReceiptsError(errorText(e)); }).finally(() => { if (live) setReceiptsLoading(false); });
    return () => { live = false; };
  }, [project, reload]);
  useEffect(() => { let live = true; setDomain(''); setDomains([]);
    if (project) (async () => {
      try { const experiments = await listGlobalExperiments(project);
        const groups = await Promise.all(experiments.map(async experiment => (await listDomainExperiments(project, experiment.id)).map(item => ({id: item.id, label: `${experiment.name} / ${item.name}`}))));
        if (live) setDomains(groups.flat());
      } catch (e) { if (live) setError(errorText(e)); }
    })();
    return () => { live = false; };
  }, [project]);
  async function more() {
    try { const {data} = await api.get<Page>(`/api/family-msa/projects/${encodeURIComponent(project)}/receipts`, {params: {after: cursor}}); setRows(old => [...old, ...data.items]); setCursor(data.next_cursor); } catch (e) { setError(errorText(e)); }
  }
  async function upload() {
    setError(''); setStatus('');
    try {
      if (!file) return;
      const {data} = await api.post<{receipt_id: string; replayed: boolean; shared_state: string}>(`/api/family-msa/projects/${encodeURIComponent(project)}/imports/zip`, file, {params: {replay_key: replayKey, domain_experiment_id: domain || undefined}, headers: {'Content-Type': 'application/zip'}});
      setStatus(data.shared_state === 'retained_unmapped' ? 'Imported. Saved views were not restored.' : data.shared_state === 'imported_with_unresolved' ? 'Imported; some saved views couldn\'t be restored.' : 'Imported.');
      setImported({project, receipt: data.receipt_id});
      setReload(n => n + 1);
    } catch (e) { setError(errorText(e)); }
  }
  async function uploadAlignment() {
    if (!alignment) return;
    setError(''); setStatus('');
    try {
      const {data} = await api.post<{receipt_id: string}>(`/api/family-msa/projects/${encodeURIComponent(project)}/imports/alignment`, alignment,
        {params: {format, molecule, replay_key: alignmentKey}, headers: {'Content-Type': 'application/octet-stream'}});
      setImported({project, receipt: data.receipt_id});
      setReload(n => n + 1);
      setStatus('Imported.');
    } catch (e) { setError(errorText(e)); }
  }
  return <section aria-label="Project alignment library" className="alignment-library"><h2>Project alignment library</h2>
    <p>Open a project's alignments or import an existing alignment.</p>
    <label>Project <select aria-label="Project" value={project} onChange={e => setProject(e.target.value)}><option value="">Choose a Project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {projectsLoading && <p role="status">Loading Projects…</p>}
    {projectsError && <p role="alert">Projects unavailable: {projectsError} <button onClick={() => setReload(n => n + 1)}>Retry Projects</button></p>}
    {!project && <div className="alignment-empty"><h3>Choose a project to open its library</h3><p>Browse alignments and import aligned FASTA, Stockholm, Clustal, A3M or an exported ZIP.</p><p>You can also start a new alignment without opening a library.</p><Link className="alignment-action" to="/alignments/new">New alignment</Link> <Link to="/projects">Open Project Manager</Link></div>}
    {!projectsLoading && !projectsError && !projects.length && <p>No Projects found. Create or organize Projects in <Link to="/projects">Project Manager</Link>.</p>}
    {error && <p role="alert">{error}</p>}{status && <p role="status">{status}</p>}
    {imported && <p><Link to={`/alignments/results/${encodeURIComponent(imported.receipt)}?project_id=${encodeURIComponent(imported.project)}`}>Open imported result</Link></p>}
    {project && <><div className="alignment-results"><h3>Alignments</h3>
      {receiptsLoading && <p role="status">Loading alignments…</p>}
      {receiptsError && <p role="alert">Alignment library unavailable: {receiptsError} <button onClick={() => setReload(n => n + 1)}>Retry results</button></p>}
      {!receiptsLoading && !receiptsError && !rows.length && <p>No alignments yet. Import one below or use New to build one.</p>}
      <AlignmentResultList rows={rows} projectId={project}/>{cursor && <button onClick={more}>Load more results</button>}</div><div className="alignment-imports">
      <fieldset><legend>Import an alignment file</legend><p>The original file is kept. A3M insertions and Stockholm annotations aren't shown.</p>
        <label>Format <select value={format} onChange={e => {setFormat(e.target.value); setAlignmentKey(crypto.randomUUID());}}><option value="fasta">Aligned FASTA</option><option value="stockholm">Stockholm</option><option value="clustal">Clustal</option><option value="a3m">A3M</option></select></label>
        <label>Molecule <select value={molecule} onChange={e => {setMolecule(e.target.value); setAlignmentKey(crypto.randomUUID());}}><option value="protein">Protein</option><option value="dna">DNA</option><option value="rna">RNA</option></select></label>
        <input aria-label="Alignment file" type="file" onChange={e => {setAlignment(e.target.files?.[0] || null); setAlignmentKey(crypto.randomUUID());}} />
        <button type="button" disabled={!alignment} onClick={uploadAlignment}>Import alignment</button>
      </fieldset>
      <fieldset><legend>Import exported ZIP</legend><p>Import an exported alignment ZIP. Choose an experiment to restore its saved views.</p>
        <label>Save to experiment <select value={domain} onChange={e => {setDomain(e.target.value); setReplayKey(crypto.randomUUID());}}><option value="">Don't restore saved views</option>{domains.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <input aria-label="Bundle ZIP" type="file" accept=".zip,application/zip" onChange={e => {setFile(e.target.files?.[0] || null); setReplayKey(crypto.randomUUID());}} />

        <button type="button" disabled={!file} onClick={upload}>Import ZIP</button>
      </fieldset></div></>}
  </section>;
}
