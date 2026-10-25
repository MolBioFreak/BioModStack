import { useEffect, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, submitJob, completeCurrentLaunchContext, EXECUTION_TARGET_STORAGE_KEY, type Job, type NativeGenerationSourceBindings } from '../lib/api';
import { FileBrowser } from './FileBrowser';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { DeNovoNativePreview } from './DeNovoNativePreview';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';

const object = (value: unknown): Record<string, any> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {};

/** Separate retained-output evaluation, never implicit in generation. */
export function LaProteinaEvaluation({ initialValues = {}, onDraftChange, runDetails }: {
    initialValues?: Record<string, unknown>; onDraftChange: (draft: Record<string, unknown>) => void; runDetails?: ReactNode;
}) {
    const [request, setRequest] = useState(() => object(initialValues.laproteina_evaluation_request));
    const [sources, setSources] = useState<NativeGenerationSourceBindings>(() => object(initialValues.native_sources));
    const [name, setName] = useState(String(initialValues.job_name ?? 'La-Proteina evaluation'));
    const [picking, setPicking] = useState<'samples_slot' | 'config_slot'>();
    const navigate = useNavigate(); const client = useQueryClient();
    const inventory = useQuery({ queryKey: ['laproteina-evaluation-schema'], retry: false,
        queryFn: async () => (await api.get<{ parameter_schema: Schema }>('/api/models/laproteina/generation-settings?mode=evaluate')).data });
    const payload = { name, model_id: 'laproteina', mode: 'evaluate',
        ...(Object.hasOwn(initialValues, 'pinned_gpu') ? { pinned_gpu: initialValues.pinned_gpu } : {}),
        params: { laproteina_evaluation_request: request, native_sources: sources } };
    const draft = JSON.stringify({ ...initialValues, job_name: name, laproteina_evaluation_request: request,
        native_sources: sources, native_job_request: payload });
    useEffect(() => { onDraftChange(JSON.parse(draft)); }, [draft, onDraftChange]);
    const submit = useMutation({ mutationFn: () => submitJob({ ...payload,
        execution_target_id: sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || null } as Partial<Job>),
        onSuccess: async response => { client.invalidateQueries({ queryKey: ['jobs'] }); navigate(await completeCurrentLaunchContext(response.data) ?? '/'); } });
    const metrics = object(request.metrics);
    const setMetric = (key: string, value: unknown) => setRequest(previous => {
        const next = { ...object(previous.metrics) }; if (value === undefined) delete next[key]; else next[key] = value;
        return { ...previous, metrics: next };
    });
    return <section aria-label="La-Proteina retained-output evaluation" className="space-y-4">
        <h2>Evaluate retained La-Proteina outputs</h2>
        <p>This is a separate operation. It does not generate samples. Select the original native sample directory (including motif tables) and composed generation config. Originals are retained unchanged; evaluation writes into a private copy.</p>
        <p>Omitted metrics inherit that config. An empty evaluation is not evidence of successful scientific metrics. Only the installed ESMFold implementation is offered; its companion assets must be available at execution.</p>
        {(['samples_slot', 'config_slot'] as const).map(key => <div key={key}>
            <button type="button" onClick={() => setPicking(key)}>{key === 'samples_slot' ? 'Choose retained samples' : 'Choose original config'}</button>
            <span>{sources[request[key]]?.path ?? 'Not selected'}</span>
        </div>)}
        {picking && <FileBrowser title={picking === 'samples_slot' ? 'Retained native samples' : 'Original generation config'}
            allowDirectory={picking === 'samples_slot'} accept={picking === 'config_slot' ? '.yaml,.yml' : undefined}
            onCancel={() => setPicking(undefined)} onSelect={path => {
                const slot = request[picking] ?? (picking === 'samples_slot' ? 'retained_samples' : 'original_config');
                setSources(previous => ({ ...previous, [slot]: { kind: 'file', path } }));
                setRequest(previous => ({ ...previous, [picking]: slot })); setPicking(undefined);
            }} />}
        {(['job_id', 'ncpus'] as const).map(key => <label key={key}>{key === 'job_id' ? 'Native generation job ID' : 'Evaluation CPUs'}
            <input aria-label={key} type="number" step="1" value={request[key] ?? ''} onChange={event => {
                const value = event.target.value; setRequest(previous => { const next = { ...previous }; if (value === '') delete next[key]; else next[key] = Number(value); return next; });
            }} /></label>)}
        <fieldset><legend>Native metric overrides</legend>
            {Object.entries(inventory.data?.parameter_schema.properties?.metrics.properties ?? {}).map(([key, schema]) => {
                const present = Object.hasOwn(metrics, key);
                return <fieldset key={key}><legend>{key.replaceAll('_', ' ')}</legend>
                    {schema.type === 'boolean' ? <select aria-label={key} value={present ? String(metrics[key]) : 'inherit'} onChange={event => setMetric(key, event.target.value === 'inherit' ? undefined : event.target.value === 'true')}>
                        <option value="inherit">Inherit original config</option><option value="true">Enabled</option><option value="false">Disabled</option>
                    </select> : <><label><input type="checkbox" aria-label={`Override ${key}`} checked={present} onChange={event => setMetric(key, event.target.checked ? [] : undefined)} />Override inherited list</label>
                        {present && (schema.items as Schema)?.enum?.map(option => <label key={String(option)}><input type="checkbox" aria-label={`${key}: ${option}`} checked={Array.isArray(metrics[key]) && metrics[key].includes(option)}
                            onChange={event => setMetric(key, event.target.checked ? [...(metrics[key] ?? []), option] : metrics[key].filter((item: unknown) => item !== option))} />{String(option)}</label>)}
                        {present && <p>An empty checked list is sent explicitly.</p>}</>}
                </fieldset>;
            })}
        </fieldset>
        {inventory.isPending && <p role="status">Loading native metric controls…</p>}
        {inventory.error && <p role="alert">{inventory.error.message}. Saved settings are retained.</p>}
        <DeNovoNativePreview request={payload as Partial<Job>} />
        <label>Job name<input aria-label="Evaluation job name" value={name} onChange={event => setName(event.target.value)} /></label>
        <ExecutionTargetPicker />{runDetails}
        <button type="button" disabled={submit.isPending} onClick={() => submit.mutate()}>Run separate evaluation</button>
        {submit.error && <p role="alert">{submit.error.message}</p>}
    </section>;
}
