import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, submitJob } from '../../lib/api';
import { getLaunchContext } from '../../lib/projectManager';
import type { JsonObject } from '../../lib/projectManager';
import { useProjectWorkflowSetup } from '../project-manager/ProjectWorkflowSetup';

type Engine = 'mafft' | 'famsa' | 'muscle' | 'twilight';
type Molecule = 'protein' | 'dna' | 'rna';
type Value = string | number | boolean | null | Record<string, string>;
type Settings = Record<string, Value> & { engine: Engine };
type Field = { key: string; label: string; kind: 'number' | 'integer' | 'boolean' | 'select' | 'text' | 'mapping'; options?: string[]; min?: number };
const fields: Record<Engine, Field[]> = {
    mafft: [{key:'strategy',label:'Strategy',kind:'select',options:['fft_ns_2','linsi','ginsi','auto']},{key:'treeout',label:'Retain guide tree',kind:'boolean'}],
    famsa: [{key:'guide_tree',label:'Guide tree',kind:'select',options:['sl','upgma','nj']},{key:'refine_mode',label:'Refinement',kind:'select',options:['auto','on','off']},{key:'keep_duplicates',label:'Keep duplicates',kind:'boolean'},{key:'medoidtree',label:'Medoid tree',kind:'boolean'}, ...['medoid_seeds','medoid_threshold'].map(key=>({key,label:key.replaceAll('_',' '),kind:'integer' as const})),{key:'trim_columns',label:'Trim columns',kind:'number'}],
    muscle: [{key:'mode',label:'Mode',kind:'select',options:['align','super5']},{key:'permutation',label:'Permutation',kind:'select',options:['none','abc','acb','bca','all']},{key:'perturb',label:'Perturbation',kind:'integer',min:0},{key:'ensemble',label:'Ensemble',kind:'select',options:['none','stratified','diversified']},{key:'replicates',label:'Replicates',kind:'integer',min:1}],
    twilight: [{key:'method',label:'Method',kind:'select',options:['pairwise','tcs']},{key:'acc_mode',label:'Accuracy mode',kind:'select',options:['0','1','2','3']},...['acc_max_group','max_subtree'].map(key=>({key,label:key.replaceAll('_',' '),kind:'integer' as const})),...['acc_sample_rate','acc_sample_top','consistency_weight','remove_gappy','match','mismatch','transition','gap_open','gap_extend','gap_ends','scale','xdrop'].map(key=>({key,label:key.replaceAll('_',' '),kind:'number' as const})),...['wildcard','rooted','prune','filter'].map(key=>({key,label:key,kind:'boolean' as const})),{key:'guide_source',label:'Guide source',kind:'select',options:['supplied','mafft']},{key:'guide_tree',label:'Supplied Newick tree',kind:'text'},{key:'leaf_mapping',label:'Leaf mapping',kind:'mapping'}],
};
const defaults: Record<Engine, Settings> = {
    mafft:{engine:'mafft',threads:1,strategy:'fft_ns_2',treeout:false},
    famsa:{engine:'famsa',threads:1,guide_tree:'sl',refine_mode:'auto',keep_duplicates:false,medoidtree:false,medoid_seeds:null,medoid_threshold:null,trim_columns:null},
    muscle:{engine:'muscle',threads:1,mode:'align',permutation:'none',perturb:0,ensemble:'none',replicates:null},
    twilight:{engine:'twilight',threads:1,method:'pairwise',acc_mode:0,acc_max_group:null,acc_sample_rate:null,acc_sample_top:null,consistency_weight:5,max_subtree:null,remove_gappy:0.95,wildcard:false,rooted:false,prune:false,filter:false,match:18,mismatch:-8,transition:-4,gap_open:null,gap_extend:null,gap_ends:null,scale:null,xdrop:600,guide_source:'supplied',guide_tree:null,leaf_mapping:{}},
};
export function fastaRoster(text: string, molecule: Molecule) {
    const rows: {row_id:string;header:string;sequence:string}[] = [];
    for (const line of text.split(/\r?\n/)) {
        if (line.startsWith('>')) rows.push({row_id:`row_${String(rows.length+1).padStart(8,'0')}`,header:line.slice(1),sequence:''});
        else if (line.trim()) { if (!rows.length) throw new Error('Sequence before FASTA header'); rows[rows.length-1].sequence += line.replace(/\s/g,''); }
    }
    return {molecule, rows, source_format:'fasta', source_text:text};
}
const inputClass = 'rounded border border-slate-500 bg-slate-900 p-1 text-white';
function MappingInput({value, onChange}: {value: Record<string,string>; onChange: (value: Record<string,string>) => void}) {
    const [text, setText] = useState(() => Object.entries(value).map(([a,b])=>`${a}\t${b}`).join('\n'));
    return <textarea className={`${inputClass} block w-full`} aria-label="Leaf mapping" placeholder="One leaf and row ID per line, separated by a tab" value={text} onChange={e=>{ const next=e.target.value; setText(next); onChange(Object.fromEntries(next.split('\n').filter(line=>line.includes('\t')).map(line=>{const [a,...rest]=line.split('\t'); return [a,rest.join('\t')];}))); }}/>;
}
export default function AlignmentEditor({ model }: { model: string }) {
    const setup = useProjectWorkflowSetup();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const urlContext = searchParams.get('launch_context_id');
    const urlJob = searchParams.get('alignment_job_id');
    const [molecule,setMolecule] = useState<Molecule>(model === 'family_msa_nucleic' ? 'dna' : 'protein');
    const [engine,setEngine] = useState<Engine>('mafft');
    const [drafts,setDrafts] = useState<Record<Engine,Settings>>(() => structuredClone(defaults));
    const [fasta,setFasta] = useState(''); const [busy,setBusy] = useState(false); const [error,setError] = useState(''); const [job,setJob] = useState(urlJob ?? '');
    const [contextId,setContextId] = useState(urlContext ?? '');
    const [returnUri,setReturnUri] = useState('');
    useEffect(() => {
        if (!urlContext) return;
        let cancelled = false;
        setContextId(urlContext);
        if (urlJob) setJob(urlJob);
        void getLaunchContext(urlContext).then(context => {
            if (!cancelled && (context.canonical_job_id || context.recovery_job_id)) setJob((context.canonical_job_id || context.recovery_job_id)!);
        }).catch(() => {});
        return () => { cancelled = true; };
    }, [urlContext, urlJob]);
    const hydrated = useRef('');
    useEffect(() => {
        if (!setup.setup) return;
        const id = `${setup.setup.project_id}:${setup.setup.setup_context_id}`;
        if (hydrated.current === id) return;
        hydrated.current = id;
        const editor = (setup.setup.draft?.editor_state ?? setup.setup.draft) as {molecule?:Molecule;engine?:Engine;drafts?:Record<Engine,Settings>;fasta?:string} | undefined;
        if (editor?.molecule) setMolecule(editor.molecule); if (editor?.engine && defaults[editor.engine]) setEngine(editor.engine);
        if (editor?.drafts) setDrafts({...structuredClone(defaults),...editor.drafts}); if (typeof editor?.fasta === 'string') setFasta(editor.fasta);
    }, [setup.setup]);
    const settings = drafts[engine];
    const update = (key:string,value:Value) => setDrafts(previous=>({...previous,[engine]:{...previous[engine],[key]:value}}));
    const request = () => ({name:'Family alignment',model_id:model,mode:'family_msa',params:{roster:fastaRoster(fasta,molecule),settings}});
    const draft = (allowIncomplete = false) => {
        const editor_state = {molecule,engine,drafts,fasta};
        try { return {native_job_request:request(),editor_state} as unknown as JsonObject; }
        catch (error) { if (!allowIncomplete) throw error; return {editor_state} as unknown as JsonObject; }
    };
    const bind = async (context: string, id: string) => {
        const binding = await api.post<{return_uri:string}>(`/api/launch-contexts/${encodeURIComponent(context)}/bind`, {job_id:id});
        const uri = binding.data.return_uri;
        if (typeof uri === 'string' && uri.startsWith('/') && !uri.startsWith('//')) setReturnUri(uri);
    };
    const remember = (context: string, id: string) => {
        setContextId(context); setJob(id);
        const next = new URLSearchParams(searchParams);
        next.set('launch_context_id', context); if (id) next.set('alignment_job_id', id); else next.delete('alignment_job_id');
        navigate({search:`?${next.toString()}`}, {replace:true});
    };
    const retryBinding = async () => {
        setBusy(true); setError('');
        try { await bind(contextId, job); }
        catch (e) { setError(`Project binding could not be confirmed: ${e instanceof Error ? e.message : String(e)}`); }
        finally { setBusy(false); }
    };
    const recover = async () => {
        setBusy(true); setError('');
        try {
            const context = await getLaunchContext(contextId);
            const id = context.canonical_job_id || context.recovery_job_id;
            if (id) remember(contextId, id);
            else setError('No Job ID is currently recorded for this launch context. Check again before starting another run.');
        } catch (e) { setError(`Recovery lookup failed: ${e instanceof Error ? e.message : String(e)}`); }
        finally { setBusy(false); }
    };
    const run = async (action:'save'|'launch') => {
        setBusy(true); setError('');
        try {
            if (setup.active) {
                if (action === 'save') await setup.saveDraft(draft(true));
                else {
                    const prepared = await setup.startRun(draft(), {stayInEditor:true});
                    const contextId = prepared.launch_context_id;
                    if (!contextId) throw new Error('The Project workflow did not return a launch context.');
                    remember(contextId, '');
                    const context = await getLaunchContext(contextId);
                    const scheduler = context.pinned_scheduler;
                    if (!scheduler) throw new Error('The Project workflow did not return a pinned scheduler.');
                    const response = await submitJob({...scheduler, launch_context_id:contextId}, {launchContext:true});
                    const id = response.data?.id;
                    if (typeof id !== 'string') throw new Error('Job API returned no Job ID');
                    remember(contextId, id);
                    await bind(contextId, id);
                }
            }
            else if (action === 'launch') {
                const response = await submitJob({...request(), ...(urlContext ? {launch_context_id:urlContext} : {})}); const id = response.data?.id;
                if (typeof id !== 'string') throw new Error('Job API returned no Job ID');
                if (urlContext) { remember(urlContext, id); await bind(urlContext, id); }
                else setJob(id);
            }
        } catch (e) { setError(`Launch or Project handoff not confirmed: ${e instanceof Error ? e.message : String(e)}. Check the Job and launch context before starting another run.`); } finally { setBusy(false); }
    };
    return <section aria-label="New family alignment" className="space-y-4 rounded border border-slate-600 p-4">
        <h2 className="text-lg font-semibold">New family alignment</h2>
        {setup.active && !setup.setup && <p role="status">Project setup must load before editing.</p>}
        <fieldset disabled={busy || (setup.active && !setup.setup)} className="space-y-3">
            <label className="block">Molecule <select aria-label="Molecule" className={inputClass} value={molecule} onChange={e=>{const next=e.target.value as Molecule; setMolecule(next); if ((next === 'protein' && engine === 'twilight') || (next !== 'protein' && engine === 'famsa')) setEngine('mafft');}}>{(model === 'family_msa_nucleic' ? ['dna','rna'] : ['protein']).map(x=><option key={x}>{x}</option>)}</select></label>
            <label className="block">Engine <select aria-label="Engine" className={inputClass} value={engine} onChange={e=>setEngine(e.target.value as Engine)}>{(['mafft','famsa','muscle','twilight'] as Engine[]).map(x=><option key={x} value={x} disabled={(molecule === 'protein' && x === 'twilight') || (molecule !== 'protein' && x === 'famsa')}>{x}{(molecule === 'protein' && x === 'twilight') || (molecule !== 'protein' && x === 'famsa') ? ' — unavailable for molecule' : ''}</option>)}</select></label>
            <label className="block">FASTA roster <textarea aria-label="FASTA roster" className={`${inputClass} block w-full font-mono`} rows={8} value={fasta} onChange={e=>setFasta(e.target.value)}/></label>
            <p className="text-sm">Direct FASTA only; Project dataset source selection is not connected. Headers and duplicate sequences are retained.</p>
            <div className="grid gap-3 sm:grid-cols-2"><label>Threads <input aria-label="Threads" className={inputClass} type="number" min={1} step={1} value={String(settings.threads)} onChange={e=>update('threads',e.target.value === '' ? null : Number(e.target.value))}/></label>
            {fields[engine].map(field => <label key={field.key} className="block">{field.label} {' '}{field.kind === 'boolean' ? <input type="checkbox" checked={Boolean(settings[field.key])} onChange={e=>update(field.key,e.target.checked)}/> : field.kind === 'select' ? <select className={inputClass} value={String(settings[field.key] ?? '')} onChange={e=>update(field.key,field.key === 'acc_mode' ? Number(e.target.value) : e.target.value)}>{field.options?.map(x=><option key={x}>{x}</option>)}</select> : field.kind === 'mapping' ? <MappingInput value={(settings[field.key] || {}) as Record<string,string>} onChange={value=>update(field.key,value)}/> : field.kind === 'text' ? <textarea className={`${inputClass} block w-full`} value={String(settings[field.key] ?? '')} onChange={e=>update(field.key,e.target.value || null)}/> : <input className={inputClass} type="number" step={field.kind === 'integer' ? 1 : 'any'} min={field.min} value={settings[field.key] == null ? '' : String(settings[field.key])} onChange={e=>update(field.key,e.target.value === '' ? null : Number(e.target.value))}/>}</label>)}
            </div>
            {setup.active && <button type="button" onClick={()=>void run('save')} className={inputClass}>Save Project draft</button>}{' '}
            <button type="button" onClick={()=>void run('launch')} className={inputClass}>Start run</button>
        </fieldset>
        {error && <p role="alert">{error}</p>}{contextId && !job && <div><p>Launch context: {contextId}</p><button type="button" disabled={busy} onClick={()=>void recover()} className={inputClass}>Recover Project Job</button></div>}{job && <div role="status"><p>Job submitted: <Link to={`/alignments/jobs/${encodeURIComponent(job)}`}>{job}</Link></p>
            {contextId && <><p>Launch context: {contextId}</p><button type="button" disabled={busy} onClick={()=>void retryBinding()} className={inputClass}>Retry Project binding</button>{returnUri && <> <Link to={returnUri}>Return to Project</Link></>}</>}
        </div>}
        <p className="text-sm">Scientific scope: MAFFT and MUSCLE support protein, DNA and RNA; FAMSA supports protein; TWILIGHT supports DNA and RNA. Direct API requests may specify other combinations; these descriptors do not validate them.</p>
        <p className="text-sm">Execution availability depends on the installed Job dispatcher and native engines. No alternative engine is selected automatically.</p>
    </section>;
}
