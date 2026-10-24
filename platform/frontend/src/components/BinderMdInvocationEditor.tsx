import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { SelectedRoundModel } from '../lib/binderSelectedRound';
import { MolecularDynamicsNativeSettings } from './MolecularDynamicsNativeSettings';
import { hydrateMolecularDynamicsNative, serializeMolecularDynamicsNativeDraft, parseMolecularDynamicsChemistryProfileInventory, type MolecularDynamicsNativeDraft } from './molecularDynamicsUiState';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';

/** Draft-only adapter: no Job, preview, source acquisition or placement mutation. */
export function BinderMdInvocationEditor({ call, onChange, nativeDraft, modeDrafts, onModeChange }: {
    call: SelectedRoundModel; onChange: (params: SelectedRoundModel['params'], draft: MolecularDynamicsNativeDraft) => void;
    nativeDraft?: MolecularDynamicsNativeDraft;
    modeDrafts: Partial<Record<MolecularDynamicsNativeDraft['input']['kind'], MolecularDynamicsNativeDraft>>;
    onModeChange: (previous: MolecularDynamicsNativeDraft, next: MolecularDynamicsNativeDraft) => void;
}) {
    const draft = nativeDraft ?? hydrateMolecularDynamicsNative(call.params) ?? {
        schema_version: 'bms.md.launch-intent.v2', name: '', input: { kind: 'guided' },
        stages: [], replicas: '', random_seed: '', execution: { ntmpi: 1, ntomp: '', gpu_offload: 'auto', pin: 'on' },
    } satisfies MolecularDynamicsNativeDraft;
    const catalog = useQuery({ queryKey: ['molecular-dynamics', 'chemistry-profiles'], retry: false,
        queryFn: async () => parseMolecularDynamicsChemistryProfileInventory((await api.get('/api/molecular-dynamics/chemistry-profiles')).data) });
    // The same native draft serializer preserves explicit blanks, zero and unknown MDP keys.
    // Only this invocation's intent is returned; outer source selection and placement stay with the round.
    const change = (value: MolecularDynamicsNativeDraft) => onChange({ ...call.params, intent: serializeMolecularDynamicsNativeDraft(value) }, value);
    return <section aria-label={`MD invocation draft ${call.invocation_id}`}>
        <p>Invocation-local native MD draft. Guided input is bound by the round owner to the exact selected structure. Placement remains with this invocation / round. This editor does not launch a Job.</p>
        <label>Name<input aria-label="MD invocation name" value={draft.name} onChange={e => change({ ...draft, name: e.target.value })} /></label>
        <label>Input kind<select aria-label="MD invocation input kind" value={draft.input.kind} onChange={e => {
            const kind = e.target.value as MolecularDynamicsNativeDraft['input']['kind'];
            const next = modeDrafts[kind] ?? { ...draft, input: { kind }, stages: kind === 'compiled' ? [] : draft.stages };
            onModeChange(draft, next);
        }}><option value="guided">Guided preparation</option><option value="prepared">Prepared system</option><option value="compiled">Compiled TPR / CPT</option></select></label>
        {draft.input.kind === 'guided' && <><label>Chemistry profile<select aria-label="MD invocation chemistry profile" value={draft.input.chemistry_profile_id ?? ''} onChange={e => {
            const profile = catalog.data?.profiles.find(p => p.id === e.target.value);
            change({ ...draft, input: { ...draft.input, chemistry_profile_id: profile?.id, chemistry_profile_sha256: profile?.profile_sha256, catalog_digest: catalog.data?.catalog_digest } });
        }}><option value="">Choose chemistry profile</option>{draft.input.chemistry_profile_id && !catalog.data?.profiles.some(p => p.id === draft.input.chemistry_profile_id) && <option value={draft.input.chemistry_profile_id}>{draft.input.chemistry_profile_id} (retained)</option>}{catalog.data?.profiles.map(p => <option key={p.id} value={p.id}>{p.id}</option>)}</select></label>{catalog.isError && <p role="status">Chemistry inventory unavailable; retained chemistry is unchanged.</p>}</>}
        <MolecularDynamicsNativeSettings value={draft} onChange={change} />
        <details><summary>Invocation native intent readback</summary><BindCraft2SettingsReadback value={call.params.intent ?? draft} /></details>
    </section>;
}
