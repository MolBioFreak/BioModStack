import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
    getDomainExperiment,
    getGlobalExperiment,
    getProject,
    getProjectSummary,
    internalRouteHref,
    projectManagerErrorMessage,
    proteinDomainAuthority,
    proteinWorkspaceHref,
    reverifySourceReceipt,
    reopenDomainResult,
    type JsonObject,
    type JsonValue,
    type ProteinWorkspaceSection,
    type ProjectManagerReadModel,
    type ResultSurface,
} from '../../../lib/projectManager';
import DomainDatasetOperator from '../../molbio-ngs/DomainDatasetOperator';
import { ProteinEvidenceOperator } from './ProteinEvidenceOperator';
import { ProteinPlanOperator } from './ProteinPlanOperator';
import { NewProjectExperimentDialog } from '../ProjectWorkflowSetup';

interface ProteinProjectWorkspaceProps {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
}

const SECTIONS: Array<{ id: ProteinWorkspaceSection; label: string }> = [
    { id: 'overview', label: 'Overview' },
    { id: 'targets', label: 'Targets' },
    { id: 'datasets', label: 'Datasets' },
    { id: 'plans', label: 'Workflows' },
    { id: 'runs', label: 'Runs' },
    { id: 'results', label: 'Results' },
    { id: 'comparisons', label: 'Comparisons' },
    { id: 'evidence', label: 'Evidence / ELN' },
    { id: 'history', label: 'History' },
    { id: 'technical', label: 'Technical details' },
];
const SECTION_IDS = new Set(SECTIONS.map((section) => section.id));
const BUTTON = 'rounded-lg border border-accent px-3 py-2 text-xs font-semibold text-accent disabled:cursor-not-allowed disabled:opacity-50';

function short(value: string | null | undefined): string {
    if (!value) return 'Unavailable';
    return value.length > 28 ? `${value.slice(0, 14)}…${value.slice(-10)}` : value;
}

function textValue(value: JsonValue | undefined): string {
    if (value === null || value === undefined || value === '') return 'Not recorded';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map((item) => typeof item === 'object' ? 'structured value' : String(item)).join(', ') || 'None';
    return 'Structured record';
}

function AuthorityCard({ label, value, full = false }: { label: string; value: string | null | undefined; full?: boolean }) {
    return <div className="rounded-lg border border-border-primary bg-surface p-3"><dt className="text-[10px] font-semibold uppercase tracking-[0.14em] text-content-muted">{label}</dt><dd title={value ?? undefined} className={`mt-1 font-mono text-xs text-content ${full ? 'break-all' : ''}`}>{full ? value ?? 'Unavailable' : short(value)}</dd></div>;
}

function EmptyState({ children }: { children: string }) {
    return <p className="rounded-xl border border-dashed border-border-primary bg-surface-secondary p-6 text-center text-sm text-content-muted">{children}</p>;
}

function RecordCards({ items, empty }: { items: JsonObject[]; empty: string }) {
    if (!items.length) return <EmptyState>{empty}</EmptyState>;
    return <div className="grid gap-3 lg:grid-cols-2">{items.map((item, index) => {
        const id = typeof item.id === 'string' ? item.id : typeof item.resource_id === 'string' ? item.resource_id : typeof item.receipt_id === 'string' ? item.receipt_id : `record-${index + 1}`;
        const visible = Object.entries(item).filter(([, value]) => value === null || ['string', 'number', 'boolean'].includes(typeof value)).slice(0, 8);
        return <article key={`${id}:${index}`} className="rounded-xl border border-border-primary bg-surface-secondary p-4"><h3 className="break-all font-mono text-xs font-semibold text-content">{id}</h3><dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">{visible.map(([key, value]) => <div key={key}><dt className="text-content-muted">{key.replaceAll('_', ' ')}</dt><dd className="break-words text-content-secondary">{textValue(value)}</dd></div>)}</dl></article>;
    })}</div>;
}

function Overview({ summary, authority, onAddWorkflow }: { summary: ProjectManagerReadModel; authority: NonNullable<ReturnType<typeof proteinDomainAuthority>>; onAddWorkflow: () => void }) {
    return <div className="space-y-4"><section className="rounded-xl border border-border-primary bg-surface-secondary p-5"><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Experiment</p><h2 className="mt-2 text-xl font-semibold text-content">{summary.selection.title}</h2><p className="mt-2 text-sm text-content-secondary">{authority.scientific_objective || 'No scientific objective is recorded.'}</p><div className="mt-4 flex flex-wrap gap-2"><span className="rounded-full border border-border-primary px-3 py-1 text-xs text-content-secondary">Protein</span><span className="rounded-full border border-border-primary px-3 py-1 text-xs text-content-secondary">{authority.targets.length} target{authority.targets.length === 1 ? '' : 's'}</span><span className="rounded-full border border-border-primary px-3 py-1 text-xs text-content-secondary">{summary.runs.items.length} Run{summary.runs.items.length === 1 ? '' : 's'}</span></div><button type="button" className={`${BUTTON} mt-4`} onClick={onAddWorkflow}>Add workflow</button></section>{summary.warnings.length > 0 && <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">{summary.warnings.join(' · ')}</p>}</div>;
}

function RunSection({ summary }: { summary: ProjectManagerReadModel }) {
    if (!summary.runs.items.length) return <EmptyState>No Protein runs are projected for this exact Domain.</EmptyState>;
    return <div className="space-y-3">{summary.runs.items.map((run) => <article key={run.run_id} className="rounded-xl border border-border-primary bg-surface-secondary p-4"><div className="flex flex-wrap justify-between gap-3"><div><h3 className="font-semibold text-content">{run.target_label || run.workflow_type}</h3><p className="mt-1 font-mono text-xs text-content-muted">{run.run_id}</p></div><span className="rounded-full border border-border-primary px-2 py-1 text-xs text-content-secondary">{run.normalized_state}</span></div><dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4"><div><dt className="text-content-muted">Workflow</dt><dd className="break-all font-mono text-content-secondary">{run.workflow_id}</dd></div><div><dt className="text-content-muted">Run Group</dt><dd className="break-all font-mono text-content-secondary">{run.batch_or_run_group_id ?? 'Unavailable'}</dd></div><div><dt className="text-content-muted">Canonical Job</dt><dd className="break-all font-mono text-content-secondary">{run.canonical_job_id ?? 'Not issued'}</dd></div><div><dt className="text-content-muted">Outputs</dt><dd className="text-content-secondary">{run.output_count}</dd></div></dl>{run.condition.message && <p className="mt-3 text-xs text-warning">{run.condition.message}</p>}</article>)}</div>;
}

function ResultCard({ surface, onOpen, opening }: { surface: ResultSurface; onOpen: (receiptId: string) => void; opening: boolean }) {
    const blocker = !surface.route ? 'The backend did not issue a canonical reopen route.' : surface.readiness !== 'ready' ? `Result readiness is ${surface.readiness}.` : null;
    return <article className="rounded-xl border border-border-primary bg-surface-secondary p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-content">{surface.surface_kind.replaceAll('_', ' ')}</h3><p className="mt-1 break-all font-mono text-xs text-content-muted">{surface.receipt_id}</p></div><span className="rounded-full border border-border-primary px-2 py-1 text-xs text-content-secondary">{surface.readiness}</span></div><dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><div><dt className="text-content-muted">Contract</dt><dd className="break-all text-content-secondary">{surface.contract_id}</dd></div><div><dt className="text-content-muted">Scientific review</dt><dd className="text-content-secondary">{surface.scientific_acceptance.state}</dd></div></dl><button type="button" className={`${BUTTON} mt-3`} disabled={Boolean(blocker) || opening} onClick={() => onOpen(surface.receipt_id)}>Open canonical result</button>{blocker && <p className="mt-2 text-xs text-warning">{blocker}</p>}</article>;
}

export function ProteinProjectWorkspace({ projectId, globalExperimentId, domainExperimentId }: ProteinProjectWorkspaceProps) {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const requestedSection = searchParams.get('section');
    const section = requestedSection && SECTION_IDS.has(requestedSection as ProteinWorkspaceSection) ? requestedSection as ProteinWorkspaceSection : 'overview';
    const [selectedDatasetRevisionIds, setSelectedDatasetRevisionIds] = useState<string[]>([]);
    const [workflowDialogOpen, setWorkflowDialogOpen] = useState(false);
    const scopeKey = [projectId, globalExperimentId, domainExperimentId];
    useEffect(() => setSelectedDatasetRevisionIds([]), [projectId, globalExperimentId, domainExperimentId]);
    const project = useQuery({ queryKey: ['protein-project', ...scopeKey, 'project'], queryFn: ({ signal }) => getProject(projectId, signal), retry: false });
    const globalExperiment = useQuery({ queryKey: ['protein-project', ...scopeKey, 'global'], queryFn: ({ signal }) => getGlobalExperiment(projectId, globalExperimentId, signal), retry: false });
    const domain = useQuery({ queryKey: ['protein-project', ...scopeKey, 'domain'], queryFn: ({ signal }) => getDomainExperiment(projectId, globalExperimentId, domainExperimentId, signal), retry: false });
    const summary = useQuery({ queryKey: ['protein-project', ...scopeKey, 'summary'], queryFn: ({ signal }) => getProjectSummary(projectId, { focusId: globalExperimentId, selectedNodeKey: `domain_experiment:${domainExperimentId}`, mapLimit: 50, runLimit: 100, resultLimit: 100, lineageLimit: 100, noteLimit: 100, decisionLimit: 100, datasetLimit: 100, activityLimit: 100, signal }), retry: false });
    const reverify = useMutation({
        mutationFn: async () => Promise.all(
            (summary.data?.source_receipt_ids ?? []).map((sourceReceiptId) => reverifySourceReceipt(
                projectId,
                globalExperimentId,
                domainExperimentId,
                sourceReceiptId,
            )),
        ),
        onSuccess: async () => {
            await summary.refetch();
        },
    });

    const projectData = project.data;
    const globalExperimentData = globalExperiment.data;
    const domainData = domain.data;
    const authority = useMemo(() => domainData ? proteinDomainAuthority(domainData) : null, [domainData]);
    const hierarchyValid = projectData?.id === projectId
        && globalExperimentData?.id === globalExperimentId
        && globalExperimentData.parent_id === projectId
        && domainData?.id === domainExperimentId
        && domainData.parent_id === globalExperimentId;

    const reopen = useMutation({
        mutationFn: async (receiptId: string) => {
            const surface = await reopenDomainResult(projectId, globalExperimentId, domainExperimentId, receiptId);
            if (!surface.route) throw new Error('The backend did not issue a canonical result route.');
            const destination = new URL(internalRouteHref(surface.route), window.location.origin);
            if (destination.origin !== window.location.origin) throw new Error('The canonical result route is not local to BioModStack.');
            destination.searchParams.set('project_id', projectId);
            destination.searchParams.set('global_experiment_id', globalExperimentId);
            destination.searchParams.set('domain_experiment_id', domainExperimentId);
            destination.searchParams.set('return_uri', proteinWorkspaceHref(projectId, globalExperimentId, domainExperimentId, 'results'));
            return `${destination.pathname}${destination.search}`;
        },
        onSuccess: (destination) => navigate(destination),
    });

    const activeError = project.error ?? globalExperiment.error ?? domain.error ?? summary.error;
    if (activeError) return <div role="alert" className="grid min-h-[32rem] place-items-center bg-surface p-6"><div className="max-w-xl rounded-xl border border-error/50 bg-error/10 p-5 text-center"><h1 className="font-semibold text-error">Protein workspace unavailable</h1><p className="mt-2 text-sm text-content-secondary">{projectManagerErrorMessage(activeError)}</p><Link className="mt-4 inline-flex text-sm font-semibold text-accent" to={`/projects/${encodeURIComponent(projectId)}`}>Return to Project Manager</Link></div></div>;
    if (project.isLoading || globalExperiment.isLoading || domain.isLoading || summary.isLoading) return <div aria-busy="true" className="grid min-h-[32rem] place-items-center bg-surface text-sm text-content-secondary">Loading exact Protein Project context…</div>;
    if (!projectData || !globalExperimentData || !domainData || !hierarchyValid || !authority || !summary.data) return <div role="alert" className="grid min-h-[32rem] place-items-center bg-surface p-6"><div className="max-w-xl rounded-xl border border-warning/50 bg-warning/10 p-5 text-center"><h1 className="font-semibold text-warning">This route is not an exact Protein Domain</h1><p className="mt-2 text-sm text-content-secondary">Project, Global Experiment, and Protein Domain parentage or current Protein revision authority could not be proven. No workspace actions are enabled.</p><Link className="mt-4 inline-flex text-sm font-semibold text-accent" to={`/projects/${encodeURIComponent(projectId)}?focus=${encodeURIComponent(globalExperimentId)}&selected=${encodeURIComponent(`domain_experiment:${domainExperimentId}`)}`}>Return to Project Manager</Link></div></div>;

    const projectReturn = `/projects/${encodeURIComponent(projectId)}?focus=${encodeURIComponent(globalExperimentId)}&selected=${encodeURIComponent(`domain_experiment:${domainExperimentId}`)}`;
    const selectSection = (next: ProteinWorkspaceSection) => { const query = new URLSearchParams(searchParams); query.set('workspace', 'protein'); query.set('section', next); setSearchParams(query); };
    const results = summary.data.result_previews;
    const comparisonSurfaces = results.filter((surface) => surface.comparison.state !== 'not_applicable');

    return <div className="min-h-full bg-surface" data-protein-project-workspace="true">
        <header className="border-b border-border-primary bg-surface-secondary px-4 py-4 lg:px-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-accent">Project</p><h1 className="mt-1 text-xl font-semibold text-content">{projectData.name}</h1><p className="mt-1 text-sm text-content-secondary">Experiment · {globalExperimentData.name} · Protein</p></div><div className="flex gap-2"><button type="button" className={BUTTON} onClick={() => setWorkflowDialogOpen(true)}>Add workflow</button><Link to={projectReturn} className="rounded-lg border border-border-primary px-3 py-2 text-xs font-semibold text-content-secondary">Back to Project Manager</Link></div></div></header>
        <nav aria-label="Protein workspace sections" className="overflow-x-auto border-b border-border-primary bg-surface-secondary px-4"><div className="flex min-w-max gap-1 py-2">{SECTIONS.map((item) => <button key={item.id} type="button" onClick={() => selectSection(item.id)} aria-current={section === item.id ? 'page' : undefined} className={`rounded-lg px-3 py-2 text-xs font-semibold ${section === item.id ? 'bg-accent text-white' : 'text-content-secondary hover:bg-surface'}`}>{item.label}</button>)}</div></nav>
        <main className="p-4 lg:p-6">
            {reopen.error && <p role="alert" className="mb-4 rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(reopen.error)}</p>}
            {reverify.error && <p role="alert" className="mb-4 rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(reverify.error)}</p>}
            {section === 'overview' && summary.data.source_receipt_ids.length > 0 && summary.data.reconciliation.state !== 'current' && <section className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning/40 bg-warning/10 p-4"><div><h2 className="text-sm font-semibold text-warning">Source freshness requires verification</h2><p className="mt-1 text-xs text-content-secondary">Verify the attached immutable source identity and digest again.</p></div><button type="button" className={BUTTON} disabled={reverify.isPending} onClick={() => reverify.mutate()}>{reverify.isPending ? 'Reverifying…' : 'Reverify sources'}</button></section>}
            {section === 'overview' && <Overview summary={summary.data} authority={authority} onAddWorkflow={() => setWorkflowDialogOpen(true)} />}
            {section === 'targets' && <div className="space-y-3">{authority.targets.length ? authority.targets.map((target) => <article key={target.target_id} className="rounded-xl border border-border-primary bg-surface-secondary p-4"><div className="flex flex-wrap justify-between gap-3"><div><h2 className="font-semibold text-content">{target.label}</h2><p className="mt-1 font-mono text-xs text-content-muted">{target.target_id}</p></div><span className="rounded-full border border-border-primary px-2 py-1 text-xs text-content-secondary">{target.role}</span></div><p className="mt-3 text-xs text-content-secondary">Source receipts: {target.source_receipt_ids.length ? target.source_receipt_ids.join(', ') : 'none recorded'}</p><p className="mt-1 text-xs text-content-secondary">Dataset members: {target.dataset_member_refs.length ? target.dataset_member_refs.map((member) => `${member.dataset_revision_id}:${member.member_id}`).join(', ') : 'none recorded'}</p></article>) : <EmptyState>No targets are recorded in the current Protein Domain revision.</EmptyState>}<p className="rounded-lg border border-border-primary bg-surface-secondary p-3 text-xs text-content-muted">Target changes are disabled here because Project Manager owns the Protein Domain revision. Use “Back to Project Manager” and edit the exact Domain revision.</p></div>}
            {section === 'datasets' && <DomainDatasetOperator projectId={projectId} globalExperimentId={globalExperimentId} domainExperimentId={domainExperimentId} canMutate mutationBlocker={null} currentStateRevisionId={null} selectedRevisionIds={selectedDatasetRevisionIds} onSelectedRevisionIdsChange={setSelectedDatasetRevisionIds} />}
            {section === 'plans' && <section className="rounded-xl border border-border-primary bg-surface-secondary p-5"><h2 className="text-lg font-semibold text-content">Workflows</h2><p className="mt-2 text-sm text-content-secondary">Add a ready Protein workflow or resume an incomplete setup from this experiment.</p><button type="button" className={`${BUTTON} mt-4`} onClick={() => setWorkflowDialogOpen(true)}>Add workflow</button></section>}
            {section === 'runs' && <RunSection summary={summary.data} />}
            {section === 'results' && (results.length ? <div className="grid gap-3 lg:grid-cols-2">{results.map((surface) => <ResultCard key={surface.receipt_id} surface={surface} opening={reopen.isPending} onOpen={(receiptId) => reopen.mutate(receiptId)} />)}</div> : <EmptyState>No receipt-backed Protein result surface is projected for this Domain.</EmptyState>)}
            {section === 'comparisons' && <div className="space-y-4"><section><h2 className="mb-3 text-sm font-semibold text-content">Declared comparison groups</h2><RecordCards items={authority.comparison_groups} empty="No comparison groups are declared in the current Protein Domain revision." /></section><section><h2 className="mb-3 text-sm font-semibold text-content">Result comparison availability</h2>{comparisonSurfaces.length ? <div className="grid gap-3 lg:grid-cols-2">{comparisonSurfaces.map((surface) => <article key={surface.receipt_id} className="rounded-xl border border-border-primary bg-surface-secondary p-4"><h3 className="font-mono text-xs text-content">{surface.receipt_id}</h3><p className="mt-2 text-sm text-content-secondary">{surface.comparison.state}</p>{surface.comparison.reason && <p className="mt-1 text-xs text-content-muted">{surface.comparison.reason}</p>}</article>)}</div> : <EmptyState>No canonical result advertises comparison availability.</EmptyState>}</section></div>}
            {section === 'evidence' && <div className="space-y-5"><ProteinEvidenceOperator projectId={projectId} globalExperimentId={globalExperimentId} domainExperimentId={domainExperimentId} /><section><h2 className="mb-3 text-sm font-semibold text-content">Evidence and lineage</h2><RecordCards items={summary.data.pagination.lineage.items} empty="No evidence or lineage records are projected for this exact Domain." /></section></div>}
            {section === 'history' && <div className="space-y-3">{summary.data.pagination.activity.items.length ? summary.data.pagination.activity.items.map((event) => <article key={event.id} className="rounded-xl border border-border-primary bg-surface-secondary p-4"><div className="flex flex-wrap justify-between gap-3"><h2 className="font-semibold text-content">{event.event_type.replaceAll('_', ' ')}</h2><time className="text-xs text-content-muted">{new Date(event.created_at).toLocaleString()}</time></div><p className="mt-2 break-all font-mono text-xs text-content-muted">{event.resource_id} · generation {event.generation ?? 'not recorded'}</p></article>) : <EmptyState>No bounded Project history is available for this exact Domain.</EmptyState>}{summary.data.pagination.activity.next_cursor && <p className="text-xs text-warning">More history exists. This Protein workspace shows only the bounded Project read-model page.</p>}</div>}
            {section === 'technical' && <div className="space-y-4"><section className="rounded-xl border border-border-primary bg-surface-secondary p-4"><h2 className="text-sm font-semibold text-content">Exact authority and diagnostics</h2><dl className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3"><AuthorityCard label="Project ID" value={projectId} full/><AuthorityCard label="Project revision" value={projectData.current_revision_id} full/><AuthorityCard label="Global Experiment ID" value={globalExperimentId} full/><AuthorityCard label="Global revision" value={globalExperimentData.current_revision_id} full/><AuthorityCard label="Protein Domain ID" value={domainExperimentId} full/><AuthorityCard label="Domain revision" value={authority.domain_revision_id} full/><AuthorityCard label="Source digest" value={summary.data.source_digest_set_sha256} full/></dl></section><ProteinPlanOperator projectId={projectId} globalExperimentId={globalExperimentId} domainExperimentId={domainExperimentId} domainRevisionId={authority.domain_revision_id} inputDatasetRevisionIds={selectedDatasetRevisionIds}/></div>}
        </main>
        <NewProjectExperimentDialog projectId={projectId} globalExperimentId={globalExperimentId} open={workflowDialogOpen} onClose={() => setWorkflowDialogOpen(false)}/>
    </div>;
}
