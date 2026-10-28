// @vitest-environment jsdom
import React, { createRef } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi } from 'vitest';
import { AtomWorksStudioHost } from '../../src/components/AtomWorksStudioHost';
import { captureStudio, isStudioMessage } from '../../src/lib/atomworksStudio';
import type { StudioCapture } from '../../src/lib/atomworksStudio';


describe('private Studio host', () => {
  it('independently binds origin, iframe window, source and owned session', () => {
    const frame = document.createElement('iframe'); document.body.append(frame);
    const valid = { origin: window.location.origin, source: frame.contentWindow,
      data: { source: 'atomworks-annotation-studio', session_id: 'owned', type: 'changed' } };
    expect(isStudioMessage(valid as MessageEvent, frame.contentWindow, window.location.origin, 'owned')).toBe(true);
    for (const bad of [ { ...valid, origin: 'https://evil.invalid' }, { ...valid, source: window },
      { ...valid, data: { ...valid.data, session_id: 'foreign' } },
      { ...valid, data: { ...valid.data, source: 'atomworks-studio' } } ]) {
      expect(isStudioMessage(bad as MessageEvent, frame.contentWindow, window.location.origin, 'owned')).toBe(false);
    }
    frame.remove();
  });

  it('captures exactly one export response; advisory issues are not refusal', async () => {
    const snapshot = { cif: 'data_exact\n# untouched bytes\n', revision: 7, name: 'x', issues: [{ check: 'advisory', message: 'observation' }] };
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal('fetch', fetcher);
    expect(await captureStudio('owned')).toEqual({ ...snapshot, session_id: 'owned' });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/atomworks-studio/api/v1/sessions/owned/export');
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', cache: 'no-store' });
    vi.unstubAllGlobals();
  });

  it('notifications mark dirty without exporting; failed capture remains recoverable', async () => {
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host); const ref = createRef<StudioCapture>();
    const dirty = vi.fn(); const ready = vi.fn(); const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await act(async () => root.render(<AtomWorksStudioHost ref={ref} sessionId="owned" onDirty={dirty} onReady={ready} />));
    const frame = host.querySelector('iframe')!;
    const notify = (type: string, source = frame.contentWindow, session_id = 'owned') => window.dispatchEvent(
      new MessageEvent('message', { origin: window.location.origin, source,
        data: { source: 'atomworks-annotation-studio', type, session_id } }));
    notify('changed', window); notify('changed', frame.contentWindow, 'wrong');
    notify('ready'); notify('changed');
    expect(ready).toHaveBeenCalledTimes(1); expect(dirty).toHaveBeenCalledTimes(1);
    expect(fetcher).not.toHaveBeenCalled();
    expect(ref.current!.generation()).toBe(1);
    fetcher.mockResolvedValueOnce({ ok: false, status: 502 });
    await act(async () => { await expect(ref.current!.capture()).rejects.toThrow('502'); });
    expect(host.textContent).toContain('Unsaved Studio edits');
    const snapshot = { cif: 'data_exact\n', revision: 3, name: 'x', issues: [] };
    fetcher.mockResolvedValueOnce({ ok: true, json: async () => snapshot });
    await act(async () => { expect(await ref.current!.capture()).toEqual({ ...snapshot, session_id: 'owned' }); });
    // Host never announces persistence or clears parent dirty: parent owns durable adoption.
    expect(dirty).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals();
  });

  it('rejects stale A -> B -> A capture even if final session id matches', async () => {
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host); const ref = createRef<StudioCapture>();
    let resolve!: (value: unknown) => void;
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(r => { resolve = r; })));
    await act(async () => root.render(<AtomWorksStudioHost ref={ref} sessionId="A" />));
    const pending = ref.current!.capture();
    await act(async () => root.render(<AtomWorksStudioHost ref={ref} sessionId="B" />));
    await act(async () => root.render(<AtomWorksStudioHost ref={ref} sessionId="A" />));
    resolve({ ok: true, json: async () => ({ cif: 'old', revision: 1, name: 'old', issues: [] }) });
    await expect(pending).rejects.toThrow('document changed');
    await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals();
  });
});
