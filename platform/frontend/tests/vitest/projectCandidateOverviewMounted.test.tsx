// Synthetic native reads and mounted navigation, not scientific acceptance.
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectCandidateOverview } from '../../src/components/project-manager/ProjectCandidateOverview';
import { buildProjectCandidateData, fetchProjectCandidateData, type CandidateSource } from '../../src/lib/projectCandidateOverview';
import type { Job } from '../../src/lib/api';
const mocks = vi.hoisted(() => ({ get: vi.fn(), project: vi.fn(), family: vi.fn(), page: vi.fn() }));
vi.mock('../../src/lib/api', () => ({ api: { get: mocks.get } }));
vi.mock('../../src/lib/projectManager', () => ({ getProject: mocks.project }));
vi.mock('../../src/lib/binderResultFamily', () => ({ fetchBinderResultFamily: mocks.family }));
vi.mock('../../src/lib/bindcraft2Results', () => ({ object: (v: unknown) => v && typeof v === 'object' && !Array.isArray(v) ? v : {}, fetchBC2Page: mocks.page }));
const job = (id: string, extra: Partial<Job> = {}) => ({ id, name: id, model_id: 'protenix', status: 'completed', mode: 'complex', params: {}, created_at: '', design_count: 1, output_dir: null, ...extra }) as Job;
const native = 'design_l3_abcdef1234567890_candidate2';
const source: CandidateSource = { id: 'source', label: 'Site A', job: job('source', { model_id: 'bindcraft2' }), draws: [{ design: native, sequence: 'AAA', trajectory_design: 'family', arm: null }] };
const jobs = [job('mono', { provenance: { binder_round_step: { source_ref: { job_id: source.id, native_record_id: native }, input_components: [{ role: 'binder', sequence: 'AAA' }, { role: 'target', sequence: 'TTT' }] } } }), ...[1, 3].map(count => job(`trimer-${count}`, { selection_source_job_id: source.id, params: { selection_source_type: 'selected_native_sequences', sequence_name: native, complex_components: [{ sequence: 'AAA', count }, { sequence: 'TTT', count: 3 }] } })), job('failed', { status: 'failed', provenance: { binder_round_step: { retry_of_job_id: 'mono' } } })];
let root: Root | undefined;
let host: HTMLDivElement;
afterEach(async () => { if (root) await act(() => root!.unmount()); root = undefined; host?.remove(); vi.clearAllMocks(); });
function HistoryControls() {
    const navigate = useNavigate();
    const location = useLocation();
    return <><output data-location>{location.pathname}{location.search}</output><button onClick={() => navigate(-1)}>Return to candidates</button></>;
}

describe('project candidate overview', () => {
    it('preserves committed search when following a result and returning', async () => {
        host = document.createElement('div'); document.body.append(host); root = createRoot(host);
        await act(() => root!.render(<MemoryRouter initialEntries={['/projects/project']}><HistoryControls /><Routes>
            <Route path="/projects/:id" element={<ProjectCandidateOverview data={buildProjectCandidateData([source], jobs)} onOpenRecords={() => {}} />} />
            <Route path="/designs/:id" element={<p>Exact validation result</p>} />
        </Routes></MemoryRouter>));
        const input = host.querySelector('input')!;
        await act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'abcdef'); input.dispatchEvent(new Event('input', { bubbles: true })); });
        expect(host.querySelector('[data-location]')?.textContent).toContain('candidate_search=abcdef');
        await act(() => (host.querySelector('a[href="/designs/mono?include_children=false"]') as HTMLAnchorElement).click());
        expect(host.textContent).toContain('Exact validation result');
        await act(() => [...host.querySelectorAll('button')].find(b => b.textContent === 'Return to candidates')!.click());
        expect(host.querySelector('input')?.value).toBe('abcdef');
        expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
    });
    it('renders exact links, local overflow, collapsed failure history, search and Records', async () => {
        host = document.createElement('div'); document.body.append(host); root = createRoot(host);
        const records = vi.fn();
        await act(() => root!.render(<MemoryRouter><ProjectCandidateOverview data={buildProjectCandidateData([source], jobs)} onOpenRecords={records} /></MemoryRouter>));
        const links = [...host.querySelectorAll('a')].map(a => a.getAttribute('href'));
        expect(links).toContain('/designs/source');
        for (const id of ['mono', 'trimer-1', 'trimer-3']) expect(links).toContain(`/designs/${id}?include_children=false`);
        expect(links).toContain('/jobs/failed');
        const exact = new URL(links.find(x => x?.includes('native_candidate'))!, 'http://local');
        expect(JSON.parse(exact.searchParams.get('native_candidate')!)).toEqual([null, 'draw', native]);
        expect(JSON.parse(exact.searchParams.get('native_scope')!)).toEqual(['bindcraft2', null, 'draw']);
        expect(host.querySelector('table')?.parentElement?.className).toContain('overflow-x-auto');
        const failed = [...host.querySelectorAll('details')].find(d => d.textContent?.includes('Failed / cancelled'))!;
        expect(failed.open).toBe(false);
        expect(host.textContent).toContain('not scientific acceptance');
        const input = host.querySelector('input')!;
        await act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'absent'); input.dispatchEvent(new Event('input', { bubbles: true })); });
        expect(host.querySelectorAll('tbody tr')).toHaveLength(0);
        await act(() => (host.querySelector('button')!).click()); expect(records).toHaveBeenCalledOnce();
    });
    it('uses only referenced verified BC2 families, paginates draws and forwards cancellation', async () => {
        mocks.project.mockResolvedValue({ payload: { external_references: [{ value: '/designs/source', label: 'Site A' }, { value: '/designs/other' }] } });
        mocks.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/source') ? source.job : job('other') }));
        mocks.family.mockResolvedValue(jobs);
        mocks.page.mockResolvedValueOnce({ rows: source.draws, total: 2 }).mockResolvedValueOnce({ rows: [{ design: 'another', sequence: 'BBB' }], total: 2 });
        const signal = new AbortController().signal;
        const result = await fetchProjectCandidateData('project', signal);
        expect(result.supported).toBe(true); expect(result.candidates).toHaveLength(1);
        expect(mocks.family).toHaveBeenCalledExactlyOnceWith('source', signal);
        expect(mocks.page).toHaveBeenNthCalledWith(2, 'source', null, 'draw', 1, signal);
        expect(mocks.get).toHaveBeenCalledTimes(2);
    });
    it('propagates cancellation rather than reporting a partial inventory', async () => {
        mocks.project.mockResolvedValue({ payload: { external_references: [{ value: '/designs/source' }] } });
        mocks.get.mockResolvedValue({ data: source.job });
        const controller = new AbortController();
        mocks.family.mockImplementation(() => { controller.abort(); throw new Error('aborted'); });
        await expect(fetchProjectCandidateData('project', controller.signal)).rejects.toThrow('aborted');
    });
    it('keeps discovered BC2 sources supported on family errors; unrelated projects use Records', async () => {
        mocks.project.mockResolvedValue({ payload: { external_references: [{ value: '/designs/source' }] } });
        mocks.get.mockResolvedValue({ data: source.job }); mocks.family.mockRejectedValue(new Error('unavailable'));
        const result = await fetchProjectCandidateData('project');
        expect(result.supported).toBe(true); expect(result.errors[0]).toContain('unavailable'); expect(result.sources).toHaveLength(1);
        mocks.project.mockResolvedValue({ payload: {} });
        expect((await fetchProjectCandidateData('unrelated')).supported).toBe(false);
    });
});
