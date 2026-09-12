import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { ExecutionPolicyControl } from '../../src/components/ExecutionPolicyControl';
import { api, submitJob } from '../../src/lib/api';
import { RESULT_POLICY_DEFAULT_KEY, setDraftExecutionPolicy } from '../../src/lib/executionPolicy';

const cleanups: (() => void)[] = [];
function mount() {
    const container = document.createElement('div'); document.body.append(container);
    const root = createRoot(container);
    act(() => root.render(<ExecutionPolicyControl />));
    const unmount = () => { act(() => root.unmount()); container.remove(); };
    cleanups.push(unmount);
    return { container, unmount };
}
afterEach(() => {
    cleanups.splice(0).forEach(fn => fn()); vi.restoreAllMocks(); localStorage.clear();
    sessionStorage.clear(); setDraftExecutionPolicy(undefined);
});
const job = { name: 'test', model_id: 'boltz2', mode: 'predict', params: { sequence: 'ACDE' } };
it('mounted control is manual until explicit opt-in; submission uses typed execution policy outside science', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    const view = mount();
    const select = view.container.querySelector('select')!;
    expect(select.value).toBe('manual');
    await submitJob(job);
    expect(post.mock.calls.at(-1)?.[1]).toEqual({ ...job, execution_target_id: null, execution_policy: { remote_result_policy: 'manual' } });
    act(() => { select.value = 'automatic'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await submitJob(job);
    expect(post.mock.calls.at(-1)?.[1]).toEqual({ ...job, execution_target_id: null, execution_policy: { remote_result_policy: 'automatic' } });
    expect(localStorage.getItem(RESULT_POLICY_DEFAULT_KEY)).toBeNull();
    expect(post.mock.calls.every(call => call[0] === '/api/jobs')).toBe(true);
});
it('explicit new-job preference survives remount but never overrides clone or explicit saved job policy', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    let view = mount();
    act(() => {
        const select = view.container.querySelector('select')!;
        select.value = 'automatic'; select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => view.container.querySelector('button')!.click());
    expect(localStorage.getItem(RESULT_POLICY_DEFAULT_KEY)).toBe('automatic');
    cleanups.pop()!();
    view = mount();
    expect(view.container.querySelector('select')!.value).toBe('automatic');
    cleanups.pop()!();
    localStorage.setItem('clonedJobData', JSON.stringify({ ...job, execution_policy: { remote_result_policy: 'manual' } }));
    view = mount();
    expect(view.container.querySelector('select')!.value).toBe('manual');
    await submitJob(job);
    expect(post.mock.calls.at(-1)?.[1]).toEqual({ ...job, execution_target_id: null, execution_policy: { remote_result_policy: 'manual' } });
    await submitJob({ ...job, execution_policy: { remote_result_policy: 'automatic' } });
    expect(post.mock.calls.at(-1)?.[1]).toEqual({ ...job, execution_target_id: null, execution_policy: { remote_result_policy: 'automatic' } });
    expect(localStorage.getItem(RESULT_POLICY_DEFAULT_KEY)).toBe('automatic');
});
it('automatic saved clones survive a manual new-job default; invalid preferences fail manual', () => {
    localStorage.setItem(RESULT_POLICY_DEFAULT_KEY, 'true');
    let view = mount();
    expect(view.container.querySelector('select')!.value).toBe('manual');
    cleanups.pop()!();
    localStorage.setItem('clonedJobData', JSON.stringify({ execution_policy: { remote_result_policy: 'automatic' } }));
    view = mount();
    expect(view.container.querySelector('select')!.value).toBe('automatic');
});
