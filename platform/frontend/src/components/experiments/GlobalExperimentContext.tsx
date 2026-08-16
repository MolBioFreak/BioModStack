import {
    createContext,
    useCallback,
    useContext,
    useMemo,
    type ReactNode,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import {
    fetchGlobalExperiments,
    fetchGlobalWorkspaces,
    fetchMolBioNgsProjectExperiments,
    VisibleApiError,
    type DomainExperimentView,
    type GlobalAggregateHead,
} from '../../lib/api';
import { getNgsMolBioBinding } from '../../lib/projectManager';

export const EXPERIMENT_CONTEXT_KEYS = [
    'workspace_id',
    'global_experiment_id',
    'domain_experiment_id',
    'state_revision_id',
] as const;

export type ExperimentContextKey = (typeof EXPERIMENT_CONTEXT_KEYS)[number];
export type ContextQueryUpdate = Partial<Record<ExperimentContextKey | string, string | null | undefined>>;

export interface DomainContextAvailability {
    status: 'incomplete' | 'loading' | 'invalid' | 'unavailable' | 'read-only' | 'available';
    canMutateDomain: boolean;
    localBinding: 'unknown' | 'acknowledged' | 'unavailable';
    globalAdapter: 'unknown' | 'available' | 'unavailable';
    reason: string;
    error: VisibleApiError | null;
}

export interface GlobalExperimentContextValue {
    workspaceId: string | null;
    globalExperimentId: string | null;
    domainExperimentId: string | null;
    stateRevisionId: string | null;
    workspaces: GlobalAggregateHead[];
    globalExperiments: GlobalAggregateHead[];
    domainExperiments: DomainExperimentView[];
    selectedWorkspace: GlobalAggregateHead | null;
    selectedGlobalExperiment: GlobalAggregateHead | null;
    selectedDomainExperiment: DomainExperimentView | null;
    availability: DomainContextAvailability;
    isLoading: boolean;
    setWorkspaceId: (workspaceId: string | null) => void;
    setGlobalExperimentId: (globalExperimentId: string | null) => void;
    setDomainExperimentId: (domainExperimentId: string | null) => void;
    setStateRevisionId: (stateRevisionId: string | null) => void;
    updateQueryParams: (updates: ContextQueryUpdate, options?: { replace?: boolean }) => void;
    contextHref: (pathname: string, updates?: ContextQueryUpdate) => string;
}

const GlobalExperimentContext = createContext<GlobalExperimentContextValue | null>(null);

function cleanQueryValue(value: string | null): string | null {
    if (value === null) return null;
    const normalized = value.trim();
    return normalized.length > 0 ? normalized : null;
}

function visibleError(error: unknown): VisibleApiError | null {
    if (!error) return null;
    if (error instanceof VisibleApiError) return error;
    const detail = error instanceof Error ? error.message : String(error);
    return new VisibleApiError(detail, null, detail);
}

export function GlobalExperimentProvider({ children }: { children: ReactNode }) {
    const location = useLocation();
    const navigate = useNavigate();
    const params = useMemo(() => new URLSearchParams(location.search), [location.search]);

    const workspaceId = cleanQueryValue(params.get('workspace_id'));
    const globalExperimentId = cleanQueryValue(params.get('global_experiment_id'));
    const domainExperimentId = cleanQueryValue(params.get('domain_experiment_id'));
    const stateRevisionId = cleanQueryValue(params.get('state_revision_id'));

    const workspaceQuery = useQuery({
        queryKey: ['global-workspaces'],
        queryFn: fetchGlobalWorkspaces,
        staleTime: 30_000,
    });
    const globalExperimentQuery = useQuery({
        queryKey: ['global-experiments', workspaceId],
        queryFn: () => fetchGlobalExperiments(workspaceId as string),
        enabled: workspaceId !== null,
        staleTime: 30_000,
    });
    const domainExperimentQuery = useQuery({
        queryKey: ['molbio-ngs-project-domain-experiments', workspaceId],
        queryFn: () => fetchMolBioNgsProjectExperiments(workspaceId as string),
        enabled: workspaceId !== null,
        retry: false,
        staleTime: 15_000,
    });
    const bindingQuery = useQuery({
        queryKey: ['ngs-molbio-binding', workspaceId, globalExperimentId, domainExperimentId],
        queryFn: ({ signal }) => getNgsMolBioBinding(
            workspaceId as string,
            globalExperimentId as string,
            domainExperimentId as string,
            signal,
        ),
        enabled: workspaceId !== null && globalExperimentId !== null && domainExperimentId !== null,
        retry: false,
        staleTime: 5_000,
        refetchInterval: (query) => query.state.data?.provisioning_state === 'provisioning' ? 2_000 : false,
    });

    const workspaces = workspaceQuery.data ?? [];
    const globalExperiments = globalExperimentQuery.data ?? [];
    const projectDomainExperiments = domainExperimentQuery.data ?? [];
    const selectedWorkspace = workspaceId
        ? workspaces.find((workspace) => workspace.id === workspaceId) ?? null
        : null;
    const selectedGlobalExperiment = globalExperimentId
        ? globalExperiments.find((experiment) => (
            experiment.id === globalExperimentId
            && experiment.workspace_id === workspaceId
        )) ?? null
        : null;
    const domainExperiments = workspaceId && globalExperimentId
        ? projectDomainExperiments.filter((domainExperiment) => (
            domainExperiment.project_id === workspaceId
            && domainExperiment.global_experiment_id === globalExperimentId
        ))
        : [];
    const selectedDomainExperiment = domainExperimentId
        ? domainExperiments.find((domainExperiment) => (
            domainExperiment.domain_experiment_id === domainExperimentId
        )) ?? null
        : null;

    const workspaceError = visibleError(workspaceQuery.error);
    const experimentError = visibleError(globalExperimentQuery.error);
    const domainError = visibleError(domainExperimentQuery.error);
    const bindingError = visibleError(bindingQuery.error);
    const isLoading = workspaceQuery.isLoading
        || (workspaceId !== null && globalExperimentQuery.isLoading)
        || (workspaceId !== null && domainExperimentQuery.isLoading)
        || (workspaceId !== null && globalExperimentId !== null && domainExperimentId !== null && bindingQuery.isLoading);

    const availability = useMemo<DomainContextAvailability>(() => {
        const error = workspaceError ?? experimentError ?? domainError;
        if (error) {
            const localUnavailable = domainError !== null;
            return {
                status: localUnavailable ? 'unavailable' : 'invalid',
                canMutateDomain: false,
                localBinding: localUnavailable ? 'unavailable' : 'unknown',
                globalAdapter: 'unknown',
                reason: error.message,
                error,
            };
        }
        if (isLoading) {
            return {
                status: 'loading',
                canMutateDomain: false,
                localBinding: 'unknown',
                globalAdapter: 'unknown',
                reason: 'Loading Project, Global Experiment, and local Domain Experiment authorities.',
                error: null,
            };
        }
        if (!workspaceId || !globalExperimentId || !domainExperimentId) {
            return {
                status: 'incomplete',
                canMutateDomain: false,
                localBinding: 'unknown',
                globalAdapter: 'unknown',
                reason: 'Select a Project (workspace), Global Experiment, and NGS/MolBio Domain Experiment.',
                error: null,
            };
        }
        if (!selectedWorkspace) {
            return {
                status: 'invalid',
                canMutateDomain: false,
                localBinding: 'unknown',
                globalAdapter: 'unknown',
                reason: `Project (workspace) ${workspaceId} was not returned by the global authority.`,
                error: null,
            };
        }
        if (!selectedGlobalExperiment) {
            return {
                status: 'invalid',
                canMutateDomain: false,
                localBinding: 'unknown',
                globalAdapter: 'unknown',
                reason: `Global Experiment ${globalExperimentId} does not belong to Project ${workspaceId}.`,
                error: null,
            };
        }
        if (!selectedDomainExperiment) {
            return {
                status: 'invalid',
                canMutateDomain: false,
                localBinding: 'unavailable',
                globalAdapter: 'unknown',
                reason: `Domain Experiment ${domainExperimentId} has no acknowledged binding to the selected Project and Global Experiment.`,
                error: null,
            };
        }
        if (bindingError) {
            return {
                status: 'read-only',
                canMutateDomain: false,
                localBinding: 'unavailable',
                globalAdapter: 'unavailable',
                reason: `The exact shared binding authority is unavailable: ${bindingError.message}`,
                error: bindingError,
            };
        }
        const binding = bindingQuery.data;
        const exactBindingReady = binding?.provisioning_state === 'ready'
            && (binding.command_state === 'applied' || binding.command_state === 'duplicate')
            && binding.domain_revision_id === selectedDomainExperiment.global_domain_experiment_revision_id
            && Boolean(binding.global_receipt_id)
            && Boolean(binding.acknowledgement_id);
        if (!exactBindingReady) {
            return {
                status: 'read-only',
                canMutateDomain: false,
                localBinding: binding?.acknowledgement_id ? 'acknowledged' : 'unavailable',
                globalAdapter: binding ? 'available' : 'unknown',
                reason: binding?.provisioning_state === 'provisioning'
                    ? 'Read/reopen is available while the sole managed connector establishes exact binding authority.'
                    : 'Read/reopen is available, but mutations require a ready acknowledged binding for the exact current Domain revision.',
                error: null,
            };
        }
        return {
            status: 'available',
            canMutateDomain: true,
            localBinding: 'acknowledged',
            globalAdapter: 'available',
            reason: 'The exact current Domain revision has a ready acknowledged global/local binding.',
            error: null,
        };
    }, [
        bindingError,
        bindingQuery.data,
        domainError,
        domainExperimentId,
        experimentError,
        globalExperimentId,
        isLoading,
        selectedDomainExperiment,
        selectedGlobalExperiment,
        selectedWorkspace,
        workspaceError,
        workspaceId,
    ]);

    const updateQueryParams = useCallback((
        updates: ContextQueryUpdate,
        options?: { replace?: boolean },
    ) => {
        const next = new URLSearchParams(location.search);
        Object.entries(updates).forEach(([key, rawValue]) => {
            const value = typeof rawValue === 'string' ? rawValue.trim() : rawValue;
            if (value === null || value === undefined || value === '') next.delete(key);
            else next.set(key, value);
        });
        navigate(
            { pathname: location.pathname, search: next.toString() ? `?${next.toString()}` : '' },
            { replace: options?.replace ?? false },
        );
    }, [location.pathname, location.search, navigate]);

    const contextHref = useCallback((pathname: string, updates: ContextQueryUpdate = {}) => {
        const queryIndex = pathname.indexOf('?');
        const targetPathname = queryIndex >= 0 ? pathname.slice(0, queryIndex) : pathname;
        const next = new URLSearchParams(location.search);
        if (queryIndex >= 0) {
            new URLSearchParams(pathname.slice(queryIndex + 1)).forEach((value, key) => next.set(key, value));
        }
        Object.entries(updates).forEach(([key, rawValue]) => {
            const value = typeof rawValue === 'string' ? rawValue.trim() : rawValue;
            if (value === null || value === undefined || value === '') next.delete(key);
            else next.set(key, value);
        });
        const search = next.toString();
        return `${targetPathname}${search ? `?${search}` : ''}`;
    }, [location.search]);

    const setWorkspaceId = useCallback((nextWorkspaceId: string | null) => {
        updateQueryParams({
            workspace_id: nextWorkspaceId,
            global_experiment_id: nextWorkspaceId === workspaceId ? globalExperimentId : null,
            domain_experiment_id: nextWorkspaceId === workspaceId ? domainExperimentId : null,
            state_revision_id: nextWorkspaceId === workspaceId ? stateRevisionId : null,
        });
    }, [domainExperimentId, globalExperimentId, stateRevisionId, updateQueryParams, workspaceId]);
    const setGlobalExperimentId = useCallback((nextGlobalExperimentId: string | null) => {
        updateQueryParams({
            global_experiment_id: nextGlobalExperimentId,
            domain_experiment_id: nextGlobalExperimentId === globalExperimentId ? domainExperimentId : null,
            state_revision_id: nextGlobalExperimentId === globalExperimentId ? stateRevisionId : null,
        });
    }, [domainExperimentId, globalExperimentId, stateRevisionId, updateQueryParams]);
    const setDomainExperimentId = useCallback((nextDomainExperimentId: string | null) => {
        updateQueryParams({
            domain_experiment_id: nextDomainExperimentId,
            state_revision_id: nextDomainExperimentId === domainExperimentId ? stateRevisionId : null,
        });
    }, [domainExperimentId, stateRevisionId, updateQueryParams]);
    const setStateRevisionId = useCallback((nextStateRevisionId: string | null) => {
        updateQueryParams({ state_revision_id: nextStateRevisionId });
    }, [updateQueryParams]);

    const value = useMemo<GlobalExperimentContextValue>(() => ({
        workspaceId,
        globalExperimentId,
        domainExperimentId,
        stateRevisionId,
        workspaces,
        globalExperiments,
        domainExperiments,
        selectedWorkspace,
        selectedGlobalExperiment,
        selectedDomainExperiment,
        availability,
        isLoading,
        setWorkspaceId,
        setGlobalExperimentId,
        setDomainExperimentId,
        setStateRevisionId,
        updateQueryParams,
        contextHref,
    }), [
        availability,
        contextHref,
        domainExperimentId,
        domainExperiments,
        globalExperimentId,
        globalExperiments,
        isLoading,
        selectedDomainExperiment,
        selectedGlobalExperiment,
        selectedWorkspace,
        setDomainExperimentId,
        setGlobalExperimentId,
        setStateRevisionId,
        setWorkspaceId,
        stateRevisionId,
        updateQueryParams,
        workspaceId,
        workspaces,
    ]);

    return (
        <GlobalExperimentContext.Provider value={value}>
            {children}
        </GlobalExperimentContext.Provider>
    );
}

export function useGlobalExperimentContext(): GlobalExperimentContextValue {
    const context = useContext(GlobalExperimentContext);
    if (!context) {
        throw new Error('useGlobalExperimentContext must be used within GlobalExperimentProvider');
    }
    return context;
}
