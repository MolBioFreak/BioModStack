import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DevIssueLedger } from '../../src/components/DevIssueLedger';

const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const screenshotMetadata = {
    sha256: 'abc123',
    media_type: 'image/png',
    byte_size: PNG_BYTES.byteLength,
    content_url: '/api/dev/issues/1/screenshot-content',
};

function issueWithScreenshot() {
    return {
        id: 1,
        issue_key: 'BMS-DEV-1',
        body: 'Pasted screenshot issue',
        status: 'open',
        scope_kind: 'page',
        scope_key: 'page:dashboard',
        page_label: 'Dashboard',
        route: '/',
        component_hint: null,
        author_kind: 'operator',
        frontend_revision: 'frontend-test',
        api_revision: 'api-test',
        created_at: '2026-08-27T12:00:00Z',
        cleared_at: null,
        resolution_note: null,
        screenshot: screenshotMetadata,
    };
}

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        await Promise.resolve();
    });
}

function pasteFile(target: Element, file: File) {
    const event = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', {
        value: {
            items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
        },
    });
    target.dispatchEvent(event);
    return event;
}

describe('DevIssueLedger clipboard screenshots', () => {
    let root: Root;
    let container: HTMLDivElement;
    let client: QueryClient;
    let fetchMock: ReturnType<typeof vi.fn>;

    beforeEach(async () => {
        vi.stubGlobal('URL', {
            ...URL,
            createObjectURL: vi.fn(() => 'blob:clipboard-preview'),
            revokeObjectURL: vi.fn(),
        });
        fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], active_count: 0 }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        }));
        vi.stubGlobal('fetch', fetchMock);
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/']}>
                        <DevIssueLedger />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await flush();
        const trigger = container.querySelector<HTMLButtonElement>('[data-bms-dev-issues-trigger]');
        await act(async () => trigger?.click());
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        client.clear();
        container.remove();
        vi.unstubAllGlobals();
    });

    it('accepts an image paste anywhere in the open drawer and previews/removes it', async () => {
        const drawer = container.querySelector('[data-bms-dev-issues-drawer]');
        const file = new File([PNG_BYTES], 'clipboard.png', { type: 'image/png' });

        await act(async () => { pasteFile(drawer!, file); });

        const preview = container.querySelector<HTMLImageElement>('img[alt="Screenshot preview"]');
        expect(preview?.src).toBe('blob:clipboard-preview');
        expect(container.textContent).toContain('clipboard.png');
        const remove = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Remove screenshot');
        await act(async () => remove?.click());
        expect(container.querySelector('img[alt="Screenshot preview"]')).toBeNull();
        expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:clipboard-preview');
    });

    it('submits pasted screenshot as multipart and renders saved screenshot metadata URL', async () => {
        let saved = false;
        fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
            const url = String(input);
            if (url === '/api/dev/issues/with-screenshot' && init?.method === 'POST') {
                expect(init.body).toBeInstanceOf(FormData);
                const form = init.body as FormData;
                expect(form.get('body')).toBe('Pasted screenshot issue');
                expect(form.get('screenshot')).toBeInstanceOf(File);
                expect((form.get('screenshot') as File).type).toBe('image/png');
                expect(init.headers).toBeUndefined();
                saved = true;
                return new Response(JSON.stringify(issueWithScreenshot()), { status: 201, headers: { 'Content-Type': 'application/json' } });
            }
            return new Response(JSON.stringify({
                items: saved ? [issueWithScreenshot()] : [],
                active_count: saved ? 1 : 0,
            }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        });
        const drawer = container.querySelector('[data-bms-dev-issues-drawer]');
        const textarea = container.querySelector<HTMLTextAreaElement>('textarea');
        const file = new File([PNG_BYTES], 'clipboard.png', { type: 'image/png' });
        await act(async () => {
            pasteFile(drawer!, file);
            if (textarea) {
                Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(textarea, 'Pasted screenshot issue');
                textarea.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        const save = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === 'Save');

        await act(async () => save?.click());
        await vi.waitFor(() => expect(saved).toBe(true));
        await vi.waitFor(() => {
            const savedImage = container.querySelector<HTMLImageElement>('img[alt="BMS-DEV-1 screenshot"]');
            expect(savedImage?.getAttribute('src')).toBe('/api/dev/issues/1/screenshot-content');
        });
    });
});
