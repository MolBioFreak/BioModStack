import { useState } from 'react';
import conditionMetadata from '../lib/rfd4ConditionMetadata.json';
const conditionTypes: Record<string, { kind: string; values: Record<string, number>; description: string }> = conditionMetadata.conditions;
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';

export const RFD4_RECIPES = [
    ['source_free', 'New chains'], ['protein_context', 'Protein context'], ['motif', 'Motif / enzyme'],
    ['mixed_context', 'Annotated molecular context'], ['partial', 'Partial diffusion'],
    ['inverse', 'Native inverse folding'], ['annotated', 'Annotated native input'],
] as const;
export const nativeRecord = (v: unknown): Record<string, any> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, any> : {};
export function rfd4RecipeRequest(recipe: string): Record<string, unknown> {
    if (recipe === 'source_free') return { input: { kind: 'unconditional', name: '', chains: [] } };
    if (['protein_context', 'motif'].includes(recipe)) return { input: { kind: 'condition_spec', examples: [] } };
    return { input: { kind: 'annotated', slots: [] }, ...(recipe === 'inverse' ? { groups: { generation: 'inverse_folding' } } : {}) };
}
/** Cleared numeric text remains explicit; only populated numbers become numeric wire values. */
export function NativeNumber({ label, value, onChange }: { label: string; value: unknown; onChange: (value: unknown) => void }) {
    return <label className="flex flex-col">{label}<input aria-label={label} type="number" value={value == null ? '' : String(value)} onChange={event => onChange(event.target.value === '' ? '' : Number(event.target.value))} />
        <button type="button" onClick={() => onChange(undefined)}>Inherit {label}</button>{value === null && <small>Explicit null</small>}</label>;
}
export function patchNative(value: Record<string, any>, key: string, next: unknown) {
    const result = { ...value }; if (next === undefined) delete result[key]; else result[key] = next; return result;
}

/** Ordered native contig tokens. This edits the actual example.contig, not a second region draft. */
export function Rfd4Regions({ value, onChange }: { value?: string; onChange: (v: string | undefined) => void }) {
    const [minimum, setMinimum] = useState(''), [maximum, setMaximum] = useState('');
    const [fragment, setFragment] = useState('');
    const tokens = value === undefined ? [] : value.split(',');
    const update = (index: number, token: string) => onChange(tokens.map((t, i) => i === index ? token : t).join(','));
    return <fieldset className="space-y-2"><legend>Ordered fragments and generated regions</legend>
        <p>Native tokens retain source chain/residue labels. Generated spans are length ranges; /0 starts a new chain. Conditions remain independent.</p>
        {tokens.map((token, i) => <div key={i} className="flex flex-wrap gap-2"><label>Region {i + 1}<input aria-label={`Region ${i + 1}`} value={token} onChange={e => update(i, e.target.value)} /></label>
            <button type="button" disabled={!i} onClick={() => { const next = [...tokens]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; onChange(next.join(',')); }}>Move region {i + 1} earlier</button>
            <button type="button" onClick={() => onChange(tokens.filter((_, n) => n !== i).join(','))}>Remove region {i + 1}</button></div>)}
        <button type="button" onClick={() => onChange([...tokens, ''].join(','))}>Add native fragment / length range</button>
        <div className="flex flex-wrap gap-2"><label>Generated minimum<input aria-label="Generated minimum" type="number" value={minimum} onChange={e => setMinimum(e.target.value)} /></label><label>Generated maximum<input aria-label="Generated maximum" type="number" value={maximum} onChange={e => setMaximum(e.target.value)} /></label><button type="button" onClick={() => onChange([...tokens, `${minimum}-${maximum}`].join(','))}>Append generated region</button></div>
        <label>Source fragment<input aria-label="Source fragment" placeholder="Native label chain/residue range" value={fragment} onChange={e => setFragment(e.target.value)} /></label><button type="button" onClick={() => onChange([...tokens, fragment].join(','))}>Append source fragment</button>
        <button type="button" onClick={() => onChange([...tokens, '/0'].join(','))}>Add chain break</button>
        <label>Native contig<input aria-label="Native contig" value={value ?? ''} onChange={e => onChange(e.target.value)} /></label>
        <button type="button" onClick={() => onChange(undefined)}>Inherit contig</button>
    </fieldset>;
}

type Visual = { slot: string; expression?: string; issue?: string };
function Conditions({ value, families, visual, onChange }: { value: Record<string, any>; families: string[]; visual?: Visual; onChange: (v: Record<string, any>) => void }) {
    const [family, setFamily] = useState('C_CRD'); const [conditionValue, setConditionValue] = useState('');
    const [expression, setExpression] = useState(''); const [group, setGroup] = useState('');
    const [undo, setUndo] = useState<Record<string, any>>();
    const change = (next: Record<string, any>) => { setUndo(value); onChange(next); };
    return <fieldset className="space-y-3"><legend>Applied native conditions</legend>
        <p>Coordinates, sequence, index and coordinate groups are independent. Values are native keys (for example true, false, group IDs or probabilities). Zero probability excludes; removing the row clears that override.</p>
        {Object.entries(value).flatMap(([name, entries]) => Object.entries(nativeRecord(entries)).flatMap(([val, rows]) => (Array.isArray(rows) ? rows : []).map((row: any, i: number) => <div key={`${name}:${val}:${i}`} className="flex flex-wrap gap-2">
            <strong>{name} · {val}</strong><label>Native selection<input aria-label={`${name} ${val} selection ${i + 1}`} value={row.select ?? ''} onChange={e => change({ ...value, [name]: { ...entries, [val]: rows.map((r: any, n: number) => n === i ? { ...r, select: e.target.value } : r) } })} /></label>
            <label>Group<input aria-label={`${name} ${val} group ${i + 1}`} value={row.group ?? ''} onChange={e => change({ ...value, [name]: { ...entries, [val]: rows.map((r: any, n: number) => n === i ? patchNative(r, 'group', e.target.value || undefined) : r) } })} /></label>
            <button type="button" onClick={() => { const entriesNext = { ...entries, [val]: rows.filter((_: any, n: number) => n !== i) }; if (!entriesNext[val].length) delete entriesNext[val]; change(Object.keys(entriesNext).length ? { ...value, [name]: entriesNext } : patchNative(value, name, undefined)); }}>Remove {name} {val} row {i + 1}</button>
        </div>)))}
        <div className="flex flex-wrap gap-3"><label>Condition family<select aria-label="Condition family" value={family} onChange={e => setFamily(e.target.value)}>{families.map(f => <option key={f}>{f}</option>)}</select></label>
            <label>Native condition value<input aria-label="Native condition value" value={conditionValue} onChange={e => setConditionValue(e.target.value)} /></label>
            {(conditionTypes[family]?.kind === 'boolean' || conditionTypes[family]?.kind === 'enum') && <label>Known native value<select aria-label="Known native value" value={conditionValue} onChange={e => setConditionValue(e.target.value)}><option value="">Choose explicitly</option>{conditionTypes[family].kind === 'boolean' ? ['true', 'false'].map(v => <option key={v}>{v}</option>) : Object.entries(conditionTypes[family].values).map(([name, code]) => <option key={name} value={name}>{name} ({code})</option>)}{conditionValue && !['true', 'false', ...Object.keys(conditionTypes[family].values)].includes(conditionValue) && <option value={conditionValue}>Manual: {conditionValue}</option>}</select></label>}
            <p className="text-xs whitespace-pre-wrap">{conditionTypes[family]?.description} {family === 'C_IDX' && 'For false, provide a group label; equal labels share native index offsets.'} {family === 'C_CRDGRP' && 'Use -1 for unknown, 0 for absolute coordinates, or a positive internal group ID. Saved internal targets are separate from current coordinates.'}</p>
            <label>Selection expression<input aria-label="Selection expression" value={expression} onChange={e => setExpression(e.target.value)} /></label>
            <label>New condition group<input aria-label="New condition group" value={group} onChange={e => setGroup(e.target.value)} /></label></div>
        {visual && <><p role="status">{visual.issue ?? `Selected from ${visual.slot}: ${visual.expression}`}</p><button type="button" onClick={() => { if (visual.expression !== undefined) setExpression(visual.expression); }}>Use visual selection in new condition</button></>}
        <button type="button" onClick={() => change({ ...value, [family]: { ...nativeRecord(value[family]), [conditionValue]: [...(nativeRecord(value[family])[conditionValue] ?? []), { select: expression, ...(group !== '' ? { group } : {}) }] } })}>Apply native condition</button>
        <button type="button" disabled={!undo} onClick={() => { if (undo) { onChange(undo); setUndo(undefined); } }}>Undo condition edit</button>
    </fieldset>;
}

export function Rfd4TaskWorkspace({ request, schema, mode, recipe, onRecipe, onChange, visual, sourceNames, renderNative, retainedTasks, sourceChains }: {
    request: Record<string, any>; schema: Schema; mode: string; recipe?: string; onRecipe: (recipe: string) => void;
    onChange: (request: Record<string, any>) => void; visual?: Visual; sourceNames: string[];
    renderNative: (key: string, schema: Schema) => React.ReactNode;
    sourceChains?: Record<string, string[]>;
    retainedTasks?: Array<{ key: string; label: string }>;
}) {
    const input = nativeRecord(request.input); const settings = nativeRecord(request.settings);
    const setInput = (next: Record<string, any>) => onChange({ ...request, input: next });
    const setSetting = (key: string, value: unknown) => onChange({ ...request, settings: patchNative(settings, key, value) });
    const conditionSchema = schema.properties?.input?.oneOf?.find(s => s.properties?.kind?.const === 'condition_spec')?.properties?.examples?.items as Schema | undefined;
    const families = Object.keys(conditionSchema?.properties?.conditions?.properties ?? {});
    const examples = Array.isArray(input.examples) ? input.examples : [];
    const exampleChange = (i: number, next: Record<string, any>) => setInput({ ...input, examples: examples.map((e: any, n: number) => n === i ? next : e) });
    const qualifiedVisual = (example: any) => {
        if (!visual || !example.sources?.some((s: any) => s.slot === visual.slot)) return undefined;
        if (example.sources.length === 1) return visual;
        // Installed loader preserves chain IDs and rejects collisions; never invent
        // renamed chains or source-index selectors. All source ledgers must be known.
        const chains = example.sources.map((s: any) => sourceChains?.[s.slot]);
        if (chains.some((ids: string[] | undefined) => !ids?.length || ids.includes(''))) return undefined;
        const flattened = chains.flat() as string[];
        return new Set(flattened).size === flattened.length ? visual : undefined;
    };
    return <div data-rfd4-task-workspace className="space-y-4 min-w-0">
        {mode !== 'evaluate' && <label>RFD4 task<select aria-label="RFD4 task" value={recipe ?? ''} onChange={e => onRecipe(e.target.value)}><option value="">Retained native request / choose task</option>{RFD4_RECIPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}{retainedTasks?.map(({ key, label }) => <option key={key} value={`legacy:${key}`}>Restore retained {label}</option>)}</select></label>}
        {mode === 'evaluate' && <h3>Evaluate annotated outputs</h3>}
        <p>Task recipes organize independent drafts. They do not add hotspots, fixed sequence, oracle choices or sampling defaults.</p>
        {input.kind === 'unconditional' && <fieldset><legend>Generated chains</legend>
            <label>Input name<input aria-label="Input name" value={input.name ?? ''} onChange={e => setInput({ ...input, name: e.target.value })} /></label>
            {(input.chains ?? []).map((chain: any, i: number) => <div key={i} className="flex flex-wrap gap-3"><label>Chain ID<input aria-label={`Chain ${i + 1} ID`} value={chain.chain_id ?? ''} onChange={e => setInput({ ...input, chains: input.chains.map((c: any, n: number) => n === i ? { ...c, chain_id: e.target.value } : c) })} /></label>
                {['min_length', 'max_length'].map(key => <NativeNumber key={key} label={`Chain ${i + 1} ${key}`} value={chain[key]} onChange={v => setInput({ ...input, chains: input.chains.map((c: any, n: number) => n === i ? patchNative(c, key, v) : c) })} />)}
                <button type="button" onClick={() => setInput({ ...input, chains: input.chains.filter((_: any, n: number) => n !== i) })}>Remove chain {i + 1}</button></div>)}
            <button type="button" onClick={() => setInput({ ...input, chains: [...(input.chains ?? []), { chain_id: '' }] })}>Add generated chain</button>
        </fieldset>}
        {(input.kind === 'annotated' || mode === 'evaluate') && <fieldset><legend>Annotated source basket</legend><p>Original saved targets and annotations are retained. No chemistry creation or annotation cleanup is implied.</p>
            {(input.slots ?? []).map((slot: string, i: number) => <div key={i}><input aria-label={`Annotated slot ${i + 1}`} list="rfd4-source-names" value={slot} onChange={e => setInput({ ...input, slots: input.slots.map((s: string, n: number) => n === i ? e.target.value : s) })} /><button type="button" onClick={() => setInput({ ...input, slots: input.slots.filter((_: string, n: number) => n !== i) })}>Remove annotated slot {i + 1}</button></div>)}
            <button type="button" onClick={() => setInput({ ...input, kind: 'annotated', slots: [...(input.slots ?? []), ''] })}>Add annotated slot</button>
        </fieldset>}
        {input.kind === 'condition_spec' && <><p>Each example has one native parse policy. Multiple input frames are not merged or reinterpreted by this editor.</p>
            {examples.map((example: any, i: number) => <fieldset key={i} className="border rounded p-3 space-y-3"><legend>Example {i + 1}</legend>
                <label>Example name<input aria-label={`Example ${i + 1} name`} value={example.name ?? ''} onChange={e => exampleChange(i, { ...example, name: e.target.value })} /></label>
                {(example.sources ?? []).map((source: any, j: number) => <div key={j}><label>Source slot<input aria-label={`Example ${i + 1} source ${j + 1}`} list="rfd4-source-names" value={source.slot} onChange={e => exampleChange(i, { ...example, sources: example.sources.map((s: any, n: number) => n === j ? { ...s, slot: e.target.value } : s) })} /></label>
                    <label>Parse policy<select aria-label={`Example ${i + 1} source ${j + 1} policy`} value={source.input_as_annotated_cif === undefined ? '' : String(source.input_as_annotated_cif)} onChange={e => exampleChange(i, { ...example, sources: example.sources.map((s: any, n: number) => n === j ? patchNative(s, 'input_as_annotated_cif', e.target.value === '' ? undefined : e.target.value === 'true') : s) })}><option value="">Inherit native policy</option><option value="true">Preserve annotated CIF</option><option value="false">Fresh native parsing</option></select></label>
                    <button type="button" onClick={() => exampleChange(i, { ...example, sources: example.sources.filter((_: any, n: number) => n !== j) })}>Remove example {i + 1} source {j + 1}</button></div>)}
                <button type="button" onClick={() => exampleChange(i, { ...example, sources: [...(example.sources ?? []), { slot: '' }] })}>Add source to example {i + 1}</button>
                <Rfd4Regions value={example.contig} onChange={v => exampleChange(i, patchNative(example, 'contig', v))} />
                <Conditions value={nativeRecord(example.conditions)} families={families} visual={qualifiedVisual(example)} onChange={v => exampleChange(i, { ...example, conditions: v })} />
                {visual && example.sources?.length > 1 && !qualifiedVisual(example) && <p>Multi-source native chain mapping is not established by this source-local selection. Manual expressions remain available.</p>}
                <fieldset><legend>Pair conditions (exact representative atoms)</legend>{(example.pair_conditions ?? []).map((pair: any, j: number) => <div key={j} className="flex flex-wrap gap-2">{['left', 'right', 'value'].map(key => <label key={key}>{key}{key === 'value' ? <select aria-label={`Pair ${j + 1} value`} value={pair.value} onChange={e => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.map((p: any, n: number) => n === j ? { ...p, value: e.target.value } : p) })}>{['UNSPECIFIED', 'PAIR', 'LOOP'].map(v => <option key={v}>{v}</option>)}</select> : <><input aria-label={`Pair ${j + 1} ${key}`} value={pair[key]} onChange={e => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.map((p: any, n: number) => n === j ? { ...p, [key]: e.target.value } : p) })} />{qualifiedVisual(example)?.expression && <button type="button" onClick={() => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.map((p: any, n: number) => n === j ? { ...p, [key]: qualifiedVisual(example)?.expression } : p) })}>Use selection for pair {j + 1} {key}</button>}</>}</label>)}<button type="button" onClick={() => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.filter((_: any, n: number) => n !== j) })}>Remove pair {j + 1}</button></div>)}
                    <button type="button" onClick={() => exampleChange(i, { ...example, pair_conditions: [...(example.pair_conditions ?? []), { left: '', right: '', value: 'UNSPECIFIED' }] })}>Add pair condition</button></fieldset>
                <button type="button" onClick={() => setInput({ ...input, examples: examples.filter((_: any, n: number) => n !== i) })}>Remove example {i + 1}</button>
            </fieldset>)}<button type="button" onClick={() => setInput({ ...input, examples: [...examples, { name: '', sources: [] }] })}>Add conditioned example</button></>}
        <datalist id="rfd4-source-names">{sourceNames.map(name => <option key={name} value={name} />)}</datalist>
        {mode !== 'evaluate' && <fieldset><legend>Sampling amount</legend><p>Designs per input are replicates; batch size is parallel sampling, not final beam count.</p><div className="flex flex-wrap gap-3">{[['num_replicates', 'Designs per input'], ['batch_size', 'Batch size'], ['seed', 'Seed']].map(([key, label]) => <NativeNumber key={key} label={label} value={settings[key]} onChange={v => setSetting(key, v)} />)}</div></fieldset>}
        {recipe === 'partial' && <fieldset><legend>Partial diffusion</legend><p>Trajectory-tail fraction counts the tail of integration steps; backbone and latent partial times are separate native controls. No fraction is chosen automatically.</p>{['partial_trajectory_fraction_steps', 'partial_t_backbone', 'partial_t_latent'].map(key => <NativeNumber key={key} label={key} value={settings.generation?.args?.[key]} onChange={v => setSetting('generation', { ...nativeRecord(settings.generation), args: patchNative(nativeRecord(settings.generation?.args), key, v) })} />)}</fieldset>}
        {recipe === 'inverse' && <p>Native inverse folding uses the selected annotated backbone with independent latent sampling. This is not fixed-sequence sidechain packing.</p>}
        {mode === 'evaluate' && <fieldset><legend>Evaluation task labels</legend>{(request.evaluation_tasks ?? []).map((row: any, i: number) => <div key={i} className="flex flex-wrap gap-2">{['slot', 'task'].map(key => <label key={key}>{key}<input aria-label={`Evaluation ${i + 1} ${key}`} list={key === 'slot' ? 'rfd4-source-names' : undefined} value={row[key] ?? ''} onChange={e => onChange({ ...request, evaluation_tasks: request.evaluation_tasks.map((r: any, n: number) => n === i ? { ...r, [key]: e.target.value } : r) })} /></label>)}<button type="button" onClick={() => onChange({ ...request, evaluation_tasks: request.evaluation_tasks.filter((_: any, n: number) => n !== i) })}>Remove evaluation task {i + 1}</button></div>)}<button type="button" onClick={() => onChange({ ...request, evaluation_tasks: [...(request.evaluation_tasks ?? []), { slot: '', task: '' }] })}>Add evaluation task</button><button type="button" onClick={() => onChange(patchNative(request, 'evaluation_tasks', undefined))}>Inherit evaluation task labels</button></fieldset>}
        <NativeSettingsDisclosure summary={<span>Native input compatibility and global conditions</span>}>{() => schema.properties?.input && renderNative('input', schema.properties.input)}</NativeSettingsDisclosure>
    </div>;
}
