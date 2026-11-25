import React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { MsaHubView } from './MsaHubView';
import {
    msaHubIndicatorClass,
    normalizeMsaHubProviders,
} from './msaHubTypes';

const MSA_PROVIDERS_QUERY_KEY = ['msa-hub-providers'] as const;

async function providerRequest(providerId: string, part: 'credential' | 'controller', method: 'PUT' | 'DELETE', body?: Record<string, string>): Promise<void> {
    const response = await fetch(`/api/msa/providers/${encodeURIComponent(providerId)}/${part}`, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: unknown } | null;
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : `Request failed (${response.status})`);
    }
}

export function MsaHubMenu() {
    const [isOpen, setIsOpen] = React.useState(false);
    const [busyProvider, setBusyProvider] = React.useState<string | null>(null);
    const [error, setError] = React.useState<string | null>(null);
    const queryClient = useQueryClient();
    const providersQuery = useQuery({
        queryKey: MSA_PROVIDERS_QUERY_KEY,
        queryFn: ({ signal }) => api.get<unknown>('/api/msa/providers', { signal, timeout: 10000 }).then(({ data }) => normalizeMsaHubProviders(data)),
        enabled: isOpen,
        refetchOnWindowFocus: false,
        retry: false,
    });
    const providers = providersQuery.data ?? [];

    const change = async (providerId: string, work: () => Promise<void>) => {
        setBusyProvider(providerId);
        setError(null);
        try {
            await work();
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : 'Request failed');
        } finally {
            await queryClient.invalidateQueries({ queryKey: MSA_PROVIDERS_QUERY_KEY });
            setBusyProvider(null);
        }
    };

    return (
        <MsaHubView
            isOpen={isOpen}
            onToggle={() => setIsOpen(!isOpen)}
            onClose={() => setIsOpen(false)}
            indicatorClass={msaHubIndicatorClass(providers)}
            providers={providers}
            providersLoaded={providersQuery.isFetched}
            busyProvider={busyProvider}
            error={error}
            onSaveCredential={(providerId, apiKey, endpoint) => {
                void change(providerId, () => providerRequest(providerId, 'credential', 'PUT', endpoint ? { api_key: apiKey, endpoint } : { api_key: apiKey }));
            }}
            onClearCredential={(providerId) => { void change(providerId, () => providerRequest(providerId, 'credential', 'DELETE')); }}
            onSetController={(providerId, enabled) => { void change(providerId, () => providerRequest(providerId, 'controller', enabled ? 'PUT' : 'DELETE')); }}
        />
    );
}

/** Top-bar MSA hub for remote provider configuration. */
export function MsaHub() {
    return <MsaHubMenu />;
}
