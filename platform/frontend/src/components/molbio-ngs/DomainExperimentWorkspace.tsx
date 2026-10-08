import { useEffect, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import {
    fetchMolBioNgsDomainState, fetchMolBioNgsStateRevision, fetchMolBioNgsStateRevisions,
    fetchMolBioNgsSamples, fetchMolBioNgsSampleRevision, fetchMolBioNgsReferences,
    fetchMolBioNgsReferenceRevision, fetchMolBioNgsEvidence, fetchProjectHub,
    updateProjectHubPlasmidInfo, type ProjectHubPlasmidInfoDraft, type ProjectHubPlasmidSummary,
} from '../../lib/api';
import { projectManagerErrorMessage } from '../../lib/projectManager';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';
import DomainDatasetOperator from './DomainDatasetOperator';
import DomainWorkflowOperator from './DomainWorkflowOperator';
import ExperimentReferenceLibrary from './ExperimentReferenceLibrary';
import { DomainSampleMutationPanel, DomainReferenceMutationPanel, DomainEvidenceMutationPanel } from './DomainScientificMutationPanels';
import ProjectHubShell from './project-hub/ProjectHubShell';

const OPERATOR_SECTIONS = ['workflow-plans', 'datasets', 'samples', 'references', 'molecular-inputs', 'evidence', 'history'];

export default function DomainExperimentWorkspace() {
    const queryClient = useQueryClient();
    const [params] = useSearchParams();
    const { workspaceId, globalExperimentId, domainExperimentId, stateRevisionId,
        selectedDomainExperiment, availability, setStateRevisionId, updateQueryParams, contextHref,
    } = useGlobalExperimentContext();
    const section = params.get('section') ?? 'overview';
    const exactDomainId = selectedDomainExperiment?.domain_experiment_id === domainExperimentId ? domainExperimentId : null;
    const hasContext = Boolean(workspaceId && globalExperimentId && exactDomainId);
    const isHub = !OPERATOR_SECTIONS.includes(section);
    const selectedDatasetRevisionIds = [...new Set((params.get('dataset_revision_ids') ?? '').split(',').map((id) => id.trim()).filter(Boolean))].slice(0, 100);
    const stateQuery = useQuery({
        queryKey: ['molbio-ngs-domain-state', exactDomainId],
        queryFn: () => fetchMolBioNgsDomainState(exactDomainId as string),
        enabled: hasContext, retry: false,
    });
    const currentStateRevisionId = stateQuery.data?.current_state_revision_id ?? null;
    const selectedStateRevisionId = stateRevisionId ?? currentStateRevisionId ?? selectedDomainExperiment?.local_state_revision_id ?? null;
    useEffect(() => {
        if (stateRevisionId === null && selectedStateRevisionId !== null) {
            updateQueryParams({ state_revision_id: selectedStateRevisionId }, { replace: true });
        }
    }, [selectedStateRevisionId, stateRevisionId, updateQueryParams]);
    const historyQuery = useQuery({
        queryKey: ['molbio-ngs-state-revisions', exactDomainId],
        queryFn: () => fetchMolBioNgsStateRevisions(exactDomainId as string),
        enabled: hasContext && ['evidence', 'history'].includes(section), retry: false,
    });
    const revisionQuery = useQuery({
        queryKey: ['molbio-ngs-state-revision', exactDomainId, selectedStateRevisionId],
        queryFn: () => fetchMolBioNgsStateRevision(exactDomainId as string, selectedStateRevisionId as string),
        enabled: hasContext && Boolean(selectedStateRevisionId) && section === 'evidence', retry: false,
    });
    const samplesQuery = useQuery({
        queryKey: ['molbio-ngs-samples', exactDomainId],
        queryFn: () => fetchMolBioNgsSamples(exactDomainId as string),
        enabled: hasContext && ['samples', 'evidence'].includes(section), retry: false,
    });
    const sampleRevisions = useQueries({ queries: (samplesQuery.data ?? []).map((sample) => ({
        queryKey: ['molbio-ngs-sample-revision', exactDomainId, sample.id, sample.current_revision_id],
        queryFn: () => fetchMolBioNgsSampleRevision(exactDomainId as string, sample.id, sample.current_revision_id as string),
        enabled: Boolean(sample.current_revision_id), retry: false,
    })) });
    const sampleRows = (samplesQuery.data ?? []).map((sample, index) => ({ sample, revision: sampleRevisions[index]?.data }));
    const referencesQuery = useQuery({
        queryKey: ['molbio-ngs-references', exactDomainId],
        queryFn: () => fetchMolBioNgsReferences(exactDomainId as string),
        enabled: hasContext && section === 'references', retry: false,
    });
    const referenceRevisions = useQueries({ queries: (referencesQuery.data ?? []).map((reference) => ({
        queryKey: ['molbio-ngs-reference-revision', reference.id, reference.current_revision_id],
        queryFn: () => fetchMolBioNgsReferenceRevision(reference.id, reference.current_revision_id as string),
        enabled: Boolean(reference.current_revision_id), retry: false,
    })) });
    const referenceRows = (referencesQuery.data ?? []).map((reference, index) => ({ reference, revision: referenceRevisions[index]?.data }));
    const evidenceQuery = useQuery({
        queryKey: ['molbio-ngs-evidence', exactDomainId],
        queryFn: () => fetchMolBioNgsEvidence(exactDomainId as string),
        enabled: hasContext && section === 'evidence', retry: false,
    });
    const projectHubQuery = useQuery({
        queryKey: ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId, selectedStateRevisionId],
        queryFn: ({ signal }) => fetchProjectHub(workspaceId as string, globalExperimentId as string, exactDomainId as string, selectedStateRevisionId as string, signal),
        enabled: hasContext && isHub && selectedStateRevisionId !== null, retry: false,
    });
    const [projectHubConflictMessage, setProjectHubConflictMessage] = useState<string | null>(null);
    const plasmidInfoMutation = useMutation({
        mutationFn: async ({ plasmid, draft }: { plasmid: ProjectHubPlasmidSummary; draft: ProjectHubPlasmidInfoDraft }) => {
            const model = projectHubQuery.data;
            if (!model || !workspaceId || !globalExperimentId || !exactDomainId) throw new Error('The exact project context is unavailable.');
            return updateProjectHubPlasmidInfo(workspaceId, globalExperimentId, exactDomainId, plasmid.sequence_id, {
                expected_molecular_revision_id: plasmid.revision_id,
                expected_state_revision_id: model.identity.current_state_revision_id,
                expected_state_head_generation: model.identity.state_head_generation,
                idempotency_key: crypto.randomUUID(),
                molecular_fields: { name: draft.name, molecule_type: draft.molecule_type, topology: draft.topology, description: draft.description, organism_host_context: draft.organism_host_context },
                project_metadata: { project_tags: draft.project_tags, project_notes: draft.project_notes },
            });
        },
        onMutate: () => setProjectHubConflictMessage(null),
        onSuccess: (model) => {
            setProjectHubConflictMessage(null);
            queryClient.setQueryData(['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId, model.identity.selected_state_revision_id], model);
            void queryClient.invalidateQueries({ queryKey: ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId] });
            if (model.identity.selected_state_revision_id !== selectedStateRevisionId) setStateRevisionId(model.identity.selected_state_revision_id);
        },
        onError: async (error) => {
            const response = (error as { response?: { status?: number; data?: { detail?: { code?: string } } } }).response;
            const code = response?.data?.detail?.code;
            if (response?.status === 409 && (code === 'stale_generation' || code === 'stale_molecular_revision')) {
                setProjectHubConflictMessage('Project state advanced. Review the refreshed state before retrying.');
                await queryClient.invalidateQueries({ queryKey: ['molbio-ngs-domain-state', exactDomainId], exact: true, refetchType: 'none' });
                const refreshedState = await queryClient.fetchQuery({ queryKey: ['molbio-ngs-domain-state', exactDomainId], queryFn: () => fetchMolBioNgsDomainState(exactDomainId as string) });
                setStateRevisionId(refreshedState.current_state_revision_id);
                void queryClient.invalidateQueries({ queryKey: ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId] });
            }
        },
    });

    if (!hasContext || !workspaceId || !globalExperimentId || !exactDomainId) {
        return <p className="p-4" role="status">Select a Project and an NGS/MolBio Domain in <Link to="/projects?scope=ngs-molbio" className="text-accent">Project Manager</Link>.</p>;
    }
    const projectReturnUri = `/projects/${encodeURIComponent(workspaceId)}?${new URLSearchParams({ focus: globalExperimentId, selected: `domain_experiment:${exactDomainId}` })}`;
    const mutationBlocker = !availability.canMutateDomain ? availability.reason
        : !selectedStateRevisionId ? 'Select an immutable local state revision.'
            : selectedStateRevisionId !== currentStateRevisionId ? 'Historical state is read-only. Select the current state to make changes.' : null;
    const canMutate = mutationBlocker === null;
    const sectionError = isHub ? projectHubQuery.error : section === 'samples' ? samplesQuery.error
        : section === 'references' ? referencesQuery.error : section === 'evidence' ? evidenceQuery.error ?? revisionQuery.error : section === 'history' ? historyQuery.error : null;

    return <div className="space-y-3 p-4">
        <nav aria-label="Domain work" className="flex flex-wrap gap-3 text-sm">
            <Link to={contextHref('/designer', { section: null })} className="text-accent">Molecular Toolkit</Link>
            <Link to={contextHref('/ngs', { section: null })} className="text-accent">NGS Toolkit</Link>
            <Link to={projectReturnUri} className="text-accent">Project Manager</Link>
            <details><summary className="cursor-pointer">More Domain tools</summary><div className="flex flex-wrap gap-3 py-2">
                {['overview', ...OPERATOR_SECTIONS].map((key) => <Link key={key} to={`/designer?${new URLSearchParams({ workspace_id: workspaceId, global_experiment_id: globalExperimentId, domain_experiment_id: exactDomainId, ...(selectedStateRevisionId ? { state_revision_id: selectedStateRevisionId } : {}), section: key })}`}>{key.replaceAll('-', ' ')}</Link>)}
            </div></details>
        </nav>
        {sectionError && <p role="alert" className="text-error">{projectManagerErrorMessage(sectionError)}</p>}
        {section === 'workflow-plans' && <DomainWorkflowOperator
            key={`${workspaceId}:${globalExperimentId}:${exactDomainId}`}
            projectId={workspaceId} globalExperimentId={globalExperimentId} domainExperimentId={exactDomainId}
            initialRunGroupId={params.get('run_group_id')}
            domainRevisionId={selectedDomainExperiment?.global_domain_experiment_revision_id ?? null}
            selectedStateRevisionId={selectedStateRevisionId} currentStateRevisionId={currentStateRevisionId}
            projectReturnUri={projectReturnUri} contextHref={contextHref} inputDatasetRevisionIds={selectedDatasetRevisionIds}
        />}
        {section === 'datasets' && <DomainDatasetOperator
            projectId={workspaceId} globalExperimentId={globalExperimentId} domainExperimentId={exactDomainId}
            canMutate={canMutate} mutationBlocker={mutationBlocker} currentStateRevisionId={currentStateRevisionId}
            selectedRevisionIds={selectedDatasetRevisionIds}
            onSelectedRevisionIdsChange={(ids) => updateQueryParams({ dataset_revision_ids: [...new Set(ids)].join(',') || null })}
        />}
        {section === 'samples' && <DomainSampleMutationPanel domainExperimentId={exactDomainId} canMutate={canMutate} mutationBlocker={mutationBlocker} rows={sampleRows} />}
        {section === 'references' && <DomainReferenceMutationPanel domainExperimentId={exactDomainId} canMutate={canMutate} mutationBlocker={mutationBlocker} rows={referenceRows} />}
        {section === 'molecular-inputs' && selectedDomainExperiment && <ExperimentReferenceLibrary
            domainExperimentId={exactDomainId} globalDomainExperimentRevisionId={selectedDomainExperiment.global_domain_experiment_revision_id}
            currentStateRevisionId={currentStateRevisionId} stateHeadGeneration={stateQuery.data?.head_generation ?? 0}
            canMutate={canMutate} mutationBlocker={mutationBlocker}
        />}
        {section === 'evidence' && <>
            <DomainEvidenceMutationPanel domainExperimentId={exactDomainId} canMutate={canMutate} mutationBlocker={mutationBlocker}
                stateRevisionId={selectedStateRevisionId} stateRevisions={historyQuery.data ?? []} members={revisionQuery.data?.members ?? []} sampleRows={sampleRows} />
            {(evidenceQuery.data ?? []).map((item) => <p key={item.evidence_id}>{item.scientific_assessment} <span className="text-content-muted">{item.job_lifecycle_state}</span></p>)}
        </>}
        {section === 'history' && <ul>{(historyQuery.data ?? []).map((revision) => <li key={revision.id}>
            <button type="button" className="text-accent" onClick={() => setStateRevisionId(revision.id)}>Revision {revision.revision_number}</button>
        </li>)}</ul>}
        {isHub && (projectHubQuery.data ? <ProjectHubShell
            model={projectHubQuery.data} canMutate={canMutate} mutationBlocker={mutationBlocker}
            selectedSection={section} selectedPlasmidId={params.get('plasmid')}
            onNavigate={(updates) => updateQueryParams(updates)}
            onSavePlasmidInfo={async (plasmid, draft) => { plasmidInfoMutation.reset(); await plasmidInfoMutation.mutateAsync({ plasmid, draft }); }}
            saveError={projectHubConflictMessage ?? (plasmidInfoMutation.error ? projectManagerErrorMessage(plasmidInfoMutation.error) : null)} saving={plasmidInfoMutation.isPending}
        /> : !sectionError && <p role="status">{stateQuery.error ? projectManagerErrorMessage(stateQuery.error) : stateQuery.isPending || projectHubQuery.isFetching ? 'Loading project data…' : 'Initialize a local state through Plans & Runs to view project data.'}</p>)}
    </div>;
}
