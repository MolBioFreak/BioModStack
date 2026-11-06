import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { LegacyNativeGenerationControls } from '../../src/components/DeNovoNativeGenerationSettings';
import { api } from '../../src/lib/api';

afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren(); });
it('edits consumed native controls without collapsing false, zero, or null', async () => {
    const initial = { pcad_laproteina_settings_json: { seed: 0, self_cond: false, bb_ca_gt_clamp_val: null } };
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: { parameter_schema: { type: 'object', additionalProperties: false,
        properties: { seed: { type: 'integer' }, self_cond: { type: 'boolean' }, bb_ca_gt_clamp_val: { type: ['number', 'null'] } } } } });
    let latest: Record<string, unknown> = initial;
    const App = () => { const [value, setValue] = useState<Record<string, unknown>>(initial); return <LegacyNativeGenerationControls model="laproteina" task="unconditional" value={value} onChange={next => { latest = next; setValue(next); }} />; };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement('div'); document.body.appendChild(container); const root = createRoot(container);
    await act(async () => root.render(<QueryClientProvider client={client}><App /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(get).toHaveBeenCalledWith('/api/models/protein_modification_experimental/generation-settings', { params: { mode: 'de_novo_design', generator: 'laproteina', task: 'unconditional' } });
    const summary = container.querySelector('summary')!;
    await act(async () => { summary.click(); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(container.textContent).toContain('Explicit false, zero and null');
    expect(latest).toEqual(initial);
    expect(container.querySelector('input[type="number"]')?.getAttribute('value')).toBe('0');
    const checkbox = container.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(checkbox).not.toBeNull();
    await act(async () => checkbox!.click());
    expect((latest.pcad_laproteina_settings_json as Record<string, unknown>).self_cond).toBe(true);
    expect((latest.pcad_laproteina_settings_json as Record<string, unknown>).seed).toBe(0);
    expect((latest.pcad_laproteina_settings_json as Record<string, unknown>).bb_ca_gt_clamp_val).toBeNull();
    await act(async () => root.unmount()); client.clear();
});
