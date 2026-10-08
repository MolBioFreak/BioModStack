import React from 'react';

export type BindCraft2Stage = 'trajectory' | 'draw' | 'retained' | 'attempt' | 'document';
export interface BindCraft2NativePage {
  analytics?: { trajectory_count: number; termination_counts: Record<string, number>; timing_seconds: { samples: number; median: number | null; total: number }; complete: boolean; warnings: string[] };
  schema: 'bindcraft2.native-readback.v1';
  arm: string | null;
  stage: BindCraft2Stage;
  offset: number;
  limit: number;
  total: number;
  accounting: Record<string, number | null>;
  arms: { name: string | null; accounting: Record<string, number | null> }[];
  metadata: Record<string, unknown> | null;
  rows: Record<string, unknown>[];
  artifacts?: { path: string; media_type: string; bytes: number; download_url?: string | null }[];
}

const missing = 'Unknown / not emitted';
const scalar = (value: unknown): string => value === null || value === undefined || value === '' ? missing : String(value);
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Read-only nested settings, retaining false, zero, null and empty collections. */
export function BindCraft2SettingsReadback({ value }: { value: unknown }) {
  if (value === null) return <span>Explicit null</span>;
  if (Array.isArray(value)) return value.length ? <ol className="space-y-1">{value.map((item, index) => <li key={index}><BindCraft2SettingsReadback value={item} /></li>)}</ol> : <span>Empty list</span>;
  if (typeof value !== 'object') return <span>{scalar(value)}</span>;
  const entries = Object.entries(object(value));
  return entries.length ? <dl className="grid gap-2 sm:grid-cols-[minmax(10rem,1fr)_minmax(0,2fr)]">{entries.map(([key, entry]) =>
    <React.Fragment key={key}><dt className="font-medium break-words">{key}</dt><dd className="min-w-0 break-words"><BindCraft2SettingsReadback value={entry} /></dd></React.Fragment>)}</dl> : <span>Empty object</span>;
}
