import { useEffect, useState } from 'react';
import {
    useBioXpInterlinkConnect,
    useBioXpInterlinkDiagnostics,
    useBioXpInterlinkDisconnect,
    useBioXpInterlinkLogs,
    useBioXpInterlinkState,
    useForgetBioXpInterlinkSettings,
    useSaveBioXpInterlinkSettings,
} from '../lib/bioxpClient';
import type { BioXpInterlinkSettings } from '../lib/bioxpClient';

const getErrorMessage = (error: unknown) => {
    if (error instanceof Error) return error.message;
    if (typeof error === 'string') return error;
    if (error && typeof error === 'object') return JSON.stringify(error);
    return null;
};

const actionSummary = (data: unknown) => {
    if (!data || typeof data !== 'object') return null;
    const record = data as Record<string, any>;
    const commandResult = record.command_result as Record<string, any> | undefined;
    const pieces = [
        record.action ? String(record.action).toUpperCase() : null,
        typeof record.active === 'boolean' ? `active=${record.active ? 'yes' : 'no'}` : null,
        typeof record.reachable === 'boolean' ? `reachable=${record.reachable ? 'yes' : 'no'}` : null,
        typeof commandResult?.returncode === 'number' ? `rc=${commandResult.returncode}` : null,
        record.runtime_note ? String(record.runtime_note) : null,
    ].filter(Boolean);
    return pieces.length ? pieces.join(' | ') : JSON.stringify(data);
};

export function BioXpInterlinkMenu() {
    const [isOpen, setIsOpen] = useState(false);
    const state = useBioXpInterlinkState(false, isOpen ? 5000 : false);
    const saveSettings = useSaveBioXpInterlinkSettings();
    const forgetSettings = useForgetBioXpInterlinkSettings();
    const connect = useBioXpInterlinkConnect();
    const disconnect = useBioXpInterlinkDisconnect();
    const diagnostics = useBioXpInterlinkDiagnostics();
    const logs = useBioXpInterlinkLogs();

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
    const indicatorClass = active
        ? reachable === false
            ? 'bg-amber-400'
            : 'bg-emerald-400'
        : configured
            ? 'bg-sky-400'
            : 'bg-slate-600';
    const statusLabel = active
        ? reachable === false
            ? 'DEGRADED'
            : 'LINKED'
        : configured
            ? 'SAVED'
            : 'QUIET';

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
        getErrorMessage(logs.error);

    const busy =
        saveSettings.isPending ||
        forgetSettings.isPending ||
        connect.isPending ||
        disconnect.isPending ||
        diagnostics.isPending ||
        logs.isPending;

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
                            <div>
                                <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider">BioXP robot interlink</p>
                                <p className="text-[11px] text-slate-500">
                                    Saved profile is inactive until an operator presses Connect. This panel does not home, arm, recover motion, or move axes.
                                </p>
                            </div>
                            <button
                                type="button"
                                onClick={() => state.refetch()}
                                disabled={state.isFetching || busy}
                                className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                Refresh
                            </button>
                        </div>

                        <div className="grid grid-cols-2 gap-2 text-[11px] font-mono text-slate-400">
                            <div>state: <span className="text-slate-200">{statusLabel}</span></div>
                            <div>reachable: <span className="text-slate-200">{reachable == null ? 'unknown' : reachable ? 'yes' : 'no'}</span></div>
                            <div className="col-span-2 break-all">active URL: <span className="text-cyan-300">{state.data?.active ? state.data?.robot_api_url : '(inactive)'}</span></div>
                            <div className="col-span-2 break-all">recommended: <span className="text-cyan-300">{state.data?.recommended_url || 'robot runtime URL pending'}</span></div>
                        </div>

                        <div className="space-y-2 rounded border border-slate-700 bg-slate-900/40 p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-300">Profile</p>
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

                        <div className="grid grid-cols-2 gap-2">
                            <button
                                type="button"
                                onClick={() => runAction((callbacks) => diagnostics.mutate({ probe: true }, callbacks))}
                                disabled={busy}
                                className="px-3 py-2 text-xs font-semibold rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                Diagnostics
                            </button>
                            <div className="space-y-1 rounded border border-slate-700 bg-slate-900/60 p-2">
                                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Robot service logs</p>
                                <p className="text-[11px] text-slate-400">Fetches the last 120 robot-local API service lines when supported by this deployment.</p>
                                <button
                                    type="button"
                                    onClick={() => runAction((callbacks) => logs.mutate({ tail: 120 }, callbacks))}
                                    disabled={busy || !configured}
                                    className="mt-1 px-3 py-2 text-xs font-semibold rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                >
                                    Fetch robot logs
                                </button>
                            </div>
                        </div>

                        <div className="space-y-2 rounded border border-slate-600/40 bg-slate-900/60 p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-300">Lifecycle actions unavailable here</p>
                            <p className="text-[11px] text-slate-400">
                                BMS is a thin HTTP interlink in this deployment. The API container cannot reach the robot's raw FastAPI port from outside Docker networking and cannot execute robot SSH lifecycle commands, so reset/reboot controls are intentionally not shown.
                            </p>
                            <p className="text-[11px] text-slate-500">
                                Use the robot-local control path for service restart/reboot, then explicitly reconnect BIOXP LINK. This panel never homes, arms, recovers motion, or moves axes.
                            </p>
                        </div>

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
