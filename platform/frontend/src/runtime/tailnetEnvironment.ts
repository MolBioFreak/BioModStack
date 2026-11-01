export type TailnetEnvironmentName = 'development' | 'production';

export interface TailnetEnvironmentStatus {
  selected_environment: TailnetEnvironmentName;
  tailnet_origin?: string;
  selector_revision?: string;
  project_revision?: string;
}

type FetchLike = typeof fetch;

declare global {
  interface Window {
    __BMS_API_BASE_URL__?: string;
  }
}

const STATUS_ENDPOINT = '/api/tailnet-environment/status';
const LOCAL_STATS_ENTRY = 'http://127.0.0.1:18180/stats/';
const TAILNET_STATS_ENTRY = '/stats/embed/?ui=66882da';

export async function readTailnetEnvironmentStatus(
  fetchImpl: FetchLike = fetch,
): Promise<TailnetEnvironmentStatus | null> {
  const response = await fetchImpl(STATUS_ENDPOINT, { cache: 'no-store' });
  if (response.status === 403 || response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Tailnet environment status failed (${response.status})`);
  }
  const payload = await response.json() as TailnetEnvironmentStatus;
  if (payload.selected_environment !== 'development' && payload.selected_environment !== 'production') {
    throw new Error('Tailnet environment status returned an invalid selection');
  }
  return payload;
}

export function isTailnetHostname(hostname: string): boolean {
  return hostname.toLowerCase().endsWith('.ts.net');
}

export function resolveConfiguredTailnetOrigin(apiBaseUrl?: string): string | null {
  if (!apiBaseUrl) return null;
  try {
    const url = new URL(apiBaseUrl);
    return url.protocol === 'https:' && isTailnetHostname(url.hostname) ? url.origin : null;
  } catch {
    return null;
  }
}

export function resolveStatsToolkitEntryUrl(entryUrl: string, hostname: string, apiBaseUrl?: string): string {
  // Downloaded Cordova UI lives at localhost; its API owner is still remote.
  const configuredOrigin = resolveConfiguredTailnetOrigin(apiBaseUrl);
  if (configuredOrigin) return new URL(TAILNET_STATS_ENTRY, configuredOrigin).href;
  if (isTailnetHostname(hostname)) {
    return TAILNET_STATS_ENTRY;
  }
  return entryUrl.startsWith('/') ? LOCAL_STATS_ENTRY : entryUrl;
}
