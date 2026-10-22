import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/lib/projectManager', () => ({
    listProjects: vi.fn(async () => ({ items: [{ project_id: 'proj-1', name: 'Plasmid project' }], next_cursor: null })),
}));

import { ShareDialog } from '../../src/components/MolBioToolkit/ShareDialog';
import { MobileMolBioToolbar } from '../../src/components/MolBioToolkit/MobileMolBioToolbar';
import { formatShareRemaining } from '../../src/lib/molbioShares';

const grant = (overrides: Record<string, unknown> = {}) => ({
    grant_id: 'g_1', label: '', creator: 'operator',
    scope: { kind: 'items', sequence_ids: ['seq-1'], operation_ids: [], counts: { scoped_sequences: 1 } },
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 3600_000).toISOString(),
    revoked_at: null, revoked_by: null, state: 'active', active: true, bundle_bytes: 10,
    access_log: [{ time: 't', ip: '1.2.3.4', route: '/b', outcome: 'bundle_ok' }],
    ...overrides,
});

function json(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('ShareDialog', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        vi.unstubAllGlobals();
    });

    async function render(fetchImpl: (url: string, init?: RequestInit) => Promise<Response>) {
        const fetchMock = vi.fn(fetchImpl);
        vi.stubGlobal('fetch', fetchMock);
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        await act(async () => root?.render(
            <ShareDialog
                open
                onClose={() => undefined}
                currentSequence={{ id: 'seq-1', name: 'PL931' }}
                sequences={[{ id: 'seq-2', name: 'pUC19' }]}
                operations={[]}
                projectContext={null}
            />,
        ));
        return fetchMock;
    }

    const button = (label: string) => [...container!.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)!;

    it('creates a current-sequence share by default and shows link + passcode once', async () => {
        const fetchMock = await render(async (url, init) => {
            if (url.endsWith('/config')) return json({ configured: true, max_expiry_seconds: 86400, min_expiry_seconds: 60, public_port: 8443 });
            if (init?.method === 'POST') return json({ grant: grant(), link: 'https://x.example/s/abc', passcode: 'ABCD-EFGH', notice: 'once' });
            return json({ schema: 'bms.molbio.share-list.v1', active_count: 0, items: [] });
        });
        await act(async () => button('4h').click());
        await act(async () => button('Create link').click());
        const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
        expect(post[1]?.credentials).toBe('same-origin');
        expect(JSON.parse(String(post[1]?.body))).toEqual({
            scope: { kind: 'items', sequence_ids: ['seq-1'], operation_ids: [] },
            expires_in_seconds: 4 * 3600,
            label: '',
        });
        const values = [...container!.querySelectorAll('input[readonly]')].map((i) => (i as HTMLInputElement).value);
        expect(values).toEqual(['https://x.example/s/abc', 'ABCD-EFGH']);
        expect(container!.textContent).toContain('shown only once');
    });

    it('rejects custom expiry above 24h and shows server errors plainly', async () => {
        await render(async (url, init) => {
            if (url.endsWith('/config')) return json({ configured: true, max_expiry_seconds: 86400, min_expiry_seconds: 60, public_port: null });
            if (init?.method === 'POST') return json({ detail: { code: 'sequence_not_found', message: 'a selected sequence was not found' } }, 404);
            return json({ schema: 'bms.molbio.share-list.v1', active_count: 0, items: [] });
        });
        await act(async () => button('Custom').click());
        const hours = container!.querySelector<HTMLInputElement>('input[aria-label="Custom expiry hours"]')!;
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        await act(async () => { setter.call(hours, '30'); hours.dispatchEvent(new Event('input', { bubbles: true })); });
        expect(button('Create link').disabled).toBe(true);
        await act(async () => { setter.call(hours, '2'); hours.dispatchEvent(new Event('input', { bubbles: true })); });
        await act(async () => button('Create link').click());
        expect(container!.querySelector('[role="alert"]')?.textContent).toBe('a selected sequence was not found');
    });

    it('lists active shares collapsed and revokes after confirm', async () => {
        const fetchMock = await render(async (url) => {
            if (url.endsWith('/config')) return json({ configured: true, max_expiry_seconds: 86400, min_expiry_seconds: 60, public_port: null });
            if (url.endsWith('/revoke')) return json({ grant: grant({ active: false, state: 'revoked', revoked_at: new Date().toISOString() }) });
            return json({ schema: 'bms.molbio.share-list.v1', active_count: 1, items: [grant()] });
        });
        const summary = [...container!.querySelectorAll('summary')].find((s) => s.textContent?.startsWith('Active shares'))!;
        expect(summary.textContent).toBe('Active shares (1)');
        expect((summary.parentElement as HTMLDetailsElement).open).toBe(false);
        expect(container!.textContent).toContain('1 open');
        await act(async () => button('Revoke').click());
        expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/revoke'))).toBe(false);
        await act(async () => button('Confirm revoke').click());
        expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/api/molbio/shares/g_1/revoke') && init?.method === 'POST')).toBe(true);
        expect(container!.textContent).toContain('Active shares (0)');
    });

    it('formats the expiry countdown', () => {
        const now = Date.parse('2026-01-01T00:00:00Z');
        expect(formatShareRemaining('2026-01-01T03:12:00Z', now)).toBe('3h 12m left');
        expect(formatShareRemaining('2025-12-31T23:00:00Z', now)).toBeNull();
    });
});

describe('MobileMolBioToolbar share', () => {
    it('renders a touch-sized Share button when wired', async () => {
        const onShare = vi.fn();
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => root.render(
            <MobileMolBioToolbar constructName="PL931" digestAvailable qcAvailable hasSequence surface="map"
                onBack={() => undefined} onOpenConstructs={() => undefined} onSurfaceChange={() => undefined} onShare={onShare} />,
        ));
        const share = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Share')!;
        expect(share.className).toContain('min-h-12');
        await act(async () => share.click());
        expect(onShare).toHaveBeenCalledOnce();
        await act(async () => root.unmount());
        container.remove();
    });
});
