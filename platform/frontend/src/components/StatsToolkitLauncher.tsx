import { useQuery } from '@tanstack/react-query';

interface StatsToolkitStatus {
  id: string;
  display_name: string;
  available: boolean;
  ready: boolean;
  version: string | null;
  api_version: string | null;
  capability_count: number;
  entry_url: string;
  detail: string;
}

async function fetchStatsToolkitStatus(): Promise<StatsToolkitStatus> {
  const response = await fetch('/api/system/stats-toolkit', { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`status probe failed (${response.status})`);
  }
  return response.json() as Promise<StatsToolkitStatus>;
}

export function StatsToolkitLauncher() {
  const query = useQuery({
    queryKey: ['stats-toolkit-status'],
    queryFn: fetchStatsToolkitStatus,
    refetchInterval: 15_000,
  });
  const status = query.data;
  const ready = Boolean(status?.available && status.ready);

  return (
    <div className="mx-auto max-w-4xl p-6 text-[var(--text-primary)]">
      <section className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-6 shadow-lg">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--text-secondary)]">External add-on</p>
            <h1 className="mt-2 text-3xl font-bold">BioModStack Stats Toolkit</h1>
            <p className="mt-2 max-w-2xl text-sm text-[var(--text-secondary)]">
              Modality-neutral statistical tools served by the standalone local toolkit.
            </p>
          </div>
          <span className={`rounded-full px-3 py-1 text-sm font-semibold ${ready ? 'bg-emerald-500/20 text-emerald-300' : 'bg-amber-500/20 text-amber-300'}`}>
            {query.isLoading ? 'Checking' : ready ? 'Ready' : 'Offline'}
          </span>
        </div>

        <dl className="mt-6 grid gap-4 sm:grid-cols-3">
          <div><dt className="text-xs uppercase text-[var(--text-secondary)]">Version</dt><dd className="mt-1 font-semibold">{status?.version ?? '—'}</dd></div>
          <div><dt className="text-xs uppercase text-[var(--text-secondary)]">API</dt><dd className="mt-1 font-semibold">{status?.api_version ?? '—'}</dd></div>
          <div><dt className="text-xs uppercase text-[var(--text-secondary)]">Capabilities</dt><dd className="mt-1 font-semibold">{status?.capability_count ?? 0}</dd></div>
        </dl>

        <p className="mt-5 text-sm text-[var(--text-secondary)]">
          {query.error instanceof Error ? query.error.message : status?.detail ?? 'Waiting for standalone discovery.'}
        </p>

        <div className="mt-6 flex gap-3">
          <a
            href={status?.entry_url ?? 'http://127.0.0.1:18180/stats/'}
            className={`rounded-lg px-4 py-2 font-semibold ${ready ? 'bg-[var(--accent-primary)] text-white' : 'pointer-events-none bg-slate-700 text-slate-400'}`}
            aria-disabled={!ready}
          >
            Open Stats Toolkit
          </a>
          <button type="button" onClick={() => void query.refetch()} className="rounded-lg border border-[var(--border-primary)] px-4 py-2">
            Refresh status
          </button>
        </div>
      </section>
    </div>
  );
}
