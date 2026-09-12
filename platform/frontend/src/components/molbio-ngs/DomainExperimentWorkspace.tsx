import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchMolBioNgsDomainState, fetchProjectHub, updateProjectHubPlasmidInfo,
    type ProjectHubPlasmidInfoDraft, type ProjectHubPlasmidSummary } from '../../lib/api';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';
import ProjectHubShell from './project-hub/ProjectHubShell';

function errorText(error: unknown): string | null {
    return error ? error instanceof Error ? error.message : String(error) : null;
}

export default function DomainExperimentWorkspace() {
    const queryClient = useQueryClient();
    const { workspaceId, globalExperimentId, stateRevisionId, selectedDomainExperiment,
        availability, setStateRevisionId, updateQueryParams } = useGlobalExperimentContext();
    const exactDomainId = selectedDomainExperiment?.domain_experiment_id ?? null;
    const hasProjectHubContext = Boolean(workspaceId && globalExperimentId && exactDomainId);
    const requestedSection = new URLSearchParams(window.location.search).get('section');
    const stateQuery = useQuery({
        queryKey: ['molbio-ngs-domain-state', exactDomainId],
        queryFn: () => fetchMolBioNgsDomainState(exactDomainId as string),
        enabled: exactDomainId !== null,
        retry: false,
    });
    const selectedStateRevisionId = stateRevisionId
        ?? selectedDomainExperiment?.local_state_revision_id
        ?? stateQuery.data?.current_state_revision_id
        ?? null;

    useEffect(() => {
        if (stateRevisionId === null && selectedStateRevisionId !== null) {
            updateQueryParams({ state_revision_id: selectedStateRevisionId }, { replace: true });
        }
    }, [selectedStateRevisionId, stateRevisionId, updateQueryParams]);
    const pageScope = JSON.stringify([workspaceId, globalExperimentId, exactDomainId, selectedStateRevisionId]);
    const [pageSelection, setPageSelection] = useState<{ scope: string; cursors: Record<string, string> }>({ scope: pageScope, cursors: {} });
    const paging = pageSelection.scope === pageScope ? pageSelection.cursors : {};
    const projectHubQuery = useQuery({
        queryKey: ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId, selectedStateRevisionId, paging],
        queryFn: ({ signal }) => fetchProjectHub(
            workspaceId as string,
            globalExperimentId as string,
            exactDomainId as string,
            selectedStateRevisionId as string,
            signal,
            paging,
        ),
        enabled: hasProjectHubContext && selectedStateRevisionId !== null,
        retry: false,
    });
    const [projectHubConflictMessage, setProjectHubConflictMessage] = useState<string | null>(null);
    const plasmidInfoMutation = useMutation({
        mutationFn: async ({ plasmid, draft }: { plasmid: ProjectHubPlasmidSummary; draft: ProjectHubPlasmidInfoDraft }) => {
            const model = projectHubQuery.data;
            if (!model || !workspaceId || !globalExperimentId || !exactDomainId) {
                throw new Error('The exact project context is unavailable.');
            }
            const idempotencyKey = typeof crypto.randomUUID === 'function'
                ? crypto.randomUUID()
                : `${Date.now()}-${plasmid.sequence_id}`;
            return updateProjectHubPlasmidInfo(workspaceId, globalExperimentId, exactDomainId, plasmid.sequence_id, {
                expected_molecular_revision_id: plasmid.revision_id,
                expected_state_revision_id: model.identity.current_state_revision_id,
                expected_state_head_generation: model.identity.state_head_generation,
                idempotency_key: idempotencyKey,
                molecular_fields: {
                    name: draft.name,
                    molecule_type: draft.molecule_type,
                    topology: draft.topology,
                    description: draft.description,
                    organism_host_context: draft.organism_host_context,
                },
                project_metadata: {
                    project_tags: draft.project_tags,
                    project_notes: draft.project_notes,
                },
            });
        },
        onMutate: () => setProjectHubConflictMessage(null),
        onSuccess: (model) => {
            setProjectHubConflictMessage(null);
            queryClient.setQueryData(
                ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId, model.identity.selected_state_revision_id],
                model,
            );
            void queryClient.invalidateQueries({ queryKey: ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId] });
            if (model.identity.selected_state_revision_id !== selectedStateRevisionId) {
                setStateRevisionId(model.identity.selected_state_revision_id);
            }
        },
        onError: async (error) => {
            const response = (error as { response?: { status?: number; data?: { detail?: { code?: string } } } }).response;
            const code = response?.data?.detail?.code;
            if (response?.status === 409 && (code === 'stale_generation' || code === 'stale_molecular_revision')) {
                setProjectHubConflictMessage('Project state advanced. Review the refreshed state before retrying.');
                await queryClient.invalidateQueries({
                    queryKey: ['molbio-ngs-domain-state', exactDomainId],
                    exact: true,
                    refetchType: 'none',
                });
                const refreshedState = await queryClient.fetchQuery({
                    queryKey: ['molbio-ngs-domain-state', exactDomainId],
                    queryFn: () => fetchMolBioNgsDomainState(exactDomainId as string),
                });
                setStateRevisionId(refreshedState.current_state_revision_id);
                void queryClient.invalidateQueries({ queryKey: ['molbio-project-hub', workspaceId, globalExperimentId, exactDomainId] });
            }
        },
    });
    if (hasProjectHubContext) {
        if (projectHubQuery.isLoading || selectedStateRevisionId === null) {
            return <div className="px-6 py-10 text-sm text-content-secondary" role="status">Loading project hub…</div>;
        }
        if (projectHubQuery.error || !projectHubQuery.data) {
            return (
                <div className="px-6 py-6">
                    <div className="rounded-xl border border-error/40 bg-error/10 p-4 text-sm text-error" role="alert">
                        <strong className="block">Project hub is unavailable</strong>
                        <span className="mt-1 block">{errorText(projectHubQuery.error) ?? 'The project summary response was empty.'}</span>
                    </div>
                </div>
            );
        }
        return (
            <ProjectHubShell
                model={projectHubQuery.data}
                paging={paging}
                onPage={(section, cursor) => setPageSelection((current) => {
                    const next = { ...(current.scope === pageScope ? current.cursors : {}) };
                    if (cursor) next[`${section}_cursor`] = cursor; else delete next[`${section}_cursor`];
                    return { scope: pageScope, cursors: next };
                })}
                canMutate={availability.canMutateDomain}
                mutationBlocker={availability.canMutateDomain ? null : availability.reason}
                selectedSection={requestedSection}
                selectedPlasmidId={new URLSearchParams(window.location.search).get('plasmid')}
                onNavigate={(updates) => updateQueryParams(updates)}
                onSavePlasmidInfo={async (plasmid, draft) => {
                    plasmidInfoMutation.reset();
                    await plasmidInfoMutation.mutateAsync({ plasmid, draft });
                }}
                saveError={projectHubConflictMessage ?? errorText(plasmidInfoMutation.error)}
                saving={plasmidInfoMutation.isPending}
            />
        );
    }

    return <div role="status">Select a Project and Domain in Project Manager.</div>;
}
