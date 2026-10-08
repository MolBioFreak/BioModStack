import { useEffect, useState } from 'react';
import {
    useBioXpInterlinkConnect,
    useBioXpInterlinkDiagnostics,
    useBioXpInterlinkDisconnect,
    useBioXpInterlinkLogs,
    useBioXpInterlinkState,
    useBioXpRobotReboot,
    useBioXpRuntimeReset,
    useForgetBioXpInterlinkSettings,
    useSaveBioXpInterlinkSettings,
} from '../lib/bioxpClient';
import type { BioXpInterlinkSettings } from '../lib/bioxpClient';
import { deriveBioXpInterlinkMenuStatus } from './bioxpInterlinkStatus';

const getErrorMessage = (error: unknown) => {
    if (error instanceof Error) return error.message;
    if (typeof error === 'string') return error;
    if (error && typeof error === 'object') return JSON.stringify(error);
    return null;
};

const actionSummary = (data: unknown) => {
    if (!data || typeof data !== 'object') return null;
    const record = data as Record<string, UntypedApiValue>;
    const commandResult = record.command_result as Record<string, UntypedApiValue> | undefined;
    const pieces = [
        record.action ? String(record.action).toUpperCase() : null,
        typeof record.ok === 'boolean' ? `ok=${record.ok ? 'yes' : 'no'}` : null,
        typeof record.supported === 'boolean' ? `supported=${record.supported ? 'yes' : 'no'}` : null,
        typeof record.active === 'boolean' ? `active=${record.active ? 'yes' : 'no'}` : null,
        typeof record.reachable === 'boolean' ? `reachable=${record.reachable ? 'yes' : 'no'}` : null,
        typeof commandResult?.returncode === 'number' ? `rc=${commandResult.returncode}` : null,
        record.reason ? String(record.reason) : null,
        record.runtime_note ? String(record.runtime_note) : null,
    ].filter(Boolean);
    return pieces.length ? pieces.join(' | ') : JSON.stringify(data);
};

const maskEndpointForDisplay = (value?: string | null) => {
    if (!value) return 'not configured';

    const maskHost = (host: string) => {
        const ipv4 = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
        if (ipv4) return `${ipv4[1]}.${ipv4[2]}.xxx.xxx`;
        const parts = host.split('.').filter(Boolean);
        if (parts.length > 2) return `${parts[0]}.…`;
        if (host.length > 12) return `${host.slice(0, 6)}…${host.slice(-4)}`;
        return host;
    };

    try {
        const endpoint = new URL(value);
        endpoint.hostname = maskHost(endpoint.hostname);
        endpoint.username = '';
        endpoint.password = '';
        return endpoint.toString().replace(/\/$/, '');
    } catch {
        return value.replace(/(\d+\.\d+)\.\d+\.\d+(:\d+)?/g, '$1.xxx.xxx$2');
    }
};

const BIOXP_INTERLINK_DOC_LINKS = [
    { label: 'BMS interlink spec', href: 'https://github.com/MolBioFreak/BioModStack/blob/main/docs/plans/2026-05-08-bioxp-workstation-interlink-control-panel-spec.md' },
    { label: 'BioXP vendor', href: 'https://telesisbio.com/products/bioxp-system/' },
    { label: 'PyUSB GitHub', href: 'https://github.com/pyusb/pyusb' },
    { label: 'FastAPI docs', href: 'https://fastapi.tiangolo.com/' },
] as const;

export function BioXpInterlinkMenu() {
    const [isOpen, setIsOpen] = useState(false);
    const state = useBioXpInterlinkState(false, isOpen ? 5000 : 15000);
    const saveSettings = useSaveBioXpInterlinkSettings();
    const forgetSettings = useForgetBioXpInterlinkSettings();
    const connect = useBioXpInterlinkConnect();
    const disconnect = useBioXpInterlinkDisconnect();
    const diagnostics = useBioXpInterlinkDiagnostics();
    const logs = useBioXpInterlinkLogs();
    const runtimeReset = useBioXpRuntimeReset();
    const robotReboot = useBioXpRobotReboot();

    const [settings, setSettings] = useState<BioXpInterlinkSettings>({
        robot_api_url: '',
        robot_ssh_host: 'robot',
        connection_mode: 'direct_http',
        display_name: 'BioXP3200',
    });
    const [latestAction, setLatestAction] = useState<unknown>(null);

    useEffect(() => {
        const current = state.data;
        if (!current) return;
        setSettings((prev) => ({
            robot_api_url: current.robot_api_url || current.recommended_url || prev.robot_api_url || '',
            robot_ssh_host: current.robot_ssh_host || prev.robot_ssh_host || 'robot',
            connection_mode: current.connection_mode || prev.connection_mode || 'direct_http',
            display_name: current.display_name || prev.display_name || 'BioXP3200',
        }));
    }, [state.data]);

    const active = Boolean(state.data?.active);
    const configured = Boolean(state.data?.configured);
    const reachable = state.data?.reachable;
    const interlinkStatus = deriveBioXpInterlinkMenuStatus({
        active,
        configured,
        reachable,
        lastProbeAt: state.data?.last_probe_at,
    });
    const { indicatorClass, statusLabel, humanStatusLabel, reachabilityText } = interlinkStatus;
    const endpointForDisplay = state.data?.active
        ? state.data.robot_api_url
        : state.data?.robot_api_url || settings.robot_api_url || state.data?.recommended_url;

    const runAction = <T,>(call: (callbacks: { onSuccess: (data: T) => void; onError: (error: unknown) => void }) => void) => {
        call({
            onSuccess: (data) => setLatestAction(data),
            onError: (error) => setLatestAction({ ok: false, error: getErrorMessage(error) ?? 'BioXP interlink action failed' }),
        });
    };

    const errorMessage =
        getErrorMessage(state.error) ||
        getErrorMessage(saveSettings.error) ||
        getErrorMessage(forgetSettings.error) ||
        getErrorMessage(connect.error) ||
        getErrorMessage(disconnect.error) ||
        getErrorMessage(diagnostics.error) ||
        getErrorMessage(logs.error) ||
        getErrorMessage(runtimeReset.error) ||
        getErrorMessage(robotReboot.error);

    const busy =
        saveSettings.isPending ||
        forgetSettings.isPending ||
        connect.isPending ||
        disconnect.isPending ||
        diagnostics.isPending ||
        logs.isPending ||
        runtimeReset.isPending ||
        robotReboot.isPending;

    return (
        <div className="relative" data-bms-bioxp-interlink-menu="true">
            <button
                type="button"
                onClick={() => setIsOpen((value) => !value)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
                title="BioXP robot interlink"
            >
                <span className={`w-2 h-2 rounded-full ${indicatorClass}`} />
                BIOXP LINK
                <span className="text-[10px] text-slate-500">{statusLabel}</span>
            </button>

            {isOpen && (
                <>
                    <div className="fixed inset-0 z-40" data-bms-drag-scroll-ignore="true" onClick={() => setIsOpen(false)} />
                    <div
                        className="absolute right-0 top-full mt-2 w-[520px] max-w-[calc(100vw-1rem)] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 p-3 space-y-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="flex items-center justify-between border-b border-slate-700 pb-2">
                            <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider">BioXP robot interlink</p>
                            <button
                                type="button"
                                onClick={() => state.refetch()}
                                disabled={state.isFetching || busy}
                                className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                Refresh
                            </button>
                        </div>

                        <div className="rounded border border-slate-700 bg-slate-900/40 p-2 text-[11px] text-slate-300">
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                                <span>Status: <span className="font-semibold text-slate-100">{humanStatusLabel}</span></span>
                                <span className="text-slate-500">•</span>
                                <span>{reachabilityText}</span>
                            </div>
                            <div className="mt-1 break-all font-mono text-slate-400">
                                Endpoint: <span className="text-cyan-300">{maskEndpointForDisplay(endpointForDisplay)}</span>
                            </div>
                        </div>

                        <div className="rounded border border-slate-700 bg-slate-900/40 p-2">
                            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Documentation</p>
                            <div className="flex flex-wrap gap-2">
                                {BIOXP_INTERLINK_DOC_LINKS.map((link) => (
                                    <a
                                        key={link.href}
                                        href={link.href}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="rounded border border-slate-600 px-2 py-1 text-[11px] font-semibold text-cyan-300 hover:border-cyan-500/70 hover:bg-cyan-500/10"
                                    >
                                        {link.label}
                                    </a>
                                ))}
                            </div>
                        </div>

                        <details open={!configured} className="rounded border border-slate-700 bg-slate-900/40 p-3">
                            <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wider text-slate-300">
                                Profile settings
                            </summary>
                            <div className="mt-3 space-y-2">
                                <input
                                    type="text"
                                    value={settings.robot_api_url}
                                    onChange={(event) => setSettings((prev) => ({ ...prev, robot_api_url: event.target.value }))}
                                    placeholder={state.data?.recommended_url || 'Robot API URL'}
                                    className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-xs font-mono text-slate-200"
                                />
                                <div className="grid grid-cols-3 gap-2">
                                    <input
                                        type="text"
                                        value={settings.robot_ssh_host || ''}
                                        onChange={(event) => setSettings((prev) => ({ ...prev, robot_ssh_host: event.target.value }))}
                                        placeholder="robot"
                                        className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-xs font-mono text-slate-200"
                                    />
                                    <input
                                        type="text"
                                        value={settings.connection_mode || ''}
                                        onChange={(event) => setSettings((prev) => ({ ...prev, connection_mode: event.target.value }))}
                                        placeholder="direct_http"
                                        className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-xs font-mono text-slate-200"
                                    />
                                    <input
                                        type="text"
                                        value={settings.display_name || ''}
                                        onChange={(event) => setSettings((prev) => ({ ...prev, display_name: event.target.value }))}
                                        placeholder="BioXP3200"
                                        className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-xs font-mono text-slate-200"
                                    />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <button
                                        type="button"
                                        onClick={() => runAction((callbacks) => saveSettings.mutate(settings, callbacks))}
                                        disabled={busy || !settings.robot_api_url.trim()}
                                        className="px-3 py-2 text-xs font-semibold rounded border border-cyan-500/50 text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-50"
                                    >
                                        Save settings
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => runAction((callbacks) => connect.mutate(settings, callbacks))}
                                        disabled={busy || !settings.robot_api_url.trim()}
                                        className="px-3 py-2 text-xs font-semibold rounded border border-emerald-500/50 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
                                    >
                                        Connect
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => runAction((callbacks) => disconnect.mutate(undefined, callbacks))}
                                        disabled={busy || !active}
                                        className="px-3 py-2 text-xs font-semibold rounded border border-amber-500/50 text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
                                    >
                                        Disconnect
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => runAction((callbacks) => forgetSettings.mutate(undefined, callbacks))}
                                        disabled={busy || !configured}
                                        className="px-3 py-2 text-xs font-semibold rounded border border-rose-500/50 text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
                                    >
                                        Forget saved profile
                                    </button>
                                </div>
                            </div>
                        </details>

                        <div className="grid grid-cols-2 gap-2">
                            <button
                                type="button"
                                onClick={() => runAction((callbacks) => diagnostics.mutate({ probe: true }, callbacks))}
                                disabled={busy}
                                className="px-3 py-2 text-xs font-semibold rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                Diagnostics
                            </button>
                            <button
                                type="button"
                                onClick={() => runAction((callbacks) => logs.mutate({ tail: 120 }, callbacks))}
                                disabled={busy || !configured}
                                className="px-3 py-2 text-xs font-semibold rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                Robot logs
                            </button>
                        </div>

                        <details className="rounded border border-amber-500/30 bg-amber-950/10 p-3">
                            <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wider text-amber-200">
                                Advanced controls
                            </summary>
                            <div className="mt-3 grid grid-cols-2 gap-3">
                                <button
                                    type="button"
                                    onClick={() => runAction((callbacks) => runtimeReset.mutate({ operator_ack: 'RESET BIOXP RUNTIME', tail: 120 }, callbacks))}
                                    disabled={busy || !configured}
                                    className="px-3 py-2 text-xs font-semibold rounded border border-amber-500/50 text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
                                >
                                    Restart runtime
                                </button>
                                <button
                                    type="button"
                                    onClick={() => runAction((callbacks) => robotReboot.mutate({ operator_ack: 'REBOOT ROBOT', tail: 120 }, callbacks))}
                                    disabled={busy || !configured}
                                    className="px-3 py-2 text-xs font-semibold rounded border border-rose-500/60 text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
                                >
                                    Reboot host
                                </button>
                            </div>
                        </details>

                        {Boolean(errorMessage || latestAction) && (
                            <pre className={`max-h-48 overflow-auto rounded border p-2 text-[10px] whitespace-pre-wrap ${errorMessage ? 'border-rose-500/30 text-rose-300 bg-rose-500/5' : 'border-slate-700 text-slate-300 bg-slate-950/80'}`}>
                                {errorMessage || actionSummary(latestAction) || JSON.stringify(latestAction, null, 2)}
                            </pre>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

export default BioXpInterlinkMenu;
