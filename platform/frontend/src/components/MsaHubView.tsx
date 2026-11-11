import React from 'react';
import {
    msaProviderKeyStatus,
    type MsaHubProvider,
    type MsaLocalGpu,
    type MsaLocalServerSettings,
} from './msaHubTypes';

export interface MsaLocalServerSectionProps {
    settings: MsaLocalServerSettings;
    loading: string | null;
    availableGpus: MsaLocalGpu[];
    summary: string;
    onRefresh: () => void;
    onStart: () => void;
    onStop: () => void;
    onSaveSettings: (patch: Partial<MsaLocalServerSettings>) => void;
    onIdleMinutesChange: (minutes: number) => void;
}

export interface MsaHubViewProps {
    isOpen: boolean;
    onToggle: () => void;
    onClose: () => void;
    indicatorClass: string;
    providers: MsaHubProvider[];
    providersLoaded: boolean;
    busyProvider: string | null;
    error: string | null;
    onSaveCredential: (providerId: string, apiKey: string, endpoint?: string) => void;
    onClearCredential: (providerId: string) => void;
    onSetController: (providerId: string, enabled: boolean) => void;
    /** Present only when the installation declares the local_msa feature. */
    local: MsaLocalServerSectionProps | null;
}

function MsaProviderRow({ provider, busy, onSave, onClear, onSetController }: {
    provider: MsaHubProvider;
    busy: boolean;
    onSave: MsaHubViewProps['onSaveCredential'];
    onClear: MsaHubViewProps['onClearCredential'];
    onSetController: MsaHubViewProps['onSetController'];
}) {
    const fromEnvironment = provider.source === 'environment';
    return (
        <div className="space-y-2 border border-slate-700 rounded-lg p-2" data-msa-provider={provider.id}>
            <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-slate-300">{provider.label}</span>
                <span className="text-xs text-slate-400">{msaProviderKeyStatus(provider)}</span>
            </div>
            <p className="text-xs text-slate-400">{provider.ready ? 'Ready' : provider.message || 'Not ready'}</p>
            {provider.needs_controller && (
                <button
                    type="button"
                    onClick={() => onSetController(provider.id, !provider.controller_enabled)}
                    disabled={busy}
                    className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                >
                    {provider.controller_enabled ? 'Turn off for this computer' : 'Use this computer for searches'}
                </button>
            )}
            {provider.needs_key && (
                // Uncontrolled on purpose: the typed key is read once on submit and never held in state or props.
                <form
                    className="space-y-2"
                    onSubmit={(event) => {
                        event.preventDefault();
                        const fields = event.currentTarget.elements;
                        const keyInput = fields.namedItem('api_key') as HTMLInputElement | null;
                        const endpointInput = fields.namedItem('endpoint') as HTMLInputElement | null;
                        const apiKey = keyInput?.value ?? '';
                        if (keyInput) keyInput.value = '';
                        if (apiKey.trim()) onSave(provider.id, apiKey, endpointInput?.value || undefined);
                    }}
                >
                    {provider.endpoint_configurable && (
                        <input
                            type="text"
                            name="endpoint"
                            autoComplete="off"
                            placeholder="Endpoint (optional)"
                            disabled={busy || fromEnvironment}
                            className="w-full px-2 py-1 rounded bg-slate-900 border border-slate-600 text-slate-100 text-sm"
                        />
                    )}
                    <div className="flex items-center gap-2">
                        <input
                            type="password"
                            name="api_key"
                            autoComplete="off"
                            aria-label={`${provider.label} API key`}
                            placeholder={provider.configured ? 'Replace key' : 'API key'}
                            disabled={busy || fromEnvironment}
                            className="flex-1 min-w-0 px-2 py-1 rounded bg-slate-900 border border-slate-600 text-slate-100 text-sm"
                        />
                        <button
                            type="submit"
                            disabled={busy || fromEnvironment}
                            className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                        >
                            Save
                        </button>
                        <button
                            type="button"
                            onClick={() => onClear(provider.id)}
                            disabled={busy || provider.source !== 'saved'}
                            className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                        >
                            Clear
                        </button>
                    </div>
                </form>
            )}
        </div>
    );
}

export function MsaLocalServerSection(props: MsaLocalServerSectionProps) {
    const { settings, loading, availableGpus, summary } = props;
    return (
        <React.Fragment>
            <div className="flex items-center justify-between border-b border-slate-700 pb-2">
                <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Local MSA server</p>
                <button
                    onClick={props.onRefresh}
                    disabled={loading !== null}
                    className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                >
                    Refresh
                </button>
            </div>

            <div className="flex gap-2">
                <button
                    onClick={props.onStart}
                    disabled={loading !== null}
                    className="flex-1 px-3 py-2 text-xs font-semibold rounded border border-blue-500/50 text-blue-300 hover:bg-blue-500/20 disabled:opacity-50"
                >
                    {loading === 'start' ? 'Starting...' : 'Start Server'}
                </button>
                <button
                    onClick={props.onStop}
                    disabled={loading !== null}
                    className="flex-1 px-3 py-2 text-xs font-semibold rounded border border-rose-500/50 text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
                >
                    {loading === 'stop' ? 'Stopping...' : 'Stop Server'}
                </button>
            </div>

            <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                    type="checkbox"
                    checked={settings.include_envdb_on_start}
                    onChange={(e) => props.onSaveSettings({ include_envdb_on_start: e.target.checked })}
                    disabled={loading !== null}
                    className="rounded border-slate-500 bg-slate-700"
                />
                Start EnvDB server with UniRef (higher VRAM/IO)
            </label>

            <div className="space-y-1">
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Pinned GPU
                </label>
                <select
                    value={settings.pinned_gpu_id ?? ''}
                    onChange={(e) => {
                        const raw = e.target.value;
                        props.onSaveSettings({ pinned_gpu_id: raw === '' ? null : Number(raw) });
                    }}
                    disabled={loading !== null}
                    className="w-full px-3 py-2 rounded border border-slate-600 bg-slate-900 text-slate-100 text-sm"
                >
                    <option value="">Auto-select from scheduler</option>
                    {availableGpus.map((gpu) => (
                        <option key={gpu.index} value={gpu.index}>
                            GPU {gpu.index}: {gpu.name}{typeof gpu.memory_total_mb === 'number' ? ` (${(gpu.memory_total_mb / 1024).toFixed(0)} GB)` : ''}
                        </option>
                    ))}
                </select>
                <p className="text-xs text-slate-400">
                    Default GPU for MSA controls.
                </p>
            </div>

            <div className="space-y-2 border border-slate-700 rounded-lg p-2">
                <label className="flex items-center gap-2 text-sm text-slate-300">
                    <input
                        type="checkbox"
                        checked={settings.auto_stop_idle_enabled}
                        onChange={(e) => props.onSaveSettings({ auto_stop_idle_enabled: e.target.checked })}
                        disabled={loading !== null}
                        className="rounded border-slate-500 bg-slate-700"
                    />
                    Auto-stop when idle
                </label>
                <div className="flex items-center gap-2 text-xs text-slate-400">
                    <span>Idle threshold (minutes):</span>
                    <input
                        type="number"
                        min={1}
                        value={settings.auto_stop_idle_minutes}
                        disabled={!settings.auto_stop_idle_enabled || loading !== null}
                        onChange={(e) => {
                            const val = parseInt(e.target.value || '10', 10);
                            if (!Number.isNaN(val)) props.onIdleMinutesChange(val);
                        }}
                        onBlur={() => props.onSaveSettings({ auto_stop_idle_minutes: settings.auto_stop_idle_minutes })}
                        className="w-20 px-2 py-1 rounded bg-slate-900 border border-slate-600 text-slate-100"
                    />
                </div>
            </div>

            <textarea
                readOnly
                value={summary}
                className="w-full h-44 p-2 rounded-lg border border-slate-700 bg-slate-900 text-slate-200 text-xs font-mono resize-none"
            />
        </React.Fragment>
    );
}

export function MsaHubView(props: MsaHubViewProps) {
    return (
        <div className="relative">
            <button
                onClick={props.onToggle}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
            >
                <div className={`w-2 h-2 rounded-full ${props.indicatorClass}`} />
                MSA
            </button>

            {props.isOpen && (
                <>
                    <div className="fixed inset-0 z-40" data-bms-drag-scroll-ignore="true" onClick={props.onClose} />

                    <div
                        className="absolute right-0 top-full mt-2 w-[460px] max-w-[calc(100vw-1rem)] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 p-3 space-y-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="flex items-center justify-between border-b border-slate-700 pb-2">
                            <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Remote providers</p>
                        </div>
                        {props.providers.length === 0 && (
                            <p className="text-xs text-slate-400">
                                {props.providersLoaded ? 'Provider setup could not be read.' : 'Reading provider setup…'}
                            </p>
                        )}
                        {props.providers.map((provider) => (
                            <MsaProviderRow
                                key={provider.id}
                                provider={provider}
                                busy={props.busyProvider !== null}
                                onSave={props.onSaveCredential}
                                onClear={props.onClearCredential}
                                onSetController={props.onSetController}
                            />
                        ))}
                        {props.error && <p role="alert" className="text-xs text-rose-300">{props.error}</p>}

                        {props.local && <MsaLocalServerSection {...props.local} />}
                    </div>
                </>
            )}
        </div>
    );
}
