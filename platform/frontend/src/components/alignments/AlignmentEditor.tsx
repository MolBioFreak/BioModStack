import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, submitJob } from '../../lib/api';
import { getLaunchContext } from '../../lib/projectManager';
import type { JsonObject } from '../../lib/projectManager';
import { useProjectWorkflowSetup } from '../project-manager/ProjectWorkflowSetup';
import ProjectDatasetPicker, { type DatasetRow } from './ProjectDatasetPicker';
import requestSchema from './alignment-request.schema.json';

type Engine = 'mafft' | 'famsa' | 'muscle' | 'twilight';
type Molecule = 'protein' | 'dna' | 'rna';
type Value = string | number | boolean | null | Record<string, string>;
type Settings = Record<string, Value> & { engine: Engine };
type Field = { key: string; label: string; kind: 'number' | 'integer' | 'boolean' | 'select' | 'text' | 'mapping'; options?: string[]; min?: number; nullable?: boolean };
type PropertySchema = { type?: string; enum?: (string | number)[]; default?: Value; minimum?: number; anyOf?: PropertySchema[] };
type SettingsSchema = { $defs: Record<string, {properties: Record<string, PropertySchema>}> };
const schemaNames: Record<Engine,string> = {mafft:'MafftSettings',famsa:'FamsaSettings',muscle:'MuscleSettings',twilight:'TwilightSettings'};
export function settingsInventory(schema: SettingsSchema, engine: Engine): {fields: Field[]; defaults: Settings} {
    const properties = schema.$defs[schemaNames[engine]].properties;
    const settings: Settings = {engine};
    const result: Field[] = [];
    for (const [key, property] of Object.entries(properties)) {
        if (key === 'engine') continue;
        const shape = property.anyOf?.find(x => x.type !== 'null') ?? property;
        settings[key] = property.default ?? (key === 'leaf_mapping' ? {} : null);
        if (key === 'threads') continue;
        const kind: Field['kind'] = shape.enum ? 'select' : shape.type === 'boolean' ? 'boolean' : shape.type === 'integer' ? 'integer' : shape.type === 'number' ? 'number' : shape.type === 'object' ? 'mapping' : 'text';
        result.push({key, label:key.replaceAll('_',' '), kind, options:shape.enum?.map(String), min:shape.minimum, nullable:property.anyOf?.some(x=>x.type === 'null')});
    }
    return {fields:result,defaults:settings};
}
const inventory = Object.fromEntries((Object.keys(schemaNames) as Engine[]).map(engine => [engine, settingsInventory(requestSchema as SettingsSchema, engine)])) as Record<Engine,ReturnType<typeof settingsInventory>>;
const defaults = Object.fromEntries((Object.keys(schemaNames) as Engine[]).map(engine => [engine, inventory[engine].defaults])) as Record<Engine,Settings>;
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
function requestIdentityWarning(rows: DatasetRow[]) {
    return rows.some(({selection:s,resolved:r}) => {
        const m=s.members[0];
        return Object.entries({project_id:s.project_id,dataset_id:s.dataset_id,revision_id:s.revision_id,revision_sha256:s.revision_sha256,ordinal:m.ordinal,member_identity:m.member_identity,member_sha256:m.member_sha256}).some(([k,v])=>r[k as keyof typeof r] !== v) || Boolean((m.receipt?.entity_id && r.source_identity && m.receipt.entity_id !== r.source_identity) || (m.receipt?.content_digest && r.source_digest && m.receipt.content_digest !== r.source_digest));
    });
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
    const [schemaWarning,setSchemaWarning] = useState('');
    useEffect(() => {
        let cancelled = false;
        void Promise.resolve().then(() => api.get<SettingsSchema>('/api/family-msa/settings-schema')).then(({data}) => {
            if (!cancelled && JSON.stringify(data.$defs) !== JSON.stringify(requestSchema.$defs)) {
                // Key ordering is immaterial to JSON schemas.
                const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) => item && !Array.isArray(item) && typeof item === 'object' ? Object.fromEntries(Object.entries(item).sort(([a],[b]) => a.localeCompare(b))) : item);
                if (canonical(data.$defs) !== canonical(requestSchema.$defs)) setSchemaWarning('The editor and API request schemas differ; review settings before submission.');
            }
        }).catch(() => { if (!cancelled) setSchemaWarning('Could not compare the editor and API request schemas.'); });
        return () => { cancelled = true; };
    }, []);
    const [fasta,setFasta] = useState(''); const [busy,setBusy] = useState(false); const [error,setError] = useState(''); const [job,setJob] = useState(urlJob ?? '');
    const [datasetRows,setDatasetRows] = useState<DatasetRow[]>([]);
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
        const editor = (setup.setup.draft?.editor_state ?? setup.setup.draft) as {molecule?:Molecule;engine?:Engine;drafts?:Record<Engine,Settings>;fasta?:string;datasetRows?:DatasetRow[]} | undefined;
        setMolecule(editor?.molecule ?? (model === 'family_msa_nucleic' ? 'dna' : 'protein')); setEngine(editor?.engine && defaults[editor.engine] ? editor.engine : 'mafft');
        setDrafts(previous => Object.fromEntries((Object.keys(schemaNames) as Engine[]).map(name => [name,{...previous[name],...editor?.drafts?.[name]}])) as Record<Engine,Settings>); setFasta(typeof editor?.fasta === 'string' ? editor.fasta : '');
        setDatasetRows(Array.isArray(editor?.datasetRows) ? editor.datasetRows : []);
    }, [setup.setup]);
    const settings = drafts[engine];
    const update = (key:string,value:Value) => setDrafts(previous=>({...previous,[engine]:{...previous[engine],[key]:value}}));
    const request = () => {
        const roster = fastaRoster(fasta,molecule);
        const source_evidence = datasetRows.map(x => {
            const member=x.selection.members[0]; const expected={project_id:x.selection.project_id,dataset_id:x.selection.dataset_id,revision_id:x.selection.revision_id,revision_sha256:x.selection.revision_sha256,ordinal:member.ordinal,member_identity:member.member_identity,member_sha256:member.member_sha256};
            const mismatches=Object.entries(expected).filter(([key,value]) => x.resolved[key as keyof typeof x.resolved] != null && x.resolved[key as keyof typeof x.resolved] !== value).map(([key])=>key);
            const missing=Object.keys(expected).filter(key=>x.resolved[key as keyof typeof x.resolved] == null);
            if (member.receipt?.entity_id && x.resolved.source_identity && member.receipt.entity_id !== x.resolved.source_identity) mismatches.push('source_identity');
            if (member.receipt?.content_digest && x.resolved.source_digest && member.receipt.content_digest !== x.resolved.source_digest) mismatches.push('source_digest');
            const freeze_mismatches:string[] = x.freeze_requested ? (['project_id','dataset_id','revision_id'] as const).filter(key=>x.freeze_requested![key] !== x.selection[key]) : [];
            if (x.freeze_requested && !x.freeze_requested.ordinals.includes(member.ordinal)) freeze_mismatches.push('ordinal');
            return {selection:x.selection, freeze_requested:x.freeze_requested, freeze_mismatches, resolution:x.resolved, included:x.resolved.status === 'resolved' && typeof x.resolved.sequence === 'string', identity_mismatches:mismatches, identity_missing:missing};
        });
        const rows = [...roster.rows, ...datasetRows.filter(x=>x.resolved.status === 'resolved' && typeof x.resolved.sequence === 'string').map((x,i)=>({
            row_id:`dataset_${String(i+1).padStart(8,'0')}`, header:x.resolved.label || x.selection.members[0].member_identity,
            sequence:x.resolved.sequence!, source_member_id:x.resolved.chain === undefined
                ? x.selection.members[0].member_identity
                : JSON.stringify([x.selection.members[0].member_identity,'observed_chain',x.resolved.chain]),
            occurrence_id:x.selection.members[0].ancestry?.status === 'resolved' && x.selection.members[0].ancestry?.occurrence ? JSON.stringify([x.selection.project_id,x.selection.members[0].ancestry.occurrence.dataset_id,x.selection.members[0].ancestry.occurrence.revision_id,x.selection.members[0].ancestry.occurrence.revision_sha256,x.selection.members[0].ancestry.occurrence.ordinal,x.selection.members[0].ancestry.occurrence.member_sha256]) : null,
            source_offset:x.resolved.start, source_positions:x.resolved.source_positions ?? null,
            transformation_provenance:JSON.stringify({source_identity:x.resolved.source_identity,source_digest:x.resolved.source_digest,chain:x.resolved.chain,start:x.resolved.start,end:x.resolved.end,structure_residue_map:x.resolved.structure_residue_map ?? null,operation:'forward_slice'})
        }))];
        return {name:'Family alignment',model_id:model,mode:'family_msa',params:{roster:{...roster,rows,source_evidence,source_text:rows.length === roster.rows.length ? roster.source_text : null},settings}};
    };
    const draft = (allowIncomplete = false) => {
        const editor_state = {molecule,engine,drafts,fasta,datasetRows};
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
        {schemaWarning && <p role="status">{schemaWarning}</p>}
        {setup.active && !setup.setup && <p role="status">Project setup must load before editing.</p>}
        <fieldset disabled={busy || (setup.active && !setup.setup)} className="space-y-3">
            <label className="block">Molecule <select aria-label="Molecule" className={inputClass} value={molecule} onChange={e=>{const next=e.target.value as Molecule; setMolecule(next); if ((next === 'protein' && engine === 'twilight') || (next !== 'protein' && engine === 'famsa')) setEngine('mafft');}}>{(model === 'family_msa_nucleic' ? ['dna','rna'] : ['protein']).map(x=><option key={x}>{x}</option>)}</select></label>
            <label className="block">Engine <select aria-label="Engine" className={inputClass} value={engine} onChange={e=>setEngine(e.target.value as Engine)}>{(['mafft','famsa','muscle','twilight'] as Engine[]).map(x=><option key={x} value={x} disabled={(molecule === 'protein' && x === 'twilight') || (molecule !== 'protein' && x === 'famsa')}>{x}{(molecule === 'protein' && x === 'twilight') || (molecule !== 'protein' && x === 'famsa') ? ' — unavailable for molecule' : ''}</option>)}</select></label>
            <label className="block">FASTA roster <textarea aria-label="FASTA roster" className={`${inputClass} block w-full font-mono`} rows={8} value={fasta} onChange={e=>setFasta(e.target.value)}/></label>
            <p className="text-sm">FASTA headers and duplicate sequences are retained. Resolved Dataset occurrences are appended as separate rows; unavailable sources are shown but not added to the roster.</p>
            {setup.active && setup.setup && <ProjectDatasetPicker key={`${setup.setup.project_id}:${setup.setup.setup_context_id}`} projectId={setup.setup.project_id} globalId={setup.setup.global_experiment_id} domainId={setup.setup.domain_experiment_id} rows={datasetRows} onChange={setDatasetRows}/>}
            {requestIdentityWarning(datasetRows) && <p role="status">Some source identity evidence is missing or differs from the frozen selection; review the saved request evidence.</p>}
            {datasetRows.some(x=>x.resolved.status === 'resolved' && x.resolved.molecule && x.resolved.molecule !== molecule) && <p role="status">Some resolved sources have a different molecule type from the selected roster. Review the source and molecule before running.</p>}
            <div className="grid gap-3 sm:grid-cols-2"><label>Threads <input aria-label="Threads" className={inputClass} type="number" min={1} step={1} value={String(settings.threads)} onChange={e=>update('threads',e.target.value === '' ? null : Number(e.target.value))}/></label>
            {inventory[engine].fields.map(field => <label key={field.key} className="block">{field.label} {' '}{field.kind === 'boolean' ? <input type="checkbox" checked={Boolean(settings[field.key])} onChange={e=>update(field.key,e.target.checked)}/> : field.kind === 'select' ? <select className={inputClass} value={String(settings[field.key] ?? '')} onChange={e=>update(field.key,e.target.value === '' ? null : field.key === 'acc_mode' ? Number(e.target.value) : e.target.value)}>{field.nullable && <option value="">Native default</option>}{field.options?.map(x=><option key={x}>{x}</option>)}</select> : field.kind === 'mapping' ? <MappingInput value={(settings[field.key] || {}) as Record<string,string>} onChange={value=>update(field.key,value)}/> : field.kind === 'text' ? <textarea className={`${inputClass} block w-full`} value={String(settings[field.key] ?? '')} onChange={e=>update(field.key,e.target.value || null)}/> : <input className={inputClass} type="number" step={field.kind === 'integer' ? 1 : 'any'} min={field.min} value={settings[field.key] == null ? '' : String(settings[field.key])} onChange={e=>update(field.key,e.target.value === '' ? null : Number(e.target.value))}/>}</label>)}
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
