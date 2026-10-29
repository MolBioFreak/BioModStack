import { useState } from 'react';
import { Rfd4Regions, Rfd4SelectionSummary, rfd4ConditionName, type Rfd4VisualSelection } from './Rfd4VisualWorkbench';
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
export function NativeNumber({ label, value, inherited, inheritedLabel = 'Schema reference', onChange, required = false, maximum, help, nullable = false }: { label: string; value: unknown; inherited?: unknown; inheritedLabel?: string; onChange: (value: unknown) => void; required?: boolean; maximum?: number; help?: string; nullable?: boolean }) {
    const shown = value === undefined ? inherited : value;
    return <label className="flex flex-col">{label}<input aria-label={label} type="number" step="any" max={maximum} value={shown == null ? '' : String(shown)} onChange={event => onChange(event.target.value === '' ? '' : Number(event.target.value))} />
        <button type="button" onClick={() => onChange(undefined)}>{required ? 'Clear required value' : 'Inherit'} {label}</button>{nullable && <button type="button" onClick={() => onChange(null)}>Use native null for {label}</button>}{help && <small>{help}</small>}{shown === null && <small>{value === null ? 'Explicit null' : 'Auto / none (native null)'}</small>}
        {value === undefined && inherited === undefined && <small>{required ? 'Not supplied' : 'Inherited; value not reported'}</small>}
        {value === undefined && inherited !== undefined && <small>{inheritedLabel}; not an override</small>}</label>;
}
export type Rfd4LengthDraft = { mode: 'exact' | 'range'; exact?: unknown; minimum?: unknown; maximum?: unknown };
/** Length drafts are editor companions; only the selected numeric values enter input.chains. */
export function Rfd4ChainLengths({ chain, index, onChange, draft, onDraftChange }: {
    chain: Record<string, any>; index: number; onChange: (chain: Record<string, any>) => void;
    draft?: Rfd4LengthDraft; onDraftChange?: (draft: Rfd4LengthDraft) => void;
}) {
    const [local, setLocal] = useState<Rfd4LengthDraft>();
    const current = draft ?? local ?? { mode: chain.min_length !== undefined && chain.min_length === chain.max_length ? 'exact' : 'range', minimum: chain.min_length, maximum: chain.max_length, ...(chain.min_length === chain.max_length ? { exact: chain.min_length } : {}) };
    const retain = (next: Rfd4LengthDraft) => { setLocal(next); onDraftChange?.(next); };
    const apply = (minimum: unknown, maximum: unknown) => onChange(patchNative(patchNative(chain, 'min_length', minimum), 'max_length', maximum));
    return <fieldset><legend>Chain {index + 1} length</legend><p>Required operator-authored protein length; no example length or interface maximum.</p>
        <select aria-label={`Chain ${index + 1} length mode`} value={current.mode} onChange={e => {
            const mode = e.target.value as 'exact' | 'range';
            const next = { ...current, mode, ...(current.mode === 'exact' ? { exact: chain.min_length } : { minimum: chain.min_length, maximum: chain.max_length }) };
            retain(next); apply(mode === 'exact' ? next.exact : next.minimum, mode === 'exact' ? next.exact : next.maximum);
        }}><option value="exact">Exact length</option><option value="range">Length range</option></select>
        {current.mode === 'exact' ? <NativeNumber required label={`Chain ${index + 1} exact length`} value={chain.min_length} onChange={v => { retain({ ...current, exact: v }); apply(v, v); }} /> : ['min_length', 'max_length'].map(key => <NativeNumber required key={key} label={`Chain ${index + 1} ${key}`} value={chain[key]} onChange={v => { retain({ ...current, [key === 'min_length' ? 'minimum' : 'maximum']: v }); onChange(patchNative(chain, key, v)); }} />)}
    </fieldset>;
}
export function patchNative(value: Record<string, any>, key: string, next: unknown) {
    const result = { ...value }; if (next === undefined) delete result[key]; else result[key] = next; return result;
}

export { Rfd4Regions } from './Rfd4VisualWorkbench';
type Visual = Rfd4VisualSelection;
export type Rfd4ConditionInspection = { exampleIndex: number; family: string; value: string; rowIndex: number; select: string; group?: string };
function Conditions({ value, families, visual, onChange, onInspect, advanced }: { advanced?: boolean; value: Record<string, any>; families: string[]; visual?: Visual; onChange: (v: Record<string, any>) => void; onInspect?: (row: Omit<Rfd4ConditionInspection, 'exampleIndex'>) => void }) {
    const [family, setFamily] = useState('C_CRD'); const [conditionValue, setConditionValue] = useState('');
    const [expression, setExpression] = useState(''); const [group, setGroup] = useState('');
    const [undo, setUndo] = useState<{ before: Record<string, any>; after: Record<string, any> }>();
    const change = (next: Record<string, any>) => { setUndo({ before: value, after: next }); onChange(next); };
    return <fieldset className="space-y-3"><legend>Residue conditions</legend>
        <div className="rfd4-condition-shortcuts">{[['C_CRD', 'Fix coordinates'], ['C_SEQ', 'Fix sequence']].filter(([key]) => families.includes(key)).map(([key, label]) => <button key={key} type="button" onClick={() => {
            if (visual?.expression !== undefined) change({ ...value, [key]: { ...nativeRecord(value[key]), true: [...(nativeRecord(value[key]).true ?? []), { select: visual.expression }] } });
        }}>{label} on selection</button>)}</div>
        {!visual?.expression && <p className="rfd4-empty">Select residues in the source to assign a condition, or enter an expression below.</p>}
        <h4>Applied conditions</h4>
        {!Object.keys(value).length && <p className="rfd4-empty">No condition overrides applied.</p>}
        {Object.entries(value).flatMap(([name, entries]) => Object.entries(nativeRecord(entries)).flatMap(([val, rows]) => (Array.isArray(rows) ? rows : []).map((row: any, i: number) => <div key={`${name}:${val}:${i}`} className="rfd4-condition-card">
            <strong>{rfd4ConditionName(name)} · {val}</strong><code>{name}</code><div className="rfd4-condition-query">{row.select}</div>{onInspect && <button type="button" onClick={() => onInspect({ family: name, value: val, rowIndex: i, select: row.select, ...(row.group !== undefined ? { group: row.group } : {}) })}>Inspect {name} {val} row {i + 1}</button>}<details><summary>Edit expression & group</summary><label>Native selection<input aria-label={`${name} ${val} selection ${i + 1}`} value={row.select ?? ''} onChange={e => change({ ...value, [name]: { ...entries, [val]: rows.map((r: any, n: number) => n === i ? { ...r, select: e.target.value } : r) } })} /></label>
            <label>Group<input aria-label={`${name} ${val} group ${i + 1}`} value={row.group ?? ''} onChange={e => change({ ...value, [name]: { ...entries, [val]: rows.map((r: any, n: number) => n === i ? patchNative(r, 'group', e.target.value || undefined) : r) } })} /></label></details>
            <button type="button" onClick={() => { const entriesNext = { ...entries, [val]: rows.filter((_: any, n: number) => n !== i) }; if (!entriesNext[val].length) delete entriesNext[val]; change(Object.keys(entriesNext).length ? { ...value, [name]: entriesNext } : patchNative(value, name, undefined)); }}>Remove {name} {val} row {i + 1}</button>
        </div>)))}
        <details open={advanced || undefined}><summary>More conditions and native expressions</summary><h4>Assign a condition</h4><div className="flex flex-wrap gap-3"><label>Condition family<select aria-label="Condition family" value={family} onChange={e => setFamily(e.target.value)}>{families.map(f => <option key={f} value={f}>{rfd4ConditionName(f)} · {f}</option>)}</select></label>
            <label>Native condition value<input aria-label="Native condition value" value={conditionValue} onChange={e => setConditionValue(e.target.value)} /></label>
            {(conditionTypes[family]?.kind === 'boolean' || conditionTypes[family]?.kind === 'enum') && <label>Known native value<select aria-label="Known native value" value={conditionValue} onChange={e => setConditionValue(e.target.value)}><option value="">Choose explicitly</option>{conditionTypes[family].kind === 'boolean' ? ['true', 'false'].map(v => <option key={v}>{v}</option>) : Object.entries(conditionTypes[family].values).map(([name, code]) => <option key={name} value={name}>{name} ({code})</option>)}{conditionValue && !['true', 'false', ...Object.keys(conditionTypes[family].values)].includes(conditionValue) && <option value={conditionValue}>Manual: {conditionValue}</option>}</select></label>}
            <p className="text-xs whitespace-pre-wrap">{conditionTypes[family]?.description} {family === 'C_IDX' && 'For false, provide a group label; equal labels share native index offsets.'} {family === 'C_CRDGRP' && 'Use -1 for unknown, 0 for absolute coordinates, or a positive internal group ID. Saved internal targets are separate from current coordinates.'}</p>
            {family === 'C_CENTROID' && <fieldset><legend>Native centroid xyz (Å)</legend>{['x', 'y', 'z'].map((axis, index) => <label key={axis}>{axis}<input aria-label={`Centroid ${axis}`} type="number" value={conditionValue.split(',')[index] ?? ''} onChange={e => { const xyz = [0, 1, 2].map(i => conditionValue.split(',')[i] ?? ''); xyz[index] = e.target.value; setConditionValue(xyz.join(',')); }} /></label>)}</fieldset>}
            <label>Selection expression<input aria-label="Selection expression" value={expression} onChange={e => setExpression(e.target.value)} /></label>
            <label>New condition group<input aria-label="New condition group" value={group} onChange={e => setGroup(e.target.value)} /></label></div>
        {visual && <><p role="status" className="rfd4-condition-query">{visual.issue ?? `Selection from ${visual.slot}`}</p><button type="button" onClick={() => { if (visual.expression !== undefined) setExpression(visual.expression); }}>Use visual selection in new condition</button></>}
        <button type="button" onClick={() => change({ ...value, [family]: { ...nativeRecord(value[family]), [conditionValue]: [...(nativeRecord(value[family])[conditionValue] ?? []), { select: expression, ...(group !== '' ? { group } : {}) }] } })}>Apply native condition</button></details>
        <button type="button" disabled={!undo || JSON.stringify(value) !== JSON.stringify(undo.after)} onClick={() => { if (undo) { onChange(undo.before); setUndo(undefined); } }}>Undo condition edit</button>
    </fieldset>;
}

export function Rfd4TaskWorkspace({ advanced, request, schema, mode, recipe, onRecipe, onChange, visual, sourceNames, renderNative, renderSource, retainedTasks, sourceChains, onClearSelection, effectiveConfig, onInspectCondition, lengthDrafts, onLengthDraftsChange }: {
    advanced?: boolean; request: Record<string, any>; schema: Schema; mode: string; recipe?: string; onRecipe: (recipe: string) => void;
    onChange: (request: Record<string, any>) => void; visual?: Visual; sourceNames: string[];
    renderNative: (key: string, schema: Schema) => React.ReactNode;
    renderSource?: (label: string, value: string, onChange: (slot: string) => void) => React.ReactNode;
    sourceChains?: Record<string, string[]>;
    onClearSelection?: () => void;
    retainedTasks?: Array<{ key: string; label: string }>;
    effectiveConfig?: Record<string, any>;
    onInspectCondition?: (row: Rfd4ConditionInspection) => void;
    lengthDrafts?: Record<number, Rfd4LengthDraft>; onLengthDraftsChange?: (drafts: Record<number, Rfd4LengthDraft>) => void;
}) {
    const [localLengthDrafts, setLocalLengthDrafts] = useState<Record<number, Rfd4LengthDraft>>({});
    const activeLengthDrafts = lengthDrafts ?? localLengthDrafts;
    const retainLengths = (next: Record<number, Rfd4LengthDraft>) => { setLocalLengthDrafts(next); onLengthDraftsChange?.(next); };
    const sourceControl = (label: string, value: string, change: (slot: string) => void) => renderSource?.(label, value, change) ?? <label>{label}<input aria-label={label} list="rfd4-source-names" value={value} onChange={event => change(event.target.value)} /></label>;
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
        {mode !== 'evaluate' && <label>RFD4 task<select aria-label="RFD4 task" value={recipe ?? ''} onChange={e => onRecipe(e.target.value)}><option value="">{Object.keys(request).length ? 'Saved task / choose another' : 'Choose task'}</option>{RFD4_RECIPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}{retainedTasks?.map(({ key, label }) => <option key={key} value={`legacy:${key}`}>Restore retained {label}</option>)}</select></label>}
        {mode === 'evaluate' && <h3>Evaluate annotated outputs</h3>}
        {input.kind === 'condition_spec' && <p>Choose a source, select residues, then apply a condition.</p>}
        {input.kind === 'unconditional' && <fieldset><legend>Generated chains</legend><p className="text-xs">New chains is source-free. To edit a document in AtomWorks, choose a context, motif or annotated task.</p>
            <label>Input name<input aria-label="Input name" value={input.name ?? ''} onChange={e => setInput({ ...input, name: e.target.value })} /></label>
            {(input.chains ?? []).map((chain: any, i: number) => <div key={i} className="flex flex-wrap gap-3"><label>Chain ID<input aria-label={`Chain ${i + 1} ID`} value={chain.chain_id ?? ''} onChange={e => setInput({ ...input, chains: input.chains.map((c: any, n: number) => n === i ? { ...c, chain_id: e.target.value } : c) })} /></label>
                <Rfd4ChainLengths key={`${recipe}:${i}`} chain={chain} index={i} draft={activeLengthDrafts[i]} onDraftChange={next => retainLengths({ ...activeLengthDrafts, [i]: next })} onChange={next => setInput({ ...input, chains: input.chains.map((c: any, n: number) => n === i ? next : c) })} />
                <button type="button" onClick={() => { retainLengths(Object.fromEntries(Object.entries(activeLengthDrafts).filter(([key]) => Number(key) !== i).map(([key, value]) => [Number(key) > i ? Number(key) - 1 : Number(key), value]))); setInput({ ...input, chains: input.chains.filter((_: any, n: number) => n !== i) }); }}>Remove chain {i + 1}</button></div>)}
            <button type="button" onClick={() => setInput({ ...input, chains: [...(input.chains ?? []), { chain_id: '' }] })}>Add generated chain</button>
        </fieldset>}
        {(input.kind === 'annotated' || mode === 'evaluate') && <fieldset><legend>Annotated source basket</legend><p>Uses the structure’s saved annotations.</p>
            {visual && <><Rfd4SelectionSummary visual={visual} onClearSelection={onClearSelection} />{visual.issue && <p role="status">{visual.issue}</p>}</>}
            {(input.slots ?? []).map((slot: string, i: number) => <div key={i}>{sourceControl(`Annotated source ${i + 1}`, slot, next => setInput({ ...input, slots: input.slots.map((s: string, n: number) => n === i ? next : s) }))}<button type="button" onClick={() => setInput({ ...input, slots: input.slots.filter((_: string, n: number) => n !== i) })}>Remove annotated slot {i + 1}</button></div>)}
            {renderSource ? renderSource('Add annotated source', '', slot => { if (slot !== '') setInput({ ...input, kind: 'annotated', slots: [...(input.slots ?? []), slot] }); }) : <button type="button" onClick={() => setInput({ ...input, kind: 'annotated', slots: [...(input.slots ?? []), ''] })}>Add annotated slot</button>}
        </fieldset>}
        {input.kind === 'condition_spec' && <>
            {!examples.length && renderSource?.('Conditioned document', '', slot => { if (slot) setInput({ ...input, examples: [{ name: '', sources: [{ slot }] }] }); })}
            {examples.map((example: any, i: number) => <fieldset key={i} className="border rounded p-3 space-y-3"><legend>Example {i + 1}</legend>
                <label>Example name<input aria-label={`Example ${i + 1} name`} value={example.name ?? ''} onChange={e => exampleChange(i, { ...example, name: e.target.value })} /></label>
                <div className="rfd4-design-workbench"><div className="rfd4-design-source">
                {(example.sources ?? []).map((source: any, j: number) => <div key={j}>{sourceControl(`Example ${i + 1} source ${j + 1}`, source.slot, next => exampleChange(i, { ...example, sources: example.sources.map((s: any, n: number) => n === j ? { ...s, slot: next } : s) }))}
                    <details open={advanced || undefined}><summary>Source parsing override</summary><label>Source parse policy override<select aria-label={`Example ${i + 1} source ${j + 1} policy`} value={source.input_as_annotated_cif === undefined ? '' : String(source.input_as_annotated_cif)} onChange={e => exampleChange(i, { ...example, sources: example.sources.map((s: any, n: number) => n === j ? patchNative(s, 'input_as_annotated_cif', e.target.value === '' ? undefined : e.target.value === 'true') : s) })}><option value="">Inherit native policy</option><option value="true">Preserve annotated CIF</option><option value="false">Fresh native parsing</option></select></label></details>
                    <button type="button" onClick={() => exampleChange(i, { ...example, sources: example.sources.filter((_: any, n: number) => n !== j) })}>Remove example {i + 1} source {j + 1}</button></div>)}
                {renderSource ? renderSource(`Add structure to example ${i + 1}`, '', slot => { if (slot !== '') exampleChange(i, { ...example, sources: [...(example.sources ?? []), { slot }] }); }) : <button type="button" onClick={() => exampleChange(i, { ...example, sources: [...(example.sources ?? []), { slot: '' }] })}>Add source to example {i + 1}</button>}
                <Rfd4Regions value={example.contig} visual={qualifiedVisual(example)} onChange={v => exampleChange(i, patchNative(example, 'contig', v))} />
                </div><aside className="rfd4-condition-sidebar" aria-label={`Example ${i + 1} conditions`}>
                <Rfd4SelectionSummary visual={qualifiedVisual(example)} onClearSelection={onClearSelection} />
                <Conditions advanced={advanced} onInspect={onInspectCondition ? row => onInspectCondition({ ...row, exampleIndex: i }) : undefined} value={nativeRecord(example.conditions)} families={families} visual={qualifiedVisual(example)} onChange={v => exampleChange(i, { ...example, conditions: v })} />
                {visual && example.sources?.length > 1 && !qualifiedVisual(example) && <p>Multi-source native chain mapping is not established by this source-local selection. Manual expressions remain available.</p>}
                <fieldset><legend>Pair conditions (exact representative atoms)</legend>{(example.pair_conditions ?? []).map((pair: any, j: number) => <div key={j} className="flex flex-wrap gap-2">{['left', 'right', 'value'].map(key => <label key={key}>{key}{key === 'value' ? <select aria-label={`Pair ${j + 1} value`} value={pair.value} onChange={e => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.map((p: any, n: number) => n === j ? { ...p, value: e.target.value } : p) })}>{['UNSPECIFIED', 'PAIR', 'LOOP'].map(v => <option key={v}>{v}</option>)}</select> : <><input aria-label={`Pair ${j + 1} ${key}`} value={pair[key]} onChange={e => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.map((p: any, n: number) => n === j ? { ...p, [key]: e.target.value } : p) })} />{qualifiedVisual(example)?.expression && <button type="button" onClick={() => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.map((p: any, n: number) => n === j ? { ...p, [key]: qualifiedVisual(example)?.expression } : p) })}>Use selection for pair {j + 1} {key}</button>}</>}</label>)}<button type="button" onClick={() => exampleChange(i, { ...example, pair_conditions: example.pair_conditions.filter((_: any, n: number) => n !== j) })}>Remove pair {j + 1}</button></div>)}
                    <button type="button" onClick={() => exampleChange(i, { ...example, pair_conditions: [...(example.pair_conditions ?? []), { left: '', right: '', value: 'UNSPECIFIED' }] })}>Add pair condition</button></fieldset>
                </aside></div>
                <details open={advanced || undefined}><summary>Parsing policy</summary>
                <fieldset><legend>Example {i + 1} effective parse policy</legend>
                    <p>{(example.sources ?? []).length === 0 ? 'No source policies yet.' : new Set(example.sources.map((s: any) => s.input_as_annotated_cif ?? input.global_args?.input_as_annotated_cif ?? false)).size > 1 ? 'Mixed source policies retained. The native parser requires one policy per example; manual values remain editable.' : (example.sources[0].input_as_annotated_cif ?? input.global_args?.input_as_annotated_cif ?? false) ? 'Preserve annotated CIF (effective for every source).' : 'Fresh native parsing (effective for every source).'}</p>
                    <p>One native parse policy applies to the entire example. Changing it below explicitly updates its source overrides, not other examples.</p>
                    <button type="button" onClick={() => exampleChange(i, { ...example, sources: (example.sources ?? []).map((s: any) => ({ ...s, input_as_annotated_cif: true })) })}>Preserve annotations for example {i + 1}</button>
                    <button type="button" onClick={() => exampleChange(i, { ...example, sources: (example.sources ?? []).map((s: any) => ({ ...s, input_as_annotated_cif: false })) })}>Fresh parsing for example {i + 1}</button>
                    <button type="button" onClick={() => exampleChange(i, { ...example, sources: (example.sources ?? []).map((s: any) => patchNative(s, 'input_as_annotated_cif', undefined)) })}>Inherit policies for example {i + 1}</button>
                </fieldset>
                </details>
                <button type="button" onClick={() => setInput({ ...input, examples: examples.filter((_: any, n: number) => n !== i) })}>Remove example {i + 1}</button>
            </fieldset>)}<button type="button" onClick={() => setInput({ ...input, examples: [...examples, { name: '', sources: [] }] })}>Add conditioned example</button></>}
        <datalist id="rfd4-source-names">{sourceNames.map(name => <option key={name} value={name} />)}</datalist>
        {mode !== 'evaluate' && <fieldset><legend>Sampling amount</legend><p>Designs per input are replicates; batch size is parallel sampling, not final beam count.</p><div className="flex flex-wrap gap-3">{[['num_replicates', 'Designs per input'], ['batch_size', 'Batch size'], ['seed', 'Seed']].map(([key, label]) => <NativeNumber key={key} label={label} value={settings[key]} inheritedLabel={effectiveConfig && Object.hasOwn(effectiveConfig, key) ? 'Resolved native value' : 'Schema reference'} inherited={effectiveConfig && Object.hasOwn(effectiveConfig, key) ? effectiveConfig[key] : schema.properties?.settings?.properties?.[key]?.default} nullable={key === 'seed'} onChange={v => setSetting(key, v)} />)}</div></fieldset>}
        {recipe === 'partial' && <fieldset><legend>Partial diffusion</legend><p>Native trajectory-tail fraction has domain (0,1], not percent. Trajectory-tail fraction counts the tail of integration steps; backbone and latent partial times are separate native controls. No fraction is chosen automatically.</p>{['partial_trajectory_fraction_steps', 'partial_t_backbone', 'partial_t_latent'].map(key => <NativeNumber key={key} label={key} maximum={key === 'partial_trajectory_fraction_steps' ? 1 : undefined} help={key === 'partial_trajectory_fraction_steps' ? 'Native fraction (0,1]; exact precision retained, no default partial override.' : 'Independent native time; not a percentage.'} value={settings.generation?.args?.[key]} inherited={effectiveConfig?.generation?.args?.[key]} onChange={v => setSetting('generation', { ...nativeRecord(settings.generation), args: patchNative(nativeRecord(settings.generation?.args), key, v) })} />)}</fieldset>}
        {recipe === 'inverse' && <p>Native inverse folding uses the selected annotated backbone with independent latent sampling. This is not fixed-sequence sidechain packing.</p>}
        {mode === 'evaluate' && <fieldset><legend>Evaluation task labels</legend>{(request.evaluation_tasks ?? []).map((row: any, i: number) => <div key={i} className="flex flex-wrap gap-2">{['slot', 'task'].map(key => <label key={key}>{key}<input aria-label={`Evaluation ${i + 1} ${key}`} list={key === 'slot' ? 'rfd4-source-names' : undefined} value={row[key] ?? ''} onChange={e => onChange({ ...request, evaluation_tasks: request.evaluation_tasks.map((r: any, n: number) => n === i ? { ...r, [key]: e.target.value } : r) })} /></label>)}<button type="button" onClick={() => onChange({ ...request, evaluation_tasks: request.evaluation_tasks.filter((_: any, n: number) => n !== i) })}>Remove evaluation task {i + 1}</button></div>)}<button type="button" onClick={() => onChange({ ...request, evaluation_tasks: [...(request.evaluation_tasks ?? []), { slot: '', task: '' }] })}>Add evaluation task</button><button type="button" onClick={() => onChange(patchNative(request, 'evaluation_tasks', undefined))}>Inherit evaluation task labels</button></fieldset>}
        <NativeSettingsDisclosure summary={<span>Advanced input and global conditions</span>}>{() => schema.properties?.input && renderNative('input', schema.properties.input)}</NativeSettingsDisclosure>
    </div>;
}
