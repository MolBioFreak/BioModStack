import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { decodeBioXpReceiptDetailV2, useInvokeBioXpDeckActionV2, assertBioXpOperatorActionV2Request, type BioXpDeckSubmission } from '../../src/lib/bioxpClient';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
type Request = BioXpDeckSubmission['request'];
let hook: ReturnType<typeof useInvokeBioXpDeckActionV2>;
let root: Root; let container: HTMLDivElement; let client: QueryClient;
let generation: number;
let posts: Array<{ url: string; body: any; resolve: (value: any) => void; reject: (value: any) => void }>;
const request = (key: string, named = false): Request => ({
    expected_connection_generation: generation, schema_version: 'bioxp.operator_action_request.v2',
    idempotency_key: key, expected_ownership_generation: 4, expected_board_epoch_by_board: { '4': 2 },
    ...(named ? { action_id: 'oem.deck.move_to_location' as const, inputs: { target: 'LOC_PARK', camera_offset: false } }
        : { action_id: 'oem.deck.move_to_well' as const, inputs: { location_id: 1, well: 'H1', position_flag: 1 as const } }),
});
// Deliberately minimal transport replies qualify passthrough, not native production.
const receipt = (i: number) => ({ action_id: posts[i].url.split('/').at(-1), command_id: `native-${i}`,
    status: 'dispatched', terminal: false, physical_effect_verified: false, native_extension: { unknown: null } });
function Harness() { hook = useInvokeBioXpDeckActionV2(generation, true); return null; }
const advance = async (ms = 1) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const render = async () => { await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>)); await advance(); };
const accept = async (i: number) => { await act(async () => posts[i].resolve({ data: receipt(i) })); await advance(); };
beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks(); generation = 7; posts = [];
    vi.mocked(api.post).mockImplementation((url, body) => new Promise((resolve, reject) => posts.push({ url, body, resolve, reject })));
    vi.mocked(api.get).mockRejectedValue({ response: { status: 404 } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); vi.useRealTimers(); });
it('preserves well public identity in additive recovery detail without rewriting its outcome', () => {
    const detail: any = { action_id: 'oem.deck.move_to_well', command_id: 'well-recovery',
        terminal: true, status: 'failed', physical_effect_verified: false,
        deck_movement: { recovery_resolution: { command_id: 'well-recovery', decision_id: 'decision-1',
            semantic_state_revision: 2, transition_sequence: 3 } } };
    expect(decodeBioXpReceiptDetailV2(detail, 'well-recovery')).toBe(detail);
    expect(() => decodeBioXpReceiptDetailV2(detail, 'wrong')).toThrow();
});
it('captures explicit input/epochs, drops same-turn duplicates and shares successive mixed admission owner', async () => {
    await render();
    for (let i = 0; i < 8; i++) {
        const intent = request(`mixed-${i}`, i % 2 === 1); const captured = structuredClone(intent);
        await act(async () => { expect(hook.submit(intent)).toBe(true); expect(hook.submit(request(`dropped-${i}`))).toBe(false);
            intent.expected_board_epoch_by_board['4'] = 999;
            if (intent.action_id === 'oem.deck.move_to_well') intent.inputs.well = 'A1';
            else intent.inputs.target = 'LOC_TC';
        }); await advance();
        expect(posts).toHaveLength(i + 1);
        const { action_id, ...body } = captured;
        expect(posts[i].body).toEqual(body); expect(posts[i].url).toContain(action_id);
        expect(api.post).toHaveBeenLastCalledWith(posts[i].url, body, { timeout: 12000 });
        await accept(i);
        expect(hook.submissions[i].receipt).toEqual(receipt(i));
    }
    expect(new Set(posts.map(p => p.body.idempotency_key)).size).toBe(8);
});
it.each(['timeout', 'malformed', 'mismatch'])('reconciles %s by original identity without replay or historical lock', async mode => {
    await render(); await act(async () => hook.submit(request('original'))); await advance();
    await act(async () => mode === 'timeout' ? posts[0].reject(new Error('timeout'))
        : posts[0].resolve({ data: mode === 'malformed' ? null : { ...receipt(0), action_id: 'wrong' } }));
    await advance(2001); expect(hook.submissions[0].state).toBe('uncertain');
    vi.mocked(api.get).mockResolvedValue({ data: { ...receipt(0), action_id: 'wrong' } });
    await advance(2001); expect(hook.submissions[0].state).toBe('uncertain');
    await act(async () => hook.submit(request('later', true))); await advance(); await accept(1);
    expect(posts).toHaveLength(2); expect(hook.submissions[1].state).toBe('accepted');
    vi.mocked(api.get).mockResolvedValue({ data: receipt(0) });
    await advance(2001); await advance();
    expect(hook.submissions[0].state).toBe('accepted'); expect(hook.submissions[0].receipt).toEqual(receipt(0));
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/requests/original'), expect.objectContaining({ params: { expected_connection_generation: 7 } }));
    expect(posts).toHaveLength(2);
});
it('fences late old-generation admission and never resumes a dropped intent', async () => {
    await render(); await act(async () => { hook.submit(request('old')); hook.submit(request('dropped')); }); await advance();
    generation = 8; await render(); await accept(0); await advance(4001);
    expect(hook.submissions[0].state).toBe('uncertain'); expect(hook.submissions[0].receipt).toBeUndefined();
    expect(api.get).not.toHaveBeenCalled(); expect(posts).toHaveLength(1);
    await act(async () => hook.submit(request('current'))); await advance(); await accept(1);
    expect(hook.submissions[1].state).toBe('accepted');
    expect(posts[1].body.expected_connection_generation).toBe(8);
});
it('does not attach a late old-generation lookup to the new connection', async () => {
    let resolveLookup!: (value: any) => void;
    vi.mocked(api.get).mockImplementation(() => new Promise(resolve => { resolveLookup = resolve; }));
    await render(); await act(async () => hook.submit(request('old-lookup'))); await advance();
    await act(async () => posts[0].reject(new Error('timeout'))); await advance();
    expect(api.get).toHaveBeenCalledTimes(1);
    generation = 8; await render();
    await act(async () => resolveLookup({ data: receipt(0) })); await advance();
    expect(hook.submissions[0].state).toBe('uncertain'); expect(hook.submissions[0].receipt).toBeUndefined();
    await act(async () => hook.submit(request('new-generation'))); await advance(); await accept(1);
    expect(hook.submissions[1].receipt).toEqual(receipt(1)); expect(posts).toHaveLength(2);
});
it('keeps explicit native refusal and releases only the short submission owner', async () => {
    await render(); await act(async () => hook.submit(request('refused'))); await advance();
    const error = { response: { status: 409, data: { detail: 'OEM interlock' } } };
    await act(async () => posts[0].reject(error)); await advance();
    expect(hook.submissions[0].state).toBe('rejected'); expect(hook.submissions[0].error).toBe(error);
    await act(async () => hook.submit(request('new'))); await advance(); expect(posts).toHaveLength(2);
});
it.each([{}, { location_id: true, well: 'A1', position_flag: 1 }, { location_id: 1, well: false, position_flag: 1 },
    { location_id: 1, well: 'A1' }, { location_id: 1, well: 'A1', position_flag: 3 },
    { location_id: 1, well: 'A1', position_flag: 1, x: 0 }])('rejects non-contract inputs before submission: %j', inputs => {
    expect(() => assertBioXpOperatorActionV2Request({ ...request('invalid'), inputs } as Request)).toThrow();
    expect(api.post).not.toHaveBeenCalled();
});
