import { useEffect, useState, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchNativeGenerationSettings, submitJob, completeCurrentLaunchContext, EXECUTION_TARGET_STORAGE_KEY, type Job } from '../lib/api';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import { ProteinDesignSections, ProteinDesignPanel, ProteinDesignRun } from './ProteinDesignWorkflow';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { GeneralSequenceDesignSettings } from './GeneralSequenceDesignSettings';
import { generalSequenceRequest, hydrateGeneralSequenceDesign } from '../lib/generalSequenceDesign';

const record = (value: unknown): Record<string, any> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {};

/** Presentation of the native owner's schema, never a second scientific compiler. */
export function DeNovoNativeGenerationSettings({ model, mode = 'generate', initialValues, onDraftChange, runDetails }: {
    model: 'rfd4_proteina' | 'complexa'; mode?: string; initialValues: Record<string, unknown>;
    onDraftChange: (draft: Record<string, unknown>) => void; runDetails?: ReactNode;
}) {
    const field = model === 'rfd4_proteina' ? 'rfd4_request' : 'complexa_request';
    const [request, setRequest] = useState(() => {
        const saved = record(initialValues[field]);
        // Complexa's mode is selected by its native operation; this is routing,
        // not a sampling default. Never overwrite an explicit saved operation.
        return model === 'complexa' && mode === 'evaluate' && !Object.hasOwn(saved, 'operation') ? { ...saved, operation: 'evaluate' } : saved;
    });
    const [name, setName] = useState(typeof initialValues.job_name === 'string' ? initialValues.job_name : model);
    const [sequence, setSequence] = useState(() => hydrateGeneralSequenceDesign(initialValues));
    const [section, setSection] = useState('Design');
    const navigate = useNavigate(); const client = useQueryClient();
    const inventory = useQuery({ queryKey: ['de-novo-native-settings', model, mode],
        queryFn: async () => (await fetchNativeGenerationSettings(model, mode)).data,
        staleTime: 60_000, retry: false });
    const schema = inventory.data?.parameter_schema ?? inventory.data?.schema;
    const payload = { ...generalSequenceRequest(sequence), name, model_id: model, mode,
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        ...(Object.hasOwn(initialValues, 'native_sources') ? { native_sources: initialValues.native_sources } : {}),
        params: { [field]: request } };
    const draft = JSON.stringify({ ...sequence, job_name: name, [field]: request, native_mode: mode,
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        ...(Object.hasOwn(initialValues, 'native_sources') ? { native_sources: initialValues.native_sources } : {}),
        native_contract: inventory.data?.contract ?? schema?.$id,
        native_job_request: payload });
    useEffect(() => { onDraftChange(JSON.parse(draft)); }, [draft, onDraftChange]);
    const submit = useMutation({ mutationFn: () => submitJob({ ...payload,
        execution_target_id: sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || null } as Partial<Job>),
        onSuccess: async response => { client.invalidateQueries({ queryKey: ['jobs'] }); navigate(await completeCurrentLaunchContext(response.data) ?? '/'); } });
    const patch = (key: string, value: unknown) => setRequest(previous => {
        const next = { ...previous }; if (value === undefined) delete next[key]; else next[key] = value; return next;
    });
    const renderField = (key: string, child: Schema) => <BioXpSchemaInput label={key}
        schema={{ type: 'object', properties: { [key]: child }, additionalProperties: false, required: schema?.required?.includes(key) ? [key] : [] }} rootSchema={schema}
        value={Object.hasOwn(request, key) ? { [key]: request[key] } : {}}
        onChange={value => patch(key, record(value)[key])}
        fallback={() => <p>Saved native value retained; this schema does not describe an editable value.</p>} />;
    const primary = new Set(['version', 'mode', 'operation', 'task', 'input', 'inputs', 'profile', 'sampling', 'seed', 'num_samples']);
    return <div className="space-y-4" data-native-de-novo={model}>
        <ProteinDesignSections label="Generation sections" sections={['Design', 'Native settings', 'Sequence Design']} active={section} onChange={setSection} />
        {inventory.isPending && <p role="status">Loading model-owned settings…</p>}
        {inventory.error && <p role="alert">{inventory.error.message}. Saved native values are retained.</p>}
        <div hidden={section !== 'Design'}><ProteinDesignPanel title="Design" description="Native requests retain omitted settings separately from explicit values. Source slots refer to exact retained native documents, not PDB conversions.">
            {schema && Object.entries(schema.properties ?? {}).filter(([key]) => primary.has(key)).map(([key, child]) => <div key={key}>{renderField(key, child)}</div>)}
            {inventory.data?.limitations?.map(text => <p key={text} className="text-sm">{text}</p>)}
        </ProteinDesignPanel></div>
        <div hidden={section !== 'Native settings'}><ProteinDesignPanel title="Native settings" description="Requested overrides only. Omitted settings inherit the selected native profile at the model owner. Oracle template conditioning is not blind prediction.">
            {schema && Object.entries(schema.properties ?? {}).filter(([key]) => !primary.has(key)).map(([key, child]) => <NativeSettingsDisclosure key={key} summary={<span>{child.title ?? key.replaceAll('_', ' ')}</span>}>
                {() => renderField(key, child)}
            </NativeSettingsDisclosure>)}
        </ProteinDesignPanel></div>
        <div hidden={section !== 'Sequence Design'}><GeneralSequenceDesignSettings value={sequence} onChange={setSequence} /></div>
        <ProteinDesignRun jobName={name} onJobNameChange={setName} pending={submit.isPending} onSubmit={() => submit.mutate()} submitLabel={mode === 'evaluate' ? 'Evaluate candidates' : 'Generate candidates'}>
            <ExecutionTargetPicker workflowRequest={payload as Partial<Job>} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
            {runDetails}{submit.error && <p role="alert">{submit.error.message}</p>}
        </ProteinDesignRun>
    </div>;
}
