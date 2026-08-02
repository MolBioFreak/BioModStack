export const BMS_STATS_THEME_MESSAGE_TYPE = 'bms-theme-v1' as const;
export const BMS_STATS_THEME_READY_MESSAGE_TYPE = 'bms-theme-ready-v1' as const;

export const BMS_STATS_THEME_TOKEN_NAMES = [
  '--bg-primary',
  '--bg-secondary',
  '--bg-tertiary',
  '--bg-gradient-from',
  '--bg-gradient-via',
  '--bg-gradient-to',
  '--text-primary',
  '--text-secondary',
  '--text-muted',
  '--accent-primary',
  '--accent-secondary',
  '--accent-gradient-from',
  '--accent-gradient-to',
  '--border-primary',
  '--border-secondary',
  '--nav-bg',
  '--card-bg',
  '--card-hover',
  '--success',
  '--warning',
  '--error',
  '--link',
  '--link-hover',
] as const;

export type StatsThemeTokenName = (typeof BMS_STATS_THEME_TOKEN_NAMES)[number];
export interface StatsThemePayload {
  type: typeof BMS_STATS_THEME_MESSAGE_TYPE;
  theme: string;
  tokens: Record<StatsThemeTokenName, string>;
}

export function buildStatsThemePayload(
  theme: string,
  readToken: (name: StatsThemeTokenName) => string,
): StatsThemePayload {
  const tokens = {} as Record<StatsThemeTokenName, string>;
  for (const name of BMS_STATS_THEME_TOKEN_NAMES) {
    const value = readToken(name).trim();
    if (!value) throw new Error(`Global theme token ${name} is empty`);
    tokens[name] = value;
  }
  return { type: BMS_STATS_THEME_MESSAGE_TYPE, theme, tokens };
}

export function resolveExactTargetOrigin(entryUrl: string, baseUrl: string): string {
  return new URL(entryUrl, baseUrl).origin;
}

export function isStatsThemeReadyPayload(value: unknown): boolean {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).length === 1 && record.type === BMS_STATS_THEME_READY_MESSAGE_TYPE;
}
