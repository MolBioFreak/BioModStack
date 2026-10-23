import { useState, type ReactNode } from 'react';
import { useQueries } from '@tanstack/react-query';
import { fetchModelById } from '../lib/api';
import { NativeSetting } from './NativeBinderGeneration';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { roundCatalogFields } from '../lib/binderRoundControls';
import { blindFixedParameters, roundParameterIsBound, type BinderRoundCatalog } from '../lib/binderRound';
import { invocationDrafts, invocationModes, newInvocation, type InvocationDraftStage } from '../lib/binderInvocationPlan';
import { candidateKey, type CandidateRef, type SelectedRoundModel, type SelectedRoundStage } from '../lib/binderSelectedRound';

const control = 'rounded border p-2 bg-[var(--bg-primary)] max-w-full';
const kinds = { sequence_design: 'Sequence design', prediction: 'Blind prediction', maturation: 'Maturation', md: 'Molecular dynamics' };
const models = { sequence_design: ['proteinmpnn', 'fampnn', 'caliby_binder', 'caliby_experimental', 'ligandmpnn', 'protonpottsmpnn'], prediction: ['protenix', 'boltz2', 'esmfold2', 'boltz_cp_experimental'], md: ['molecular_dynamics'] };

function NativeMdIntent({ call, onChange }: { call: SelectedRoundModel; onChange: (params: SelectedRoundModel['params']) => void }) {
    const [text, setText] = useState(() => JSON.stringify(call.params.intent ?? {}, null, 2));
    const [error, setError] = useState('');
    return <div><p>Use the existing MD editor’s native launch intent. Guided input binds this exact selected structure; prepared/compiled system inputs stay explicit. No prediction is required. Chemistry, stages and MDP settings remain owned by MD.</p>
        <label>Native MD launch intent<textarea aria-label={`Native MD intent ${call.invocation_id}`} value={text} onChange={event => setText(event.target.value)} /></label>
        <button type="button" onClick={() => { try { const intent = JSON.parse(text); onChange({ ...call.params, intent }); setError(''); } catch { setError('Native MD intent must be JSON.'); } }}>Apply native MD intent</button>
        {error && <p role="alert">{error}</p>}
    </div>;
}

/** One intent editor for initial and selected operations. No lifecycle lives here. */
export function BinderInvocationPlan({ stages: original, onChange, candidates = [], showAdd = true, renderSettings }: {
    stages: InvocationDraftStage[]; onChange: (value: InvocationDraftStage[]) => void; candidates?: CandidateRef[]; showAdd?: boolean;
    renderSettings?: (call: SelectedRoundModel, refs: CandidateRef[], change: (params: SelectedRoundModel['params']) => void) => ReactNode;
}) {
    const [editingCall, setEditingCall] = useState<string>();
    const stages = invocationDrafts(original);
    const ids = [...new Set(stages.flatMap(item => item.stage.kind === 'maturation' ? [] : item.stage.models.map(call => call.model_id)))];
    const queries = useQueries({ queries: ids.map(id => ({ queryKey: ['binder-round-catalog', id],
        queryFn: async (): Promise<BinderRoundCatalog> => ({ ...(await fetchModelById(id)).data, id }), staleTime: 60_000, retry: false })) });
    const patch = (index: number, update: (value: InvocationDraftStage) => InvocationDraftStage) => onChange(stages.map((value, i) => i === index ? update(value) : value));
    const callPatch = (index: number, id: string, update: Partial<SelectedRoundModel>) => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, stage: { ...value.stage, models: value.stage.models.map(call => call.invocation_id === id ? { ...call, ...update } : call) } }));
    const add = (kind: SelectedRoundStage['kind']) => {
        const id = crypto.randomUUID();
        const input = { selected: true as const, scope: 'whole_composition' as const };
        onChange([...stages, { enabled: true, drafts: {}, stage: kind === 'maturation'
            ? { id, kind, input, model_id: 'binder_refinement', mode: 'refine', params: {} }
            : { id, kind, input, models: [] } }]);
    };
    return <div aria-label="Invocation plan" className="space-y-4">
        <p>Each call has its own identity, native mode and placement. OFF rows retain drafts and emit no operation. Packing requires actual coordinates and never changes the sequence.</p>
        {showAdd && <div>{(['sequence_design', 'prediction', 'md'] as const).map(kind => <button className={control} type="button" key={kind} onClick={() => add(kind)}>Add {kinds[kind]}</button>)}</div>}
        {stages.map((item, index) => <fieldset key={item.stage.id} className="border rounded p-3 space-y-3"><legend>{index + 1}. {kinds[item.stage.kind]}</legend>
            <label><input type="checkbox" checked={item.enabled} onChange={event => patch(index, value => ({ ...value, enabled: event.target.checked }))} />Include stage {index + 1}</label>
            <label>Input<select aria-label={`Stage ${index + 1} input`} className={control} value={'stage_id' in item.stage.input ? `${item.stage.input.stage_id}:${item.stage.input.invocation_id ?? ''}` : ''} onChange={event => {
                const [stage_id, invocation_id] = event.target.value.split(':');
                patch(index, value => ({ ...value, stage: { ...value.stage, input: { ...('scope' in value.stage.input ? { scope: value.stage.input.scope, chains: value.stage.input.chains } : {}), ...(stage_id ? { stage_id, ...(invocation_id ? { invocation_id } : {}) } : { selected: true as const }) } } }));
            }}><option value="">Selected candidates / initial outputs</option>{stages.slice(0, index).flatMap(previous => [<option key={previous.stage.id} value={`${previous.stage.id}:`}>{previous.stage.id} · all outputs</option>, ...(previous.stage.kind === 'maturation' ? [] : previous.stage.models.map(call => <option key={`${previous.stage.id}:${call.invocation_id}`} value={`${previous.stage.id}:${call.invocation_id}`}>{call.model_id} · {call.invocation_id}</option>))])}</select></label>
            {item.stage.kind === 'prediction' && <><label>Prediction scope<select aria-label={`Stage ${index + 1} scope`} value={item.stage.input.scope ?? 'binder'} onChange={event => patch(index, value => ({ ...value, stage: { ...value.stage, input: { ...value.stage.input, scope: event.target.value as 'binder' | 'whole_composition' } } }))}><option value="binder">Binder plus independent target</option><option value="whole_composition">Whole composition (blind)</option></select></label>
                <label>Selected chain IDs (empty = whole recipe)<input aria-label={`Stage ${index + 1} chains`} value={item.stage.input.chains?.join(',') ?? ''} onChange={event => patch(index, value => ({ ...value, stage: { ...value.stage, input: { ...value.stage.input, chains: event.target.value ? event.target.value.split(',').map(s => s.trim()) : undefined } } }))} /></label></>}
            {item.stage.kind !== 'maturation' && <>
                <div>{models[item.stage.kind].map(model => <button type="button" className={control} key={model} onClick={() => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, stage: { ...value.stage, models: [...value.stage.models, newInvocation(model, value.drafts[model])] } }))}>Add {model} call</button>)}</div>
                {item.stage.models.map(call => {
                    const id = call.invocation_id!;
                    const catalog = queries[ids.indexOf(call.model_id)]?.data;
                    const modeFields = catalog?.modes?.find(mode => mode.id === call.mode)?.params;
                    const specialized = renderSettings?.(call, 'selected' in item.stage.input ? candidates : [], params => callPatch(index, id, { params })) ?? (call.model_id === 'molecular_dynamics' ? <NativeMdIntent call={call} onChange={params => callPatch(index, id, { params })} /> : undefined);
                    const fields = catalog ? roundCatalogFields(catalog).filter(field => (!modeFields || modeFields.includes(field.name)) && (!roundParameterIsBound(field.name) || ['design_chain', 'target_chain', 'binder_chains', 'target_chains'].includes(field.name)) && !['ensembles', 'structures', 'input_json', 'caliby_request_dir', 'input_path'].includes(field.name)) : [];
                    return <section key={id} aria-label={`Invocation ${id}`} className="border p-3 space-y-2">
                        <h4>{call.model_id} · {id}</h4>
                        <label><input type="checkbox" aria-label={`Enable invocation ${id}`} checked={!item.disabledInvocationIds?.includes(id)} onChange={event => patch(index, value => ({ ...value,
                            disabledInvocationIds: event.target.checked ? value.disabledInvocationIds?.filter(key => key !== id) : [...value.disabledInvocationIds ?? [], id] }))} />Include this call (OFF retains settings)</label>
                        {call.model_id === 'boltz2' && <label>Execution route<select aria-label={`Route ${id}`} value={call.route ?? 'job'} onChange={event => callPatch(index, id, { route: event.target.value as 'job' | 'boltz_api' })}><option value="job">Ordinary Job</option><option value="boltz_api">Paid Boltz API (explicit estimate approval)</option></select></label>}
                        <label>Native mode<select aria-label={`Mode ${id}`} value={call.mode} onChange={event => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, drafts: { ...value.drafts, [`${id}:${call.mode}`]: call.params }, stage: { ...value.stage, models: value.stage.models.map(other => other.invocation_id === id ? { ...other, mode: event.target.value, params: value.drafts[`${id}:${event.target.value}`] ?? {} } : other) } }))}>{(invocationModes[call.model_id] ?? catalog?.modes?.map(mode => mode.id) ?? []).map(mode => <option key={mode}>{mode}</option>)}</select></label>
                        <button type="button" onClick={() => patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, drafts: { ...value.drafts, [call.model_id]: call.params }, stage: { ...value.stage, models: value.stage.models.filter(other => other.invocation_id !== id) } }))}>Remove call</button>
                        <label><input type="checkbox" checked={Object.hasOwn(call, 'execution_target_id')} onChange={event => {
                            const next = { ...call }; if (event.target.checked) next.execution_target_id = null; else delete next.execution_target_id;
                            patch(index, value => value.stage.kind === 'maturation' ? value : ({ ...value, stage: { ...value.stage, models: value.stage.models.map(other => other.invocation_id === id ? next : other) } }));
                        }} />Explicit placement (otherwise use round placement)</label>
                        {Object.hasOwn(call, 'execution_target_id') && <ExecutionTargetPicker value={call.execution_target_id ?? null} onChange={execution_target_id => callPatch(index, id, { execution_target_id })} />}
                        {specialized && <button type="button" onClick={() => setEditingCall(id)}>{({ fampnn: 'FA-MPNN', proteinmpnn: 'ProteinMPNN', caliby_binder: 'Caliby', protenix: 'Protenix', boltz2: 'Boltz-2' } as Record<string, string>)[call.model_id] ?? call.model_id} settings</button>}
                        <details open={editingCall === id || undefined}><summary>Native settings</summary>{specialized ? (editingCall === id ? <><button type="button" onClick={() => setEditingCall(undefined)}>Back to round</button>{specialized}</> : null) : fields.map(field => <NativeSetting key={field.name} parameter={field} values={{ ...Object.fromEntries(fields.filter(f => Object.hasOwn(f, 'default')).map(f => [f.name, f.default])), ...call.params }} onPatch={values => callPatch(index, id, { params: { ...call.params, ...values, ...(item.stage.kind === 'prediction' ? Object.fromEntries(fields.filter(field => blindFixedParameters.has(field.name)).map(field => [field.name, false])) : {}) } })} chains={[]} />)}</details>
                        {call.model_id === 'caliby_experimental' && <div aria-label={`Ordered ensembles ${id}`}>
                            <p>The first state is primary. Add states in native order; repeated selections remain explicit.</p>
                            <button type="button" onClick={() => callPatch(index, id, { input_groups: [...call.input_groups ?? [], { ensemble_id: crypto.randomUUID(), states: [] }] })}>Add ensemble</button>
                            {(call.input_groups ?? []).map((group, gi) => <fieldset key={group.ensemble_id}><legend>{group.ensemble_id}</legend>
                                <select aria-label={`Add state ${gi + 1}`} value="" onChange={event => { if (event.target.value === '') return; const groups = structuredClone(call.input_groups!); groups[gi].states.push({ state_id: crypto.randomUUID(), candidate_ref: candidates[Number(event.target.value)] }); callPatch(index, id, { input_groups: groups }); }}><option value="">Choose exact state</option>{candidates.map((ref, ri) => <option key={candidateKey(ref)} value={ri}>{candidateKey(ref)}</option>)}</select>
                                {group.states.map((state, si) => <div key={state.state_id}>{si === 0 ? 'Primary' : `State ${si + 1}`} · {candidateKey(state.candidate_ref)}
                                    <button type="button" disabled={si === 0} onClick={() => { const groups = structuredClone(call.input_groups!); [groups[gi].states[si - 1], groups[gi].states[si]] = [groups[gi].states[si], groups[gi].states[si - 1]]; callPatch(index, id, { input_groups: groups }); }}>Move state up</button>
                                    <button type="button" onClick={() => { const groups = structuredClone(call.input_groups!); groups[gi].states.splice(si, 1); callPatch(index, id, { input_groups: groups }); }}>Remove state</button>
                                    {(['fixed_pos_seq', 'fixed_pos_scn', 'fixed_pos_override_seq', 'pos_restrict_aatype', 'symmetry_pos'] as const).map(key => <label key={key}>{key}<input aria-label={`${state.state_id} ${key}`} value={state.settings?.[key] ?? ''} onChange={event => { const groups = structuredClone(call.input_groups!); groups[gi].states[si].settings = { ...state.settings, [key]: event.target.value }; callPatch(index, id, { input_groups: groups }); }} /></label>)}
                                </div>)}<button type="button" onClick={() => callPatch(index, id, { input_groups: call.input_groups!.filter((_, i) => i !== gi) })}>Remove ensemble</button>
                            </fieldset>)}
                        </div>}
                    </section>;
                })}
            </>}
        </fieldset>)}
    </div>;
}
