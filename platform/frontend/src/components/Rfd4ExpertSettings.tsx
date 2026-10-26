import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeNumber, nativeRecord, patchNative } from './Rfd4TaskWorkspace';

// Inspect schema structure only; defaults are not authored by opening a panel.
function collectionKind(child: Schema, root: Schema, seen = new Set<string>()): 'object' | 'array' | undefined {
    if (child.$ref?.startsWith('#/') && !seen.has(child.$ref)) {
        seen.add(child.$ref);
        let target: any = root;
        for (const part of child.$ref.slice(2).split('/')) target = target?.[part.replace(/~1/g, '/').replace(/~0/g, '~')];
        if (target && typeof target === 'object') return collectionKind({ ...target, ...child, $ref: undefined }, root, seen);
    }
    const types = Array.isArray(child.type) ? child.type : [child.type];
    if (types.includes('object') || child.properties) return 'object';
    if (types.includes('array') || child.items) return 'array';
    for (const branch of child.anyOf ?? child.oneOf ?? []) {
        const kind = collectionKind(branch, root, new Set(seen));
        if (kind) return kind;
    }
}
export function NativeProfileSnapshot({ model, mode, request, discovery }: { model: string; mode: string; request: Record<string, any>; discovery: unknown }) {
    const identity = JSON.stringify([model, mode, request]);
    const [readback, setReadback] = useState<{ identity: string; result?: Record<string, any>; error?: string }>();
    const [busy, setBusy] = useState(false);
    const inspect = async () => {
        setBusy(true);
        try {
            const response = await api.post(`/api/models/${model}/generation-settings/preview`, request, { params: { mode } });
            setReadback({ identity, result: response.data });
        } catch (error) { setReadback({ identity, error: error instanceof Error ? error.message : String(error) }); }
        finally { setBusy(false); }
    };

    const data = nativeRecord(discovery);
    const groups = Object.entries(nativeRecord(request.groups)).filter(([key]) => ['generation', 'recycle_strategy'].includes(key));
    const snapshots = model === 'complexa' ? [[String(request.task ?? 'Choose task'), nativeRecord(data.tasks)[String(request.task)]]] : groups.length ? groups.map(([key, value]) => [`${key}: ${value}`, nativeRecord(nativeRecord(data.generation_by_group)[key])[String(value)]]) : [['Native default generation', data.default_generation]];
    return <NativeSettingsDisclosure summary={<span>Profile reference</span>}>{() => <div className="space-y-3">
        <p className="break-all">{String(data.authority ?? 'Native snapshot authority unavailable')}. Source: {String(data.reviewed_source_revision ?? data.source_revision ?? 'not reported')}. Image: {String(data.runtime_image_sha256 ?? 'not reported')}.</p>
        <p>{String(data.scope ?? 'Profile reference only; not the combined effective configuration.')}</p>
        {groups.length > 1 && <p role="status">These are independent single-group snapshots, not the combined effective request. Native preparation resolves the combination.</p>}
        {snapshots.map(([label, value]) => <section key={String(label)}><h4>{String(label)}</h4><pre className="whitespace-pre-wrap break-all max-h-96 overflow-auto" aria-label={`Native snapshot ${label}`}>{value === undefined ? 'Snapshot not reported for this selection.' : JSON.stringify(value, null, 2)}</pre></section>)}
        {model === 'rfd4_proteina' && <section><button type="button" disabled={busy} onClick={() => void inspect()} className="rounded border px-3 py-2">{busy ? 'Reading native composition…' : 'Read selected effective configuration'}</button>
            <p>Reads configuration only. Does not run a model.</p>
            {readback && readback.identity !== identity && <p role="status">Request changed since readback. Read again for current effective values.</p>}
            {readback?.identity === identity && (readback.error ? <p role="status">Native readback unavailable: {readback.error}. Requested values are unchanged.</p> : <><p className="break-all">Selected runtime: {String(readback.result?.runtime_image ?? 'not reported')}</p><pre aria-label="Selected native effective configuration" className="whitespace-pre-wrap break-all max-h-96 overflow-auto">{JSON.stringify(readback.result?.effective_config, null, 2)}</pre></>)}
        </section>}
        <p>Requested overrides</p>
        <pre className="whitespace-pre-wrap break-all max-h-64 overflow-auto" aria-label="Snapshot requested overrides">{JSON.stringify(request, null, 2)}</pre>
    </div>}</NativeSettingsDisclosure>;
}

/** Named model-specific expert sections reuse native typed leaves, not a second schema/composer. */
export function Rfd4ExpertSettings({ request, schema, onChange, fileSlots = {} }: { request: Record<string, any>; schema: Schema; onChange: (v: Record<string, any>) => void; fileSlots?: Record<string, string> }) {
    const [step, setStep] = useState('');
    const [metricQuery, setMetricQuery] = useState('');
    const [metricKey, setMetricKey] = useState('');
    const catalog = Object.entries(schema.properties?.providers?.properties ?? {}).flatMap(([provider, declaration]) => (declaration.oneOf ?? [declaration]).flatMap(branch => {
        const profile = String(branch.properties?.profile?.const ?? '');
        return Object.entries(branch.properties?.overrides?.properties ?? {}).map(([metric, child]) => ({ provider, profile, metric, child, key: JSON.stringify([provider, profile, metric]) }));
    }));
    const chosen = catalog.find(row => row.key === metricKey);

    const settings = nativeRecord(request.settings), settingsSchema = schema.properties?.settings;
    const generation = nativeRecord(settings.generation), generationSchema = settingsSchema?.properties?.generation;
    const search = nativeRecord(settings.search), searchSchema = settingsSchema?.properties?.search;
    const patchSettings = (key: string, value: unknown) => onChange({ ...request, settings: patchNative(settings, key, value) });
    // Only ancestors created by this editor may disappear when an override is
    // removed. Existing empty siblings/ancestors are explicit retained values.
    const introduced = useRef(new WeakSet<object>());
    const changeLeaf = (path: (string | number)[], value: unknown) => {
        const update = (current: Record<string, any> | undefined, depth: number): Record<string, any> | undefined => {
            const next = { ...current };
            const key = path[depth];
            const child = depth === path.length - 1 ? value : update(current?.[key], depth + 1);
            if (child === undefined) delete next[key]; else next[key] = child;
            const created = current === undefined || introduced.current.has(current);
            if (depth > 0 && created && Object.keys(next).length === 0 && value === undefined) return undefined;
            if (created) introduced.current.add(next);
            return next;
        };
        onChange(update(request, 0)!);
    };
    const leaf = (label: string, child: Schema | undefined, value: unknown, path: (string | number)[]) => {
        if (!child) return null;
        const kind = collectionKind(child, schema);
        return <div key={label} data-rfd4-override={label}>
            {kind && value === undefined ? <fieldset className="space-y-2"><legend>{label}</legend>
                <p>From selected profile</p>
                <button type="button" aria-label={`Set override for ${label}`} onClick={() => changeLeaf(path, kind === 'array' ? [] : {})}>Set override</button>
            </fieldset> : <BioXpSchemaInput label={label} nativeLabels schema={child} rootSchema={schema} path={path} value={value} onChange={v => changeLeaf(path, v)} fallback={() => value === undefined ? <p>From selected profile</p> : <p>Retained value does not match this schema.</p>} />}
            {kind && value !== undefined && <button type="button" aria-label={`Use profile value for ${label}`} onClick={() => changeLeaf(path, undefined)}>Use profile value</button>}
        </div>;
    };
    const section = (title: string, content: () => React.ReactNode) => <NativeSettingsDisclosure key={title} summary={<span>{title}</span>}>{content}</NativeSettingsDisclosure>;
    const cpSchema = searchSchema?.properties?.checkpoints?.anyOf?.find(s => s.type === 'object')?.additionalProperties as Schema | undefined;
    const knownSettings = new Set(['num_replicates', 'batch_size', 'seed', 'generation', 'search']);
    return <div className="space-y-3" data-rfd4-expert>
        <p>Unchanged settings use the selected profile.</p>
        {section('Profiles and recycling', () => <>
            {leaf('Native profiles', schema.properties?.groups, request.groups, ['groups'])}
            {Object.entries(generationSchema?.properties ?? {}).filter(([key]) => !['args', 'model', 'dataset'].includes(key)).map(([key, child]) => leaf(key, child, generation[key], ['settings', 'generation', key]))}
        </>)}
        {section('Integration and partial / inverse sampling', () => <>{leaf('Integration arguments', generationSchema?.properties?.args, generation.args, ['settings', 'generation', 'args'])}</>)}
        {['bb_ca', 'local_latents'].map(modality => section(modality === 'bb_ca' ? 'Backbone schedule and simulation' : 'Sequence latent schedule and simulation', () => <>
            {leaf(modality, generationSchema?.properties?.model?.properties?.[modality], generation.model?.[modality], ['settings', 'generation', 'model', modality])}
        </>))}
        {section('Search checkpoint timeline', () => <>
            <p>Steps are native integration-step keys. Clean-prediction ranking is not a completed rollout. Opening this panel does not enable search.</p>
            {Object.entries(nativeRecord(search.checkpoints)).map(([checkpoint, value]) => <fieldset key={checkpoint}><legend>Step {checkpoint}</legend>
                {leaf(`Checkpoint ${checkpoint}`, cpSchema, value, ['settings', 'search', 'checkpoints', checkpoint])}
                <button type="button" onClick={() => patchSettings('search', { ...search, checkpoints: patchNative(nativeRecord(search.checkpoints), checkpoint, undefined) })}>Remove checkpoint {checkpoint}</button>
            </fieldset>)}
            <label>Integration step<input aria-label="Integration step" type="number" value={step} onChange={e => setStep(e.target.value)} /></label>
            <button type="button" onClick={() => { if (step !== '' && !Object.hasOwn(nativeRecord(search.checkpoints), step)) patchSettings('search', { ...search, checkpoints: { ...nativeRecord(search.checkpoints), [step]: {} } }); }}>Add checkpoint</button>
            <button type="button" onClick={() => patchSettings('search', { ...search, checkpoints: {} })}>Explicit empty checkpoints</button>
            <button type="button" onClick={() => patchSettings('search', patchNative(search, 'checkpoints', undefined))}>Inherit checkpoints</button>
            {Object.entries(searchSchema?.properties ?? {}).filter(([key]) => key !== 'checkpoints').map(([key, child]) => leaf(key, child, search[key], ['settings', 'search', key]))}
            <p>Rewards retain signed weight, normalization and clipping. Criteria retain inclusive min/max, strict min_exclusive/max_exclusive, explicit null bound clears and min_of/max_of/mean_of reductions.</p>
        </>)}
        {section('Metric providers, constructors and native oracles', () => <>
            <p>Profile membership and explicit overrides are separate. Removing a metric override is not an off switch. Only selected oracle dependencies are resolved by the existing owner.</p>
            <label>Search native providers and metrics<input aria-label="Search native providers and metrics" value={metricQuery} onChange={e => setMetricQuery(e.target.value)} /></label>
            <label>Provider / metric<select aria-label="Provider / metric" value={metricKey} onChange={e => setMetricKey(e.target.value)}><option value="">Choose a native declaration</option>{catalog.filter(row => `${row.provider} ${row.profile} ${row.metric}`.toLowerCase().includes(metricQuery.toLowerCase()) || row.key === metricKey).map(row => <option key={row.key} value={row.key}>{row.provider} / {row.profile} / {row.metric}</option>)}</select></label>
            {chosen && <section><p>Exact constructor override: {chosen.metric}. Selecting a catalog row does not activate a provider or change its profile.</p>
                {nativeRecord(request.providers)[chosen.provider]?.profile !== chosen.profile ? <p>Choose {chosen.profile} in the native provider control below to edit this constructor. Existing profile overrides remain unchanged.</p> : leaf(`Selected metric ${chosen.metric}`, chosen.child, request.providers?.[chosen.provider]?.overrides?.[chosen.metric], ['providers', chosen.provider, 'overrides', chosen.metric])}
            </section>}
            {['providers', 'oracle_parameter_family', 'metric_extensions'].map(key => leaf(key, schema.properties?.[key], request[key], [key]))}
        </>)}
        {section('Guidance and dataset transforms', () => <>
            <p>contract_ca is a registered mechanics example, not a general biological reward. Explicit empty metric selection remains empty.</p>
            {leaf('Guidance', schema.properties?.guidance, request.guidance, ['guidance'])}
            {leaf('Dataset transforms', generationSchema?.properties?.dataset, generation.dataset, ['settings', 'generation', 'dataset'])}
        </>)}
        {section('Runtime, output and retained assets', () => <>
            <p>Native/BioIR incompatibilities remain native validation. Saved expert values are never stripped when switching backends. Resume state is distinct from an external adapter and is not a promise of full interrupted RNG replay.</p>
            {Object.entries(settingsSchema?.properties ?? {}).filter(([key]) => !knownSettings.has(key)).map(([key, child]) => leaf(key, child, settings[key], ['settings', key]))}
            {['resume_slot', 'adapter_slot'].map(key => <fieldset key={key}><legend>{key === 'resume_slot' ? 'Retained native search state' : 'External adapter asset'}</legend>
                <label>{key}<input aria-label={key} list="rfd4-file-slots" value={request[key] ?? ''} onChange={e => onChange({ ...request, [key]: e.target.value })} /></label>
                <p className="break-all">{request[key] === undefined ? 'Inherited / not requested' : fileSlots[request[key]] ?? 'No managed file binding for this slot. Add or inspect its file/archive binding in Native sources.'}</p>
                <button type="button" onClick={() => onChange(patchNative(request, key, undefined))}>Omit {key}</button>
            </fieldset>)}
            <datalist id="rfd4-file-slots">{Object.entries(fileSlots).map(([slot, path]) => <option key={slot} value={slot}>{path}</option>)}</datalist>
            <p>Inspect native state identity before choosing continuation seed in Search checkpoint timeline. No file inspection or compatibility result is inferred from the filename.</p>
        </>)}
        {section('Requested and inherited identity', () => <>
            <p>Reviewed source: {String(schema['x-reviewed-source-revision'] ?? 'not reported')}. Runtime image: {String(schema['x-runtime-image-sha256'] ?? 'not reported')}.</p>
            <p>Selected native default groups (metadata only): {JSON.stringify(schema['x-selected-default-groups'] ?? {})}</p>
            <p>Profile reference shows declared defaults, not combined effective values.</p>
            <pre className="whitespace-pre-wrap break-all" aria-label="Requested RFD4 values">{JSON.stringify(request, null, 2)}</pre>
        </>)}
        {section('Primary amount overrides', () => <>{['num_replicates', 'batch_size', 'seed'].filter(key => settingsSchema?.properties?.[key]).map(key => <NativeNumber key={key} label={`Requested ${key}`} value={settings[key]} onChange={v => patchSettings(key, v)} />)}</>)}
    </div>;
}
