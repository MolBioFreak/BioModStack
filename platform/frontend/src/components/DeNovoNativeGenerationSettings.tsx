import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, fetchNativeGenerationSettings, submitJob, completeCurrentLaunchContext, EXECUTION_TARGET_STORAGE_KEY, type Job, type NativeGenerationSourceBindings, type StructureMaterialization } from '../lib/api';
import { BioXpSchemaInput, BioXpSchemaControlContext } from './BioXpSchemaInput';
import { DeNovoNativeSources } from './DeNovoNativeSources';
import { DeNovoNativePreview } from './DeNovoNativePreview';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import { ProteinDesignSections, ProteinDesignPanel, ProteinDesignRun } from './ProteinDesignWorkflow';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { GeneralSequenceDesignSettings } from './GeneralSequenceDesignSettings';
import { generalSequenceRequest, hydrateGeneralSequenceDesign } from '../lib/generalSequenceDesign';
import { changeNativeTask, nativeTaskKey, type NativeTaskDrafts } from '../lib/nativeModelDrafts';
import { buildRfd4NativeSelection, type Rfd4NativeSelectionIdentity } from '../lib/rfd4NativeSelection';

const record = (value: unknown): Record<string, any> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {};

/** Typed advanced CAD overrides; old simple controls remain the defaults. */
export function LegacyNativeGenerationControls({ model, task, value, onChange }: {
    model: 'laproteina' | 'disco'; task: string; value: Record<string, unknown>;
    onChange: (value: Record<string, unknown>) => void;
}) {
    const inventory = useQuery({ queryKey: ['legacy-native-settings', model, task],
        queryFn: async () => (await api.get<{ parameter_schema: Schema; input_schema?: Schema }>(
            '/api/models/protein_modification_experimental/generation-settings',
            { params: { mode: 'de_novo_design', generator: model, task } })).data,
        staleTime: 60_000, retry: false });
    const key = `pcad_${model}_settings_json`;
    const schema = inventory.data?.parameter_schema;
    return <NativeSettingsDisclosure summary={<span>{model === 'disco' ? 'DISCO' : 'La-Proteina'} native overrides</span>}>
        {() => <div className="space-y-3">
            <p>Only requested overrides are sent. These take precedence over the simple controls; omitted values retain the selected preset. Explicit false, zero and null are retained.</p>
            {inventory.error && <p role="alert">{inventory.error.message}. Saved overrides are retained.</p>}
            {schema && <BioXpSchemaInput label="Native settings" nativeLabels schema={schema} rootSchema={schema}
                value={value[key] ?? {}} onChange={settings => onChange({ ...value, [key]: settings })}
                fallback={() => <p>Saved native settings retained.</p>} />}
            {model === 'disco' && inventory.data?.input_schema && <>
                <p>Explicit native entities are ordered strands/copies. DNA does not add a reverse complement here. Legacy DNA shorthand retains its two-strand meaning. Effort fast stays fast; max is a recommendation, not a forced change.</p>
                <BioXpSchemaInput label="Explicit DISCO jobs" nativeLabels schema={inventory.data.input_schema}
                    rootSchema={inventory.data.input_schema} value={value.pcad_disco_jobs_json}
                    onChange={jobs => { const next = { ...value }; if (jobs === undefined) delete next.pcad_disco_jobs_json; else next.pcad_disco_jobs_json = jobs; onChange(next); }}
                    fallback={() => <p>Saved native jobs retained.</p>} />
            </>}
        </div>}
    </NativeSettingsDisclosure>;
}

/** Presentation of the native owner's schema, never a second scientific compiler. */
export function DeNovoNativeGenerationSettings({ model, mode = 'generate', initialValues, onDraftChange, runDetails }: {
    model: 'rfd4_proteina' | 'complexa'; mode?: string; initialValues: Record<string, unknown>;
    onDraftChange: (draft: Record<string, unknown>) => void; runDetails?: ReactNode;
}) {
    const field = model === 'rfd4_proteina' ? 'rfd4_request' : 'complexa_request';
    const [nativeDraft, setNativeDraft] = useState(() => {
        const saved = record(initialValues[field]);
        // Complexa's mode is selected by its native operation; this is routing,
        // not a sampling default. Never overwrite an explicit saved operation.
        const request = model === 'complexa' && mode === 'evaluate' && !Object.hasOwn(saved, 'operation') ? { ...saved, operation: 'evaluate' } : saved;
        return { request, inactive: record(initialValues.native_task_drafts) as NativeTaskDrafts,
            sources: Object.hasOwn(initialValues, 'native_sources') ? record(initialValues.native_sources) as NativeGenerationSourceBindings : undefined,
            inactiveSources: record(initialValues.native_source_drafts) as Record<string, NativeGenerationSourceBindings | null> };
    });
    const request = nativeDraft.request;
    const [visual, setVisual] = useState<{ slot: string; receipt: StructureMaterialization; binding: string; identities: Rfd4NativeSelectionIdentity[] }>();
    const visualResult = useMemo(() => visual ? buildRfd4NativeSelection(visual.identities, { ...visual.receipt, documentId: visual.receipt.path }) : undefined, [visual]);
    const currentVisual = visual && visual.binding === JSON.stringify(nativeDraft.sources?.[visual.slot]) ? visual : undefined;
    const [name, setName] = useState(typeof initialValues.job_name === 'string' ? initialValues.job_name : model);
    const [sequence, setSequence] = useState(() => hydrateGeneralSequenceDesign(initialValues));
    const [explicitSequenceNull] = useState(() => initialValues.sequence_design === null || initialValues.sequence_design_null === true);
    const [section, setSection] = useState('Design');
    const navigate = useNavigate(); const client = useQueryClient();
    const inventory = useQuery({ queryKey: ['de-novo-native-settings', model, mode],
        queryFn: async () => (await fetchNativeGenerationSettings(model, mode)).data,
        staleTime: 60_000, retry: false });
    const schema = inventory.data?.parameter_schema ?? inventory.data?.schema;
    const contract = inventory.data?.contract ?? (typeof schema?.$id === 'string' ? schema.$id : schema?.properties?.schema_version?.const !== undefined ? `${model}:${schema.properties.schema_version.const}` : typeof initialValues.native_contract === 'string' ? initialValues.native_contract : `${model}:unversioned`);
    const payload = { ...generalSequenceRequest(sequence), name, model_id: model, mode,
        ...(!sequence.invocation_draft && !sequence.sequence_design.enabled && explicitSequenceNull ? { sequence_design: null } : {}),
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        ...(Object.hasOwn(initialValues, 'source_structure') ? { source_structure: initialValues.source_structure } : {}),
        params: { [field]: request, ...(nativeDraft.sources !== undefined ? { native_sources: nativeDraft.sources } : {}) } };
    const draft = JSON.stringify({ ...sequence, job_name: name, [field]: request, native_mode: mode,
        ...(explicitSequenceNull ? { sequence_design_null: true } : {}),
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        ...(nativeDraft.sources !== undefined ? { native_sources: nativeDraft.sources } : {}),
        ...(Object.hasOwn(initialValues, 'source_structure') ? { source_structure: initialValues.source_structure } : {}),
        native_contract: contract, native_task_drafts: nativeDraft.inactive, native_source_drafts: nativeDraft.inactiveSources,
        native_job_request: payload });
    useEffect(() => { onDraftChange(JSON.parse(draft)); }, [draft, onDraftChange]);
    const submit = useMutation({ mutationFn: () => submitJob({ ...payload,
        execution_target_id: sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || null } as Partial<Job>),
        onSuccess: async response => { client.invalidateQueries({ queryKey: ['jobs'] }); navigate(await completeCurrentLaunchContext(response.data) ?? '/'); } });
    const patch = (key: string, value: unknown) => setNativeDraft(previous => {
        const next = { ...previous.request }; if (value === undefined) delete next[key]; else next[key] = value;
        const changed = changeNativeTask(model, mode, contract, previous.request, next, previous.inactive);
        const before = nativeTaskKey(model, mode, contract, previous.request), after = nativeTaskKey(model, mode, contract, next);
        if (before === after) return { ...previous, ...changed };
        const inactiveSources = { ...previous.inactiveSources, [before]: previous.sources ?? null };
        const sources = inactiveSources[after] ?? undefined; delete inactiveSources[after];
        return { ...changed, sources, inactiveSources };
    });
    const renderField = (key: string, child: Schema) => <BioXpSchemaInput label={key} nativeLabels
        schema={{ type: 'object', properties: { [key]: child }, additionalProperties: false, required: schema?.required?.includes(key) ? [key] : [] }} rootSchema={schema}
        value={Object.hasOwn(request, key) ? { [key]: request[key] } : {}}
        onChange={value => patch(key, record(value)[key])}
        fallback={() => <p>Saved native value retained; this schema does not describe an editable value.</p>} />;
    const primary = new Set(['version', 'mode', 'operation', 'task', 'input', 'inputs', 'profile', 'sampling', 'seed', 'num_samples']);
    return <BioXpSchemaControlContext.Provider value={({ label, schema: child, value, onChange, path }) => {
        if (model === 'rfd4_proteina' && path.length === 1 && path[0] === 'settings' && !child['x-bms-settings-group'] && child.type === 'object' && value !== null && typeof value === 'object' && !Array.isArray(value)) {
            // Presentation only: partition the native schema, never its request.
            // Merging a group's edits leaves omitted and inactive backend values exact.
            const fields = child.properties ?? {};
            const groups: Array<[string, string[]]> = [
                ['Sampling controls', ['num_replicates', 'batch_size', 'seed', 'generation']],
                ['Runtime acceleration', ['inference_backend', 'precision', 'nn', 'compile_lengths', 'bioir', 'torch_deterministic', 'num_workers']],
                ['Search, checkpoints and continuation', ['search']],
                ['Output and retention', ['skip_existing', 'save_chain_of_interest', 'compress_outputs', 'metrics_tags', 'delete_failing_pre_oracle']],
            ];
            const grouped = new Set(groups.flatMap(([, keys]) => keys));
            groups.push(['Other native settings', [...new Set([...Object.keys(fields), ...Object.keys(record(value))])].filter(key => !grouped.has(key))]);
            return <div data-rfd4-sampling>
                <p>Sampling, partial diffusion and inverse folding retain their native meanings. Backbone and latent schedules are independent.</p>
                {groups.map(([title, keys]) => {
                    const properties = Object.fromEntries(keys.filter(key => Object.hasOwn(fields, key) || Object.hasOwn(record(value), key)).map(key => [key, fields[key] ?? {}]));
                    if (!Object.keys(properties).length) return null;
                    return <NativeSettingsDisclosure key={title} summary={<span>{title}</span>}>{() => <>
                        {title === 'Runtime acceleration' && <p>Native torch.compile/cuEquivariance is the common path. BioIR is optional and requires CUDA 13; state/resume, clean-prediction scoring, continuation and per-checkpoint branching have native limitations. Switching backend retains requested expert values; the native owner reports unsupported combinations without silently changing settings.</p>}
                        <BioXpSchemaInput label={label} nativeLabels schema={{ ...child, 'x-bms-settings-group': true, properties, required: child.required?.filter(key => Object.hasOwn(properties, key)) }} rootSchema={schema}
                            path={path} value={Object.fromEntries(Object.entries(record(value)).filter(([key]) => Object.hasOwn(properties, key)))}
                            onChange={next => {
                                const merged = { ...record(value) };
                                for (const key of Object.keys(properties)) delete merged[key];
                                onChange({ ...merged, ...record(next) });
                            }} fallback={() => <p>Saved native values retained.</p>} />
                    </>}</NativeSettingsDisclosure>;
                })}
            </div>;
        }
        if (model === 'rfd4_proteina' && child.type === 'string' && path[0] === 'input' && path.at(-1) === 'select' && String(child['x-native-mapping']).includes('.conditions.')) return <>
            <label>{label}<input aria-label={label} value={String(value ?? '')} onChange={event => onChange(event.target.value)} /></label>
            {currentVisual && path.at(-3) === currentVisual.slot && <button type="button" aria-label={`Use source selection for ${label}`} onClick={() => {
                if (visualResult?.expression !== undefined) onChange(visualResult.expression);
            }}>Use selection from {currentVisual.slot}</button>}
        </>;
        if (child.type !== 'string' || !child['x-native-source-kind']) return undefined;
        const names = Object.entries(nativeDraft.sources ?? {}).filter(([, source]) => child['x-native-source-kind'] === 'file' ? 'kind' in source && source.kind === 'file' : !('kind' in source)).map(([name]) => name);
        return <label>{label}<input aria-label={label} list={`${label}-native-sources`} value={String(value ?? '')} onChange={event => onChange(event.target.value)} />
            <datalist id={`${label}-native-sources`}>{names.map(name => <option key={name} value={name} />)}</datalist></label>;
    }}><div className="space-y-4" data-native-de-novo={model}>
        <ProteinDesignSections label="Generation sections" sections={['Design', 'Native settings', 'Sequence Design']} active={section} onChange={setSection} />
        {inventory.isPending && <p role="status">Loading model-owned settings…</p>}
        {inventory.error && <p role="alert">{inventory.error.message}. Saved native values are retained.</p>}
        <div hidden={section !== 'Design'}><ProteinDesignPanel title="Design" description="Native requests retain omitted settings separately from explicit values. Source slots refer to exact retained native documents, not PDB conversions.">
            <NativeSettingsDisclosure summary={<span>Native sources</span>}>{() => <DeNovoNativeSources value={nativeDraft.sources} onChange={sources => setNativeDraft(previous => ({ ...previous, sources }))}
                onSelection={(slot, receipt, selection) => {
                    const identities = selection.identities.flatMap(identity => 'first' in identity && 'second' in identity ? [identity.first, identity.second] : [identity]) as Rfd4NativeSelectionIdentity[];
                    const binding = JSON.stringify(nativeDraft.sources?.[slot]);
                    setVisual(previous => ({ slot, receipt, binding, identities: [...(previous?.slot === slot && previous.receipt.path === receipt.path && previous.binding === binding ? previous.identities : []), ...identities] }));
                }} />}</NativeSettingsDisclosure>
            {currentVisual && model === 'rfd4_proteina' && <div role="status"><p>{visualResult?.issue ?? `Native source query from ${currentVisual.slot}: ${visualResult?.expression}`}</p><p>Select the destination condition below. Coordinates, grouped/internal coordinates and sequence conditions remain independent; no role is assigned until you choose it.</p><button type="button" onClick={() => setVisual(undefined)}>Clear visual selection</button></div>}
            {schema && Object.entries(schema.properties ?? {}).filter(([key]) => primary.has(key)).map(([key, child]) => <div key={key}>{renderField(key, child)}</div>)}
            {inventory.data?.limitations?.map(text => <p key={text} className="text-sm">{text}</p>)}
            <DeNovoNativePreview request={payload as Partial<Job>} />
        </ProteinDesignPanel></div>
        <div hidden={section !== 'Native settings'}><ProteinDesignPanel title="Native settings" description="Requested overrides only. Omitted settings inherit the selected native profile at the model owner. Oracle template conditioning is not blind prediction.">
            {schema && Object.entries(schema.properties ?? {}).filter(([key]) => !primary.has(key)).map(([key, child]) => <NativeSettingsDisclosure key={key} summary={<span>{model === 'rfd4_proteina' && key === 'settings' ? 'Sampling' : child.title ?? key.replaceAll('_', ' ')}</span>}>
                {() => renderField(key, child)}
            </NativeSettingsDisclosure>)}
        </ProteinDesignPanel></div>
        <div hidden={section !== 'Sequence Design'}><GeneralSequenceDesignSettings value={sequence} onChange={setSequence} /></div>
        <ProteinDesignRun jobName={name} onJobNameChange={setName} pending={submit.isPending} onSubmit={() => submit.mutate()} submitLabel={mode === 'evaluate' ? 'Evaluate candidates' : 'Generate candidates'}>
            <ExecutionTargetPicker workflowRequest={payload as Partial<Job>} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
            {runDetails}{submit.error && <p role="alert">{submit.error.message}</p>}
        </ProteinDesignRun>
    </div></BioXpSchemaControlContext.Provider>;
}
