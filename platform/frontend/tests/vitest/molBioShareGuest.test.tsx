import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({
    SequenceViewer: (props: { sequenceData: { name: string; sequence: string }; restrictionAnalysis: unknown }) => (
        <div data-viewer data-name={props.sequenceData.name} data-length={props.sequenceData.sequence.length} data-analysis={props.restrictionAnalysis ? 'yes' : 'no'} />
    ),
}));

import { ShareGuestApp } from '../../src/share/ShareGuestApp';
import { guestSequenceData } from '../../src/share/guestSequenceData';
import {
    fetchGuestShare,
    linkKeyFromHash,
    normalizeGuestPasscode,
    parseGuestShare,
    shareTransport,
    type Transport,
} from '../../src/share/shareGuestApi';

const KEY = 'A'.repeat(40) + 'b-_';
const future = () => new Date(Date.now() + 3 * 3600_000).toISOString();

function seq(over: Record<string, unknown> = {}, record: Record<string, unknown> = {}) {
    return {
        sequence_id: 'doc-1', revision_id: 'rev-1', revision_number: 2, role: 'scoped', change_kind: 'edit',
        content_sha256: 'x', content_length: 8, created_at: '2026-10-21T00:00:00Z', derived_by_operation_id: 'op-1',
        parent_sequence_id: null, project_tags: ['cloning'], project_notes: 'note',
        record: {
            name: 'pUC19', description: null, sequence: 'GAATTCAA', sequence_type: 'dna', molecule_strandedness: 'double',
            molecule_orientation: 'not_applicable', is_circular: true, length: 8,
            features: [{ id: 'f1', name: 'lacZ', type: 'CDS', start: 0, end: 4, strand: 1 }], primers: [], analysis_tracks: [],
            organism: null, accession: null, operation: null, operation_params: null, gc_content: null, source_file: null, ...record,
        },
        ...over,
    };
}

function envelope(overrides: Record<string, unknown> = {}) {
    return {
        grant: { grant_id: 'g'.repeat(32), label: 'For Dana', expires_at: future(), created_at: '2026-10-21T00:00:00Z' },
        bundle: {
            schema: 'bms.molbio.share-bundle.v1', title: 'Proj — Domain', scope: { kind: 'items' },
            sequences: [
                seq(),
                seq({ sequence_id: 'doc-0', revision_id: 'rev-0', revision_number: 1, role: 'ancestor', derived_by_operation_id: null }, { name: 'pUC19 parent' }),
            ],
            operations: [{
                operation_id: 'op-1', role: 'scoped', operation_kind: 'restriction_digest', title: 'EcoRI digest', status: 'completed',
                implementation: 'bms', implementation_version: '1', parameters: { enzyme: 'EcoRI', summary: { title: 'x' } }, warnings: ['star activity'],
                created_at: '2026-10-21T00:00:00Z',
                inputs: [{ revision_id: 'rev-0', role: 'template', position: 0 }],
                outputs: [{ revision_id: 'rev-1', role: 'product', position: 0 }],
            }],
        },
        ...overrides,
    };
}

function json(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('share guest API', () => {
    it('reads only a well-formed key from the fragment', () => {
        expect(linkKeyFromHash(`#${KEY}`)).toBe(KEY);
        expect(linkKeyFromHash('#short')).toBeNull();
        expect(linkKeyFromHash('')).toBeNull();
    });

    it('normalizes passcodes to the server alphabet', () => {
        expect(normalizeGuestPasscode('abcd-efgh')).toBe('ABCDEFGH');
        expect(normalizeGuestPasscode(' ab cd ef gh ')).toBe('ABCDEFGH');
        expect(normalizeGuestPasscode('ABCD-EFG1')).toBeNull();
        expect(normalizeGuestPasscode('ABC')).toBeNull();
    });

    it('sends credentials as headers and never sends cookies', async () => {
        const base = vi.fn<Transport>(async () => json(envelope()));
        await fetchGuestShare({ key: KEY, passcode: 'ABCDEFGH' }, base);
        const [url, init] = base.mock.calls[0];
        expect(url).toBe('/api/share/bundle');
        const headers = new Headers(init?.headers);
        expect(headers.get('X-BMS-Share-Key')).toBe(KEY);
        expect(headers.get('X-BMS-Share-Passcode')).toBe('ABCDEFGH');
        expect(init?.credentials).toBe('omit');
        expect(String(url)).not.toContain(KEY);
    });

    it.each([
        [401, 'wrong_passcode'], [403, 'blocked'], [404, 'invalid_link'], [410, 'ended'], [423, 'locked'], [429, 'rate_limited'], [500, 'unavailable'],
    ])('maps HTTP %s to %s', async (status, kind) => {
        const base: Transport = async () => json({ detail: { code: 'x', message: 'server said' } }, status);
        await expect(fetchGuestShare({ key: KEY, passcode: 'ABCDEFGH' }, base)).rejects.toMatchObject({ kind });
    });

    it('rejects an unknown bundle schema', () => {
        const bad = envelope();
        (bad.bundle as Record<string, unknown>).schema = 'other';
        expect(() => parseGuestShare(bad)).toThrow(/unsupported format/u);
    });

    it('converts frozen records into viewer data and skips records without a sequence', () => {
        const parsed = parseGuestShare(envelope());
        const data = guestSequenceData(parsed.bundle.sequences[0])!;
        expect(data.name).toBe('pUC19');
        expect(data.circular).toBe(true);
        expect(data.features).toHaveLength(1);
        expect(guestSequenceData({ ...parsed.bundle.sequences[0], record: { ...parsed.bundle.sequences[0].record, sequence: null } })).toBeNull();
    });

    it('shareTransport keeps caller headers', async () => {
        const base = vi.fn<Transport>(async () => json({}));
        await shareTransport({ key: KEY, passcode: 'ABCDEFGH' }, base)('/api/molbio/restriction/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const headers = new Headers(base.mock.calls[0][1]?.headers);
        expect(headers.get('Content-Type')).toBe('application/json');
        expect(headers.get('X-BMS-Share-Key')).toBe(KEY);
    });
});

describe('ShareGuestApp', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;
    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
    });

    async function render(hash: string, transport: Transport) {
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        await act(async () => root?.render(<ShareGuestApp hash={hash} transport={transport} />));
    }
    async function flush() {
        for (let i = 0; i < 6; i += 1) await act(async () => { await Promise.resolve(); });
    }
    async function enterPasscode(value: string) {
        const input = container!.querySelector<HTMLInputElement>('input[aria-label="Passcode"]')!;
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        await act(async () => { setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); });
        await act(async () => container!.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
        await flush();
    }
    const button = (label: string) => [...container!.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith(label))!;

    it('explains a link with no key instead of asking for a passcode', async () => {
        await render('', vi.fn());
        expect(container!.textContent).toContain('Link incomplete');
        expect(container!.querySelector('input')).toBeNull();
    });

    it('shows the wrong-passcode message and keeps the gate', async () => {
        await render(`#${KEY}`, async () => json({ detail: { code: 'share_passcode_incorrect', message: 'the passcode is incorrect (2 attempts left)' } }, 401));
        await enterPasscode('ABCD-EFGH');
        expect(container!.querySelector('[role="alert"]')?.textContent).toBe('the passcode is incorrect (2 attempts left)');
        expect(container!.querySelector('input[aria-label="Passcode"]')).not.toBeNull();
    });

    it.each([
        [403, 'share_client_banned', 'Access blocked'],
        [423, 'share_locked', 'Share locked'],
    ])('replaces the gate when HTTP %s (%s)', async (status, code, title) => {
        await render(`#${KEY}`, async () => json({ detail: { code, message: 'x' } }, status));
        await enterPasscode('ABCDEFGH');
        expect(container!.textContent).toContain(title);
        expect(container!.querySelector('input[aria-label="Passcode"]')).toBeNull();
    });

    it('replaces the gate with an ended message for revoked shares', async () => {
        await render(`#${KEY}`, async () => json({ detail: { code: 'share_revoked', message: 'x' } }, 410));
        await enterPasscode('ABCDEFGH');
        expect(container!.textContent).toContain('Share ended');
        expect(container!.querySelector('input[aria-label="Passcode"]')).toBeNull();
    });

    it('opens the workspace, shows the scoped sequence, and navigates lineage', async () => {
        await render(`#${KEY}`, async () => json(envelope()));
        await enterPasscode('abcd-efgh');
        expect(container!.querySelector('[data-share-guest-workspace]')).not.toBeNull();
        expect(container!.textContent).toContain('For Dana');
        expect(container!.textContent).toMatch(/Read-only share · \d+h \d+m left/u);
        expect(container!.querySelector('[data-viewer]')?.getAttribute('data-name')).toBe('pUC19');
        expect(container!.textContent).toContain('cloning');
        const lineage = [...container!.querySelectorAll('summary')].find((s) => s.textContent?.startsWith('Lineage'))!;
        expect(lineage.textContent).toBe('Lineage (1)');
        expect((lineage.parentElement as HTMLDetailsElement).open).toBe(false);

        await act(async () => button('EcoRI digest').click());
        expect(container!.querySelector('[data-viewer]')).toBeNull();
        expect(container!.textContent).toContain('star activity');
        expect(container!.textContent).toContain('template');

        await act(async () => button('pUC19 parent').click());
        expect(container!.querySelector('[data-viewer]')?.getAttribute('data-name')).toBe('pUC19 parent');
    });

    it('has no controls that could change shared data', async () => {
        await render(`#${KEY}`, async () => json(envelope()));
        await enterPasscode('ABCDEFGH');
        const labels = [...container!.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '').join(' ');
        expect(labels).not.toMatch(/Save|Delete|Edit|Revoke|Share|Upload|Import/u);
    });
});
