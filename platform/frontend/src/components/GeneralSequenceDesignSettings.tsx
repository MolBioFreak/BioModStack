import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { NativeSetting } from './NativeBinderGeneration';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import { ProteinDesignPanel } from './ProteinDesignWorkflow';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import { changeGeneralSequenceDesigner, fetchGeneralSequenceInventory, generalSequenceDesigners, generalSequenceNames,
    withGeneralSequenceInventory, type GeneralSequenceDesignDraft, type GeneralSequenceDesigner, type GeneralSequenceParameter } from '../lib/generalSequenceDesign';

// Presentation only: native names, types, defaults and applicability stay in discovery.
const isCount = (name: string) => ['seqs_per_design', 'num_seqs_per_pdb'].includes(name);
const isPrimary = (name: string) => /^(mpnn_temperature|fampnn_temperature|temperature)$/.test(name);
function advancedGroup({ name }: GeneralSequenceParameter): string {
    if (/extra_config/.test(name)) return 'Native overrides';
    if (/checkpoint|model_name/.test(name)) return 'Model and checkpoint';
    if (/mutation/.test(name)) return 'Mutation analysis';
    if (/gaussian/.test(name)) return 'Conformer perturbations';
    if (/potts/.test(name)) return 'Potts sampling';
    if (/scn_|repack|psce|sidechain/.test(name)) return 'Side-chain sampling';
    if (/relax|intermediate/.test(name)) return 'Relaxation';
    if (/timestep|num_steps|backbone_noise/.test(name)) return 'Denoising and backbone noise';
    if (/position|pos_|symmetry|omit|bias|exclude|primary_res|mismatch/.test(name)) return 'Sequence constraints';
    if (/worker|batch|verbose|seed|presort/.test(name)) return 'Execution and reproducibility';
    return 'Additional native settings';
}
function presentation(parameter: GeneralSequenceParameter): GeneralSequenceParameter {
    const result = { ...parameter, label: isCount(parameter.name) ? 'Sequences per backbone'
        : parameter.label ?? parameter.name.replace(/^(fampnn|mpnn)_/, '').replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase()) };
    // Discovery serializes absent bounds/defaults as null. Do not display a
    // fictitious unbounded range or repeat null as if it were a recommendation.
    // This changes metadata presentation only, never retained or submitted values.
    if (result.minimum == null) delete result.minimum;
    if (result.maximum == null) delete result.maximum;
    if (result.default === null) delete result.default;
    return result;
}

/** Optional source-bound composition; all numerical settings remain model-owned. */
export function GeneralSequenceDesignSettings({ value, onChange }: {
    value: GeneralSequenceDesignDraft;
    onChange: (value: GeneralSequenceDesignDraft) => void;
}) {
    const model = value.sequence_design.model_id;
    const query = useQuery({ queryKey: ['general-sequence-settings', model], queryFn: () => fetchGeneralSequenceInventory(model), staleTime: 60_000, retry: false });
    const draft = query.data ? withGeneralSequenceInventory(value, query.data) : value;
    const signature = JSON.stringify(draft);
    const original = JSON.stringify(value);
    useEffect(() => { if (signature !== original) onChange(JSON.parse(signature)); }, [signature, original, onChange]);
    const request = draft.sequence_design;
    const patch = (params: Record<string, UntypedApiValue>) => onChange(changeGeneralSequenceDesigner(draft, model, { params }));
    const render = (parameter: GeneralSequenceParameter, input = false) => {
        const values = input ? request.input_settings ?? {} : request.params;
        const update = (values: Record<string, UntypedApiValue>) => input
            ? onChange(changeGeneralSequenceDesigner(draft, model, { input_settings: values })) : patch(values);
        if (parameter.schema && ['array', 'object'].includes(parameter.type)) return <BioXpSchemaInput label={parameter.name}
            schema={parameter.schema} value={values[parameter.name]} onChange={next => update({ [parameter.name]: next })}
            fallback={() => <p>Native schema does not advertise a typed editor for this value. Saved value is retained.</p>} />;
        return <NativeSetting parameter={presentation(parameter)} values={values} onPatch={update} chains={[]} />;
    };
    const parameters = query.data?.parameters ?? [];
    const primary = () => <div className="grid gap-4 md:grid-cols-2" data-general-sequence-primary>{parameters.filter(parameter => isPrimary(parameter.name)).map(parameter =>
        <div key={parameter.name} data-sequence-designer-field={parameter.name}>{render(parameter)}</div>)}</div>;
    const advanced = () => <div className="space-y-3" data-general-sequence-advanced>
        <SequenceDesignerSettings key={model} fields={parameters.filter(parameter => !isCount(parameter.name) && !isPrimary(parameter.name))}
            groupForField={advancedGroup} initiallyOpenGroups={[]} renderField={parameter => render(parameter)} />
        {!!query.data?.inputParameters.length && <details className="rounded-lg border border-[var(--border-primary)] p-3"><summary>Generated-state constraints</summary>
            <p className="my-2 text-sm text-[var(--text-secondary)]">Native author-residue strings are passed unchanged to Caliby.</p>
            <div className="grid gap-3 md:grid-cols-2">{query.data.inputParameters.map(parameter => <div key={parameter.name} data-general-input-setting={parameter.name}>{render(parameter, true)}</div>)}</div>
        </details>}
    </div>;
    return <ProteinDesignPanel title="Sequence Design"><section aria-label="General generation sequence design" className="space-y-4">
        <label className="flex items-center gap-2"><input aria-label="Enable sequence design" type="checkbox" checked={request.enabled}
            onChange={event => onChange({ ...draft, sequence_design: { ...request, enabled: event.target.checked } })} />Design sequences for generated candidates</label>
        <p className="text-sm text-[var(--text-secondary)]">{request.enabled ? 'Design sequences for each generated candidate. Structure prediction and sequence validation are separate operations.' : 'Off · generation only. Designer choices and settings below are retained for later.'}</p>
        <div className="grid gap-4 md:grid-cols-2">
            <label className="block space-y-2 text-sm font-medium">Sequence designer<select aria-label="Sequence designer" className="block w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2" value={model}
                onChange={event => onChange(changeGeneralSequenceDesigner(draft, event.target.value as GeneralSequenceDesigner))}>
                {generalSequenceDesigners.map(id => <option key={id} value={id}>{generalSequenceNames[id]}</option>)}
            </select></label>
            {parameters.filter(parameter => isCount(parameter.name)).map(parameter => <div key={parameter.name} data-sequence-designer-field={parameter.name}>{render(parameter)}</div>)}
        </div>
        <p className="text-sm text-[var(--text-secondary)]">Structures come from generated candidates; no additional structure input is needed.</p>
        {model === 'caliby_experimental' && <p className="text-sm text-[var(--text-secondary)]">Each generated structure is one single-state Caliby ensemble, not a multi-state conditioning set.</p>}
        {query.isPending && <p role="status">Loading model-owned settings…</p>}
        {query.error && <p role="status">{query.error.message} Saved settings remain intact.</p>}
        {query.data && (request.enabled ? <>
            {primary()}
            <div className="space-y-3"><h4 className="text-sm font-medium">Advanced native settings</h4>{advanced()}</div>
        </> : <NativeSettingsDisclosure className="rounded-lg border border-[var(--border-primary)] p-3" data-general-retained-settings summary={<span className="cursor-pointer text-sm font-medium">Retained native settings</span>}>
            {() => <div className="mt-4 space-y-4">{primary()}{advanced()}</div>}
        </NativeSettingsDisclosure>)}
    </section></ProteinDesignPanel>;
}
