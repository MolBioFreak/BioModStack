import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import type { Job } from '../../src/lib/api';

let mounted: ReactTestRenderer | undefined;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
afterEach(async () => { if (mounted) await act(async () => mounted!.unmount()); vi.unstubAllGlobals(); });

it('mounts verified BC2-native pages without a generic Design results link', async () => {
  const paths: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    paths.push(url);
    if (!url.includes('/bindcraft2-results?')) throw new Error(`Unexpected generic fetch: ${url}`);
    const query = new URL(url, 'http://example.test').searchParams;
    return { ok: true, json: async () => ({
      schema: 'bindcraft2.native-readback.v1', arm: 'native_arm', stage: query.get('stage'),
      offset: Number(query.get('offset')), limit: 25, total: 26,
      accounting: { claimed_attempts: 30, generated_rows: 26 },
      arms: [{ name: 'native_arm', accounting: {} }],
      metadata: null, selection: { eligible: false, reason: 'no verified target-state map' },
      rows: [{ design: 'native-1', native_score: 0.91 }],
    }) };
  }));
  const job = { id: 'bc2', model_id: 'bindcraft2', mode: 'native', status: 'completed',
    name: 'Native campaign', output_dir: '/results/bc2', design_count: 0 } as unknown as Job;
  await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter>
  </QueryClientProvider>); });
  expect(paths).toHaveLength(1);
  expect(paths[0]).not.toContain('arm=');
  await vi.waitFor(() => expect(text(mounted!.root)).toContain('native-1'));
  expect(text(mounted!.root)).toContain('Selection unavailable: no verified target-state map');
  expect(text(mounted!.root)).not.toContain('Open in Results Viewer');
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'Native records' }).props.onChange({ target: { value: 'attempt' } }));
  await vi.waitFor(() => expect(paths.at(-1)).toContain('stage=attempt'));
  expect(paths.at(-1)).toContain('arm=native_arm');
  await vi.waitFor(() => expect(mounted!.root.findAllByType('button').some(n => text(n) === 'Next')).toBe(true));
  await act(async () => mounted!.root.findAllByType('button').find(n => text(n) === 'Next')!.props.onClick());
  await vi.waitFor(() => expect(paths.at(-1)).toContain('offset=25'));
});
