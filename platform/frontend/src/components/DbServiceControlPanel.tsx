import { useEffect, useState } from 'react';

const DB_SERVICE_ENDPOINT = '/api/system/db-service';
const DB_SERVICE_OFFLINE_MESSAGE = 'db_service_offline — use BMS DB service → Start';
const DB_SERVICE_COMMANDS = [
    'bms db-service status',
    'bms db-service start',
    'bms db-service restart',
    'bms db-service logs --tail 120',
];

interface DbLogicalDatabaseStatus {
    name?: string;
    role?: string;
    storage_mode?: string;
    status?: string;
    reachable?: boolean;
    note?: string | null;
}

interface DbServiceStatus {
    component?: string;
    service_id?: string;
    display_name?: string;
    service_name?: string;
    container_name?: string;
    optional_at_boot?: boolean;
    control_mode?: string;
    state?: string;
    health?: string;
    runtime_available?: boolean;
    runtime_note?: string | null;
    offline_message?: string;
    host_agent_available?: boolean;
    logical_databases?: DbLogicalDatabaseStatus[];
    commands?: string[];
    logs?: string;
    logs_tail?: number;
    last_action?: string;
    action_output?: string;
}

interface DbServiceControlPanelProps {
    embeddedContext?: 'assay-db-debug' | 'topbar-control-panel';
    title?: string;
    subtitle?: string;
    className?: string;
    autoRefresh?: boolean;
}

export function DbServiceControlPanel({
    embeddedContext = 'assay-db-debug',
    title = 'BMS DB service',
    subtitle = 'Start, restart, inspect health, and tail logs for the optional DB runtime. API/web boot stays available when this is offline.',
    className = '',
    autoRefresh = true,
}: DbServiceControlPanelProps) {
    const [loading, setLoading] = useState<string | null>(null);
    const [status, setStatus] = useState<DbServiceStatus | null>(null);
    const [message, setMessage] = useState<string | null>(null);

    const fetchDbServiceStatus = async (): Promise<DbServiceStatus | null> => {
        try {
            const response = await fetch(DB_SERVICE_ENDPOINT, { cache: 'no-store' });
            const body = await response.json().catch(() => ({})) as DbServiceStatus & { detail?: unknown };
            if (!response.ok) {
                throw new Error(String(body?.detail || `BMS DB service status failed (${response.status})`));
            }
            setStatus(body);
            return body;
        } catch (error) {
            const fallback: DbServiceStatus = {
                component: 'db-service',
                service_id: 'bms-db-service',
                display_name: 'BMS DB service',
                state: 'unreachable',
                health: 'offline',
                runtime_available: false,
                runtime_note: error instanceof Error ? error.message : String(error),
                offline_message: DB_SERVICE_OFFLINE_MESSAGE,
                commands: DB_SERVICE_COMMANDS,
                logical_databases: [
                    {
                        name: 'bms_core_runtime',
                        role: 'core-runtime',
                        storage_mode: 'sqlite-legacy',
                        status: 'legacy-fallback-active',
                        reachable: true,
                    },
                    {
                        name: 'bms_analytical_data',
                        role: 'assay-analytics',
                        storage_mode: 'postgres',
                        status: 'offline',
                        reachable: false,
                    },
                ],
            };
            setStatus(fallback);
            return fallback;
        }
    };

    useEffect(() => {
        if (!autoRefresh) {
            return;
        }
        void fetchDbServiceStatus();
    }, [autoRefresh]);

    const runAction = async (action: 'start' | 'restart' | 'health' | 'logs') => {
        setLoading(action);
        setMessage(null);
        try {
            const response = await fetch(`/api/system/db-service/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tail: 120 }),
            });
            const body = await response.json().catch(() => ({})) as DbServiceStatus & { detail?: unknown };
            if (!response.ok) {
                throw new Error(String(body?.detail || `BMS DB service ${action} failed (${response.status})`));
            }
            setStatus(body);
            setMessage(`✓ ${action === 'health' ? 'Health refreshed' : action === 'logs' ? 'Logs refreshed' : `${action} requested`}`);
        } catch (error) {
            setMessage(`✗ ${error instanceof Error ? error.message : String(error)}`);
        } finally {
            setLoading(null);
        }
    };

    const health = String(status?.health || 'unknown');
    const state = String(status?.state || 'unknown');
    const runtimeAvailable = Boolean(status?.runtime_available);
    const displayName = status?.display_name || 'BMS DB service';
    const indicatorClass = health === 'healthy'
        ? 'bg-emerald-400'
        : health === 'degraded' || state === 'running'
            ? 'bg-amber-400'
            : health === 'offline' || state === 'stopped' || state === 'missing' || state === 'unreachable'
                ? 'bg-rose-400'
                : 'bg-slate-600';
    const commands = status?.commands && status.commands.length > 0 ? status.commands : DB_SERVICE_COMMANDS;
    const note = status?.runtime_note || status?.offline_message || (!runtimeAvailable ? DB_SERVICE_OFFLINE_MESSAGE : 'BMS DB service available');
    const logicalDatabases = status?.logical_databases ?? [];

    return (
        <section
            className={`rounded-xl border border-slate-700 bg-slate-950/45 p-3 space-y-3 ${className}`}
            data-bms-db-service-control-panel={embeddedContext}
        >
            <div className="flex items-start justify-between gap-3 border-b border-slate-700 pb-2">
                <div>
                    <p className="text-xs font-semibold text-slate-200 uppercase tracking-wider">{title}</p>
                    <p className="text-[11px] text-slate-400">{subtitle}</p>
                </div>
                <div className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 rounded-full ${indicatorClass}`} title={`${state} / ${health}`} />
                    <button
                        onClick={() => void fetchDbServiceStatus()}
                        disabled={loading !== null}
                        className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                    >
                        Refresh
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-2 text-xs text-slate-300 md:grid-cols-4">
                <div><span className="text-slate-500">State:</span> {state}</div>
                <div><span className="text-slate-500">Health:</span> {health}</div>
                <div><span className="text-slate-500">Service:</span> {status?.service_name || 'bms-analytical-postgres'}</div>
                <div><span className="text-slate-500">Mode:</span> {status?.control_mode || 'unknown'}</div>
            </div>

            <div className={`rounded border px-2 py-1.5 text-[11px] ${runtimeAvailable ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100' : 'border-amber-500/30 bg-amber-500/10 text-amber-100'}`}>
                {note}
            </div>

            {logicalDatabases.length > 0 && (
                <div className="grid gap-2 text-xs text-slate-300 md:grid-cols-2">
                    {logicalDatabases.map((database) => (
                        <div key={`${database.role || 'db'}-${database.name || 'unknown'}`} className="rounded border border-slate-700 bg-slate-950/50 p-2">
                            <div className="font-semibold text-slate-200">{database.name || 'unknown database'}</div>
                            <div className="text-[11px] text-slate-400">{database.role || 'unknown role'} · {database.storage_mode || 'unknown storage'}</div>
                            <div className={database.reachable ? 'text-emerald-300' : 'text-amber-300'}>{database.status || (database.reachable ? 'reachable' : 'offline')}</div>
                        </div>
                    ))}
                </div>
            )}

            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                <button onClick={() => void runAction('start')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-emerald-500/50 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50">Start BMS DB service</button>
                <button onClick={() => void runAction('restart')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-blue-500/50 text-blue-300 hover:bg-blue-500/20 disabled:opacity-50">Restart BMS DB service</button>
                <button onClick={() => void runAction('health')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50">Health</button>
                <button onClick={() => void runAction('logs')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50">Logs</button>
            </div>

            {loading && <div className="text-[11px] text-cyan-300">Running {displayName} {loading}...</div>}
            {message && <div className={`text-[11px] ${message.startsWith('✗') ? 'text-rose-300' : 'text-emerald-300'}`}>{message}</div>}

            <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Operator commands</p>
                <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded bg-slate-950/70 p-2 text-[11px] leading-relaxed text-slate-200 border border-slate-700">{commands.join('\n')}</pre>
            </div>

            <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Logs</p>
                <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded bg-slate-950/70 p-2 text-[11px] leading-relaxed text-slate-300 border border-slate-700">{status?.logs || status?.action_output || 'No BMS DB service logs loaded yet.'}</pre>
            </div>
        </section>
    );
}

export function DbServiceMenu() {
    const [isOpen, setIsOpen] = useState(false);

    return (
        <div className="relative shrink-0" data-bms-db-service-menu="true">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
                title="BMS DB service control panel"
            >
                <span className="h-2 w-2 rounded-full bg-slate-500" />
                BMS DB
            </button>

            {isOpen && (
                <>
                    <div className="fixed inset-0 z-40" data-bms-drag-scroll-ignore="true" onClick={() => setIsOpen(false)} />
                    <div
                        className="absolute right-0 top-full mt-2 w-[620px] max-w-[calc(100vw-1rem)] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 p-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <DbServiceControlPanel
                            embeddedContext="topbar-control-panel"
                            title="BMS DB service"
                            subtitle="Optional at API/web boot. Start/restart the DB and inspect degraded assay analytics state."
                            autoRefresh={isOpen}
                        />
                    </div>
                </>
            )}
        </div>
    );
}
