import React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useBmsFeatures } from '../runtime/installFeatures';
import { MsaHubView, type MsaLocalServerSectionProps } from './MsaHubView';
import {
    DEFAULT_MSA_LOCAL_SERVER_SETTINGS,
    msaHubIndicatorClass,
    normalizeMsaHubProviders,
    type MsaLocalGpu,
    type MsaLocalServerSettings,
} from './msaHubTypes';

const MSA_MENU_QUERY_KEY = ['msa-server-menu'] as const;
const MSA_PROVIDERS_QUERY_KEY = ['msa-hub-providers'] as const;

function useMSAMenuResource(endpoint: string, enabled: boolean) {
    return useQuery({
        queryKey: [...MSA_MENU_QUERY_KEY, endpoint],
        queryFn: ({ signal }) => api.get<UntypedApiValue>(endpoint, { signal, timeout: 10000 }).then(({ data }) => data),
        enabled,
        refetchInterval: 10000,
        refetchOnWindowFocus: false,
        retry: false,
    });
}

/** Legacy local MMseqs2 server controls. Idle (no requests) unless the feature is on and the hub is open. */
function useLocalMsaServer(enabled: boolean, isOpen: boolean): { section: MsaLocalServerSectionProps; running: boolean; allRunning: boolean } {
    const active = enabled && isOpen;
    const [loading, setLoading] = React.useState<string | null>(null);
    const queryClient = useQueryClient();
    const { data: status, dataUpdatedAt: statusUpdatedAt } = useMSAMenuResource('/api/msa/server/status', active);
    const { data: savedSettings, dataUpdatedAt: settingsUpdatedAt } = useMSAMenuResource('/api/msa/server/settings', active);
    const { data: gpuCatalog } = useMSAMenuResource('/api/gpu/gpus', active);
    const availableGpus: MsaLocalGpu[] = Array.isArray(gpuCatalog?.gpus) ? gpuCatalog.gpus : [];
    const [settings, setSettings] = React.useState<MsaLocalServerSettings>(DEFAULT_MSA_LOCAL_SERVER_SETTINGS);

    React.useEffect(() => {
        if (status?.settings || savedSettings?.settings) {
            setSettings((prev) => ({ ...prev, ...status?.settings, ...savedSettings?.settings }));
        }
    }, [status?.settings, savedSettings?.settings, statusUpdatedAt, settingsUpdatedAt]);

    React.useEffect(() => {
        if (!active) return;
        return () => { void queryClient.cancelQueries({ queryKey: MSA_MENU_QUERY_KEY }); };
    }, [active, queryClient]);

    const fetchState = async () => {
        // Refresh after mutations must replace older reads; closed menus stay idle.
        await queryClient.cancelQueries({ queryKey: MSA_MENU_QUERY_KEY });
        await queryClient.invalidateQueries({ queryKey: MSA_MENU_QUERY_KEY });
    };

    const saveSettings = async (patch: Partial<MsaLocalServerSettings>) => {
        const next = { ...settings, ...patch };
        setSettings(next);
        setLoading('save');
        try {
            await fetch('/api/msa/server/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch)
            });
            await fetchState();
        } catch (error) {
            console.error('Failed to save MSA server settings:', error);
        } finally {
            setLoading(null);
        }
    };

    const startServers = async () => {
        setLoading('start');
        try {
            await fetch('/api/msa/server/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    include_envdb: settings.include_envdb_on_start,
                    gpu_id: settings.pinned_gpu_id ?? undefined,
                })
            });
            await fetchState();
        } catch (error) {
            console.error('Failed to start MSA server:', error);
        } finally {
            setLoading(null);
        }
    };

    const stopServers = async () => {
        setLoading('stop');
        try {
            await fetch('/api/msa/server/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    gpu_id: settings.pinned_gpu_id ?? undefined,
                })
            });
            await fetchState();
        } catch (error) {
            console.error('Failed to stop MSA server:', error);
        } finally {
            setLoading(null);
        }
    };

    const running = Boolean(status?.running);
    const allRunning = Boolean(status?.all_running);
    const serverLines = Array.isArray(status?.servers)
        ? status.servers.map((srv: UntypedApiValue) => {
            const state = srv?.running ? 'RUNNING' : 'STOPPED';
            const alias = srv?.db_alias || 'unknown';
            const pid = srv?.pid ?? 'n/a';
            const gpu = srv?.cuda_visible_devices ?? 'n/a';
            return `${alias.padEnd(7)} ${state.padEnd(8)} pid=${pid} gpu=${gpu}`;
        })
        : [];
    const summary = [
        `Pinned GPU: ${settings.pinned_gpu_id ?? 'auto'}`,
        `GPU: ${status?.effective_gpu_id ?? 'n/a'}`,
        `Running: ${running ? 'yes' : 'no'} | Full target set: ${allRunning ? 'yes' : 'no'}`,
        `Expected DBs: ${(status?.expected_aliases || []).join(', ') || 'n/a'}`,
        `Include EnvDB on start: ${settings.include_envdb_on_start ? 'yes' : 'no'}`,
        `Idle auto-stop: ${settings.auto_stop_idle_enabled ? `enabled (${settings.auto_stop_idle_minutes} min)` : 'disabled'}`,
        `Idle seconds: ${typeof status?.idle_seconds === 'number' ? Math.round(status.idle_seconds) : 'n/a'}`,
        `Last query: ${status?.query_activity?.updated_at || 'n/a'}`,
        status?.auto_stop_reason ? `Auto-stop note: ${status.auto_stop_reason}` : '',
        '',
        'Servers:',
        ...(serverLines.length > 0 ? serverLines : ['none'])
    ].filter(Boolean).join('\n');

    return {
        running,
        allRunning,
        section: {
            settings,
            loading,
            availableGpus,
            summary,
            onRefresh: () => { void fetchState(); },
            onStart: () => { void startServers(); },
            onStop: () => { void stopServers(); },
            onSaveSettings: (patch) => { void saveSettings(patch); },
            onIdleMinutesChange: (minutes) => setSettings((prev) => ({ ...prev, auto_stop_idle_minutes: minutes })),
        },
    };
}

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

export function MsaHubMenu({ localMsaEnabled }: { localMsaEnabled: boolean }) {
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
    const local = useLocalMsaServer(localMsaEnabled, isOpen);

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
            indicatorClass={msaHubIndicatorClass(providers, localMsaEnabled ? local : null)}
            providers={providers}
            providersLoaded={providersQuery.isFetched}
            busyProvider={busyProvider}
            error={error}
            onSaveCredential={(providerId, apiKey, endpoint) => {
                void change(providerId, () => providerRequest(providerId, 'credential', 'PUT', endpoint ? { api_key: apiKey, endpoint } : { api_key: apiKey }));
            }}
            onClearCredential={(providerId) => { void change(providerId, () => providerRequest(providerId, 'credential', 'DELETE')); }}
            onSetController={(providerId, enabled) => { void change(providerId, () => providerRequest(providerId, 'controller', enabled ? 'PUT' : 'DELETE')); }}
            local={localMsaEnabled ? local.section : null}
        />
    );
}

/** Top-bar MSA hub. The install profile (launcher) decides whether local MSA controls exist. */
export function MsaHub() {
    const features = useBmsFeatures();
    return <MsaHubMenu localMsaEnabled={features.local_msa} />;
}
