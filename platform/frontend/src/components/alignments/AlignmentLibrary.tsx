import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { searchProjects, type ProjectListItem } from '../../lib/projectManager';

type Receipt = {receipt_id: string; entity_kind: string; availability: string};
type Page = {items: Receipt[]; next_cursor: string | null};

/** Project-owned receipts only: imports do not create compute Jobs. */
export default function AlignmentLibrary({ initialProject }: {initialProject?: string | null}) {
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [project, setProject] = useState(initialProject || '');
  const [rows, setRows] = useState<Receipt[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [replayKey, setReplayKey] = useState<string>(() => crypto.randomUUID());
  const [alignmentKey, setAlignmentKey] = useState<string>(() => crypto.randomUUID());
  const [alignment, setAlignment] = useState<File | null>(null);
  const [format, setFormat] = useState('fasta');
  const [molecule, setMolecule] = useState('protein');
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [imported, setImported] = useState<{project: string; receipt: string} | null>(null);
  useEffect(() => { let live = true; (async () => {
    try { let cursor: string | undefined; const all: ProjectListItem[] = [];
      do { const page = await searchProjects({cursor, archive: 'all'}); all.push(...page.items); cursor = page.next_cursor || undefined; } while (cursor);
      if (live) setProjects(all);
    } catch (e) { if (live) setError(String(e)); }
  })(); return () => { live = false; }; }, []);
  useEffect(() => { let live = true; setRows([]); setCursor(null); setError('');
    if (project) api.get<Page>(`/api/family-msa/projects/${encodeURIComponent(project)}/receipts`).then(({data}) => { if (live) { setRows(data.items); setCursor(data.next_cursor); } }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [project]);
  async function more() {
    try { const {data} = await api.get<Page>(`/api/family-msa/projects/${encodeURIComponent(project)}/receipts`, {params: {after: cursor}}); setRows(old => [...old, ...data.items]); setCursor(data.next_cursor); } catch (e) { setError(String(e)); }
  }
  async function upload() {
    setError(''); setStatus('');
    try {
      const relative = (f: File) => f.webkitRelativePath.split('/').slice(1).join('/');
      const byPath = new Map(files.map(f => [relative(f), f]));
      const manifestFile = byPath.get('manifest.json');
      if (!manifestFile) throw new Error('Select a portable bundle directory containing manifest.json.');
      const manifest = JSON.parse(await manifestFile.text());
      const metadata = new TextEncoder().encode(JSON.stringify({replay_key: replayKey, manifest, domain_experiment_id: null}));
      const prefix = new Uint8Array(8); new DataView(prefix.buffer).setBigUint64(0, BigInt(metadata.length));
      const parts: BlobPart[] = [prefix, metadata];
      for (const entry of manifest.files) { const file = byPath.get(entry.path); if (!file) throw new Error(`Missing bundle file: ${entry.path}`); parts.push(file); }
      const {data} = await api.post<{receipt_id: string; replayed: boolean; shared_state: string}>(`/api/family-msa/projects/${encodeURIComponent(project)}/imports/stream`, new Blob(parts), {headers: {'Content-Type': 'application/vnd.bms.alignment-bundle-stream'}});
      setStatus(data.shared_state === 'retained_unmapped' ? 'Bundle imported. Shared state is retained but not restored.' : 'Bundle imported.');
      setImported({project, receipt: data.receipt_id});
    } catch (e) { setError(String(e)); }
  }
  async function uploadAlignment() {
    if (!alignment) return;
    setError(''); setStatus('');
    try {
      const {data} = await api.post<{receipt_id: string}>(`/api/family-msa/projects/${encodeURIComponent(project)}/imports/alignment`, alignment,
        {params: {format, molecule, replay_key: alignmentKey}, headers: {'Content-Type': 'application/octet-stream'}});
      setImported({project, receipt: data.receipt_id});
      setStatus('Alignment imported. Original bytes are retained.');
    } catch (e) { setError(String(e)); }
  }
  return <section aria-label="Project alignment library"><h2>Project alignment library</h2>
    <label>Project <select aria-label="Project" value={project} onChange={e => setProject(e.target.value)}><option value="">Choose a Project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {error && <p role="alert">{error}</p>}{status && <p role="status">{status}</p>}
    {imported && <p><Link to={`/alignments/results/${encodeURIComponent(imported.receipt)}?project_id=${encodeURIComponent(imported.project)}`}>Open imported result</Link></p>}
    {project && <><ul>{rows.map((row, index) => <li key={row.receipt_id}><Link to={`/alignments/results/${encodeURIComponent(row.receipt_id)}?project_id=${encodeURIComponent(project)}`}>{row.entity_kind === 'family_msa_import' ? 'Imported alignment' : 'Alignment result'} {index + 1}</Link> · {row.availability}</li>)}</ul>{cursor && <button onClick={more}>Load more results</button>}
      <fieldset><legend>Import an alignment file</legend><p>Original bytes are retained. A3M displays match columns only; insertions remain in the original file. Stockholm annotations remain in the original file. Lossless conversion is not offered.</p>
        <label>Format <select value={format} onChange={e => {setFormat(e.target.value); setAlignmentKey(crypto.randomUUID());}}><option value="fasta">Aligned FASTA</option><option value="stockholm">Stockholm</option><option value="clustal">Clustal</option><option value="a3m">A3M</option></select></label>
        <label>Molecule <select value={molecule} onChange={e => {setMolecule(e.target.value); setAlignmentKey(crypto.randomUUID());}}><option value="protein">Protein</option><option value="dna">DNA</option><option value="rna">RNA</option></select></label>
        <input aria-label="Alignment file" type="file" onChange={e => {setAlignment(e.target.files?.[0] || null); setAlignmentKey(crypto.randomUUID());}} />
        <button type="button" disabled={!alignment} onClick={uploadAlignment}>Import alignment</button>
      </fieldset>
      <fieldset><legend>Import portable Project bundle</legend><p>Select the exported bundle directory. The import retains its files and does not create a Job.</p>
        <input aria-label="Bundle directory" type="file" multiple {...({webkitdirectory: ''} as object)} onChange={e => {setFiles(Array.from(e.target.files || [])); setReplayKey(crypto.randomUUID());}} />

        <button type="button" disabled={!files.length} onClick={upload}>Import bundle</button>
      </fieldset></>}
  </section>;
}
