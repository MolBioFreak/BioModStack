import { useState } from 'react';
import { FileBrowser } from './FileBrowser';
import { discoEntityKinds, discoInputAuthority, moveDiscoEntity, removeDiscoEntity, type DiscoRow } from '../lib/discoAuthoring';
const field = 'w-full rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2';
/** Native ordered records, not a molecule translator. Unknown imported fields survive edits. */
export function DiscoJobsInput({ value, path, onChange }: { value: unknown; path: string; onChange: (value: unknown) => void }) {
    const [file, setFile] = useState<{ job: number; entity: number }>();
    const jobs = Array.isArray(value) ? value as DiscoRow[] : [];
    const update = (index: number, job: DiscoRow) => onChange(jobs.map((item, i) => i === index ? job : item));
    const entityUpdate = (ji: number, ei: number, entity: DiscoRow) => update(ji, { ...jobs[ji], sequences: jobs[ji].sequences.map((row: DiscoRow, i: number) => i === ei ? entity : row) });
    return <section aria-label="DISCO ordered jobs" className="space-y-3">
        <h3 className="font-semibold">Ordered jobs and molecular entities</h3>
        <p role="status">Active input: {discoInputAuthority(value, path)}. Explicit jobs override the native JSON file, which overrides task shorthand. Clearing this override restores the retained file or shorthand.</p>
        <p className="text-sm">Protein “-” masks request sequence design. Each explicit DNA/RNA row is one strand: no reverse complement is added. Entity, copy and residue positions are one-based. Bond atom names and integer atom maps are different native types; integer-map runtime support is not qualified.</p>
        {value !== undefined && !Array.isArray(value) && <p>Retained native input: {JSON.stringify(value)}. Adding a job deliberately replaces it.</p>}
        {jobs.map((job, ji) => !job || typeof job !== 'object' ? <p key={ji}>Retained invalid job: {JSON.stringify(job)}</p> : <fieldset key={ji} className="space-y-3 rounded border p-3"><legend>Job {ji + 1}</legend>
            <label>Job name<input aria-label={`DISCO job ${ji + 1} name`} className={field} value={job.name ?? ''} onChange={e => update(ji, { ...job, name: e.target.value })} /></label>
            {Array.isArray(job.sequences) ? job.sequences.map((entity: DiscoRow, ei: number) => {
                if (!entity || typeof entity !== 'object') return <p key={ei}>Retained entity {ei + 1}: {JSON.stringify(entity)}</p>;
                const kind = discoEntityKinds.find(key => Object.hasOwn(entity, key));
                if (!kind || !entity[kind] || typeof entity[kind] !== 'object') return <p key={ei}>Retained entity {ei + 1}: {JSON.stringify(entity)}</p>;
                const payload = entity[kind]; const key = kind === 'ligand' ? 'ligand' : kind === 'ion' ? 'ion' : 'sequence';
                const patch = (name: string, next: unknown) => entityUpdate(ji, ei, { ...entity, [kind]: { ...payload, [name]: next } });
                return <div key={ei} className="space-y-2 border p-2"><h4>Entity {ei + 1} · {kind}</h4>
                    <label>{kind === 'ligand' ? 'Native ligand: SMILES, CCD_code or FILE_path' : kind === 'ion' ? 'Ion CCD code' : kind === 'proteinChain' ? 'Protein sequence / design masks' : 'Fixed strand sequence'}<textarea aria-label={`Job ${ji + 1} entity ${ei + 1} ${key}`} className={field} value={payload[key] ?? ''} onChange={e => patch(key, e.target.value)} /></label>
                    {kind === 'ligand' && <button type="button" onClick={() => setFile({ job: ji, entity: ei })}>Browse ligand file</button>}
                    <label>Copies<input className={field} type="number" min={1} value={payload.count ?? ''} onChange={e => patch('count', e.target.value === '' ? '' : Number(e.target.value))} /></label>
                    {ei > 0 && <button type="button" onClick={() => update(ji, moveDiscoEntity(job, ei, ei - 1))}>Move entity {ei + 1} up (remap bonds)</button>}
                    <button type="button" onClick={() => update(ji, removeDiscoEntity(job, ei))}>Remove entity {ei + 1} and its bonds</button>
                </div>;
            }) : <p>Retained sequences: {JSON.stringify(job.sequences)}</p>}
            <div className="flex flex-wrap gap-2">{discoEntityKinds.map(kind => <button type="button" key={kind} onClick={() => update(ji, { ...job, sequences: [...(Array.isArray(job.sequences) ? job.sequences : []), { [kind]: { [kind === 'ligand' ? 'ligand' : kind === 'ion' ? 'ion' : 'sequence']: '', count: 1 } }] })}>Add {kind}</button>)}</div>
            <h4>Native covalent bonds</h4>
            {Array.isArray(job.covalent_bonds) && job.covalent_bonds.map((bond: DiscoRow, bi: number) => <fieldset key={bi} className="grid gap-2 sm:grid-cols-2"><legend>Bond {bi + 1}</legend>{['left', 'right'].map(side => <div key={side}>{['entity', 'position', 'copy', 'atom'].map(part => {
                const key = `${side}_${part}`; const patch = (next: unknown) => update(ji, { ...job, covalent_bonds: job.covalent_bonds.map((b: DiscoRow, i: number) => { if (i !== bi) return b; const row = { ...b }; if (next === undefined) delete row[key]; else row[key] = next; return row; }) });
                return <label key={key} className="block">{key}{part === 'atom' && <select aria-label={`${side} atom type`} value={typeof bond[key] === 'number' ? 'integer' : 'name'} onChange={e => patch(e.target.value === 'integer' ? 0 : '')}><option value="name">Atom name</option><option value="integer">Integer map</option></select>}<input className={field} aria-label={`Bond ${bi + 1} ${key}`} type={part !== 'atom' || typeof bond[key] === 'number' ? 'number' : 'text'} value={bond[key] ?? ''} onChange={e => patch(e.target.value === '' && part === 'copy' ? undefined : part !== 'atom' || typeof bond[key] === 'number' ? e.target.value === '' ? '' : Number(e.target.value) : e.target.value)} /></label>;
            })}</div>)}<button type="button" onClick={() => update(ji, { ...job, covalent_bonds: job.covalent_bonds.filter((_: unknown, i: number) => i !== bi) })}>Remove bond {bi + 1}</button></fieldset>)}
            <p className="text-xs">Removing an entity explicitly removes bonds touching that entity and remaps remaining valid references; reorder remaps valid entity references. Unresolved references remain native-owner validation, not a new launch gate.</p>
            <button type="button" onClick={() => update(ji, { ...job, covalent_bonds: [...(job.covalent_bonds ?? []), {}] })}>Add bond</button>
            {ji > 0 && <button type="button" onClick={() => { const next = [...jobs]; [next[ji - 1], next[ji]] = [next[ji], next[ji - 1]]; onChange(next); }}>Move job {ji + 1} up</button>}
            <button type="button" onClick={() => onChange(jobs.filter((_, i) => i !== ji))}>Remove job {ji + 1}</button>
        </fieldset>)}
        <button type="button" onClick={() => onChange([...jobs, { name: '', sequences: [] }])}>Add DISCO job</button>
        {value !== undefined && <button type="button" onClick={() => onChange(undefined)}>Clear explicit jobs override</button>}
        {file && <FileBrowser accept=".sdf,.mol,.mol2" title="Select native ligand file" onCancel={() => setFile(undefined)} onSelect={path => { const entity = jobs[file.job].sequences[file.entity]; entityUpdate(file.job, file.entity, { ...entity, ligand: { ...entity.ligand, ligand: `FILE_${path}` } }); setFile(undefined); }} />}
    </section>;
}
