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
    const isLoading = workspaceQuery.isLoading
        || (workspaceId !== null && globalExperimentQuery.isLoading)
        || (workspaceId !== null && domainExperimentQuery.isLoading);

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
        return {
            status: 'read-only',
            canMutateDomain: false,
            localBinding: 'acknowledged',
            globalAdapter: 'unavailable',
            reason: 'Read/reopen is available, but domain mutations are disabled because the finalized global adapter is unavailable.',
            error: null,
        };
    }, [
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
        const next = new URLSearchParams(location.search);
        Object.entries(updates).forEach(([key, rawValue]) => {
            const value = typeof rawValue === 'string' ? rawValue.trim() : rawValue;
            if (value === null || value === undefined || value === '') next.delete(key);
            else next.set(key, value);
        });
        const search = next.toString();
        return `${pathname}${search ? `?${search}` : ''}`;
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
