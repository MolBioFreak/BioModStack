import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
    createDomainWorkflowPlan,
    getDomainWorkflowPlan,
    issuePreparedLaunchContext,
    launchDomainRunGroup,
    listDomainCapabilities,
    listDomainWorkflowPlanRevisions,
    listDomainWorkflowPlans,
    prepareDomainWorkflowPlanRevision,
    projectManagerErrorMessage,
    proteinWorkspaceHref,
    publishDomainWorkflowPlanRevision,
    replaceDomainWorkflowPlanDraft,
    type DomainWorkflowPlanHead,
    type JsonObject,
    type JsonValue,
} from '../../../lib/projectManager';

interface ProteinPlanOperatorProps {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
    domainRevisionId: string;
    inputDatasetRevisionIds: string[];
}

export interface SchemaField {
    name: string;
    title: string;
    type: 'string' | 'integer' | 'number' | 'boolean';
    required: boolean;
    readOnly: boolean;
    enumValues: string[] | null;
    defaultValue?: JsonValue;
    constValue?: JsonValue;
    minimum?: number;
    maximum?: number;
    minLength?: number;
    maxLength?: number;
    pattern?: string;
}

const INPUT = 'w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content disabled:cursor-not-allowed disabled:opacity-60';
const BUTTON = 'rounded-lg border border-accent px-3 py-2 text-xs font-semibold text-accent disabled:cursor-not-allowed disabled:opacity-50';

function isObject(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function parseSchema(plan: DomainWorkflowPlanHead | undefined): { fields: SchemaField[]; error: string | null } {
    if (!plan) return { fields: [], error: null };
    const schema = plan.capability_contract?.parameter_schema;
    if (!isObject(schema) || schema.type !== 'object' || schema.additionalProperties !== false || !isObject(schema.properties)) {
        return { fields: [], error: 'The pinned capability does not contain a closed parameter schema.' };
    }
    const required = new Set(Array.isArray(schema.required) ? schema.required.filter((value): value is string => typeof value === 'string') : []);
    const fields: SchemaField[] = [];
    for (const [name, rawField] of Object.entries(schema.properties)) {
        if (!isObject(rawField) || !['string', 'integer', 'number', 'boolean'].includes(String(rawField.type))) {
            return { fields: [], error: `The pinned schema field “${name}” has no supported typed control.` };
        }
        const enumValues = Array.isArray(rawField.enum) && rawField.enum.every((value) => typeof value === 'string')
            ? rawField.enum as string[]
            : null;
        if (typeof rawField.pattern === 'string') {
            try { new RegExp(rawField.pattern); } catch { return { fields: [], error: `The pinned schema field “${name}” has an invalid pattern.` }; }
        }
        fields.push({
            name,
            title: typeof rawField.title === 'string' ? rawField.title : name,
            type: rawField.type as SchemaField['type'],
            required: required.has(name),
            readOnly: rawField['x-bms-ui-control'] === 'read_only' || rawField.const !== undefined,
            enumValues,
            defaultValue: rawField.default as JsonValue | undefined,
            constValue: rawField.const as JsonValue | undefined,
            minimum: typeof rawField.minimum === 'number' ? rawField.minimum : undefined,
            maximum: typeof rawField.maximum === 'number' ? rawField.maximum : undefined,
            minLength: typeof rawField.minLength === 'number' ? rawField.minLength : undefined,
            maxLength: typeof rawField.maxLength === 'number' ? rawField.maxLength : undefined,
            pattern: typeof rawField.pattern === 'string' ? rawField.pattern : undefined,
        });
    }
    return { fields, error: null };
}

function initialValues(plan: DomainWorkflowPlanHead | undefined, fields: SchemaField[]): Record<string, JsonValue> {
    const draftParameters = isObject(plan?.draft) && isObject(plan?.draft?.parameters) ? plan.draft.parameters as Record<string, JsonValue> : {};
    const values: Record<string, JsonValue> = { ...draftParameters };
    for (const field of fields) {
        if (values[field.name] !== undefined) continue;
        if (field.constValue !== undefined) values[field.name] = field.constValue;
        else if (field.defaultValue !== undefined) values[field.name] = field.defaultValue;
    }
    return values;
}

export function fieldError(field: SchemaField, value: JsonValue | undefined): string | null {
    if (value === undefined || value === null) return field.required ? `${field.title} is required.` : null;
    if (value === '') {
        if (field.type === 'string' && field.enumValues?.includes('')) return null;
        return field.required ? `${field.title} is required.` : null;
    }
    if (field.type === 'boolean' && typeof value !== 'boolean') return `${field.title} must be true or false.`;
    if ((field.type === 'integer' || field.type === 'number') && typeof value !== 'number') return `${field.title} must be numeric.`;
    if (field.type === 'integer' && !Number.isInteger(value)) return `${field.title} must be an integer.`;
    if (typeof value === 'number' && field.minimum !== undefined && value < field.minimum) return `${field.title} must be at least ${field.minimum}.`;
    if (typeof value === 'number' && field.maximum !== undefined && value > field.maximum) return `${field.title} must be at most ${field.maximum}.`;
    if (field.type === 'string' && typeof value !== 'string') return `${field.title} must be text.`;
    if (typeof value === 'string' && field.minLength !== undefined && value.length < field.minLength) return `${field.title} is too short.`;
    if (typeof value === 'string' && field.maxLength !== undefined && value.length > field.maxLength) return `${field.title} is too long.`;
    if (typeof value === 'string' && field.pattern && !(new RegExp(field.pattern).test(value))) return `${field.title} does not match the pinned format.`;
    if (field.enumValues && !field.enumValues.includes(String(value))) return `${field.title} is not an advertised option.`;
    return null;
}

function SettingControl({ field, value, onChange }: { field: SchemaField; value: JsonValue | undefined; onChange: (value: JsonValue) => void }) {
    if (field.type === 'boolean') {
        return <label className="flex items-center gap-3 rounded-lg border border-border-primary bg-surface p-3 text-sm text-content-secondary"><input type="checkbox" checked={value === true} disabled={field.readOnly} onChange={(event) => onChange(event.target.checked)} /><span>{field.title}</span>{field.readOnly && <span className="ml-auto text-[10px] text-content-muted">Pinned</span>}</label>;
    }
    if (field.enumValues) {
        return <label className="block text-xs font-semibold text-content-secondary">{field.title}<select className={`${INPUT} mt-1`} value={typeof value === 'string' ? value : ''} disabled={field.readOnly} onChange={(event) => onChange(event.target.value)}>{field.enumValues.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>;
    }
    return <label className="block text-xs font-semibold text-content-secondary">{field.title}<input className={`${INPUT} mt-1 font-mono`} type={field.type === 'integer' || field.type === 'number' ? 'number' : 'text'} min={field.minimum} max={field.maximum} value={typeof value === 'string' || typeof value === 'number' ? value : ''} readOnly={field.readOnly} onChange={(event) => onChange(field.type === 'integer' || field.type === 'number' ? Number(event.target.value) : event.target.value)} /></label>;
}

export function ProteinPlanOperator({ projectId, globalExperimentId, domainExperimentId, domainRevisionId, inputDatasetRevisionIds }: ProteinPlanOperatorProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const scopeKey = [projectId, globalExperimentId, domainExperimentId];
    const [selectedPlanId, setSelectedPlanId] = useState('');
    const [selectedRevisionId, setSelectedRevisionId] = useState('');
    const [planName, setPlanName] = useState('');
    const [capabilityId, setCapabilityId] = useState('');
    const [changeSummary, setChangeSummary] = useState('');
    const [values, setValues] = useState<Record<string, JsonValue>>({});
    const [preparation, setPreparation] = useState<Awaited<ReturnType<typeof prepareDomainWorkflowPlanRevision>> | null>(null);

    useEffect(() => {
        setSelectedPlanId('');
        setSelectedRevisionId('');
        setPlanName('');
        setCapabilityId('');
        setChangeSummary('');
        setValues({});
        setPreparation(null);
    }, [projectId, globalExperimentId, domainExperimentId, domainRevisionId]);

    const capabilities = useQuery({ queryKey: ['protein-project', ...scopeKey, 'capabilities'], queryFn: ({ signal }) => listDomainCapabilities(projectId, globalExperimentId, domainExperimentId, signal), retry: false });
    const plans = useQuery({ queryKey: ['protein-project', ...scopeKey, 'plans'], queryFn: ({ signal }) => listDomainWorkflowPlans(projectId, globalExperimentId, domainExperimentId, signal), retry: false });
    const plan = useQuery({ queryKey: ['protein-project', ...scopeKey, 'plan', selectedPlanId], queryFn: ({ signal }) => getDomainWorkflowPlan(projectId, globalExperimentId, domainExperimentId, selectedPlanId, signal), enabled: Boolean(selectedPlanId), retry: false });
    const revisions = useQuery({ queryKey: ['protein-project', ...scopeKey, 'plan', selectedPlanId, 'revisions'], queryFn: ({ signal }) => listDomainWorkflowPlanRevisions(projectId, globalExperimentId, domainExperimentId, selectedPlanId, signal), enabled: Boolean(selectedPlanId), retry: false });
    const parsedSchema = useMemo(() => parseSchema(plan.data), [plan.data]);

    useEffect(() => {
        setValues(initialValues(plan.data, parsedSchema.fields));
        setSelectedRevisionId(plan.data?.current_revision_id ?? '');
        setPreparation(null);
    }, [plan.data?.plan_id, plan.data?.updated_at, parsedSchema.fields]);

    const validationErrors = parsedSchema.fields.map((field) => fieldError(field, values[field.name])).filter((value): value is string => Boolean(value));
    const mutationBlocker = !domainRevisionId
        ? 'The exact current Protein Domain revision ID is unavailable.'
        : capabilities.isError
            ? `Capability authority is unavailable: ${projectManagerErrorMessage(capabilities.error)}`
            : null;

    const createPlan = useMutation({
        mutationFn: () => createDomainWorkflowPlan(projectId, globalExperimentId, domainExperimentId, { name: planName.trim(), capability_id: capabilityId, expected_domain_revision_id: domainRevisionId }),
        onSuccess: async (created) => { setSelectedPlanId(created.plan_id); setPlanName(''); await queryClient.invalidateQueries({ queryKey: ['protein-project', ...scopeKey, 'plans'] }); },
    });
    const saveDraft = useMutation({
        mutationFn: async () => {
            if (!plan.data || plan.data.draft_generation === null) throw new Error('The selected Plan has no mutable draft generation.');
            const currentDraft = isObject(plan.data.draft) ? plan.data.draft as JsonObject : {};
            const currentScheduler = isObject(currentDraft.scheduler) ? currentDraft.scheduler as JsonObject : null;
            if (!currentScheduler || typeof currentScheduler.model_id !== 'string' || typeof currentScheduler.mode !== 'string') {
                throw new Error('The selected Plan has no exact server-owned scheduler authority.');
            }
            const adapterId = plan.data.capability_contract.capability.workflow_adapter_id;
            return replaceDomainWorkflowPlanDraft(projectId, globalExperimentId, domainExperimentId, plan.data.plan_id, plan.data.draft_generation, {
                ...currentDraft,
                parameters: values,
                scheduler: {
                    ...currentScheduler,
                    params: { ...values, workflow_adapter: adapterId },
                },
            });
        },
        onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['protein-project', ...scopeKey, 'plan', selectedPlanId] }); },
    });
    const publish = useMutation({
        mutationFn: async () => {
            if (!plan.data || plan.data.draft_generation === null) throw new Error('The selected Plan has no publishable draft generation.');
            if (validationErrors.length) throw new Error(validationErrors[0]);
            await saveDraft.mutateAsync();
            const refreshed = await getDomainWorkflowPlan(projectId, globalExperimentId, domainExperimentId, plan.data.plan_id);
            if (refreshed.draft_generation === null) throw new Error('The saved draft generation is unavailable.');
            return publishDomainWorkflowPlanRevision(projectId, globalExperimentId, domainExperimentId, plan.data.plan_id, { expected_head_generation: refreshed.head_generation, expected_draft_generation: refreshed.draft_generation, change_summary: changeSummary.trim() });
        },
        onSuccess: async (revision) => { setSelectedRevisionId(revision.revision_id); setChangeSummary(''); await queryClient.invalidateQueries({ queryKey: ['protein-project', ...scopeKey, 'plan', selectedPlanId] }); await queryClient.invalidateQueries({ queryKey: ['protein-project', ...scopeKey, 'plan', selectedPlanId, 'revisions'] }); },
    });
    const prepare = useMutation({
        mutationFn: () => prepareDomainWorkflowPlanRevision(projectId, globalExperimentId, domainExperimentId, selectedPlanId, selectedRevisionId, inputDatasetRevisionIds),
        onSuccess: setPreparation,
    });
    const openNativeSetup = useMutation({
        mutationFn: async () => {
            if (!preparation || !plan.data) throw new Error('Prepare an immutable Plan revision first.');
            const pinned = plan.data.capability_contract.capability;
            if (pinned.launch_mode !== 'typed_launcher_handoff') throw new Error('This capability does not advertise a typed native handoff.');
            if (!pinned.canonical_source_destination.startsWith('/') || pinned.canonical_source_destination.startsWith('//')) throw new Error('The pinned native destination is not a safe local route.');
            const returnUri = proteinWorkspaceHref(projectId, globalExperimentId, domainExperimentId, 'runs');
            const context = await issuePreparedLaunchContext(projectId, globalExperimentId, domainExperimentId, preparation.preparation_id, returnUri);
            if (context.project_id !== projectId || context.global_experiment_id !== globalExperimentId || context.domain_experiment_id !== domainExperimentId || context.workflow_id !== selectedPlanId || context.workflow_revision_id !== selectedRevisionId || context.preparation_id !== preparation.preparation_id || context.normalized_request_sha256 !== preparation.normalized_request_sha256) throw new Error('The prepared launch context does not match the selected Protein Plan authority.');
            const runGroup = await launchDomainRunGroup(projectId, globalExperimentId, domainExperimentId, [{ preparation_id: preparation.preparation_id, launch_context_id: context.launch_context_id }]);
            const attempt = runGroup.runs.find((run) => run.preparation_id === preparation.preparation_id)?.attempts.at(-1);
            if (!attempt || attempt.launch_context?.launch_context_id !== context.launch_context_id || attempt.state !== 'pending') {
                throw new Error('The Project Run Group did not reserve the exact prepared native handoff.');
            }
            const destination = new URL(pinned.canonical_source_destination, window.location.origin);
            destination.searchParams.set('launch_context_id', context.launch_context_id);
            return `${destination.pathname}${destination.search}`;
        },
        onSuccess: (destination) => navigate(destination),
    });

    const error = createPlan.error ?? saveDraft.error ?? publish.error ?? prepare.error ?? openNativeSetup.error ?? plan.error ?? revisions.error ?? plans.error;
    return <div className="space-y-4">
        {error && <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(error)}</p>}
        {mutationBlocker && <p className="rounded-lg border border-warning/50 bg-warning/10 p-3 text-xs text-warning">{mutationBlocker}</p>}
        <section className="rounded-xl border border-border-primary bg-surface-secondary p-4">
            <h2 className="text-sm font-semibold text-content">Create a Protein Workflow Plan</h2>
            <p className="mt-1 text-xs text-content-muted">Plan choices come from the exact Domain capability route, not the Project capability catalogue. Arbitrary capability IDs and raw JSON are not accepted.</p>
            <div className="mt-3 grid gap-3 md:grid-cols-[1fr_1fr_auto]">
                <input className={INPUT} value={planName} onChange={(event) => setPlanName(event.target.value)} placeholder="Plan name" />
                <select className={INPUT} value={capabilityId} onChange={(event) => setCapabilityId(event.target.value)}><option value="">Select an accepted Protein capability</option>{(capabilities.data?.items ?? []).map((capability) => <option key={capability.capability_id} value={capability.capability_id}>{capability.label}</option>)}</select>
                <button className={BUTTON} type="button" disabled={Boolean(mutationBlocker) || !planName.trim() || !capabilityId || createPlan.isPending} onClick={() => createPlan.mutate()}>Create Plan</button>
            </div>
            {!capabilities.isLoading && !capabilities.isError && (capabilities.data?.items ?? []).length === 0 && <p className="mt-3 text-xs text-warning">No accepted capability is advertised for the exact Protein experiment mode. Plan creation is disabled.</p>}
        </section>

        <section className="rounded-xl border border-border-primary bg-surface-secondary p-4">
            <label className="block text-xs font-semibold text-content-secondary">Workflow Plan<select className={`${INPUT} mt-1`} value={selectedPlanId} onChange={(event) => setSelectedPlanId(event.target.value)}><option value="">Select a Plan</option>{(plans.data?.items ?? []).map((item) => <option key={item.plan_id} value={item.plan_id}>{item.name} · {item.capability_id}</option>)}</select></label>
            {plan.data && <div className="mt-4 space-y-4">
                <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4"><div><dt className="text-content-muted">Plan ID</dt><dd className="break-all font-mono text-content">{plan.data.plan_id}</dd></div><div><dt className="text-content-muted">Domain revision</dt><dd className="break-all font-mono text-content">{plan.data.domain_revision_id ?? 'Unavailable'}</dd></div><div><dt className="text-content-muted">Capability</dt><dd className="break-all font-mono text-content">{plan.data.capability_id}</dd></div><div><dt className="text-content-muted">Contract digest</dt><dd className="break-all font-mono text-content">{plan.data.capability_contract_sha256}</dd></div></dl>
                {parsedSchema.error ? <p className="rounded-lg border border-warning/50 bg-warning/10 p-3 text-xs text-warning">Typed settings are disabled: {parsedSchema.error}</p> : <div className="grid gap-3 md:grid-cols-2">{parsedSchema.fields.map((field) => <SettingControl key={field.name} field={field} value={values[field.name]} onChange={(value) => setValues((current) => ({ ...current, [field.name]: value }))} />)}</div>}
                {validationErrors.length > 0 && <p className="text-xs text-warning">{validationErrors[0]}</p>}
                <div className="flex flex-wrap gap-2"><button className={BUTTON} type="button" disabled={Boolean(mutationBlocker) || Boolean(parsedSchema.error) || saveDraft.isPending} onClick={() => saveDraft.mutate()}>Save typed settings</button><input className={`${INPUT} max-w-md`} value={changeSummary} onChange={(event) => setChangeSummary(event.target.value)} placeholder="Required publication summary" /><button className={BUTTON} type="button" disabled={Boolean(mutationBlocker) || Boolean(parsedSchema.error) || validationErrors.length > 0 || !changeSummary.trim() || publish.isPending} onClick={() => publish.mutate()}>Publish immutable revision</button></div>
            </div>}
        </section>

        {plan.data && <section className="rounded-xl border border-border-primary bg-surface-secondary p-4">
            <h2 className="text-sm font-semibold text-content">Prepare an immutable revision</h2>
            <div className="mt-3 grid gap-3 md:grid-cols-[1fr_auto]"><select className={INPUT} value={selectedRevisionId} onChange={(event) => { setSelectedRevisionId(event.target.value); setPreparation(null); }}><option value="">Select an immutable revision</option>{(revisions.data?.items ?? []).map((revision) => <option key={revision.revision_id} value={revision.revision_id}>Revision {revision.revision_number} · {revision.revision_id}</option>)}</select><button className={BUTTON} type="button" disabled={!selectedRevisionId || prepare.isPending} onClick={() => prepare.mutate()}>Prepare selected revision</button></div>
            <p className="mt-2 text-xs text-content-muted">Exact Dataset revision inputs: {inputDatasetRevisionIds.length ? inputDatasetRevisionIds.join(', ') : 'none selected'}</p>
            {preparation && <div className="mt-4 rounded-lg border border-success/40 bg-success/10 p-3 text-xs text-content-secondary"><p className="font-semibold text-success">Preparation {preparation.status}</p><dl className="mt-2 grid gap-2 md:grid-cols-3"><div><dt>Preparation ID</dt><dd className="break-all font-mono">{preparation.preparation_id}</dd></div><div><dt>Request digest</dt><dd className="break-all font-mono">{preparation.normalized_request_sha256}</dd></div><div><dt>Validation receipt</dt><dd className="break-all font-mono">{preparation.validation_receipt_id}</dd></div><div><dt>Expected outputs</dt><dd>{preparation.expected_cardinality}</dd></div></dl><button className={`${BUTTON} mt-3`} type="button" disabled={preparation.status !== 'valid' || openNativeSetup.isPending} onClick={() => openNativeSetup.mutate()}>Open native Protein setup</button>{preparation.status !== 'valid' && <p className="mt-2 text-warning">Native setup is disabled because the preparation status is not valid.</p>}</div>}
        </section>}
    </div>;
}
