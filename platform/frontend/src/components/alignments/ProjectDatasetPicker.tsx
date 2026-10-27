import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { listDomainDatasets, listDomainDatasetRevisions } from '../../lib/projectManager';
import type { DomainDatasetHead, DomainDatasetRevisionSummary } from '../../lib/projectManager';

type Occurrence = {ancestry?: {status:string; occurrence?:{dataset_id:string; revision_id:string; revision_sha256:string; ordinal:number; member_sha256:string}}; ordinal:number; role:string; member_identity:string; member_sha256:string; receipt:{entity_kind:string; entity_id:string; content_digest?:string} | null};
type Selection = {project_id:string; dataset_id:string; revision_id:string; revision_sha256:string; members:Occurrence[]};
type ResidueMap = {schema:string; source_identity:string; source_digest:string; format:string; numbering:string; model_selection:string; entries:{observed_index:number;chain_id:string;model_index:number;model_serial:number|null;sequence_number:number;insertion_code:string;hetero_flag:string;residue_name:string}[]};
type Resolved = {project_id?:string; dataset_id?:string; revision_id?:string; revision_sha256?:string; ordinal?:number; member_identity?:string; member_sha256?:string; status:string; reason?:string; sequence?:string; label?:string; molecule?:string; start?:number; end?:number; source_positions?:number[]; source_identity?:string; source_digest?:string; chain?:string; structure_residue_map?:ResidueMap | null; chains?:{chain_id:string;length:number}[]};
export type DatasetRow = {selection:Selection; resolved:Resolved; freeze_requested?:{project_id:string; dataset_id:string; revision_id:string; ordinals:number[]}};
type Props = {projectId:string; globalId:string; domainId:string; rows:DatasetRow[]; onChange:React.Dispatch<React.SetStateAction<DatasetRow[]>>};
const enc = encodeURIComponent;
const cls = 'rounded border border-slate-500 bg-slate-900 p-1 text-white';
export default function ProjectDatasetPicker({projectId,globalId,domainId,rows,onChange}:Props) {
    const [datasets,setDatasets] = useState<DomainDatasetHead[]>([]); const [dc,setDc] = useState<string|null>(null);
    const [dataset,setDataset] = useState(''); const [revisions,setRevisions] = useState<DomainDatasetRevisionSummary[]>([]); const [rc,setRc] = useState<string|null>(null); const [revision,setRevision] = useState('');
    const [items,setItems] = useState<Occurrence[]>([]); const [offset,setOffset] = useState(0); const [next,setNext] = useState<number|null>(null); const [total,setTotal] = useState(0);
    const [selected,setSelected] = useState<number[]>([]); const [start,setStart] = useState(''); const [end,setEnd] = useState(''); const [error,setError] = useState(''); const [busy,setBusy] = useState(false);
    const [chainOptions,setChainOptions] = useState<Record<number,{chain_id:string;length:number}[]>>({}); const [chains,setChains] = useState<Record<number,string>>({});
    const generation = useRef(0); const revisionsGeneration = useRef(0); const addGeneration = useRef(0);
    const scope = `${projectId}\0${globalId}\0${domainId}`;
    const scopeRef = useRef(scope);
    if (scopeRef.current !== scope) { scopeRef.current = scope; generation.current++; revisionsGeneration.current++; addGeneration.current++; }
    useEffect(()=>()=>{generation.current++; revisionsGeneration.current++; addGeneration.current++;},[]);
    const loadDatasets = async (cursor:string|null) => { const token=++generation.current; try { const page=await listDomainDatasets(projectId,globalId,domainId,undefined,cursor); if(token!==generation.current) return; setDatasets(old=>cursor ? [...old,...page.items] : page.items); setDc(page.next_cursor); } catch(e) {if(token===generation.current) setError(String(e));} };
    useEffect(()=>{ setDatasets([]); setDc(null); setDataset(''); setRevisions([]); setRc(null); setRevision(''); setItems([]); setSelected([]); setOffset(0); setBusy(false); setError(''); if (projectId && globalId && domainId) void loadDatasets(null); },[projectId,globalId,domainId]);
    const loadRevisions = async (id:string,cursor:string|null) => { const token=++revisionsGeneration.current; try { const page=await listDomainDatasetRevisions(projectId,globalId,domainId,id,undefined,cursor); if(token!==revisionsGeneration.current) return; setRevisions(old=>cursor ? [...old,...page.items] : page.items); setRc(page.next_cursor); } catch(e) {if(token===revisionsGeneration.current) setError(String(e));} };
    const base = `/api/family-msa/projects/${enc(projectId)}/datasets/${enc(dataset)}/revisions/${enc(revision)}/resources`;
    const baseRef = useRef(base); baseRef.current = base;
    useEffect(()=>{ setChainOptions({}); setChains({}); },[base]);
    useEffect(()=>{ if (!dataset || !revision) return; let cancelled=false; setError(''); void api.get<{items:Occurrence[];next_offset:number|null;total:number}>(base,{params:{offset,limit:50}}).then(r=>{if (!cancelled) {setItems(r.data.items); setNext(r.data.next_offset); setTotal(r.data.total);}}).catch(e=>{if (!cancelled) setError(String(e));}); return ()=>{cancelled=true;}; },[base,dataset,revision,offset]);
    const add = async () => { const token=++addGeneration.current; setBusy(true); setError(''); try {
        const frozen=(await api.post<Selection>(`${base}/freeze`,{ordinals:selected})).data;
        if(token!==addGeneration.current) return;
        const added:DatasetRow[]=[];
        for (const member of frozen.members) {
            const params:Record<string,number|string>={}; if (start !== '') params.start=Number(start); if (end !== '') params.end=Number(end);
            let resolved:Resolved;
            try { if (member.receipt?.entity_kind === 'design') { if (chains[member.ordinal] !== undefined) params.chain=chains[member.ordinal]; resolved=(await api.get<Resolved>(`${base}/${member.ordinal}/design`,{params})).data; } else resolved=(await api.get<Resolved>(`${base}/${member.ordinal}/molecular`,{params})).data; }
            catch(e) { resolved={status:'unavailable',reason:`Resolution request failed: ${String(e)}`}; }
            if(token!==addGeneration.current) return;
            added.push({selection:{...frozen,members:[member]},resolved,freeze_requested:{project_id:projectId,dataset_id:dataset,revision_id:revision,ordinals:[...selected]}});
        }
        onChange(previous=>[...previous,...added]); setSelected([]);
    } catch(e) {if(token===addGeneration.current) setError(String(e));} finally {if(token===addGeneration.current) setBusy(false);} };
    return <section aria-label="Project Dataset sources" className="space-y-2 border border-slate-600 p-3">
        <h3>Project Dataset sources</h3>
        <label>Dataset <select aria-label="Dataset" className={cls} value={dataset} onChange={e=>{const id=e.target.value; revisionsGeneration.current++; addGeneration.current++; setBusy(false); setRc(null); setDataset(id); setRevision(''); setRevisions([]); setItems([]); setSelected([]); setOffset(0); if(id) void loadRevisions(id,null);}}><option value="">Choose a Dataset</option>{datasets.map(d=><option key={d.dataset_id} value={d.dataset_id}>{d.name} — {d.dataset_id}</option>)}</select></label>{dc && <button type="button" className={cls} onClick={()=>void loadDatasets(dc)}>More Datasets</button>}
        <label> Revision <select aria-label="Dataset revision" className={cls} value={revision} onChange={e=>{addGeneration.current++; setBusy(false); setRevision(e.target.value);setOffset(0);setSelected([]);setItems([]);}}><option value="">Choose a revision</option>{revisions.map(r=><option key={r.revision_id} value={r.revision_id}>Revision {r.revision_number} — {r.revision_id}</option>)}</select></label>{rc && <button type="button" className={cls} onClick={()=>void loadRevisions(dataset,rc)}>More revisions</button>}
        {revision && <><p>Resources {offset + (items.length ? 1 : 0)}–{offset+items.length} of {total}; selections persist across pages.</p>{items.map(item=><label className="block" key={item.ordinal}><input type="checkbox" checked={selected.includes(item.ordinal)} onChange={e=>{setSelected(old=>e.target.checked ? [...old,item.ordinal] : old.filter(x=>x!==item.ordinal)); if(e.target.checked && item.receipt?.entity_kind === 'design') { const current=base; void api.get<Resolved>(`${base}/${item.ordinal}/design`).then(r=>{if(current === baseRef.current) setChainOptions(old=>({...old,[item.ordinal]:r.data.chains ?? []}));}).catch(()=>{}); }}}/> #{item.ordinal} {item.role} — {item.member_identity} ({item.receipt?.entity_kind ?? 'unknown resource'}) {selected.includes(item.ordinal) && item.receipt?.entity_kind === 'design' && <select aria-label={`Chain for occurrence ${item.ordinal}`} className={cls} value={chains[item.ordinal] ?? ''} onChange={e=>setChains(old=>({...old,[item.ordinal]:e.target.value}))}><option value="">Choose chain</option>{(chainOptions[item.ordinal] ?? []).map(c=><option key={c.chain_id} value={c.chain_id}>{c.chain_id} ({c.length} residues)</option>)}</select>}</label>)}<button type="button" className={cls} disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous resources</button> <button type="button" className={cls} disabled={next===null} onClick={()=>setOffset(next!)}>Next resources</button>
        <div><label>Region start (0-based) <input aria-label="Region start" className={cls} type="number" min="0" value={start} onChange={e=>setStart(e.target.value)}/></label> <label>End (exclusive) <input aria-label="Region end" className={cls} type="number" min="1" value={end} onChange={e=>setEnd(e.target.value)}/></label></div><button type="button" className={cls} disabled={busy || !selected.length} onClick={()=>void add()}>Add selected resources</button></>}
        {error && <p role="alert">Dataset source: {error}</p>}
        {rows.map((row,i)=><div key={i}>Revision {row.selection.revision_id}, occurrence #{row.selection.members[0].ordinal}, {row.selection.members[0].member_identity}: {row.resolved.status === 'resolved' ? `${row.resolved.label ?? 'sequence'} [${row.resolved.start}, ${row.resolved.end}) ${row.resolved.molecule}` : `unavailable: ${row.resolved.reason ?? 'unknown'}`} <button type="button" className={cls} onClick={()=>onChange(previous=>previous.filter((_,j)=>j!==i))}>Remove source</button>{row.resolved.structure_residue_map && <details><summary>Observed structure residue identities</summary><a download="structure-residue-map.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(row.resolved.structure_residue_map,null,2))}`}>Export residue map JSON</a><pre>{JSON.stringify(row.resolved.structure_residue_map,null,2)}</pre></details>}</div>)}
        <p>Source resolution currently supports verified molecular revisions and Design structure chains; other producer resources remain visible but may be unavailable.</p>
    </section>;
}
