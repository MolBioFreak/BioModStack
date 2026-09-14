import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../src/lib/api';
import type { StructureWorkbenchProps } from '../../src/structureViewer/StructureWorkbench';

// Boundary acceptance only: actual parser, collector, grouping and metric mapping
// run unchanged. No claim about WebGL/canvas rendering or side-panel behavior.
const boundary = vi.hoisted(() => ({ props: null as StructureWorkbenchProps | null }));
vi.mock('../../src/structureViewer/StructureWorkbench.js', () => ({
    StructureWorkbench: (props: StructureWorkbenchProps) => {
        boundary.props = props;
        return <div data-testid="structure-boundary">Structure boundary</div>;
    },
}));
vi.mock('../../src/components/FrustraMpnnPlotlyAnalytics.js', () => ({ default: () => null }));
vi.mock('../../src/components/FrustraMpnnCandidateHandoffPanel.js', () => ({ default: () => null }));
vi.mock('../../src/components/frustrampnn/FrustraMpnnReviewExportPanel.js', () => ({ default: () => null }));
vi.mock('../../src/components/frustrampnn/FrustraMpnnSettingsPanel.js', () => ({ FrustraMpnnSettingsPanel: () => null }));
import Viewer from '../../src/components/FrustraMpnnResultsViewer';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
// Externally retained captured/producer-projected JSON; never copy large runs into source.
const wireRoot = process.env.BMS_TEST_FRUSTRA_WIRE!;
const load = (name: string) => JSON.parse(readFileSync(join(wireRoot, name), 'utf8'));
const manifest = load('manifest.json') as { candidates: string[] };
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => vi.unstubAllGlobals());
const modes = ['populated', 'statistics-http-failure', 'statistics-malformed', 'inline-malformed'] as const;

describe('captured FrustraMPNN result-read mounted boundary acceptance', () => {
    it('contains exactly five distinct captured candidates', () => {
        expect(manifest.candidates).toHaveLength(5);
        expect(new Set(manifest.candidates).size).toBe(5);
    });
    for (const candidate of manifest.candidates) {
        it.each(modes)(`${candidate}: %s preserves populated core and exact mapped layers`, async (mode) => {
            const wire = load(`${candidate}.json`);
            const detail = structuredClone(wire.detail);
            if (mode === 'inline-malformed') detail.statistics_json = { deliberately_invalid_derived_document: true };
            const original = api.defaults.adapter;
            const calls: { url: string; offset?: number }[] = [];
            const forbidden: string[] = [];
            boundary.props = null;
            api.defaults.adapter = async (config) => {
                const url = String(config.url);
                calls.push({ url, offset: config.params?.offset });
                if (config.method !== 'get') { forbidden.push(`${config.method} ${url}`); throw new Error('No mutations allowed'); }
                let data: unknown;
                if (url.endsWith(`/jobs/${detail.parent_job_id}/results`)) data = wire.list;
                else if (url.endsWith('/statistics/analysis')) data = wire.analysis;
                else if (url.endsWith('/statistics')) {
                    if (mode === 'statistics-http-failure') throw new Error('TEST statistics transport failure');
                    data = mode === 'statistics-malformed' ? { ...wire.statistics, statistics_json: { invalid: true } } : wire.statistics;
                } else if (url.endsWith('/landscape')) {
                    data = wire.pages.find((page: { offset: number }) => page.offset === (config.params?.offset ?? 0));
                    if (!data) throw new Error(`Uncaptured landscape offset ${config.params?.offset}`);
                } else if (url.endsWith('/artifacts')) data = wire.artifacts;
                else if (url === wire.mapUrl) data = wire.map;
                else if (url.endsWith(`/results/${encodeURIComponent(detail.invocation_id)}`)) data = detail;
                else { forbidden.push(url); throw new Error(`Uncaptured GET ${url}`); }
                return { data, status: 200, statusText: 'OK', headers: {}, config };
            };
            const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
            const container = document.createElement('div'); document.body.append(container);
            const root = createRoot(container);
            try {
                await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><Viewer
                    job={{ id: detail.parent_job_id, name: 'Captured GFP', model_id: 'conformational_mapping', status: 'completed', params: {}, created_at: '', design_count: 5 } as never}
                    preferredInvocationId={detail.invocation_id} onBack={() => {}} onOpenJob={() => {}}
                /></QueryClientProvider></MemoryRouter>));
                for (let attempt = 0; attempt < 100 && !container.textContent?.includes('238 mapped residues'); attempt++) {
                    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
                }
                expect(boundary.props?.metricLayers, container.textContent ?? '').toHaveLength(3);
                expect(boundary.props?.label).toBe(candidate);
                expect(boundary.props?.structureUrl).toBe(wire.normalizedUrl);
                expect(boundary.props?.activeMetricId).toBe('frustrampnn-native-index');
                for (const layer of boundary.props!.metricLayers!) expect(layer.values).toHaveLength(238);
                expect(container.textContent).toContain('238 mapped residues');
                expect(container.textContent).toContain('Exact 20-substitution profiles');
                expect(container.textContent).toContain('Settings used');
                expect(container.querySelector<HTMLDetailsElement>('[aria-label="Requested and effective FrustraMPNN settings"]')?.open).toBe(false);
                expect(container.textContent).not.toMatch(/Result authority:|source_hash_conflict|canonical_result_authority_conflict/);
                expect(Array.from(container.querySelectorAll<HTMLOptionElement>('select[aria-label="Structure"] option')).map(option => option.value)).toEqual(wire.list.items.map((item: { invocation_id: string }) => item.invocation_id));
                const nativeRows = wire.pages.flatMap((page: { items: { native: boolean; score: number; auth_asym_id: string; auth_seq_id: number }[] }) => page.items).filter((row: { native: boolean }) => row.native);
                expect(nativeRows).toHaveLength(238);
                for (const value of boundary.props!.metricLayers![0].values) {
                    const row = nativeRows.find((item: { auth_asym_id: string; auth_seq_id: number }) => item.auth_asym_id === value.identity.authAsymId && item.auth_seq_id === value.identity.authSeqId);
                    expect(row).toBeDefined();
                    expect(value.value).toBe(row.score);
                }
                const pages = new Set(calls.filter(call => call.url.endsWith('/landscape')).map(call => call.offset));
                expect([...pages].sort((a, b) => a! - b!)).toEqual(wire.pages.map((page: { offset: number }) => page.offset));
                expect(calls.some(call => call.url.endsWith('/statistics'))).toBe(true);
                if (mode === 'statistics-http-failure' || mode === 'statistics-malformed') {
                    expect(container.textContent).toContain('Statistics unavailable');
                    expect(container.querySelector('[role="alert"]')).not.toBeNull();
                    if (mode === 'statistics-http-failure') expect(container.textContent).toContain('TEST statistics transport failure');
                } else {
                    expect(container.textContent).toContain('Scoreable slots');
                    expect(container.textContent).not.toContain('Statistics unavailable');
                }
                expect(forbidden).toEqual([]);
            } catch (error) {
                console.error(JSON.stringify({ candidate, mode, calls, forbidden, text: container.textContent }));
                throw error;
            } finally {
                await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = original;
            }
        }, 30000);
    }
});
