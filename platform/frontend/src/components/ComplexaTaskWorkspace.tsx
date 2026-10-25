import type { ReactNode } from 'react';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { ComplexaAtomSelection } from '../lib/complexaNativeSelection';
import { NativeNumber, nativeRecord, patchNative } from './Rfd4TaskWorkspace';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';

export function ComplexaTaskWorkspace({ request, schema, onChange, onTask, renderNative, visual, sourceNames, controlDraft, onControlDraft }: {
    request: Record<string, any>; schema: Schema; onChange: (request: Record<string, any>) => void; onTask: (task: string) => void;
    renderNative: (key: string, schema: Schema) => ReactNode; sourceNames: string[];
    visual?: { slot: string; atoms?: ComplexaAtomSelection[]; issue?: string };
    controlDraft: Record<string, any>; onControlDraft: (value: Record<string, any>) => void;
}) {
    const source = nativeRecord(request.source);
    const sourcePatch = (key: string, value: unknown) => onChange({ ...request, source: patchNative(source, key, value) });
    const operation = request.operation ?? 'generate';
    const stages: Record<string, string> = { generate: 'Generate', design: 'Generate → filter → evaluate → analyze', evaluate: 'Evaluate', analysis: 'Evaluate → analyze' };
    return <div data-complexa-task-workspace className="space-y-4 min-w-0">
        <label>Complexa task<select aria-label="Complexa task" value={request.task ?? ''} onChange={e => onTask(e.target.value)}><option value="">Choose native task</option>{(schema.properties?.task?.enum ?? []).map(t => <option key={String(t)} value={String(t)}>{String(t).replaceAll('_', ' ')}</option>)}</select></label>
        <label>Native operation<select aria-label="Native operation" value={operation} onChange={e => onChange({ ...request, operation: e.target.value })}>{(schema.properties?.operation?.enum ?? []).map(op => <option key={String(op)} value={String(op)}>{String(op)}</option>)}</select></label>
        <p>Executed stages: {stages[operation] ?? operation}. Native rewards/refolding are not independent blind prediction.</p>
        <label>Native task name<input aria-label="Native task name" value={request.task_name ?? ''} onChange={e => onChange({ ...request, task_name: e.target.value })} /></label>
        <fieldset className="space-y-3"><legend>{request.task === 'AME' ? 'Motif and ligand source (separate atom roles)' : request.task === 'ligand_binder' ? 'Ligand context' : request.task?.includes('motif') ? 'Motif source' : 'Target context'}</legend>
            <label>Source document slot<input aria-label="Source document slot" list="complexa-source-names" value={source.document_id ?? ''} onChange={e => sourcePatch('document_id', e.target.value)} /></label>
            <datalist id="complexa-source-names">{sourceNames.map(s => <option key={s} value={s} />)}</datalist>
            <p>Visual adoption uses exact author chain/residue/insertion/atom identities. The native preparer owns PDB derivation and AME role remapping. Multi-character chains are never truncated.</p>
            {visual && <div role="status"><p>{visual.issue ?? `Selected from ${visual.slot}: ${visual.atoms?.map(a => `${a.chain}${a.residue}${a.insertion_code} [${a.atoms.join(', ')}]`).join('; ')}`}</p>
                {visual.slot === source.document_id && ['motif_atoms', 'ligand_atoms'].map(role => <button key={role} type="button" onClick={() => { if (visual.atoms) sourcePatch(role, [...(source[role] ?? []), ...visual.atoms]); }}>Apply selection to {role === 'motif_atoms' ? 'motif' : 'ligand'} atoms</button>)}
                <p>Target hotspot numbers use native input-spec indexing, not author residue numbers. Edit them explicitly below; no ambiguous index conversion is performed.</p>
            </div>}
            {['input_spec', 'contig_string', 'segment_order', 'SMILES', 'pdb_id'].map(key => <label key={key} className="flex flex-col">{key}<input aria-label={`Complexa ${key}`} value={source[key] ?? ''} onChange={e => sourcePatch(key, e.target.value)} /><button type="button" onClick={() => sourcePatch(key, undefined)}>Inherit {key}</button></label>)}
            <fieldset><legend>Native target hotspot indices</legend><p>These are native indices relative to input_spec. Author residue numbers are not automatically converted.</p>{(source.target_hotspots ?? []).map((n: number, i: number) => <div key={i}><NativeNumber label={`Target hotspot ${i + 1}`} value={n} onChange={v => sourcePatch('target_hotspots', source.target_hotspots.map((x: unknown, j: number) => i === j ? v : x))} /><button type="button" onClick={() => sourcePatch('target_hotspots', source.target_hotspots.filter((_: unknown, j: number) => i !== j))}>Remove hotspot {i + 1}</button></div>)}<button type="button" onClick={() => sourcePatch('target_hotspots', [...(source.target_hotspots ?? []), ''])}>Add target hotspot</button></fieldset>
            {['motif_atoms', 'ligand_atoms'].map(role => <fieldset key={role}><legend>{role === 'motif_atoms' ? 'Motif atom rows' : 'Ligand atom rows'}</legend>{(source[role] ?? []).map((row: any, i: number) => <div key={i} className="flex flex-wrap gap-2">
                {['chain', 'insertion_code'].map(key => <label key={key}>{key}<input aria-label={`${role} ${i + 1} ${key}`} value={row[key] ?? ''} onChange={e => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? { ...r, [key]: e.target.value } : r))} /></label>)}
                <NativeNumber label={`${role} ${i + 1} residue`} value={row.residue} onChange={v => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? patchNative(r, 'residue', v) : r))} />
                {(row.atoms ?? []).map((atom: string, j: number) => <label key={j}>Atom<input aria-label={`${role} ${i + 1} atom ${j + 1}`} value={atom} onChange={e => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? { ...r, atoms: r.atoms.map((a: string, k: number) => k === j ? e.target.value : a) } : r))} /></label>)}
                <button type="button" onClick={() => sourcePatch(role, source[role].map((r: any, n: number) => n === i ? { ...r, atoms: [...(r.atoms ?? []), ''] } : r))}>Add atom to {role} {i + 1}</button>
                <button type="button" onClick={() => sourcePatch(role, source[role].filter((_: any, n: number) => n !== i))}>Remove {role} row {i + 1}</button>
            </div>)}<button type="button" onClick={() => sourcePatch(role, [...(source[role] ?? []), { chain: '', insertion_code: '', atoms: [] }])}>Add {role} row</button></fieldset>)}
            <NativeSettingsDisclosure summary={<span>Source chemistry, center and native compatibility</span>}>{() => schema.properties?.source && renderNative('source', schema.properties.source)}</NativeSettingsDisclosure>
        </fieldset>
        <fieldset><legend>Sampling amount and lengths</legend><p>{request.task === 'monomer_motif_indexed' ? 'Indexed motif nsamples belongs to native MotifFeatures; it is not a length sweep.' : 'Dataset samples, repeats and search branches are distinct; they do not guarantee a final filtered count.'}</p><div className="flex flex-wrap gap-3">{['nsamples', 'min_length', 'max_length', 'batch_size', 'nrepeat_per_sample', 'n_recycle', 'seed'].map(key => <NativeNumber key={key} label={key} value={request[key]} onChange={v => onChange(patchNative(request, key, v))} />)}</div></fieldset>
        <NativeSettingsDisclosure summary={<span>Reward activation</span>}>{() => <><label>Native reward state<select aria-label="Native reward state" value={request.reward === null ? 'off' : request.reward === undefined || request.reward === 'profile' ? 'profile' : 'configured'} onChange={e => { if (request.reward !== null && typeof request.reward === 'object') onControlDraft({ ...controlDraft, configured_reward: request.reward }); onChange(patchNative(request, 'reward', e.target.value === 'off' ? null : e.target.value === 'profile' ? 'profile' : controlDraft.configured_reward ?? {})); }}><option value="profile">Inherit selected task profile</option><option value="off">Explicitly off</option><option value="configured">Configured composite</option></select></label><p>Removing an override restores the selected native profile; it does not disable its reward. Configure exact AF2/RF3/TMOL/bioinformatics settings in Native settings.</p></>}</NativeSettingsDisclosure>
    </div>;
}
