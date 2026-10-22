import { fetchMolBioNgsDomainState } from './api';
import { createDomainExperiment, createGlobalExperiment, listDomainExperiments, listGlobalExperiments } from './projectManager';

export interface NativeWorkspace { globalExperimentId: string; domainExperimentId: string; label?: string }
const pending = new Map<string, Promise<NativeWorkspace>>();

export async function listNativeProjectWorkspaces(projectId: string) {
    const globals = (await listGlobalExperiments(projectId)).filter((item) => item.lifecycle_state !== 'archived' && item.status !== 'archived');
    return (await Promise.all(globals.map(async (global) => (await listDomainExperiments(projectId, global.id))
        .filter((item) => item.domain_kind === 'ngs_molbio' && item.lifecycle_state !== 'archived' && item.status !== 'archived')
        .map((domain) => ({ globalExperimentId: global.id, domainExperimentId: domain.id, label: domain.name }))))).flat();
}

// Use the managed PM connector. Opening a tool does not create acquisition or
// analysis intent, invent scientific criteria, or write directly to native DBs.
export function ensureNativeProjectWorkspace(projectId: string, preferred?: NativeWorkspace | null): Promise<NativeWorkspace> {
    if (preferred) return Promise.resolve(preferred);
    const existing = pending.get(projectId);
    if (existing) return existing;
    const work = (async () => {
        const globals = (await listGlobalExperiments(projectId)).filter((item) => item.lifecycle_state !== 'archived' && item.status !== 'archived');
        const groups = await Promise.all(globals.map(async (global) => ({ global, domains: (await listDomainExperiments(projectId, global.id)).filter((item) => item.domain_kind === 'ngs_molbio' && item.lifecycle_state !== 'archived' && item.status !== 'archived') })));
        const candidates = groups.flatMap(({ global, domains }) => domains.map((domain) => ({ globalExperimentId: global.id, domainExperimentId: domain.id })));
        if (candidates.length > 1) throw new Error('This Project has multiple NGS/MolBio workspaces. Select one in the Project tree first.');
        if (candidates.length === 1) return candidates[0];
        // A tagged container also makes retry after connector failure reusable.
        const tagged = globals.find((item) => Array.isArray(item.payload?.tags) && item.payload.tags.includes('bns-project-workspace'));
        if (!tagged && globals.length > 1) throw new Error('Select the experiment to use in the Project tree before adding a workspace.');
        const global = tagged ?? globals[0] ?? await createGlobalExperiment(projectId, {
            schema: 'bms.global-experiment.v2', name: 'Sequences and NGS data',
            objective: '', description: 'Project sequence and NGS workspace', status: 'active',
            tags: ['bns-project-workspace'], change_summary: 'Opened native Project tools',
        });
        const domain = await createDomainExperiment(projectId, global.id, {
            schema: 'bms.domain-experiment.v4', domain_kind: 'ngs_molbio', domain_contract_version: '3',
            name: 'Sequences and NGS data', objective: '', status: 'active', tags: ['bns-project-workspace'],
            source_receipt_ids: [], dataset_revision_ids: [], change_summary: 'Opened native Project tools',
            domain_payload: { schema: 'bms.ngs-molbio-experiment.v2', experiment_mode: 'molecular_design',
                scientific_objective: '', planned_capability_ids: [], grouping_intent: [], acceptance_criteria: [], evidence_plan: [] },
        });
        return { globalExperimentId: global.id, domainExperimentId: domain.id };
    })().then(async (context) => {
        for (let attempt = 0; ; attempt++) {
            try { await fetchMolBioNgsDomainState(context.domainExperimentId); return context; }
            catch (error) {
                const status = (error as { status?: number; response?: { status?: number } }).status ?? (error as { response?: { status?: number } }).response?.status;
                if (status !== 404 || attempt >= 14) throw error;
                await new Promise((resolve) => setTimeout(resolve, 1000));
            }
        }
    }).finally(() => { pending.delete(projectId); });
    pending.set(projectId, work);
    return work;
}

export function nativeProjectToolHref(projectId: string, context: NativeWorkspace, tool: 'molbio' | 'ngs' | 'add-sequence') {
    const params = new URLSearchParams({ workspace_id: projectId, global_experiment_id: context.globalExperimentId, domain_experiment_id: context.domainExperimentId });
    if (tool === 'add-sequence') params.set('action', 'add-plasmid');
    return `${tool === 'ngs' ? '/ngs' : '/designer'}?${params}`;
}
