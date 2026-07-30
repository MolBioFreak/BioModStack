import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { resolveStatsToolkitEntryUrl } from '../runtime/tailnetEnvironment';

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

function StatsToolkitRedirect({ entryUrl }: { entryUrl: string }) {
  useEffect(() => {
    window.location.replace(entryUrl);
  }, [entryUrl]);

  return (
    <div className="flex min-h-[24rem] items-center justify-center text-sm text-[var(--text-secondary)]">
      Opening BioModStack Stats Toolkit…
    </div>
  );
}

export function StatsToolkitLauncher() {
  const query = useQuery({
    queryKey: ['stats-toolkit-status'],
    queryFn: fetchStatsToolkitStatus,
    refetchInterval: 15_000,
  });
  const status = query.data;

  if (query.isLoading) {
    return <div className="flex min-h-[24rem] items-center justify-center text-sm text-[var(--text-secondary)]">Connecting to BioModStack Stats Toolkit…</div>;
  }

  if (!status || !status.available || !status.ready) {
    return (
      <div className="mx-auto max-w-3xl p-6 text-[var(--text-primary)]">
        <section className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-6">
          <h1 className="text-2xl font-bold">BioModStack Stats Toolkit unavailable</h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            {query.error instanceof Error ? query.error.message : status?.detail ?? 'Standalone add-on discovery failed.'}
          </p>
          <button type="button" onClick={() => void query.refetch()} className="mt-5 rounded-lg border border-[var(--border-primary)] px-4 py-2">
            Retry connection
          </button>
        </section>
      </div>
    );
  }

  const entryUrl = resolveStatsToolkitEntryUrl(status.entry_url, window.location.hostname);
  return <StatsToolkitRedirect entryUrl={entryUrl} />;
}
