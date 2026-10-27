import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { listDomainDatasets, listDomainDatasetRevisions } from '../../lib/projectManager';
import type { DomainDatasetHead, DomainDatasetRevisionSummary } from '../../lib/projectManager';

type Occurrence = {ordinal:number; role:string; member_identity:string; member_sha256:string; receipt:{entity_kind:string; entity_id:string} | null};
type Selection = {project_id:string; dataset_id:string; revision_id:string; revision_sha256:string; members:Occurrence[]};
type Resolved = {status:string; reason?:string; sequence?:string; label?:string; molecule?:string; start?:number; end?:number; source_identity?:string; source_digest?:string};
export type DatasetRow = {selection:Selection; resolved:Resolved};
type Props = {projectId:string; globalId:string; domainId:string; rows:DatasetRow[]; onChange:(rows:DatasetRow[])=>void};
const enc = encodeURIComponent;
const cls = 'rounded border border-slate-500 bg-slate-900 p-1 text-white';
export default function ProjectDatasetPicker({projectId,globalId,domainId,rows,onChange}:Props) {
    const [datasets,setDatasets] = useState<DomainDatasetHead[]>([]); const [dc,setDc] = useState<string|null>(null);
    const [dataset,setDataset] = useState(''); const [revisions,setRevisions] = useState<DomainDatasetRevisionSummary[]>([]); const [rc,setRc] = useState<string|null>(null); const [revision,setRevision] = useState('');
    const [items,setItems] = useState<Occurrence[]>([]); const [offset,setOffset] = useState(0); const [next,setNext] = useState<number|null>(null); const [total,setTotal] = useState(0);
    const [selected,setSelected] = useState<number[]>([]); const [start,setStart] = useState(''); const [end,setEnd] = useState(''); const [error,setError] = useState(''); const [busy,setBusy] = useState(false);
    const loadDatasets = async (cursor:string|null) => { try { const page=await listDomainDatasets(projectId,globalId,domainId,undefined,cursor); setDatasets(old=>cursor ? [...old,...page.items] : page.items); setDc(page.next_cursor); } catch(e) {setError(String(e));} };
    useEffect(()=>{ if (projectId && globalId && domainId) void loadDatasets(null); },[projectId,globalId,domainId]);
    const loadRevisions = async (id:string,cursor:string|null) => { try { const page=await listDomainDatasetRevisions(projectId,globalId,domainId,id,undefined,cursor); setRevisions(old=>cursor ? [...old,...page.items] : page.items); setRc(page.next_cursor); } catch(e) {setError(String(e));} };
    const base = `/api/family-msa/projects/${enc(projectId)}/datasets/${enc(dataset)}/revisions/${enc(revision)}/resources`;
    useEffect(()=>{ if (!dataset || !revision) return; let cancelled=false; setError(''); void api.get<{items:Occurrence[];next_offset:number|null;total:number}>(base,{params:{offset,limit:50}}).then(r=>{if (!cancelled) {setItems(r.data.items); setNext(r.data.next_offset); setTotal(r.data.total);}}).catch(e=>{if (!cancelled) setError(String(e));}); return ()=>{cancelled=true;}; },[base,dataset,revision,offset]);
    const add = async () => { setBusy(true); setError(''); try {
        const frozen=(await api.post<Selection>(`${base}/freeze`,{ordinals:selected})).data;
        const added:DatasetRow[]=[];
        for (const member of frozen.members) {
            const params:Record<string,number>={}; if (start !== '') params.start=Number(start); if (end !== '') params.end=Number(end);
            const resolved=(await api.get<Resolved>(`${base}/${member.ordinal}/molecular`,{params})).data;
            added.push({selection:{...frozen,members:[member]},resolved});
        }
        onChange([...rows,...added]); setSelected([]);
    } catch(e) {setError(String(e));} finally {setBusy(false);} };
    return <section aria-label="Project Dataset sources" className="space-y-2 border border-slate-600 p-3">
        <h3>Project Dataset sources</h3>
        <label>Dataset <select aria-label="Dataset" className={cls} value={dataset} onChange={e=>{const id=e.target.value; setDataset(id); setRevision(''); setRevisions([]); setItems([]); setSelected([]); setOffset(0); if(id) void loadRevisions(id,null);}}><option value="">Choose a Dataset</option>{datasets.map(d=><option key={d.dataset_id} value={d.dataset_id}>{d.name} — {d.dataset_id}</option>)}</select></label>{dc && <button type="button" className={cls} onClick={()=>void loadDatasets(dc)}>More Datasets</button>}
        <label> Revision <select aria-label="Dataset revision" className={cls} value={revision} onChange={e=>{setRevision(e.target.value);setOffset(0);setSelected([]);setItems([]);}}><option value="">Choose a revision</option>{revisions.map(r=><option key={r.revision_id} value={r.revision_id}>Revision {r.revision_number} — {r.revision_id}</option>)}</select></label>{rc && <button type="button" className={cls} onClick={()=>void loadRevisions(dataset,rc)}>More revisions</button>}
        {revision && <><p>Resources {offset + (items.length ? 1 : 0)}–{offset+items.length} of {total}; selections persist across pages.</p>{items.map(item=><label className="block" key={item.ordinal}><input type="checkbox" checked={selected.includes(item.ordinal)} onChange={e=>setSelected(old=>e.target.checked ? [...old,item.ordinal] : old.filter(x=>x!==item.ordinal))}/> #{item.ordinal} {item.role} — {item.member_identity} ({item.receipt?.entity_kind ?? 'unknown resource'})</label>)}<button type="button" className={cls} disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous resources</button> <button type="button" className={cls} disabled={next===null} onClick={()=>setOffset(next!)}>Next resources</button>
        <div><label>Region start (0-based) <input aria-label="Region start" className={cls} type="number" min="0" value={start} onChange={e=>setStart(e.target.value)}/></label> <label>End (exclusive) <input aria-label="Region end" className={cls} type="number" min="1" value={end} onChange={e=>setEnd(e.target.value)}/></label></div><button type="button" className={cls} disabled={busy || !selected.length} onClick={()=>void add()}>Add selected resources</button></>}
        {error && <p role="alert">Dataset source: {error}</p>}
        {rows.map((row,i)=><div key={i}>Revision {row.selection.revision_id}, occurrence #{row.selection.members[0].ordinal}, {row.selection.members[0].member_identity}: {row.resolved.status === 'resolved' ? `${row.resolved.label ?? 'sequence'} [${row.resolved.start}, ${row.resolved.end}) ${row.resolved.molecule}` : `unavailable: ${row.resolved.reason ?? 'unknown'}`} <button type="button" className={cls} onClick={()=>onChange(rows.filter((_,j)=>j!==i))}>Remove source</button></div>)}
        <p>Source resolution currently supports verified molecular revisions; other producer resources remain visible but may be unavailable.</p>
    </section>;
}
