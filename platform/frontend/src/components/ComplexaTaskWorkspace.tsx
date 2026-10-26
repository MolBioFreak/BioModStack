import type { ReactNode } from 'react';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { ComplexaAtomSelection } from '../lib/complexaNativeSelection';
import { NativeNumber, nativeRecord, patchNative } from './Rfd4TaskWorkspace';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';

export function ComplexaTaskWorkspace({ request, schema, onChange, onTask, renderNative, visual, sourceNames, renderSource, controlDraft, onControlDraft }: {
    request: Record<string, any>; schema: Schema; onChange: (request: Record<string, any>) => void; onTask: (task: string) => void;
    renderNative: (key: string, schema: Schema) => ReactNode; sourceNames: string[];
    renderSource?: (label: string, value: string, onChange: (slot: string) => void) => ReactNode;
    visual?: { slot: string; atoms?: ComplexaAtomSelection[]; issue?: string };
    controlDraft: Record<string, any>; onControlDraft: (value: Record<string, any>) => void;
}) {
    const source = nativeRecord(request.source);
    // Discovery owns transport capability; older adapters keep integer editing.
    const sourceSchema = nativeRecord(schema).$defs?.Source ?? schema.properties?.source;
    const hotspotSchema = sourceSchema?.properties?.target_hotspots;
    const hotspotArrays = [hotspotSchema, ...(hotspotSchema?.anyOf ?? [])].filter(s => s?.type === 'array');
    const tokenTransport = hotspotArrays.some(s => [s.items, ...(s.items?.anyOf ?? [])].some(item => item?.type === 'string'));
    const sourcePatch = (key: string, value: unknown) => onChange({ ...request, source: patchNative(source, key, value) });
    const operation = request.operation ?? 'generate';
    // Presentation only: config.py builds motif dictionaries for monomer tasks,
    // and target dictionaries (including target/ligand context) for binder evaluation.
    const monomerMotif = request.task?.startsWith('monomer_motif');
    const evaluates = ['design', 'evaluate', 'analysis'].includes(operation);
    const targetContext = request.task === 'protein_binder' || (!monomerMotif && evaluates);
    const ligandContext = ['ligand_binder', 'AME'].includes(request.task) || (!monomerMotif && evaluates);
    const motifContext = monomerMotif || request.task === 'AME';
    const atomRoles = [...(motifContext ? ['motif_atoms'] : []), ...(ligandContext ? ['ligand_atoms'] : [])];
    const sourceFields = [
        ...(targetContext ? ['input_spec'] : []),
        ...(motifContext ? ['contig_string', 'segment_order'] : []),
        ...(ligandContext ? ['SMILES'] : []),
        ...(!monomerMotif ? ['pdb_id'] : []),
    ];
    const titles: Record<string, string> = { input_spec: 'Target chains and residues', contig_string: 'Motif contig', segment_order: 'Segment order', SMILES: 'Ligand SMILES', pdb_id: 'PDB identifier' };
    const stages: Record<string, string> = { generate: 'Generate', design: 'Generate → filter → evaluate → analyze', evaluate: 'Evaluate', analysis: 'Evaluate → analyze' };
    return <div data-complexa-task-workspace className="space-y-4 min-w-0">
        <label>Complexa task<select aria-label="Complexa task" value={request.task ?? ''} onChange={e => onTask(e.target.value)}><option value="">Choose native task</option>{(schema.properties?.task?.enum ?? []).map(t => <option key={String(t)} value={String(t)}>{String(t).replaceAll('_', ' ')}</option>)}</select></label>
        <label>Operation<select aria-label="Native operation" value={operation} onChange={e => onChange({ ...request, operation: e.target.value })}>{(schema.properties?.operation?.enum ?? []).map(op => <option key={String(op)} value={String(op)}>{String(op)}</option>)}</select></label>
        <p>Stages: {stages[operation] ?? operation}</p>
        {['evaluate', 'analysis'].includes(operation) && <fieldset className="space-y-2"><legend>Evaluation candidate basket</legend>
            <p>Choose candidates in evaluation order; target context stays separate.</p>
            {(request.evaluation_slots ?? []).map((slot: string, i: number) => <div key={i} className="flex flex-wrap items-end gap-2">
                {renderSource ? renderSource(`Candidate ${i + 1}`, slot, value => onChange({ ...request, evaluation_slots: request.evaluation_slots.map((s: string, j: number) => i === j ? value : s) })) : <label>Candidate {i + 1}<input aria-label={`Complexa evaluation slot ${i + 1}`} list="complexa-source-names" value={slot} onChange={e => onChange({ ...request, evaluation_slots: request.evaluation_slots.map((s: string, j: number) => i === j ? e.target.value : s) })} /></label>}
                <button type="button" onClick={() => onChange({ ...request, evaluation_slots: request.evaluation_slots.filter((_: string, j: number) => i !== j) })}>Remove evaluation candidate {i + 1}</button>
            </div>)}
            <button type="button" onClick={() => onChange({ ...request, evaluation_slots: [...(request.evaluation_slots ?? []), ''] })}>Add evaluation candidate</button>
            <button type="button" onClick={() => onChange(patchNative(request, 'evaluation_slots', undefined))}>Omit evaluation basket</button>
        </fieldset>}
        <label>Task name<input aria-label="Native task name" value={request.task_name ?? ''} onChange={e => onChange({ ...request, task_name: e.target.value })} /></label>
        <fieldset className="space-y-3"><legend>{request.task === 'AME' ? 'Motif and ligand source (separate atom roles)' : request.task === 'ligand_binder' ? 'Ligand context' : request.task?.includes('motif') ? 'Motif source' : 'Target context'}</legend>
            {renderSource ? renderSource('Source structure', source.document_id ?? '', slot => sourcePatch('document_id', slot)) : <label>Source document slot<input aria-label="Source document slot" list="complexa-source-names" value={source.document_id ?? ''} onChange={e => sourcePatch('document_id', e.target.value)} /></label>}
            <datalist id="complexa-source-names">{sourceNames.map(s => <option key={s} value={s} />)}</datalist>
            {visual && <div role="status"><p>{visual.issue ?? `Selected from ${visual.slot}: ${visual.atoms?.map(a => `${a.chain}${a.residue}${a.insertion_code} [${a.atoms.join(', ')}]`).join('; ')}`}</p>
                {visual.slot === source.document_id && atomRoles.map(role => <button key={role} type="button" onClick={() => { if (visual.atoms) sourcePatch(role, [...(source[role] ?? []), ...visual.atoms]); }}>Apply selection to {role === 'motif_atoms' ? 'motif' : 'ligand'} atoms</button>)}
                <p>Select atoms, then apply them to a role.</p>
            </div>}
            {sourceFields.map(key => <label key={key} className="flex flex-col">{titles[key]} <small>{key}</small><input aria-label={`Complexa ${key}`} value={source[key] ?? ''} onChange={e => sourcePatch(key, e.target.value)} /><button type="button" onClick={() => sourcePatch(key, undefined)}>Inherit {key}</button></label>)}
            <>{motifContext && <p>Use native contig and segment notation for the motif.</p>}{targetContext && <fieldset><legend>Target hotspots</legend><p>Enter chain and residue, for example A42.</p>{(source.target_hotspots ?? []).some((value: unknown) => typeof value === 'number') && <p>Legacy numeric hotspots are retained; native targeting uses chain/residue tokens.</p>}{(source.target_hotspots ?? []).map((n: number | string, i: number) => <div key={i}>
                {typeof n === 'number' || !tokenTransport && n === '' ? <NativeNumber label={`Target hotspot ${i + 1}`} value={n} onChange={v => sourcePatch('target_hotspots', source.target_hotspots.map((x: unknown, j: number) => i === j ? v : x))} /> : <label>Target hotspot {i + 1}<input aria-label={`Target hotspot ${i + 1}`} value={n ?? ''} onChange={e => sourcePatch('target_hotspots', source.target_hotspots.map((x: unknown, j: number) => i === j ? e.target.value : x))} /></label>}
                <button type="button" onClick={() => sourcePatch('target_hotspots', source.target_hotspots.filter((_: unknown, j: number) => i !== j))}>Remove hotspot {i + 1}</button></div>)}<button type="button" onClick={() => sourcePatch('target_hotspots', [...(source.target_hotspots ?? []), ''])}>Add target hotspot</button></fieldset>}</>
            {atomRoles.map(role => <fieldset key={role}><legend>{role === 'motif_atoms' ? 'Motif atoms' : 'Ligand atoms'}</legend>{(source[role] ?? []).map((row: any, i: number) => <div key={i} className="flex flex-wrap gap-2">
                {['chain', 'insertion_code'].map(key => <label key={key}>{key}<input aria-label={`${role} ${i + 1} ${key}`} value={row[key] ?? ''} onChange={e => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? { ...r, [key]: e.target.value } : r))} /></label>)}
                <NativeNumber label={`${role} ${i + 1} residue`} value={row.residue} onChange={v => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? patchNative(r, 'residue', v) : r))} />
                {(row.atoms ?? []).map((atom: string, j: number) => <label key={j}>Atom<input aria-label={`${role} ${i + 1} atom ${j + 1}`} value={atom} onChange={e => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? { ...r, atoms: r.atoms.map((a: string, k: number) => k === j ? e.target.value : a) } : r))} /></label>)}
                <button type="button" onClick={() => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? { ...r, atoms: [...(r.atoms ?? []), ''] } : r))}>Add atom to {role} {i + 1}</button>
                <button type="button" onClick={() => sourcePatch(role, source[role].filter((_: any, n: number) => n !== i))}>Remove {role} row {i + 1}</button>
            </div>)}<button type="button" onClick={() => sourcePatch(role, [...(source[role] ?? []), { chain: '', insertion_code: '', atoms: [] }])}>Add {role} row</button></fieldset>)}
            <NativeSettingsDisclosure summary={<span>Advanced source settings</span>}>{() => schema.properties?.source && renderNative('source', schema.properties.source)}</NativeSettingsDisclosure>
        </fieldset>
        <fieldset><legend>Sampling amount and lengths</legend><p>{request.task === 'monomer_motif_indexed' ? 'Indexed motif nsamples belongs to native MotifFeatures; it is not a length sweep.' : 'Dataset samples, repeats and search branches are distinct; they do not guarantee a final filtered count.'}</p><div className="flex flex-wrap gap-3">{['nsamples', 'min_length', 'max_length', 'batch_size', 'nrepeat_per_sample', 'n_recycle', 'seed'].map(key => <NativeNumber key={key} label={key} value={request[key]} onChange={v => onChange(patchNative(request, key, v))} />)}</div></fieldset>
        <NativeSettingsDisclosure summary={<span>Reward activation</span>}>{() => <><label>Native reward state<select aria-label="Native reward state" value={request.reward === null ? 'off' : request.reward === undefined || request.reward === 'profile' ? 'profile' : 'configured'} onChange={e => { if (request.reward !== null && typeof request.reward === 'object') onControlDraft({ ...controlDraft, configured_reward: request.reward }); onChange(patchNative(request, 'reward', e.target.value === 'off' ? null : e.target.value === 'profile' ? 'profile' : controlDraft.configured_reward ?? {})); }}><option value="profile">Inherit selected task profile</option><option value="off">Explicitly off</option><option value="configured">Configured composite</option></select></label><p>Profile rewards are not independent blind prediction. Configure AF2, RF3, TMOL and bioinformatics in Native settings.</p></>}</NativeSettingsDisclosure>
    </div>;
}
