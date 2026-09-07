import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
let root: Root;
const render = (node: React.ReactNode) => { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); act(() => root.render(node)); };
const cleanup = () => { act(() => root?.unmount()); document.body.replaceChildren(); };
const screen = {
    getByLabelText: (label: string): HTMLInputElement => { const element = document.querySelector<HTMLInputElement>(`[aria-label="${label}"]`); if (!element) throw Error(`Missing control: ${label}`); return element; },
    getByText: (text: string | RegExp): HTMLElement => { const element = Array.from(document.querySelectorAll<HTMLElement>('button,p')).find(el => typeof text === 'string' ? el.textContent === text : text.test(el.textContent || '')); if (!element) throw Error(`Missing text: ${text}`); return element; },
};
const fireEvent = {
    click: (element: HTMLElement) => act(() => element.click()),
    change: (element: HTMLInputElement, event: { target: { value: string } }) => act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, event.target.value); element.dispatchEvent(new Event('input', { bubbles: true })); }),
};
import { afterEach, expect, it, vi } from 'vitest';
import { FampnnAnalysisControls, fampnnOverridePayload, type FampnnAnalysisOverrides } from '../../src/components/FampnnAnalysisControls';
import { submitJob } from '../../src/lib/api';
import axios from 'axios';
vi.mock('axios', () => {
    const client = { post: vi.fn(async (_url: string, payload: unknown) => ({ data: payload })), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } };
    return { default: { create: () => client } };
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function Launcher() {
    const [value, setValue] = useState<FampnnAnalysisOverrides>();
    return <><FampnnAnalysisControls value={value} onChange={setValue} summaryDefault="Authorized sequence-design region" />
        <button onClick={() => submitJob({ model_id: 'fampnn', mode: 'design', params: {}, ...fampnnOverridePayload(value) }, { launchContext: false })}>Submit</button></>;
}
it('rejects malformed saved overrides instead of silently dropping them', () => {
    expect(() => fampnnOverridePayload({ mutation: [{ chain_id: 'AB', author_number: 1, insertion_code: '' }] })).toThrow();
    expect(() => fampnnOverridePayload({ mutation: [{ chain_id: 'A', author_number: Number.NaN, insertion_code: '' }] })).toThrow();
});
it('mounted typed scope controls serialize omission, empty and insertion-code selection through submitJob', () => {
    render(<Launcher />);
    const post = axios.create().post;
    fireEvent.click(screen.getByText('Submit'));
    expect(vi.mocked(post).mock.calls.at(-1)?.[1]).not.toHaveProperty('fampnn_analysis_overrides');
    fireEvent.click(screen.getByLabelText('Override mutation scope'));
    fireEvent.click(screen.getByText('Submit'));
    expect(vi.mocked(post).mock.calls.at(-1)?.[1]).toMatchObject({ fampnn_analysis_overrides: { mutation: [] } });
    fireEvent.change(screen.getByLabelText('mutation chain'), { target: { value: 'H' } });
    fireEvent.change(screen.getByLabelText('mutation author residue'), { target: { value: '-4' } });
    fireEvent.change(screen.getByLabelText('mutation insertion code'), { target: { value: 'A' } });
    fireEvent.click(screen.getByText('Add mutation residue'));
    fireEvent.click(screen.getByText('Submit'));
    expect(vi.mocked(post).mock.calls.at(-1)?.[1]).toMatchObject({ fampnn_analysis_overrides: { mutation: [{ chain_id: 'H', author_number: -4, insertion_code: 'A' }] } });
    expect(screen.getByText(/only narrow/i)).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Override mutation scope'));
    fireEvent.click(screen.getByText('Submit'));
    expect(vi.mocked(post).mock.calls.at(-1)?.[1]).not.toHaveProperty('fampnn_analysis_overrides');
});
