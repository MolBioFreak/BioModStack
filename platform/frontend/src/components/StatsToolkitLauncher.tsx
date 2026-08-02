import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { resolveStatsToolkitEntryUrl } from '../runtime/tailnetEnvironment';
import { useTheme } from './themeContext';
import {
  buildStatsThemePayload,
  isStatsThemeReadyPayload,
  resolveExactTargetOrigin,
} from '../runtime/statsToolkitThemeBridge';

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
  const { theme } = useTheme();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const query = useQuery({
    queryKey: ['stats-toolkit-status'],
    queryFn: fetchStatsToolkitStatus,
    refetchInterval: 15_000,
  });
  const status = query.data;
  const entryUrl = useMemo(
    () => status?.entry_url ? resolveStatsToolkitEntryUrl(status.entry_url, window.location.hostname) : null,
    [status?.entry_url],
  );
  const sendTheme = useCallback(() => {
    const frameWindow = frameRef.current?.contentWindow;
    if (!frameWindow || !entryUrl) return;
    const computed = window.getComputedStyle(document.documentElement);
    const payload = buildStatsThemePayload(theme, (name) => computed.getPropertyValue(name));
    frameWindow.postMessage(payload, resolveExactTargetOrigin(entryUrl, window.location.href));
  }, [entryUrl, theme]);
  const themeFrameRequestRef = useRef<number | null>(null);
  const scheduleTheme = useCallback(() => {
    if (themeFrameRequestRef.current !== null) window.cancelAnimationFrame(themeFrameRequestRef.current);
    themeFrameRequestRef.current = window.requestAnimationFrame(() => {
      themeFrameRequestRef.current = null;
      sendTheme();
    });
  }, [sendTheme]);

  useEffect(() => {
    scheduleTheme();
    return () => {
      if (themeFrameRequestRef.current !== null) window.cancelAnimationFrame(themeFrameRequestRef.current);
      themeFrameRequestRef.current = null;
    };
  }, [scheduleTheme]);

  useLayoutEffect(() => {
    if (!entryUrl) return undefined;
    const frameOrigin = resolveExactTargetOrigin(entryUrl, window.location.href);
    const listener = (event: MessageEvent<unknown>) => {
      if (event.source !== frameRef.current?.contentWindow || event.origin !== frameOrigin) return;
      if (isStatsThemeReadyPayload(event.data)) scheduleTheme();
    };
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, [entryUrl, scheduleTheme]);

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

  return (
    <div className="h-[calc(100vh-3.5rem)] min-h-[42rem] w-full overflow-hidden bg-[var(--bg-primary)]">
      <iframe
        ref={frameRef}
        className="h-full w-full border-0"
        src={entryUrl ?? undefined}
        onLoad={scheduleTheme}
        title="BioModStack Stats Toolkit workspace"
      />
    </div>
  );
}
