export interface MsaHubProvider {
    id: string;
    label: string;
    needs_key: boolean;
    configured: boolean;
    source: 'environment' | 'saved' | null;
    endpoint_configurable: boolean;
    ready: boolean;
    message: string;
}

export interface MsaLocalServerSettings {
    include_envdb_on_start: boolean;
    auto_stop_idle_enabled: boolean;
    auto_stop_idle_minutes: number;
    pinned_gpu_id: number | null;
}

export interface MsaLocalGpu {
    index: number;
    name: string;
    memory_total_mb?: number;
}

export const DEFAULT_MSA_LOCAL_SERVER_SETTINGS: MsaLocalServerSettings = {
    include_envdb_on_start: false,
    auto_stop_idle_enabled: false,
    auto_stop_idle_minutes: 10,
    pinned_gpu_id: null,
};

/** Keep only the documented fields so nothing else from a response can reach the markup. */
export function normalizeMsaHubProviders(payload: unknown): MsaHubProvider[] {
    const items = payload && typeof payload === 'object' ? (payload as { items?: unknown }).items : null;
    if (!Array.isArray(items)) return [];
    return items
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && typeof (item as { id?: unknown }).id === 'string')
        .map((item) => ({
            id: String(item.id),
            label: typeof item.label === 'string' ? item.label : String(item.id),
            needs_key: item.needs_key === true,
            configured: item.configured === true,
            source: item.source === 'environment' || item.source === 'saved' ? item.source : null,
            endpoint_configurable: item.endpoint_configurable === true,
            ready: item.ready === true,
            message: typeof item.message === 'string' ? item.message : '',
        }));
}

export function msaProviderKeyStatus(provider: MsaHubProvider): string {
    if (!provider.needs_key) return 'No key needed';
    if (provider.source === 'environment') return 'Set by environment';
    if (provider.source === 'saved' && provider.configured) return 'Key saved';
    return 'No key';
}

export function msaHubIndicatorClass(providers: MsaHubProvider[], local: { running: boolean; allRunning: boolean } | null): string {
    if (local) return local.allRunning ? 'bg-blue-400' : local.running ? 'bg-amber-400' : 'bg-slate-600';
    const ready = providers.filter((provider) => provider.ready).length;
    if (providers.length > 0 && ready === providers.length) return 'bg-blue-400';
    return ready > 0 ? 'bg-amber-400' : 'bg-slate-600';
}
