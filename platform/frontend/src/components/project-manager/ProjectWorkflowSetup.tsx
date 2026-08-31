import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
    createProjectWorkflowSetup,
    deleteProjectWorkflowSetup,
    getProjectWorkflowSetup,
    listProteinProjectCapabilities,
    prepareProjectWorkflowSetup,
    projectManagerErrorMessage,
    saveProjectWorkflowSetupDraft,
    type JsonObject,
    type ProjectWorkflowSetup,
} from '../../lib/projectManager';

const INPUT = 'w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content';
const BUTTON = 'rounded-lg border border-accent px-3 py-2 text-xs font-semibold text-accent disabled:cursor-not-allowed disabled:opacity-50';

function nativeDestination(setup: ProjectWorkflowSetup): string {
    const destination = new URL(setup.setup_destination, window.location.origin);
    if (destination.origin !== window.location.origin) throw new Error('The setup destination is not local to BioModStack.');
    destination.searchParams.set('setup_context_id', setup.setup_context_id);
    destination.searchParams.set('project_id', setup.project_id);
    return `${destination.pathname}${destination.search}`;
}

export function NewProjectExperimentDialog({ projectId, globalExperimentId, open, onClose }: { projectId: string; globalExperimentId?: string; open: boolean; onClose: () => void }) {
    const navigate = useNavigate();
    const [name, setName] = useState('');
    const [objective, setObjective] = useState('');
    const [capabilityId, setCapabilityId] = useState('');
    const capabilities = useQuery({ queryKey: ['protein-project-setup-capabilities'], queryFn: ({ signal }) => listProteinProjectCapabilities(signal), enabled: open, retry: false });
    const ready = useMemo(() => (capabilities.data?.capabilities ?? []).filter((item) => item.project_setup_state === 'ready'), [capabilities.data]);
    const create = useMutation({
        mutationFn: () => createProjectWorkflowSetup(projectId, {
            schema: 'bms.project-workflow-setup.create.v1',
            context: globalExperimentId ? 'follow_up' : 'primary',
            ...(globalExperimentId ? { global_experiment_id: globalExperimentId } : {}),
            name: globalExperimentId ? name.trim() || 'Related workflow' : name.trim(),
            objective: objective.trim(),
            domain: 'protein',
            capability_id: capabilityId,
        }),
        onSuccess: (setup) => navigate(nativeDestination(setup)),
    });
    if (!open) return null;
    const valid = Boolean(capabilityId) && (Boolean(globalExperimentId) || (Boolean(name.trim()) && Boolean(objective.trim())));
    return <div role="dialog" aria-modal="true" aria-label={globalExperimentId ? 'Add workflow' : 'New experiment'} className="fixed inset-0 z-[80] grid place-items-center bg-black/60 p-4">
        <section className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-2xl border border-border-primary bg-surface-secondary p-5 shadow-2xl">
            <div className="flex justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-accent">{globalExperimentId ? 'Related follow-up' : 'New experiment'}</p><h2 className="mt-1 text-lg font-semibold text-content">{globalExperimentId ? 'Add workflow' : 'What do you want to do?'}</h2></div><button type="button" onClick={onClose} aria-label="Close">×</button></div>
            {!globalExperimentId && <div className="mt-4 grid gap-3"><label className="text-xs font-semibold text-content-secondary">Experiment name<input aria-label="Experiment name" className={`${INPUT} mt-1`} value={name} onChange={(event) => setName(event.target.value)}/></label><label className="text-xs font-semibold text-content-secondary">Short objective<input aria-label="Short objective" className={`${INPUT} mt-1`} value={objective} onChange={(event) => setObjective(event.target.value)}/></label></div>}
            <div className="mt-4"><p className="text-xs font-semibold text-content-secondary">Domain</p><p className="mt-1 rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content">Protein</p></div>
            <fieldset className="mt-4 grid gap-2"><legend className="mb-2 text-xs font-semibold text-content-secondary">Choose one ready workflow</legend>{ready.map((item) => <label key={item.capability_id} className="flex cursor-pointer gap-3 rounded-xl border border-border-primary bg-surface p-3 text-sm text-content"><input type="radio" name="project-workflow" value={item.capability_id} checked={capabilityId === item.capability_id} onChange={() => setCapabilityId(item.capability_id)}/><span><strong>{item.label}</strong></span></label>)}</fieldset>
            {!capabilities.isLoading && ready.length === 0 && <p role="status" className="mt-3 text-xs text-warning">No Project-ready Protein workflow is currently advertised by the server.</p>}
            {(capabilities.error || create.error) && <p role="alert" className="mt-3 text-xs text-error">{projectManagerErrorMessage(capabilities.error ?? create.error)}</p>}
            <div className="mt-5 flex justify-end gap-2"><button type="button" className={BUTTON} onClick={onClose}>Cancel</button><button type="button" className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:opacity-50" disabled={!valid || create.isPending} onClick={() => create.mutate()}>Continue to setup</button></div>
        </section>
    </div>;
}

export function ProjectWorkflowCard({ projectId, setup }: { projectId: string; setup: ProjectWorkflowSetup }) {
    const queryClient = useQueryClient();
    const remove = useMutation({ mutationFn: () => deleteProjectWorkflowSetup(projectId, setup.setup_context_id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project-workflow-setups', projectId] }) });
    return <article className="rounded-xl border border-border-primary bg-surface-secondary p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[.16em] text-accent">Workflow</p><h3 className="mt-1 font-semibold text-content">{setup.workflow_label}</h3></div><span className="rounded-full border border-warning/40 px-2 py-1 text-xs text-warning">{setup.state === 'draft' ? 'Setup incomplete' : setup.state}</span></div><div className="mt-4 flex flex-wrap gap-2"><Link className={BUTTON} to={nativeDestination(setup)}>Resume setup</Link><button type="button" className={BUTTON} disabled={remove.isPending} onClick={() => remove.mutate()}>Delete draft</button></div>{remove.error && <p role="alert" className="mt-2 text-xs text-error">{projectManagerErrorMessage(remove.error)}</p>}</article>;
}

export function ProjectTechnicalDetails({ setup, children }: { setup: ProjectWorkflowSetup; children?: React.ReactNode }) {
    const [open, setOpen] = useState(false);
    return <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="rounded-xl border border-border-primary bg-surface-secondary p-4"><summary className="cursor-pointer text-sm font-semibold text-content">Technical details / Diagnostics</summary>{open && <div className="mt-3 space-y-3 text-xs text-content-secondary">{children}<pre className="overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3">{JSON.stringify(setup.diagnostics, null, 2)}</pre></div>}</details>;
}

export function ProjectWorkflowSetupBanner({ setup }: { setup: ProjectWorkflowSetup }) {
    return <aside aria-label="Project workflow setup context" className="border-b border-accent/30 bg-accent/10 px-4 py-3"><div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold text-content">{setup.project_label} · {setup.experiment_label}</p><p className="text-xs text-content-secondary">{setup.workflow_label} · {setup.state === 'draft' ? 'Setup incomplete' : setup.state}</p></div><Link className={BUTTON} to={setup.return_uri}>Back to experiment</Link></div>{Object.entries(setup.field_errors).length > 0 && <ul className="mx-auto mt-2 max-w-7xl text-xs text-error">{Object.entries(setup.field_errors).map(([field, message]) => <li key={field}><strong>{field}:</strong> {message}</li>)}</ul>}</aside>;
}

export function useProjectWorkflowSetup() {
    const [searchParams] = useSearchParams();
    const setupContextId = searchParams.get('setup_context_id');
    const projectId = searchParams.get('project_id');
    const active = Boolean(setupContextId && projectId);
    const queryClient = useQueryClient();
    const query = useQuery({ queryKey: ['project-workflow-setup', projectId, setupContextId], queryFn: ({ signal }) => getProjectWorkflowSetup(projectId as string, setupContextId as string, signal), enabled: active, retry: false });
    const setup = query.data;
    const settings = setup?.draft.settings && typeof setup.draft.settings === 'object' && !Array.isArray(setup.draft.settings) ? setup.draft.settings as JsonObject : {};
    const saveDraft = async (exactSettings: JsonObject) => {
        if (!setup) throw new Error('Project workflow setup is not hydrated.');
        const updated = await saveProjectWorkflowSetupDraft(setup.project_id, setup.setup_context_id, { expected_generation: setup.generation, draft: { ...setup.draft, settings: exactSettings } });
        queryClient.setQueryData(['project-workflow-setup', projectId, setupContextId], updated);
        return updated;
    };
    const prepare = async () => {
        if (!setup) throw new Error('Project workflow setup is not hydrated.');
        const updated = await prepareProjectWorkflowSetup(setup.project_id, setup.setup_context_id);
        queryClient.setQueryData(['project-workflow-setup', projectId, setupContextId], updated);
        return updated;
    };
    return { active, setup, settings, isLoading: query.isLoading, error: query.error, saveDraft, prepare };
}
