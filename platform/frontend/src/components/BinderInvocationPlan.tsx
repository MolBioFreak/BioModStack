import { useState, type ReactNode } from 'react';
import { BinderMdInvocationEditor } from './BinderMdInvocationEditor';
import { serializeMolecularDynamicsNativeDraft, type MolecularDynamicsNativeDraft } from './molecularDynamicsUiState';
import { useQueries } from '@tanstack/react-query';
import { fetchModelById } from '../lib/api';
import { InvocationNativeSettings } from './InvocationNativeSettings';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { invocationSettingsFields, type InvocationCatalog as BinderRoundCatalog } from '../lib/invocationSettingsFields';
import { invocationNames, invocationCandidateLabel, changeInvocationMode, invocationLabel, invocationPlacementLabel, invocationDrafts, invocationModes, newInvocation, type InvocationDraftStage } from '../lib/binderInvocationPlan';
import { candidateKey, type CandidateRef, type SelectedRoundModel, type SelectedRoundStage } from '../lib/binderSelectedRound';

const control = 'rounded border border-transparent p-2 bg-[var(--bg-primary)] max-w-full focus-visible:outline-2 focus-visible:outline-sky-500';
const kinds = { sequence_design: 'Sequence design', prediction: 'Blind prediction', maturation: 'Maturation', md: 'Molecular dynamics' };
const models = { sequence_design: ['proteinmpnn', 'fampnn', 'caliby_binder', 'caliby_experimental', 'ligandmpnn', 'protonpottsmpnn'], prediction: ['protenix', 'boltz2', 'esmfold2', 'boltz_cp_experimental'], md: ['molecular_dynamics'] };

/** One intent editor for initial and selected operations. No lifecycle lives here. */
export function BinderInvocationPlan({ stages: original, onChange, candidates, candidateLabels, showAdd = true, renderSettings }: {
    stages: InvocationDraftStage[]; onChange: (value: InvocationDraftStage[]) => void; candidates?: CandidateRef[]; showAdd?: boolean;
    candidateLabels?: Record<string, string>;
    renderSettings?: (call: SelectedRoundModel, refs: CandidateRef[], change: (params: SelectedRoundModel['params']) => void) => ReactNode;
}) {
    const selectedBinding = candidates !== undefined;
    const exactCandidates = candidates ?? [];
    const [editingCall, setEditingCall] = useState<string>();
    const stages = invocationDrafts(original);
    const ids = [...new Set(stages.flatMap(item => item.stage.kind === 'maturation' ? [] : item.stage.models.map(call => call.model_id)))];
    const queries = useQueries({ queries: ids.map(id => ({ queryKey: ['binder-round-catalog', id],
        queryFn: async (): Promise<BinderRoundCatalog> => ({ ...(await fetchModelById(id)).data, id }), staleTime: 60_000, retry: false })) });
    const patch = (index: number, update: (value: InvocationDraftStage) => InvocationDraftStage) => onChange(stages.map((value, i) => i === index ? update(value) : value));
    const callPatch = (index: number, id: string, update: Partial<SelectedRoundModel>) => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, stage: { ...value.stage, models: value.stage.models.map(call => call.invocation_id === id ? { ...call, ...update } : call) } }));
    const sourceLabel = (item: InvocationDraftStage) => {
        const input = item.stage.input;
        if (!('stage_id' in input)) return selectedBinding ? 'Selected results' : 'Original generated outputs';
        const pi = stages.findIndex(previous => previous.stage.id === input.stage_id);
        const previous = stages[pi];
        if (!previous) return 'Saved stage source (not in this draft)';
        const calls = previous.stage.kind === 'maturation' ? [] : previous.stage.models;
        const ci = calls.findIndex(call => call.invocation_id === input.invocation_id);
        const off = !previous.enabled || (input.invocation_id && previous.disabledInvocationIds?.includes(input.invocation_id));
        return `Stage ${pi + 1} · ${input.invocation_id ? (ci < 0 ? 'Saved model call' : invocationLabel(calls[ci], ci)) : 'all outputs'}${off ? ' · Source is off' : ''}`;
    };
    const add = (kind: SelectedRoundStage['kind']) => {
        const id = crypto.randomUUID();
        const input = { selected: true as const, scope: 'whole_composition' as const };
        onChange([...stages, { enabled: true, drafts: {}, stage: kind === 'maturation'
            ? { id, kind, input, model_id: 'binder_refinement', mode: 'refine', params: {} }
            : { id, kind, input, models: [] } }]);
    };
    return <div aria-label="Invocation plan" className="binder-invocation-plan min-w-0 space-y-4 [overflow-wrap:anywhere] [&_label]:block [&_label]:space-y-1 [&_select]:block [&_input:not([type=checkbox])]:block [&_input]:max-w-full [&_select]:max-w-full [&_textarea]:max-w-full [&_button]:rounded [&_button]:border-transparent [&_button]:bg-[var(--bg-secondary)] [&_button]:px-3 [&_button]:py-2 [&_button:focus-visible]:outline-sky-500">
        {stages.length > 1 && <p>Stages use the input chosen below; order alone does not connect them.</p>}
        {showAdd && <div className="flex flex-wrap gap-2">{(['sequence_design', 'prediction', 'md'] as const).map(kind => <button className={control} type="button" key={kind} onClick={() => add(kind)}>Add {kinds[kind]}</button>)}</div>}
        {stages.map((item, index) => <fieldset key={item.stage.id} className="min-w-0 border rounded p-3 space-y-3"><legend>{index + 1}. {kinds[item.stage.kind]}</legend>
            <label><input type="checkbox" checked={item.enabled} onChange={event => patch(index, value => ({ ...value, enabled: event.target.checked }))} />Include stage {index + 1}</label>
            <label>Input source<select aria-label={`Stage ${index + 1} input`} className={control} value={'stage_id' in item.stage.input ? `${item.stage.input.stage_id}:${item.stage.input.invocation_id ?? ''}` : ''} onChange={event => {
                const [stage_id, invocation_id] = event.target.value.split(':');
                patch(index, value => ({ ...value, stage: { ...value.stage, input: { ...('scope' in value.stage.input ? { scope: value.stage.input.scope, chains: value.stage.input.chains } : {}), ...(stage_id ? { stage_id, ...(invocation_id ? { invocation_id } : {}) } : { selected: true as const }) } } }));
            }}><option value="">{selectedBinding ? 'Selected results' : 'Original generated outputs'}</option>{stages.slice(0, index).flatMap((previous, pi) => [<option key={previous.stage.id} value={`${previous.stage.id}:`}>Stage {pi + 1} · {kinds[previous.stage.kind]} · all outputs{previous.enabled ? '' : ' (OFF)'}</option>, ...(previous.stage.kind === 'maturation' ? [] : previous.stage.models.map((call, ci) => <option key={`${previous.stage.id}:${call.invocation_id}`} value={`${previous.stage.id}:${call.invocation_id}`}>Stage {pi + 1} · {invocationLabel(call, ci)}</option>))])}</select></label>
            <p aria-label={`Stage ${index + 1} source`}>Source: {sourceLabel(item)}</p>
            {item.stage.kind === 'prediction' && <><label>Prediction scope<select aria-label={`Stage ${index + 1} scope`} value={item.stage.input.scope ?? 'binder'} onChange={event => patch(index, value => ({ ...value, stage: { ...value.stage, input: { ...value.stage.input, scope: event.target.value as 'binder' | 'whole_composition' } } }))}><option value="binder">Binder plus independent target</option><option value="whole_composition">Whole composition (blind)</option></select></label>
                <label>Selected chain IDs (empty = whole recipe)<input aria-label={`Stage ${index + 1} chains`} value={item.stage.input.chains?.join(',') ?? ''} onChange={event => patch(index, value => ({ ...value, stage: { ...value.stage, input: { ...value.stage.input, chains: event.target.value ? event.target.value.split(',').map(s => s.trim()) : undefined } } }))} /></label></>}
            {item.stage.kind !== 'maturation' && <>
                <div className="flex flex-wrap gap-2">{models[item.stage.kind].map(model => <button type="button" className={control} key={model} onClick={() => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, stage: { ...value.stage, models: [...value.stage.models, newInvocation(model, value.drafts[model])] } }))}>Add {invocationNames[model] ?? model}</button>)}</div>
                {item.stage.models.map((call, ci) => {
                    const id = call.invocation_id!;
                    const catalog = queries[ids.indexOf(call.model_id)]?.data;

                    const specialized = renderSettings?.(call, 'selected' in item.stage.input ? exactCandidates : [], params => callPatch(index, id, { params })) ?? (call.model_id === 'molecular_dynamics' ? <BinderMdInvocationEditor call={call}
                        nativeDraft={item.drafts[`${id}:md-active`] as unknown as MolecularDynamicsNativeDraft | undefined}
                        modeDrafts={Object.fromEntries(['guided', 'prepared', 'compiled'].map(kind => [kind, item.drafts[`${id}:md:${kind}`]])) as Partial<Record<MolecularDynamicsNativeDraft['input']['kind'], MolecularDynamicsNativeDraft>>}
                        onChange={(params, draft) => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value,
                            drafts: { ...value.drafts, [`${id}:md-active`]: draft },
                            stage: { ...value.stage, models: value.stage.models.map(other => other.invocation_id === id ? { ...other, params } : other) } }))}
                        onModeChange={(previous, next) => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value,
                            drafts: { ...value.drafts, [`${id}:md:${previous.input.kind}`]: previous, [`${id}:md-active`]: next },
                            stage: { ...value.stage, models: value.stage.models.map(other => other.invocation_id === id ? { ...other, params: { ...other.params, intent: serializeMolecularDynamicsNativeDraft(next) } } : other) } }))} /> : undefined);
                    const fields = catalog ? invocationSettingsFields(catalog, call.mode) : [];
                    return <section key={id} aria-label={`Invocation ${id}`} className="min-w-0 rounded bg-[var(--bg-secondary)] p-4 space-y-4">
                        <h4>{invocationLabel(call, ci)}</h4><details><summary>Call identity</summary><p>Stage: {item.stage.id}</p><p>Invocation: {id}</p></details>
                        <label><input type="checkbox" aria-label={`Enable invocation ${id}`} checked={!item.disabledInvocationIds?.includes(id)} onChange={event => patch(index, value => ({ ...value,
                            disabledInvocationIds: event.target.checked ? value.disabledInvocationIds?.filter(key => key !== id) : [...value.disabledInvocationIds ?? [], id] }))} />Include model</label>
                        {item.disabledInvocationIds?.includes(id) && <p>Off · settings kept</p>}
                        {call.model_id === 'boltz2' && <label>Execution route<select aria-label={`Route ${id}`} value={call.route ?? 'job'} onChange={event => callPatch(index, id, { route: event.target.value as 'job' | 'boltz_api' })}><option value="job">Ordinary Job</option><option value="boltz_api">Paid Boltz API (explicit estimate approval)</option></select></label>}
                        <label>Mode<select aria-label={`Mode ${id}`} value={call.mode} onChange={event => patch(index, value => changeInvocationMode(value, id, event.target.value))}>{(invocationModes[call.model_id] ?? catalog?.modes?.map(mode => mode.id) ?? []).map(mode => <option key={mode} value={mode}>{mode.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())}</option>)}</select></label>
                        {specialized && <button type="button" onClick={() => setEditingCall(id)}>{invocationNames[call.model_id] ?? call.model_id} settings</button>}
                        <details open={editingCall === id || undefined}><summary>Settings</summary>{specialized ? (editingCall === id ? <><button type="button" onClick={() => setEditingCall(undefined)}>Back to round</button>{specialized}</> : null) : <InvocationNativeSettings call={call} fields={fields} prediction={item.stage.kind === 'prediction'} onChange={params => callPatch(index, id, { params })} />}</details>
                        {queries[ids.indexOf(call.model_id)]?.isPending && <p role="status">Loading model-owned settings…</p>}
                        {queries[ids.indexOf(call.model_id)]?.error && <p role="status">Settings discovery failed. Saved settings remain intact. <button type="button" onClick={() => void queries[ids.indexOf(call.model_id)].refetch()}>Retry settings</button></p>}
                        {call.model_id === 'caliby_experimental' && <div aria-label={`Ordered ensembles ${id}`}>
                            {call.mode === 'sidechain_pack' && <p>Packing requires coordinates and does not change the sequence. Design constraints are kept for ensemble design.</p>}
                            <p>The first state is primary. Add states in native order; repeated selections remain explicit.</p>
                            {selectedBinding && 'selected' in item.stage.input ? <button type="button" onClick={() => callPatch(index, id, { input_groups: [...call.input_groups ?? [], { ensemble_id: crypto.randomUUID(), states: [] }] })}>Add ensemble</button> : <p>Each produced structure is bound independently by the native adapter. Future outputs are not pooled into an ensemble.</p>}
                            {(call.input_groups ?? []).map((group, gi) => <fieldset key={group.ensemble_id}><legend>Ensemble {gi + 1}</legend>
                                <select aria-label={`Add state ${gi + 1}`} value="" onChange={event => { if (event.target.value === '') return; const groups = structuredClone(call.input_groups!); groups[gi].states.push({ state_id: crypto.randomUUID(), candidate_ref: exactCandidates[Number(event.target.value)] }); callPatch(index, id, { input_groups: groups }); }}><option value="">Choose exact state</option>{exactCandidates.map((ref, ri) => <option key={candidateKey(ref)} value={ri}>{ri + 1}. {invocationCandidateLabel(ref, candidateLabels?.[candidateKey(ref)])}</option>)}</select>
                                {group.states.map((state, si) => <div key={state.state_id}>{si === 0 ? 'Primary' : `State ${si + 1}`} · {invocationCandidateLabel(state.candidate_ref, candidateLabels?.[candidateKey(state.candidate_ref)])}<details><summary>Exact state identity</summary><code>{candidateKey(state.candidate_ref)}</code></details>
                                    <button type="button" disabled={si === 0} onClick={() => { const groups = structuredClone(call.input_groups!); [groups[gi].states[si - 1], groups[gi].states[si]] = [groups[gi].states[si], groups[gi].states[si - 1]]; callPatch(index, id, { input_groups: groups }); }}>Move state up</button>
                                    <button type="button" onClick={() => { const groups = structuredClone(call.input_groups!); groups[gi].states.splice(si, 1); callPatch(index, id, { input_groups: groups }); }}>Remove state</button>
                                    {call.mode === 'ensemble_design' && (['fixed_pos_seq', 'fixed_pos_scn', 'fixed_pos_override_seq', 'pos_restrict_aatype', 'symmetry_pos'] as const).map(key => <label key={key}>{key}<input aria-label={`${state.state_id} ${key}`} value={state.settings?.[key] ?? ''} onChange={event => { const groups = structuredClone(call.input_groups!); groups[gi].states[si].settings = { ...state.settings, [key]: event.target.value }; callPatch(index, id, { input_groups: groups }); }} /></label>)}
                                </div>)}<button type="button" onClick={() => callPatch(index, id, { input_groups: call.input_groups!.filter((_, i) => i !== gi) })}>Remove ensemble</button>
                            </fieldset>)}
                        </div>}
                        <div className="space-y-2" aria-label={`Run location ${id}`}><h5>Run location</h5>
                        <label><input type="checkbox" checked={Object.hasOwn(call, 'execution_target_id')} onChange={event => {
                            const next = { ...call }; if (event.target.checked) next.execution_target_id = null; else delete next.execution_target_id;
                            patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, stage: { ...value.stage, models: value.stage.models.map(other => other.invocation_id === id ? next : other) } }));
                        }} />Choose run location</label>
                        <p>{invocationPlacementLabel(call)}</p>
                        {Object.hasOwn(call, 'execution_target_id') && <ExecutionTargetPicker value={call.execution_target_id ?? null} onChange={execution_target_id => callPatch(index, id, { execution_target_id })} />}
                        </div>
                        <button type="button" aria-label={`Remove ${invocationLabel(call, ci)}`} onClick={() => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, drafts: { ...value.drafts, [call.model_id]: call.params }, stage: { ...value.stage, models: value.stage.models.filter(other => other.invocation_id !== id) } }))}>Remove call</button>
                    </section>;
                })}
            </>}
        </fieldset>)}
    </div>;
}
