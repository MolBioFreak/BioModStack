import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, prepareJobSubmission, type Job } from '../../src/lib/api';
import { DeNovoNativePreview } from '../../src/components/DeNovoNativePreview';

// Real component, prepareJobSubmission and Axios helper. Only molecular capture
// and the outbound transport are doubles; no stores, launches or native science.
function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
const draft = (seed = 0, path = 'original.cif'): Partial<Job> => ({
    model_id: 'rfd4_proteina', mode: 'generate', execution_target_id: null,
    params: { rfd4_request: { settings: { seed, compress_outputs: false, search: null } }, native_sources: { source: { path } } },
});
const response = (marker: string) => ({ plan: { requested_json: { marker }, effective_json: { marker } }, blockers: [] });
let root: Root | undefined;
let previous: typeof api.defaults.adapter;
let calls: Array<{ payload: Partial<Job>; reply: ReturnType<typeof deferred<unknown>> }>;
let unexpected: string[];
let xhr: ReturnType<typeof vi.spyOn>, fetch: ReturnType<typeof vi.spyOn>;
let props: React.ComponentProps<typeof DeNovoNativePreview>;
const button = () => document.querySelector('button') as HTMLButtonElement;
const text = () => document.body.textContent ?? '';
async function render(next: Partial<typeof props> = {}) {
    props = { ...props, ...next };
    if (!root) { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); }
    await act(async () => root!.render(<DeNovoNativePreview {...props} />));
}
async function inspect() { await act(async () => button().click()); }
async function answer(index: number, marker: string) { await act(async () => calls[index].reply.resolve(response(marker))); }
beforeEach(() => {
    calls = []; unexpected = []; props = { request: draft() }; previous = api.defaults.adapter;
    api.defaults.adapter = async config => {
        if (config.method !== 'post' || config.url !== '/api/jobs/execution-plan/preview') {
            unexpected.push(`${config.method} ${config.url}`); throw new Error('Unexpected HTTP');
        }
        const reply = deferred<unknown>();
        calls.push({ payload: JSON.parse(config.data), reply });
        return { config, status: 200, statusText: 'OK', headers: {}, data: await reply.promise };
    };
    xhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('Network disabled'); });
    fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('Network disabled'); });
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount()); root = undefined;
    document.body.replaceChildren(); api.defaults.adapter = previous; xhr.mockRestore(); fetch.mockRestore();
    expect(unexpected).toEqual([]);
});

it('awaits slow capture and previews the exact latest serialized settings, not the pre-export prop', async () => {
    const capture = deferred<Partial<Job>>(); const prepare = vi.fn(() => capture.promise);
    await render({ prepareRequest: prepare }); await inspect();
    expect(prepare).toHaveBeenCalledTimes(1); expect(calls).toHaveLength(0); expect(button().disabled).toBe(true);
    // A real settings edit while export is pending must survive in the callback snapshot.
    await render({ request: draft(19) });
    const frozen = draft(19, 'immutable/frozen.cif');
    await act(async () => capture.resolve(frozen));
    expect(calls).toHaveLength(1); expect(calls[0].payload).toEqual(prepareJobSubmission(frozen));
    await render({ request: frozen }); await answer(0, 'latest-capture');
    expect(text()).toContain('Effective: latest-capture'); expect(button().disabled).toBe(false);
});

it.each(['before response', 'after response'])('accepts the exact capture-owned prop update %s', async timing => {
    const frozen = draft(0, 'immutable/exact.cif');
    await render({ prepareRequest: vi.fn(async () => frozen) }); await inspect();
    if (timing === 'before response') await render({ request: frozen });
    await answer(0, 'capture-owned');
    if (timing === 'after response') await render({ request: frozen });
    expect(text()).toContain('Effective: capture-owned');
});

it('accepts a capture source update rendered before preparation resolves', async () => {
    const capture = deferred<Partial<Job>>(); const frozen = draft(0, 'immutable/early.cif');
    await render({ prepareRequest: () => capture.promise }); await inspect();
    await render({ request: frozen }); await act(async () => capture.resolve(frozen));
    await answer(0, 'early-update'); expect(text()).toContain('Effective: early-update');
});

it('rejects an old response arriving last after settings A → B → A', async () => {
    await render(); await inspect();
    await render({ request: draft(7) }); expect(button().disabled).toBe(false);
    await render({ request: draft() }); await inspect();
    await answer(1, 'current-settings'); await answer(0, 'obsolete-settings');
    expect(text()).toContain('Effective: current-settings'); expect(text()).not.toContain('obsolete-settings');
});

it('does not treat an unrelated edit followed by the expected frozen source as a capture-owned update', async () => {
    const frozen = draft(0, 'immutable/exact.cif');
    await render({ prepareRequest: async () => frozen }); await inspect();
    await render({ request: draft(8) }); await render({ request: frozen });
    await answer(0, 'obsolete-capture'); expect(text()).not.toContain('obsolete-capture');
    expect(button().disabled).toBe(false);
});

it('invalidates displayed results immediately on a settings edit', async () => {
    await render(); await inspect(); await answer(0, 'old-visible');
    await render({ request: draft(2) });
    expect(text()).not.toContain('old-visible'); expect(text()).toContain('Settings changed. Refresh preview.');
    await render({ request: draft() }); expect(text()).not.toContain('old-visible');
});

it('rejects dirty-document A → B → A even while the frozen source path is unchanged', async () => {
    await render({ documentGeneration: 0 }); await inspect();
    await render({ documentGeneration: 1 }); await render({ documentGeneration: 2 });
    await inspect(); await answer(1, 'current-document'); await answer(0, 'old-document');
    expect(text()).toContain('Effective: current-document'); expect(text()).not.toContain('old-document');
});

it('does not send a snapshot captured across a dirty document edit', async () => {
    const capture = deferred<Partial<Job>>();
    await render({ prepareRequest: () => capture.promise, documentGeneration: 0 }); await inspect();
    await render({ documentGeneration: 1 }); await render({ documentGeneration: 2 });
    await act(async () => capture.resolve(draft(0, 'immutable/stale.cif')));
    expect(calls).toHaveLength(0); expect(button().disabled).toBe(false);
});

it('keeps manual/source-free preview explicit and independent of a preparation callback', async () => {
    const sourceFree: Partial<Job> = { model_id: 'rfd4_proteina', mode: 'generate', params: { rfd4_request: { settings: { seed: 0 } } } };
    await render({ request: sourceFree }); expect(calls).toHaveLength(0);
    await inspect(); expect(calls).toHaveLength(1); expect(calls[0].payload.params).toEqual(sourceFree.params);
    await answer(0, 'source-free'); expect(text()).toContain('Effective: source-free');
});

it('reports preparation failure as advisory, leaves retry available and does not send stale props', async () => {
    const prepare = vi.fn<() => Promise<Partial<Job>>>().mockRejectedValueOnce(new Error('Frozen export unavailable')).mockResolvedValue(draft());
    await render({ prepareRequest: prepare }); await inspect();
    expect(calls).toHaveLength(0); expect(text()).toContain('Preview unavailable: Frozen export unavailable');
    expect(button().disabled).toBe(false); await inspect(); await answer(0, 'retried');
    expect(text()).toContain('Effective: retried'); expect(text()).not.toContain('Preview unavailable');
});

it.each(['capture', 'transport'])('discards pending %s after unmount', async phase => {
    const capture = deferred<Partial<Job>>();
    await render(phase === 'capture' ? { prepareRequest: () => capture.promise } : {}); await inspect();
    await act(async () => root!.unmount()); root = undefined;
    if (phase === 'capture') { await act(async () => capture.resolve(draft())); expect(calls).toHaveLength(0); }
    else await answer(0, 'unmounted');
    expect(text()).not.toContain('unmounted');
});

it('excludes optional operations from captured payloads and from invalidation', async () => {
    const frozen = { ...draft(0, 'immutable/ops.cif'), sequence_design: { enabled: true } } as Partial<Job>;
    await render({ request: frozen, prepareRequest: async () => frozen }); await inspect();
    expect(calls[0].payload).not.toHaveProperty('sequence_design');
    await render({ request: { ...frozen, sequence_design: null } });
    await answer(0, 'native-only'); expect(text()).toContain('Effective: native-only');
});

it('invalidates a visible preview on a dirty document edit before a new source path exists', async () => {
    await render({ documentGeneration: 10 }); await inspect(); await answer(0, 'visible-document');
    await render({ documentGeneration: 11 });
    expect(text()).not.toContain('visible-document'); expect(text()).toContain('Settings changed. Refresh preview.');
});

it('keeps a newer capture pending when an invalidated older capture fails last', async () => {
    const oldCapture = deferred<Partial<Job>>(); const newCapture = deferred<Partial<Job>>();
    const prepare = vi.fn<() => Promise<Partial<Job>>>().mockReturnValueOnce(oldCapture.promise).mockReturnValueOnce(newCapture.promise);
    await render({ prepareRequest: prepare, documentGeneration: 0 }); await inspect();
    await render({ documentGeneration: 1 }); await inspect();
    await act(async () => oldCapture.reject(new Error('old export failure')));
    expect(calls).toHaveLength(0); expect(button().disabled).toBe(true); expect(text()).not.toContain('old export failure');
    await act(async () => newCapture.resolve(draft(0, 'immutable/newer.cif')));
    await answer(0, 'newer-capture'); expect(text()).toContain('Effective: newer-capture');
});

it('ignores stale transport errors and does not let old completion clear a newer pending preview', async () => {
    await render(); await inspect(); await render({ request: draft(1) }); await inspect();
    await act(async () => calls[0].reply.reject(new Error('stale error')));
    expect(button().disabled).toBe(true); expect(text()).not.toContain('stale error');
    await answer(1, 'newer'); expect(button().disabled).toBe(false); expect(text()).toContain('Effective: newer');
});
