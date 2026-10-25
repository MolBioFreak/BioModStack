import { useState } from 'react';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeNumber, nativeRecord, patchNative } from './Rfd4TaskWorkspace';

/** Named model-specific expert sections reuse native typed leaves, not a second schema/composer. */
export function Rfd4ExpertSettings({ request, schema, onChange }: { request: Record<string, any>; schema: Schema; onChange: (v: Record<string, any>) => void }) {
    const [step, setStep] = useState('');
    const settings = nativeRecord(request.settings), settingsSchema = schema.properties?.settings;
    const generation = nativeRecord(settings.generation), generationSchema = settingsSchema?.properties?.generation;
    const search = nativeRecord(settings.search), searchSchema = settingsSchema?.properties?.search;
    const patchSettings = (key: string, value: unknown) => onChange({ ...request, settings: patchNative(settings, key, value) });
    const leaf = (label: string, child: Schema | undefined, value: unknown, change: (v: unknown) => void, path: (string | number)[]) => child ? <BioXpSchemaInput key={label} label={label} nativeLabels schema={child} rootSchema={schema} path={path} value={value} onChange={change} fallback={() => <p>Retained value is outside this discovered schema; native compatibility remains available.</p>} /> : null;
    const section = (title: string, content: () => React.ReactNode) => <NativeSettingsDisclosure key={title} summary={<span>{title}</span>}>{content}</NativeSettingsDisclosure>;
    const cpSchema = searchSchema?.properties?.checkpoints?.anyOf?.find(s => s.type === 'object')?.additionalProperties as Schema | undefined;
    const knownSettings = new Set(['num_replicates', 'batch_size', 'seed', 'generation', 'search']);
    return <div className="space-y-3" data-rfd4-expert>
        <p>Only requested overrides are sent. A displayed profile declaration is not a resolved effective configuration. Preview provides model-owned request/dependency readback; no browser Hydra composition occurs.</p>
        {section('Profiles and recycling', () => <>
            {leaf('Native profiles', schema.properties?.groups, request.groups, v => onChange(patchNative(request, 'groups', v)), ['groups'])}
            {Object.entries(generationSchema?.properties ?? {}).filter(([key]) => !['args', 'model', 'dataset'].includes(key)).map(([key, child]) => leaf(key, child, generation[key], v => patchSettings('generation', patchNative(generation, key, v)), ['settings', 'generation', key]))}
        </>)}
        {section('Integration and partial / inverse sampling', () => <>{leaf('Integration arguments', generationSchema?.properties?.args, generation.args, v => patchSettings('generation', patchNative(generation, 'args', v)), ['settings', 'generation', 'args'])}</>)}
        {['bb_ca', 'local_latents'].map(modality => section(modality === 'bb_ca' ? 'Backbone schedule and simulation' : 'Sequence latent schedule and simulation', () => <>
            {leaf(modality, generationSchema?.properties?.model?.properties?.[modality], generation.model?.[modality], v => patchSettings('generation', { ...generation, model: patchNative(nativeRecord(generation.model), modality, v) }), ['settings', 'generation', 'model', modality])}
        </>))}
        {section('Search checkpoint timeline', () => <>
            <p>Steps are native integration-step keys. Clean-prediction ranking is not a completed rollout. Opening this panel does not enable search.</p>
            {Object.entries(nativeRecord(search.checkpoints)).map(([checkpoint, value]) => <fieldset key={checkpoint}><legend>Step {checkpoint}</legend>
                {leaf(`Checkpoint ${checkpoint}`, cpSchema, value, v => patchSettings('search', { ...search, checkpoints: patchNative(nativeRecord(search.checkpoints), checkpoint, v) }), ['settings', 'search', 'checkpoints', checkpoint])}
                <button type="button" onClick={() => patchSettings('search', { ...search, checkpoints: patchNative(nativeRecord(search.checkpoints), checkpoint, undefined) })}>Remove checkpoint {checkpoint}</button>
            </fieldset>)}
            <label>Integration step<input aria-label="Integration step" type="number" value={step} onChange={e => setStep(e.target.value)} /></label>
            <button type="button" onClick={() => { if (step !== '' && !Object.hasOwn(nativeRecord(search.checkpoints), step)) patchSettings('search', { ...search, checkpoints: { ...nativeRecord(search.checkpoints), [step]: {} } }); }}>Add checkpoint</button>
            <button type="button" onClick={() => patchSettings('search', { ...search, checkpoints: {} })}>Explicit empty checkpoints</button>
            <button type="button" onClick={() => patchSettings('search', patchNative(search, 'checkpoints', undefined))}>Inherit checkpoints</button>
            {Object.entries(searchSchema?.properties ?? {}).filter(([key]) => key !== 'checkpoints').map(([key, child]) => leaf(key, child, search[key], v => patchSettings('search', patchNative(search, key, v)), ['settings', 'search', key]))}
            <p>Rewards retain signed weight, normalization and clipping. Criteria retain inclusive min/max, strict min_exclusive/max_exclusive, explicit null bound clears and min_of/max_of/mean_of reductions.</p>
        </>)}
        {section('Metric providers, constructors and native oracles', () => <>
            <p>Profile membership and explicit overrides are separate. Removing a metric override is not an off switch. Only selected oracle dependencies are resolved by the existing owner.</p>
            {['providers', 'oracle_parameter_family', 'metric_extensions'].map(key => leaf(key, schema.properties?.[key], request[key], v => onChange(patchNative(request, key, v)), [key]))}
        </>)}
        {section('Guidance and dataset transforms', () => <>
            <p>contract_ca is a registered mechanics example, not a general biological reward. Explicit empty metric selection remains empty.</p>
            {leaf('Guidance', schema.properties?.guidance, request.guidance, v => onChange(patchNative(request, 'guidance', v)), ['guidance'])}
            {leaf('Dataset transforms', generationSchema?.properties?.dataset, generation.dataset, v => patchSettings('generation', patchNative(generation, 'dataset', v)), ['settings', 'generation', 'dataset'])}
        </>)}
        {section('Runtime, output and retained assets', () => <>
            <p>Native/BioIR incompatibilities remain native validation. Saved expert values are never stripped when switching backends. Resume state is distinct from an external adapter and is not a promise of full interrupted RNG replay.</p>
            {Object.entries(settingsSchema?.properties ?? {}).filter(([key]) => !knownSettings.has(key)).map(([key, child]) => leaf(key, child, settings[key], v => patchSettings(key, v), ['settings', key]))}
            {['resume_slot', 'adapter_slot'].map(key => leaf(key, schema.properties?.[key], request[key], v => onChange(patchNative(request, key, v)), [key]))}
        </>)}
        {section('Requested and inherited identity', () => <>
            <p>Reviewed source: {String(schema['x-reviewed-source-revision'] ?? 'not reported')}. Runtime image: {String(schema['x-runtime-image-sha256'] ?? 'not reported')}.</p>
            <p>Selected native default groups (metadata only): {JSON.stringify(schema['x-selected-default-groups'] ?? {})}</p>
            <p>Selected effective composition is unavailable in this discovery document unless returned by the model owner. Schema defaults are not copied into the request.</p>
            <pre className="whitespace-pre-wrap break-all" aria-label="Requested RFD4 values">{JSON.stringify(request, null, 2)}</pre>
        </>)}
        {section('Primary amount overrides', () => <>{['num_replicates', 'batch_size', 'seed'].filter(key => settingsSchema?.properties?.[key]).map(key => <NativeNumber key={key} label={`Requested ${key}`} value={settings[key]} onChange={v => patchSettings(key, v)} />)}</>)}
    </div>;
}
