import { useEffect, useState } from 'react';

const STATS_TOOLS_ENDPOINT = '/api/system/stats-tools';
const STATS_TOOLS_OFFLINE_MESSAGE = 'stats_tools_offline — press Start';
const STATS_TOOLS_COMMANDS = [
    'bms stats-tools status',
    'bms stats-tools start',
    'bms stats-tools stop',
    'bms stats-tools restart',
    'bms stats-tools logs --tail 120',
];

const STATS_TOOLS_DOC_LINKS = [
    { label: 'BMS stats plan', href: 'https://github.com/MolBioFreak/BioModStack/blob/main/docs/reports/2026-05-05-bms-workflow-stats-tools-containerization-plan.md' },
    { label: 'R Project', href: 'https://www.r-project.org/' },
    { label: 'Plotly docs', href: 'https://plotly.com/javascript/' },
] as const;

interface StatsToolsStatus {
    component?: string;
    service_name?: string;
    container_name?: string;
    externalized?: boolean;
    optional_at_boot?: boolean;
    control_mode?: string;
    state?: string;
    health?: string;
    runtime_available?: boolean;
    runtime_note?: string | null;
    offline_message?: string;
    commands?: string[];
    logs?: string;
    logs_tail?: number;
    last_action?: string;
    action_output?: string;
}

interface StatsToolsControlPanelProps {
    embeddedContext?: 'stats-toolkit-debug' | 'topbar-control-panel';
    title?: string;
    subtitle?: string;
    className?: string;
    autoRefresh?: boolean;
}

export function StatsToolsControlPanel({
    embeddedContext = 'stats-toolkit-debug',
    title = 'Stats Tools',
    subtitle = 'Stats runtime actions + logs.',
    className = '',
    autoRefresh = true,
}: StatsToolsControlPanelProps) {
    const [loading, setLoading] = useState<string | null>(null);
    const [status, setStatus] = useState<StatsToolsStatus | null>(null);
    const [message, setMessage] = useState<string | null>(null);

    const fetchStatsToolsStatus = async (): Promise<StatsToolsStatus | null> => {
        try {
            const response = await fetch(STATS_TOOLS_ENDPOINT, { cache: 'no-store' });
            const body = await response.json().catch(() => ({})) as StatsToolsStatus & { detail?: unknown };
            if (!response.ok) {
                throw new Error(String(body?.detail || `stats-tools status failed (${response.status})`));
            }
            setStatus(body);
            return body;
        } catch (error) {
            const fallback: StatsToolsStatus = {
                component: 'stats-tools',
                state: 'unreachable',
                health: 'offline',
                runtime_available: false,
                runtime_note: error instanceof Error ? error.message : String(error),
                offline_message: STATS_TOOLS_OFFLINE_MESSAGE,
                commands: STATS_TOOLS_COMMANDS,
            };
            setStatus(fallback);
            return fallback;
        }
    };

    useEffect(() => {
        if (!autoRefresh) {
            return;
        }
        void fetchStatsToolsStatus();
    }, [autoRefresh]);

    const runAction = async (action: 'start' | 'stop' | 'restart' | 'health' | 'logs') => {
        setLoading(action);
        setMessage(null);
        try {
            const response = await fetch(`/api/system/stats-tools/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tail: 120 }),
            });
            const body = await response.json().catch(() => ({})) as StatsToolsStatus & { detail?: unknown };
            if (!response.ok) {
                throw new Error(String(body?.detail || `stats-tools ${action} failed (${response.status})`));
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
    const indicatorClass = health === 'healthy'
        ? 'bg-emerald-400'
        : health === 'degraded' || state === 'running'
            ? 'bg-amber-400'
            : health === 'offline' || state === 'stopped' || state === 'unreachable'
                ? 'bg-rose-400'
                : 'bg-slate-600';
    const commands = status?.commands && status.commands.length > 0 ? status.commands : STATS_TOOLS_COMMANDS;
    const rawNote = status?.runtime_note || status?.offline_message || (!runtimeAvailable ? STATS_TOOLS_OFFLINE_MESSAGE : '');
    const note = runtimeAvailable
        ? (status?.runtime_note && !/offline/i.test(status.runtime_note) ? status.runtime_note : '')
        : rawNote;

    return (
        <section
            className={`rounded-xl border border-slate-700 bg-slate-950/45 p-3 space-y-3 ${className}`}
            data-bms-stats-tools-control-panel={embeddedContext}
        >
            <div className="flex items-start justify-between gap-3 border-b border-slate-700 pb-2">
                <div>
                    <p className="text-xs font-semibold text-slate-200 uppercase tracking-wider">{title}</p>
                    <p className="text-[11px] text-slate-400">{subtitle}</p>
                </div>
                <div className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 rounded-full ${indicatorClass}`} title={`${state} / ${health}`} />
                    <button
                        onClick={() => void fetchStatsToolsStatus()}
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
                <div><span className="text-slate-500">Service:</span> {status?.service_name || 'bms-stats-tools'}</div>
                <div><span className="text-slate-500">Mode:</span> {status?.control_mode || 'unknown'}</div>
            </div>

            {note && (
                <div className={`rounded border px-2 py-1.5 text-[11px] ${runtimeAvailable ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100' : 'border-amber-500/30 bg-amber-500/10 text-amber-100'}`}>
                    {note}
                </div>
            )}

            <div className="rounded border border-slate-700 bg-slate-950/50 p-2">
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Documentation</p>
                <div className="flex flex-wrap gap-2">
                    {STATS_TOOLS_DOC_LINKS.map((link) => (
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

            <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
                <button onClick={() => void runAction('start')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-emerald-500/50 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50">Start</button>
                <button onClick={() => void runAction('stop')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-rose-500/50 text-rose-300 hover:bg-rose-500/20 disabled:opacity-50">Stop</button>
                <button onClick={() => void runAction('restart')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-blue-500/50 text-blue-300 hover:bg-blue-500/20 disabled:opacity-50">Restart</button>
                <button onClick={() => void runAction('health')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50">Health</button>
                <button onClick={() => void runAction('logs')} disabled={loading !== null} className="px-2 py-1.5 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50">Logs</button>
            </div>

            {loading && <div className="text-[11px] text-cyan-300">Running stats-tools {loading}...</div>}
            {message && <div className={`text-[11px] ${message.startsWith('✗') ? 'text-rose-300' : 'text-emerald-300'}`}>{message}</div>}

            <details className="rounded border border-slate-700 bg-slate-950/50 p-2 text-[11px] text-slate-300">
                <summary className="cursor-pointer font-semibold uppercase tracking-wider text-slate-400">CLI commands</summary>
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-slate-950/70 p-2 leading-relaxed text-slate-200 border border-slate-800">{commands.join('\n')}</pre>
            </details>

            <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Logs</p>
                <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded bg-slate-950/70 p-2 text-[11px] leading-relaxed text-slate-300 border border-slate-700">{status?.logs || status?.action_output || 'No stats-tools logs loaded yet.'}</pre>
            </div>
        </section>
    );
}

export function StatsToolsMenu() {
    const [isOpen, setIsOpen] = useState(false);

    return (
        <div className="relative shrink-0" data-bms-stats-tools-menu="true">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
                title="Stats Toolkit control panel"
            >
                <span className="h-2 w-2 rounded-full bg-slate-500" />
                Stats Tools
            </button>

            {isOpen && (
                <>
                    <div className="fixed inset-0 z-40" data-bms-drag-scroll-ignore="true" onClick={() => setIsOpen(false)} />
                    <div
                        className="absolute right-0 top-full mt-2 w-[560px] max-w-[calc(100vw-1rem)] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 p-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <StatsToolsControlPanel
                            embeddedContext="topbar-control-panel"
                            title="Stats Tools"
                            subtitle="Stats runtime actions + logs."
                            autoRefresh={isOpen}
                        />
                    </div>
                </>
            )}
        </div>
    );
}
