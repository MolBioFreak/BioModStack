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

// Reserved for mutations that save/attach into a project with no NGS/MolBio domain yet.
// Opening the Molecular Toolset never calls this; use nativeProjectToolHref directly.
export function ensureNativeProjectWorkspace(projectId: string, preferred?: NativeWorkspace | null): Promise<NativeWorkspace> {
    if (preferred) return Promise.resolve(preferred);
    const existing = pending.get(projectId);
    if (existing) return existing;
    const work = (async () => {
        const globals = (await listGlobalExperiments(projectId)).filter((item) => item.lifecycle_state !== 'archived' && item.status !== 'archived');
        const groups = await Promise.all(globals.map(async (global) => ({ global, domains: (await listDomainExperiments(projectId, global.id)).filter((item) => item.domain_kind === 'ngs_molbio' && item.lifecycle_state !== 'archived' && item.status !== 'archived') })));
        const candidates = groups.flatMap(({ global, domains }) => domains.map((domain) => ({ globalExperimentId: global.id, domainExperimentId: domain.id })));
        if (candidates.length > 1) throw new Error('This Project has multiple NGS/MolBio workspaces. Choose the destination workspace before saving.');
        if (candidates.length === 1) return candidates[0];
        const tagged = globals.find((item) => Array.isArray(item.payload?.tags) && item.payload.tags.includes('bns-project-workspace'));
        if (!tagged && globals.length > 1) throw new Error('Select the destination experiment before adding a workspace.');
        const global = tagged ?? globals[0] ?? await createGlobalExperiment(projectId, {
            schema: 'bms.global-experiment.v2', name: 'Sequences and NGS data',
            objective: '', description: 'Project sequence and NGS workspace', status: 'active',
            tags: ['bns-project-workspace'], change_summary: 'Opened native Project tools',
        });
        const domain = await createDomainExperiment(projectId, global.id, {
            schema: 'bms.domain-experiment.v4', domain_kind: 'ngs_molbio', domain_contract_version: '3',
            name: 'Sequences and NGS data', objective: '', status: 'draft', tags: ['bns-project-workspace'],
            source_receipt_ids: [], dataset_revision_ids: [], change_summary: 'Opened native Project tools',
            domain_payload: { schema: 'bms.ngs-molbio-experiment.v2', experiment_mode: 'molecular_design',
                scientific_objective: '', planned_capability_ids: [], grouping_intent: [], acceptance_criteria: [], evidence_plan: [] },
        });
        for (let attempt = 0; attempt < 15; attempt += 1) {
            try {
                await fetchMolBioNgsDomainState(domain.id);
                return { globalExperimentId: global.id, domainExperimentId: domain.id };
            } catch (error) {
                const status = (error as { status?: number; response?: { status?: number } }).status ?? (error as { response?: { status?: number } }).response?.status;
                if (status !== 404 || attempt >= 14) throw error;
                await new Promise((resolve) => setTimeout(resolve, 1000));
            }
        }
        return { globalExperimentId: global.id, domainExperimentId: domain.id };
    })().finally(() => { pending.delete(projectId); });
    pending.set(projectId, work);
    return work;
}

export function nativeProjectToolHref(
    projectId: string,
    contextOrTool?: NativeWorkspace | 'molbio' | 'ngs' | 'add-sequence' | null,
    toolArg?: 'molbio' | 'ngs' | 'add-sequence',
): string {
    const tool = typeof contextOrTool === 'string' ? contextOrTool : (toolArg ?? 'molbio');
    const context = typeof contextOrTool === 'object' ? contextOrTool : null;
    if (tool === 'molbio' || tool === 'add-sequence') {
        const params = new URLSearchParams({ project_id: projectId });
        if (tool === 'add-sequence') params.set('action', 'add-plasmid');
        return `/designer?${params.toString()}`;
    }
    const params = context
        ? new URLSearchParams({
            workspace_id: projectId,
            global_experiment_id: context.globalExperimentId,
            domain_experiment_id: context.domainExperimentId,
        })
        : new URLSearchParams({ project_id: projectId });
    return `/ngs?${params.toString()}`;
}
