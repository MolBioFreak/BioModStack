import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpMethodExecutionText, type BioXpMethodExecutionTextProps } from '../../src/components/BioXpMethodExecutionText';
import { buildMethodRunRequest } from '../../src/lib/bioxpMethodRunRequest';
import type { MethodValue } from '../../src/lib/bioxpMethods';

const input: MethodValue = { method: { name: 'Synthetic method', steps: [], editor_state: { recovery_linkage: { original_job_id: 'original', occurrence: null } } }, bindings: { amount: '0002.5000', retained: null }, dependencies: [{ id: 'class', revision: 2 }], initial_state: null };
const first = { action_id: 'one', kind: 'note', params: { value: '0002.5000', retained: null } };
const second = { action_id: 'two', kind: 'note', params: { value: 'x'.repeat(60000) + 'COMPLETE-END' } };
const result = { document: { stages: [{ stage_id: 's1', name: 'First', actions: [first] }, { stage_id: 's2', name: 'Second', actions: [second] }] }, digest: 'fixture', issues: [] };
let host: HTMLDivElement, root: Root;
let fetchSpy: ReturnType<typeof vi.fn>, postSpy: ReturnType<typeof vi.spyOn>, identitySpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    fetchSpy = vi.fn(() => { throw new Error('No network allowed'); }); vi.stubGlobal('fetch', fetchSpy);
    postSpy = vi.spyOn(api, 'post'); identitySpy = vi.spyOn(crypto, 'randomUUID');
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove();
    expect(fetchSpy).not.toHaveBeenCalled(); expect(postSpy).not.toHaveBeenCalled(); expect(identitySpy).not.toHaveBeenCalled();
    vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function mount(props: Partial<BioXpMethodExecutionTextProps> = {}) {
    await act(async () => root.render(<BioXpMethodExecutionText input={input} result={result} actions={[first]} pageLabel="Preparation" generation={17} {...props} />));
}
async function click(label: string) {
    const button = [...host.querySelectorAll('button')].find(el => el.textContent === label)!;
    await act(async () => button.click());
}
const text = (label = 'Execution text') => host.querySelector<HTMLTextAreaElement>(`textarea[aria-label="${label}"]`)!;

it.each([
    { pinned: { id: 'saved / α', revision: 7 }, basis: { id: 'saved / α', revision: 7, edited: false } },
    { pinned: null, basis: null },
    { pinned: null, basis: { id: 'saved', revision: 4, edited: true } },
])('matches authoritative submit body for $pinned and $basis', ({ pinned, basis }) => {
    const frozen = JSON.parse(JSON.stringify(input));
    const request = buildMethodRunRequest(frozen, pinned, basis, 'existing-key', 17);
    expect(request.path).toBe(pinned ? `library/${encodeURIComponent(pinned.id)}/runs` : 'quick-runs');
    expect(request.body).toStrictEqual({ ...(pinned ? { revision: pinned.revision } : { method: frozen.method, ...(basis ? { based_on: basis } : {}) }), bindings: frozen.bindings, dependencies: frozen.dependencies, initial_state: frozen.initial_state, recovery: frozen.method.editor_state.recovery_linkage, idempotency_key: 'existing-key', expected_generation: 17, acknowledge_live: true });
    expect(request.body.recovery).not.toBe(frozen.method.editor_state.recovery_linkage);
    expect(frozen).toStrictEqual(input);
});

it('preserves serialized omission, explicit null, numeric spelling and falsey recovery semantics', () => {
    for (const linkage of [undefined, null, false, '', 0]) {
        const frozen = { method: { editor_state: { recovery_linkage: linkage } }, dependencies: null };
        const body = JSON.parse(JSON.stringify(buildMethodRunRequest(frozen, null, null, 'key', 0).body));
        expect(body).not.toHaveProperty('recovery'); expect(body).not.toHaveProperty('bindings'); expect(body).not.toHaveProperty('initial_state'); expect(body.dependencies).toBeNull();
    }
    expect(buildMethodRunRequest(input, null, null, 'key', 0).body.bindings).toEqual(input.bindings);
});

it('starts closed and displays ordered selected/full native JSON without truncation or submission', async () => {
    await mount();
    expect([...host.querySelectorAll('details')].every(el => !el.open)).toBe(true);
    expect(host.querySelector('summary')?.textContent).toBe('Execution text (optional)');
    expect(text().readOnly).toBe(true); expect(text().wrap).toBe('off');
    expect(text().value).toContain('Stage: Preparation'); expect(text().value).toContain(JSON.stringify([first], null, 2)); expect(text().value).not.toContain('COMPLETE-END');
    await click('Full workflow');
    expect(text().value).toContain(JSON.stringify([second], null, 2)); expect(text().value.indexOf('Stage: First')).toBeLessThan(text().value.indexOf('Stage: Second'));
    expect(text().value).toContain('not individual HTTP calls');
    await click('Selected page'); expect(text().value).not.toContain('COMPLETE-END');
});

it('shows exact check and shared run template, with explicit key and generation placeholders', async () => {
    const pinned = { id: 'saved/a', revision: 3 };
    await mount({ pinned });
    const requestText = text('API request text').value;
    expect(requestText).toContain(`POST /api/bioxp/methods/check\n${JSON.stringify(input, null, 2)}`);
    const request = buildMethodRunRequest(input, pinned, null, 'GENERATED_ON_RUN', 17);
    expect(requestText).toContain(`POST /api/bioxp/methods/${request.path}\n${JSON.stringify(request.body, null, 2)}`);
    expect(requestText).toContain('display placeholder');
    await mount({ generation: undefined }); expect(text('API request text').value).toContain('"expected_generation": "UNKNOWN"');
});

it('never substitutes stale selected actions without fresh compilation', async () => {
    await mount({ result: undefined, actions: [second] });
    expect(text().value).toContain('Compiled actions unavailable'); expect(text().value).toContain(JSON.stringify(input, null, 2)); expect(text().value).not.toContain('COMPLETE-END');
    await click('Full workflow'); expect(text().value).toContain(JSON.stringify(input, null, 2));
    await mount({ result: { ...result, document: null } }); expect(text().value).toContain('Compiled actions unavailable');
});

it('copies and downloads the complete displayed text without execution', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: { writeText } } }));
    const create = vi.fn().mockReturnValue('blob:fixture'), revoke = vi.fn();
    vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
    const linkClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await mount(); await click('Full workflow'); await click('Copy');
    expect(writeText).toHaveBeenCalledWith(text().value);
    await click('Download txt'); expect(linkClick).toHaveBeenCalledOnce();
    const blob = create.mock.calls[0][0] as Blob;
    const downloaded = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob); });
    expect(downloaded).toBe(text().value);
    await new Promise(resolve => setTimeout(resolve, 5)); expect(revoke).toHaveBeenCalledWith('blob:fixture');
});
