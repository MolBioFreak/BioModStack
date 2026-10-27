import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, fetchNativeGenerationSettings, submitJob, completeCurrentLaunchContext, EXECUTION_TARGET_STORAGE_KEY, type Job, type NativeGenerationSourceBindings, type StructureMaterialization } from '../lib/api';
import { BioXpSchemaInput, BioXpSchemaControlContext } from './BioXpSchemaInput';
import { DeNovoNativeSources, NativeSourceInput } from './DeNovoNativeSources';
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
import { buildComplexaNativeSelection } from '../lib/complexaNativeSelection';
import { Rfd4TaskWorkspace, rfd4RecipeRequest } from './Rfd4TaskWorkspace';
import { Rfd4ExpertSettings, NativeProfileSnapshot } from './Rfd4ExpertSettings';
import { ComplexaTaskWorkspace } from './ComplexaTaskWorkspace';
import type { MetricSelection } from '../structureViewer/metrics/metricContracts';

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
                    onChange={jobs => { const next = { ...value }; if (jobs === undefined) { delete next.pcad_disco_jobs_json; delete next.pcad_disco_jobs_source_path; } else next.pcad_disco_jobs_json = jobs; onChange(next); }}
                    fallback={() => <p>Saved native jobs retained.</p>} />
            </>}
        </div>}
    </NativeSettingsDisclosure>;
}

/** Presentation of the native owner's schema, never a second scientific compiler. */
export function DeNovoNativeGenerationSettings({ model, mode = 'generate', initialValues, onDraftChange, runDetails, suiteView }: {
    model: 'rfd4_proteina' | 'complexa'; mode?: string; initialValues: Record<string, unknown>;
    onDraftChange: (draft: Record<string, unknown>) => void; runDetails?: ReactNode; suiteView?: 'simple' | 'advanced';
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
    const [recipe, setRecipe] = useState<string | undefined>(() => typeof initialValues.native_recipe === 'string' ? initialValues.native_recipe : undefined);
    const [controlDrafts, setControlDrafts] = useState(() => record(initialValues.native_control_drafts));
    const activeMode = model === 'complexa' ? ['evaluate', 'analysis'].includes(String(request.operation ?? mode)) ? 'evaluate' : 'generate' : mode;
    const readbackIdentity = JSON.stringify([model, activeMode, request]);
    const readbackGeneration = useRef({ identity: readbackIdentity, value: 0 });
    if (readbackGeneration.current.identity !== readbackIdentity) readbackGeneration.current = { identity: readbackIdentity, value: readbackGeneration.current.value + 1 };
    const [effectiveReadback, setEffectiveReadback] = useState<{ generation: number; effective: Record<string, any> }>();
    const [inspections, setInspections] = useState<Record<string, { receipt: StructureMaterialization; binding: string }>>({});
    const [visual, setVisual] = useState<{ slot: string; receipt: StructureMaterialization; binding: string; identities: Rfd4NativeSelectionIdentity[] }>();
    const visualResult = useMemo(() => visual ? buildRfd4NativeSelection(visual.identities, { ...visual.receipt, documentId: visual.receipt.path }) : undefined, [visual]);
    const currentVisual = visual && visual.binding === JSON.stringify(nativeDraft.sources?.[visual.slot]) ? visual : undefined;
    const [name, setName] = useState(typeof initialValues.job_name === 'string' ? initialValues.job_name : model);
    const [sequence, setSequence] = useState(() => hydrateGeneralSequenceDesign(initialValues));
    const [explicitSequenceNull] = useState(() => initialValues.sequence_design === null || initialValues.sequence_design_null === true);
    const [section, setSection] = useState('Design');
    useEffect(() => { if (suiteView) setSection(suiteView === 'advanced' ? 'Native settings' : 'Design'); }, [suiteView]);
    const navigate = useNavigate(); const client = useQueryClient();
    const inventory = useQuery({ queryKey: ['de-novo-native-settings', model, mode],
        queryFn: async () => (await fetchNativeGenerationSettings(model, mode)).data,
        staleTime: 60_000, retry: false });
    const schema = inventory.data?.parameter_schema ?? inventory.data?.schema;
    const contract = inventory.data?.contract ?? (typeof schema?.$id === 'string' ? schema.$id : schema?.properties?.schema_version?.const !== undefined ? `${model}:${schema.properties.schema_version.const}` : typeof initialValues.native_contract === 'string' ? initialValues.native_contract : `${model}:unversioned`);
    const payload = { ...generalSequenceRequest(sequence), name, model_id: model, mode: activeMode,
        ...(!sequence.invocation_draft && !sequence.sequence_design.enabled && explicitSequenceNull ? { sequence_design: null } : {}),
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        ...(Object.hasOwn(initialValues, 'source_structure') ? { source_structure: initialValues.source_structure } : {}),
        params: { [field]: request, ...(nativeDraft.sources !== undefined ? { native_sources: nativeDraft.sources } : {}) } };
    const draft = JSON.stringify({ ...sequence, job_name: name, [field]: request, native_mode: activeMode, ...(recipe ? { native_recipe: recipe } : {}),
        ...(explicitSequenceNull ? { sequence_design_null: true } : {}),
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        ...(nativeDraft.sources !== undefined ? { native_sources: nativeDraft.sources } : {}),
        ...(Object.hasOwn(initialValues, 'source_structure') ? { source_structure: initialValues.source_structure } : {}),
        native_contract: contract, native_task_drafts: nativeDraft.inactive, native_source_drafts: nativeDraft.inactiveSources, native_control_drafts: controlDrafts,
        native_job_request: payload });
    useEffect(() => { onDraftChange(JSON.parse(draft)); }, [draft, onDraftChange]);
    const submit = useMutation({ mutationFn: () => submitJob({ ...payload,
        execution_target_id: sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || null } as Partial<Job>),
        onSuccess: async response => { client.invalidateQueries({ queryKey: ['jobs'] }); navigate(await completeCurrentLaunchContext(response.data) ?? '/'); } });
    const patch = (key: string, value: unknown) => setNativeDraft(previous => {
        const next = { ...previous.request }; if (value === undefined) delete next[key]; else next[key] = value;
        if (recipe && model === 'rfd4_proteina') return { ...previous, request: next };
        const changed = changeNativeTask(model, mode, contract, previous.request, next, previous.inactive);
        const before = nativeTaskKey(model, mode, contract, previous.request), after = nativeTaskKey(model, mode, contract, next);
        if (before === after) return { ...previous, ...changed };
        const inactiveSources = { ...previous.inactiveSources, [before]: previous.sources ?? null };
        const sources = inactiveSources[after] ?? undefined; delete inactiveSources[after];
        return { ...changed, sources, inactiveSources };
    });
    const changeRequest = (next: Record<string, any>) => setNativeDraft(previous => ({ ...previous, request: next }));
    const changeRecipe = (nextRecipe: string) => {
        if (!nextRecipe || nextRecipe === recipe) return;
        const legacyKey = nextRecipe.startsWith('legacy:') ? nextRecipe.slice(7) : undefined;
        setNativeDraft(previous => {
            const before = nativeTaskKey(model, mode, contract, previous.request, recipe);
            const starter = rfd4RecipeRequest(nextRecipe);
            const after = legacyKey ?? nativeTaskKey(model, mode, contract, starter, nextRecipe);
            const inactive = { ...previous.inactive, [before]: previous.request };
            const next = inactive[after] ?? starter; delete inactive[after];
            const inactiveSources = { ...previous.inactiveSources, [before]: previous.sources ?? null };
            const sources = inactiveSources[after] ?? undefined; delete inactiveSources[after];
            return { request: next, inactive, sources, inactiveSources };
        });
        setRecipe(legacyKey ? undefined : nextRecipe); setVisual(undefined);
    };
    const sourceSequence = useRef(0);
    const selectSource = (slot: string, receipt: StructureMaterialization, selection: MetricSelection) => {
        const identities = selection.identities.flatMap(identity => 'first' in identity && 'second' in identity ? [identity.first, identity.second] : [identity]) as Rfd4NativeSelectionIdentity[];
        const binding = JSON.stringify(nativeDraft.sources?.[slot]);
        setVisual(previous => ({ slot, receipt, binding, identities: selection.metricId === 'native-source-sequence-selection' ? identities : [...(previous?.slot === slot && previous.receipt.path === receipt.path && previous.binding === binding ? previous.identities : []), ...identities] }));
    };
    const renderSource = (label: string, value: string, onChange: (slot: string) => void) => <NativeSourceInput
        key={label} label={label} integrated={model === 'rfd4_proteina'} value={value} bindings={nativeDraft.sources} onChange={onChange}
        onSelection={selectSource} selectedIdentities={currentVisual?.slot === value ? currentVisual.identities : []}
        inspection={inspections[value]?.binding === JSON.stringify(nativeDraft.sources?.[value]) ? inspections[value]?.receipt : undefined}
        onAcquire={source => {
            // A task's existing source name keeps its shared references. New
            // inputs are named only after successful acquisition.
            let slot = value || Object.entries(nativeDraft.sources ?? {}).find(([, binding]) => JSON.stringify(binding) === JSON.stringify(source))?.[0];
            if (slot === undefined || slot === '') {
                do { slot = `input_${++sourceSequence.current}`; } while (Object.hasOwn(nativeDraft.sources ?? {}, slot));
            }
            const acquiredSlot = slot;
            setNativeDraft(previous => ({ ...previous, sources: { ...previous.sources, [acquiredSlot]: source } }));
            onChange(slot);
            return slot;
        }}
        onInspection={(slot, receipt, binding) => setInspections(previous => ({ ...previous, [slot]: { receipt, binding: JSON.stringify(binding) } }))} />;
    const renderField = (key: string, child: Schema) => <BioXpSchemaInput label={key} nativeLabels
        schema={{ type: 'object', properties: { [key]: child }, additionalProperties: false, required: schema?.required?.includes(key) ? [key] : [] }} rootSchema={schema}
        value={Object.hasOwn(request, key) ? { [key]: request[key] } : {}}
        onChange={value => patch(key, record(value)[key])}
        fallback={() => <p>Saved native value retained; this schema does not describe an editable value.</p>} />;
    const primary = new Set(['version', 'mode', 'operation', 'task', 'task_name', 'source', 'input', 'inputs', 'profile', 'sampling', 'seed', 'num_samples', 'nsamples', 'min_length', 'max_length', 'batch_size', 'nrepeat_per_sample', 'n_recycle']);
    return <BioXpSchemaControlContext.Provider value={({ label, schema: child, value, onChange, path }) => {
        if (model === 'rfd4_proteina' && child.type === 'string' && path[0] === 'input' && path.at(-1) === 'select' && String(child['x-native-mapping']).includes('.conditions.')) return <>
            <label>{label}<input aria-label={label} value={String(value ?? '')} onChange={event => onChange(event.target.value)} /></label>
            {/* Conditions are keyed by value, not source slot. Visual adoption lives
                in the source-qualified example workspace; never infer it from labels. */}
        </>;
        if (child.type !== 'string' || !child['x-native-source-kind']) return undefined;
        if (child['x-native-source-kind'] !== 'file') return renderSource(label, String(value ?? ''), onChange);
        const names = Object.entries(nativeDraft.sources ?? {}).filter(([, source]) => child['x-native-source-kind'] === 'file' ? 'kind' in source && source.kind === 'file' : !('kind' in source)).map(([name]) => name);
        return <label>{label}<input aria-label={label} list={`${label}-native-sources`} value={String(value ?? '')} onChange={event => onChange(event.target.value)} />
            <datalist id={`${label}-native-sources`}>{names.map(name => <option key={name} value={name} />)}</datalist></label>;
    }}><div className="space-y-4" data-native-de-novo={model}>
        <style>{`
            [data-native-de-novo] :is([data-rfd4-task-workspace], [data-complexa-task-workspace], [data-native-source-bindings], [data-rfd4-expert]) label { display: inline-flex; flex-direction: column; gap: .375rem; max-width: 100%; margin: .25rem .5rem .5rem 0; vertical-align: top; }
            [data-native-de-novo] :is([data-rfd4-task-workspace], [data-complexa-task-workspace], [data-native-source-bindings], [data-rfd4-expert]) :is(input, select) { max-width: 100%; min-width: 0; border: 1px solid var(--border-primary, #475569); border-radius: .375rem; background: var(--bg-tertiary, #1e293b); color: var(--text-primary, #e2e8f0); padding: .5rem; }
            [data-native-de-novo] :is([data-rfd4-task-workspace], [data-complexa-task-workspace], [data-native-source-bindings], [data-rfd4-expert]) button { border: 1px solid transparent; background: var(--bg-tertiary, #1e293b); border-radius: .375rem; padding: .375rem .625rem; margin: .25rem .375rem .25rem 0; white-space: normal; text-align: left; }
            [data-native-de-novo] :is(input, select, button):focus-visible { outline: 2px solid var(--accent-primary, #60a5fa); outline-offset: 2px; }
            [data-native-de-novo] :is([data-rfd4-task-workspace], [data-complexa-task-workspace], [data-rfd4-expert]) fieldset { min-width: 0; }
            [data-native-de-novo] :is([data-rfd4-task-workspace], [data-complexa-task-workspace], [data-rfd4-expert]) legend { font-weight: 600; margin-bottom: .5rem; }
        `}</style>
        <ProteinDesignSections label="Generation sections" sections={['Design', 'Native settings', 'Optional operations']} active={section} onChange={setSection} />
        {inventory.isPending && <p role="status">Loading settings…</p>}
        {inventory.error && <p role="alert">{inventory.error.message}. Saved native values are retained.</p>}
        <div hidden={section !== 'Design'}><ProteinDesignPanel title="Design" showTitle={false}>
            {schema && (model === 'rfd4_proteina' ? <Rfd4TaskWorkspace request={request} schema={schema} mode={mode} recipe={recipe} onRecipe={changeRecipe} onChange={changeRequest} renderNative={renderField} renderSource={renderSource} sourceNames={Object.keys(nativeDraft.sources ?? {})}
                sourceChains={Object.fromEntries(Object.entries(inspections).filter(([slot, item]) => item.binding === JSON.stringify(nativeDraft.sources?.[slot]) && !item.receipt.inspection_error && !!item.receipt.source_positions?.length).map(([slot, item]) => [slot, [...new Set(item.receipt.source_positions!.map(row => String(row.label_asym_id ?? '')))]]))}
                retainedTasks={Object.entries(nativeDraft.inactive).filter(([key]) => { try { const parts = JSON.parse(key); return parts[0] === model && parts[1] === mode && parts[2] === contract && parts[3] !== 'recipe'; } catch { return false; } }).map(([key, value]) => ({ key, label: String(record(value.input).kind ?? 'native request') }))}
                onClearSelection={() => setVisual(undefined)}
                visual={currentVisual ? { slot: currentVisual.slot, identities: currentVisual.identities, receipt: currentVisual.receipt, ...visualResult } : undefined} /> : <ComplexaTaskWorkspace request={request} schema={schema} onChange={changeRequest} onTask={task => patch('task', task)} renderNative={renderField} renderSource={renderSource} sourceNames={Object.keys(nativeDraft.sources ?? {})}
                controlDraft={record(controlDrafts[nativeTaskKey(model, mode, contract, request)])} onControlDraft={value => setControlDrafts(previous => ({ ...previous, [nativeTaskKey(model, mode, contract, request)]: value }))}
                visual={currentVisual ? { slot: currentVisual.slot, ...buildComplexaNativeSelection(currentVisual.identities, { ...currentVisual.receipt, documentId: currentVisual.receipt.path }) } : undefined} />)}
            {<NativeSettingsDisclosure summary={<span>Input structures</span>}>{() => <DeNovoNativeSources onInspection={(slot, receipt, binding) => setInspections(previous => ({ ...previous, [slot]: { receipt, binding: JSON.stringify(binding) } }))} selection={currentVisual} value={nativeDraft.sources} onChange={sources => setNativeDraft(previous => ({ ...previous, sources }))}
                onSelection={selectSource} />}</NativeSettingsDisclosure>}
            {!!inventory.data?.limitations?.length && <NativeSettingsDisclosure summary={<span>Model limits</span>}>{() => inventory.data?.limitations?.map(text => <p key={text} className="text-sm">{text}</p>)}</NativeSettingsDisclosure>}
            <DeNovoNativePreview request={payload as Partial<Job>} />
        </ProteinDesignPanel></div>
        <div hidden={section !== 'Native settings'}><ProteinDesignPanel title="Native settings" description="Unchanged settings use the selected profile.">
            <NativeProfileSnapshot model={model} mode={activeMode} request={request} discovery={record(inventory.data).native_profile_defaults} generation={readbackGeneration.current.value} onReadback={(generation, effective) => setEffectiveReadback(effective ? { generation, effective } : undefined)} />
            {schema && model === 'rfd4_proteina' && <Rfd4ExpertSettings discovery={record(inventory.data).native_profile_defaults} effectiveConfig={effectiveReadback?.generation === readbackGeneration.current.value ? effectiveReadback.effective : undefined} request={request} schema={schema} onChange={changeRequest} fileSlots={Object.fromEntries(Object.entries(nativeDraft.sources ?? {}).filter(([, value]) => 'kind' in value && value.kind === 'file').map(([key, value]) => [key, value.path ?? '']))} />}
            {schema && model === 'complexa' && Object.entries(schema.properties ?? {}).filter(([key]) => !primary.has(key)).map(([key, child]) => <NativeSettingsDisclosure key={key} summary={<span>{child.title ?? key.replaceAll('_', ' ')}</span>}>
                {() => renderField(key, child)}
            </NativeSettingsDisclosure>)}
        </ProteinDesignPanel></div>
        <div hidden={section !== 'Optional operations'}><GeneralSequenceDesignSettings value={sequence} onChange={setSequence} /></div>
        <ProteinDesignRun jobName={name} onJobNameChange={setName} pending={submit.isPending} onSubmit={() => submit.mutate()} submitLabel={model === 'complexa' && request.operation === 'design' ? 'Generate, filter, evaluate and analyze' : model === 'complexa' && request.operation === 'analysis' ? 'Evaluate and analyze' : activeMode === 'evaluate' ? 'Evaluate candidates' : 'Generate candidates'}>
            <ExecutionTargetPicker workflowRequest={payload as Partial<Job>} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
            {runDetails}{submit.error && <p role="alert">{submit.error.message}</p>}
        </ProteinDesignRun>
    </div></BioXpSchemaControlContext.Provider>;
}
