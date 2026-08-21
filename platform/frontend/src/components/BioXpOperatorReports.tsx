import { useBioXpOperatorReportCommands, useBioXpOperatorReportSummary } from '../lib/bioxpClient';

export function BioXpOperatorReports({ generation, connected }: { generation: number; connected: boolean }) {
    const summaryQuery = useBioXpOperatorReportSummary(generation, connected);
    const commandsQuery = useBioXpOperatorReportCommands(generation, connected, 25);
    const summary = connected && !summaryQuery.isError ? summaryQuery.data : undefined;
    const rows = connected && !commandsQuery.isError ? (commandsQuery.data?.rows ?? []) : [];
    const statusCounts = summary?.commands?.by_status ?? {};

    return (
        <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" aria-labelledby="bioxp-operator-reports-title">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 id="bioxp-operator-reports-title" className="text-lg font-semibold">Operator reports</h2>
                    <p className="mt-1 text-sm text-slate-400">Robot-owned audit summaries. BMS stores no duplicate command history.</p>
                </div>
                <span className="rounded bg-slate-900 px-2 py-1 text-xs font-mono text-slate-300">
                    generation {generation || '—'}
                </span>
            </div>
            {!connected && <p className="mt-3 text-sm text-slate-400">Connect to BioXP to read reports.</p>}
            {connected && (summaryQuery.isLoading || commandsQuery.isLoading) && (
                <p role="status" className="mt-3 text-sm text-cyan-200">Loading robot reports…</p>
            )}
            {connected && (summaryQuery.isError || commandsQuery.isError) && (
                <p role="alert" className="mt-3 text-sm text-amber-200">Robot report data is unavailable. No local history is shown.</p>
            )}
            {summary && (
                <dl className="mt-4 grid gap-2 sm:grid-cols-4">
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Commands</dt><dd className="mt-1 text-xl font-semibold">{summary.commands?.total ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Pipette operations</dt><dd className="mt-1 text-xl font-semibold">{summary.pipette_operations?.total ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Completed</dt><dd className="mt-1 text-xl font-semibold text-emerald-300">{statusCounts.completed ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Failed</dt><dd className="mt-1 text-xl font-semibold text-red-300">{statusCounts.failed ?? 0}</dd></div>
                </dl>
            )}
            {rows.length > 0 && (
                <div className="mt-4 overflow-x-auto">
                    <table className="min-w-full text-left text-xs">
                        <thead className="text-slate-400"><tr><th className="px-2 py-2">Command</th><th className="px-2 py-2">Operation</th><th className="px-2 py-2">Status</th><th className="px-2 py-2">Outcome</th></tr></thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={String(row.command_id ?? row.sequence)} className="border-t border-slate-800">
                                    <td className="px-2 py-2 font-mono text-cyan-200">{String(row.command_id ?? '—')}</td>
                                    <td className="px-2 py-2">{String(row.operation ?? '—')}</td>
                                    <td className="px-2 py-2">{String(row.status ?? '—')}</td>
                                    <td className="px-2 py-2">{String(row.outcome ?? row.failure_code ?? '—')}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {connected && !summaryQuery.isLoading && !commandsQuery.isLoading && rows.length === 0 && !summaryQuery.isError && !commandsQuery.isError && (
                <p className="mt-4 text-sm text-slate-400">No robot command records match the current report scope.</p>
            )}
        </section>
    );
}
