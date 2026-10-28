import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeNumber, nativeRecord, patchNative } from './Rfd4TaskWorkspace';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';

const meanings: Record<string, string> = {
    'single-pass': 'Generate once for the batch. Final reward computation remains independent.',
    'best-of-n': 'Return ALL replicas as final samples; native filtering/ranking is a separate later stage, not an internal winner selection.',
    'beam-search': 'Complete lookaheads and retain top-k rewards per sample at checkpoints.',
    'fk-steering': 'Draw branches with replacement using softmax(reward / temperature), not top-k selection.',
    mcts: 'Explore or exploit UCB during rollouts; advance the root to the highest average-reward child.',
};
const groups: Record<string, string> = { 'best-of-n': 'best_of_n', 'beam-search': 'beam_search', 'fk-steering': 'fk_steering', mcts: 'mcts' };
const labels: Record<string, string> = { replicas: 'Replicas (all returned)', n_branch: 'Branches', beam_width: 'Beam width', temperature: 'Reward temperature', n_simulations: 'Simulations per checkpoint', exploration_prob: 'Exploration probability', exploration_constant: 'UCB exploration constant', max_batch_size: 'Maximum search batch', reward_threshold: 'Retained lookahead reward threshold' };
function resolve(schema: Schema | undefined, root: Schema): Schema {
    if (!schema) return {};
    if (schema.$ref?.startsWith('#/$defs/')) return nativeRecord(root).$defs?.[schema.$ref.slice(8)] ?? schema;
    const object = schema.anyOf?.find(s => s.type !== 'null');
    return object ? resolve(object, root) : schema;
}
/** Discovery supplies enums/defaults. Reading a section never authors Search. */
export function ComplexaSearchControls({ request, schema, onChange, controlDraft, onControlDraft }: {
    request: Record<string, any>; schema: Schema; onChange: (r: Record<string, any>) => void;
    controlDraft: Record<string, any>; onControlDraft: (v: Record<string, any>) => void;
}) {
    const search = nativeRecord(request.search);
    const algorithm = search.algorithm;
    const searchSchema = resolve(schema.properties?.search, schema);
    const group = groups[algorithm];
    const branch = nativeRecord(search[group]);
    const branchSchema = resolve(searchSchema.properties?.[group], schema);
    const patch = (key: string, value: unknown) => onChange({ ...request, search: patchNative(search, key, value) });
    const patchBranch = (key: string, value: unknown) => patch(group, patchNative(branch, key, value));
    const quantity = (key: string, nested = false) => {
        const values = nested ? branch : search;
        const spec = (nested ? branchSchema : searchSchema).properties?.[key];
        const inherited = values[key] === undefined;
        return <div key={key}><NativeNumber label={`Complexa search ${labels[key] ?? key}`} value={inherited ? spec?.default : values[key]} onChange={v => (nested ? patchBranch : patch)(key, v)} />
            <small>{inherited ? spec && Object.hasOwn(spec, 'default') ? 'Native constructor default (task composition may override)' : 'Inherited from selected task; unresolved here' : 'Custom'}</small>
            <button type="button" onClick={() => (nested ? patchBranch : patch)(key, undefined)}>Use default {key}</button></div>;
    };
    return <fieldset className="space-y-3"><legend>Native search strategy</legend>
        <label>Strategy<select aria-label="Complexa search strategy" value={algorithm ?? ''} onChange={e => {
            if (!e.target.value) { onControlDraft({ ...controlDraft, configured_search: request.search }); onChange(patchNative(request, 'search', undefined)); }
            else onChange({ ...request, search: { ...nativeRecord(controlDraft.configured_search), ...search, algorithm: e.target.value } });
        }}><option value="">Inherit selected task profile</option>{(searchSchema.properties?.algorithm?.enum ?? []).map(a => <option key={String(a)} value={String(a)}>{String(a)}</option>)}{algorithm && !(searchSchema.properties?.algorithm?.enum ?? []).includes(algorithm) && <option value={algorithm}>{algorithm} (retained)</option>}</select></label>
        <p>{algorithm ? meanings[algorithm] : 'Search remains owned by the selected native task profile. Viewing this section does not activate an override.'}</p>
        {algorithm && <>
            {Object.keys(branchSchema.properties ?? {}).filter(k => !['keep_lookahead_samples', 'save_intermediate_states'].includes(k)).map(k => quantity(k, true))}
            <NativeSettingsDisclosure summary={<span>Search checkpoints and retention</span>}>{() => <>
                {['max_batch_size', 'reward_threshold'].map(k => quantity(k))}
                <p>The lookahead threshold only controls saved lookaheads, not the later filtering reward threshold. Checkpoint end must match native integration steps; native constraints/errors remain authoritative.</p>
                <fieldset><legend>Step checkpoints</legend>{(search.step_checkpoints ?? searchSchema.properties?.step_checkpoints?.default ?? []).map((v: number, i: number) => <div key={i}><NativeNumber label={`Search checkpoint ${i + 1}`} value={v} onChange={next => patch('step_checkpoints', (search.step_checkpoints ?? searchSchema.properties?.step_checkpoints?.default ?? []).map((n: number, j: number) => i === j ? next : n))} /><button type="button" onClick={() => patch('step_checkpoints', (search.step_checkpoints ?? searchSchema.properties?.step_checkpoints?.default ?? []).filter((_: number, j: number) => i !== j))}>Remove checkpoint {i + 1}</button></div>)}
                    <button type="button" onClick={() => patch('step_checkpoints', [...(search.step_checkpoints ?? searchSchema.properties?.step_checkpoints?.default ?? []), ''])}>Add search checkpoint</button>
                    <button type="button" onClick={() => patch('step_checkpoints', undefined)}>Inherit checkpoints</button></fieldset>
                {['keep_lookahead_samples', 'save_intermediate_states'].filter(k => branchSchema.properties?.[k]).map(k => <label key={k}>{k.replaceAll('_', ' ')}<select aria-label={`Complexa search ${k}`} value={branch[k] === undefined ? '' : String(branch[k])} onChange={e => patchBranch(k, e.target.value === '' ? undefined : e.target.value === 'true')}><option value="">Inherit ({String(branchSchema.properties?.[k]?.default ?? 'unresolved')})</option><option value="true">On</option><option value="false">Off</option></select></label>)}
            </>}</NativeSettingsDisclosure>
        </>}
    </fieldset>;
}
