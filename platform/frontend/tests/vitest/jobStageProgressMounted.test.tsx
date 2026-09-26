import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { JobStageProgress } from '../../src/components/JobStageProgress';
import { JobQueueTable } from '../../src/components/dashboard/JobQueueTable';
import type { ExecutionStage, Job } from '../../src/lib/api';
import { getModelDisplayName } from '../../src/constants/displayNames';

const statuses: Job['status'][] = ['queued', 'running', 'completed', 'awaiting_input', 'failed', 'cancelled'];
const states: ExecutionStage['state'][] = ['planned', 'running', 'completed', 'awaiting_input', 'failed', 'cancelled', 'unknown'];
const models = ['ppiflow', 'boltzgen', 'rfd3', 'bindcraft2', 'rfantibody', 'antibody_denovo', 'antifold', 'iggm', 'boltz2', 'protenix', 'af2', 'rf3', 'esmfold2', 'proteinmpnn', 'fampnn', 'md', 'nanopore', 'future_native_model'];
const job = (overrides: Partial<Job> = {}): Job => ({
    id: 'stage-job', name: 'Stage fixture', model_id: 'ppiflow', mode: 'protein_binder', status: 'completed',
    params: {}, created_at: '2026-09-01T00:00:00Z', design_count: 0, output_dir: null, ...overrides,
});
let root: Root | undefined;
let host: HTMLDivElement;
async function mount(element: React.ReactNode) {
    host = document.createElement('div'); document.body.append(host);
    root = createRoot(host);
    await act(async () => root!.render(element));
}
function badges() { return [...host.querySelectorAll<HTMLElement>('[data-stage-id]')]; }
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('authoritative stage presentation', () => {
    for (const status of statuses) for (const state of states) {
        it(`${status} job preserves authoritative ${state} stage`, async () => {
            await mount(<JobStageProgress job={job({ status, execution_stages: [{ id: 'custom', label: 'Native optional validation', state, source: 'plan' }] })} />);
            expect(badges()).toHaveLength(1);
            expect(badges()[0].dataset.stageState).toBe(state);
            expect(badges()[0].getAttribute('aria-label')).toBe(`Native optional validation: ${state.replace('_', ' ')}`);
            expect(badges()[0].title).toContain('Source: plan');
            expect(badges()[0].className.includes('emerald')).toBe(state === 'completed');
        });
    }
    for (const model_id of models) for (const status of statuses) {
        it(`${model_id}/${status} missing history stays neutral without mode/default guesses`, async () => {
            await mount(<JobStageProgress job={job({ model_id, status, mode: 'antibody_binder_monomer_oligo',
                all_stages: null, current_stage: 'Complete', completed_stages: null,
                params: { run_maturation: true, run_ppiflow_backbone_refine: true, run_post_validation_maturation: true },
            })} />);
            expect(badges().map(b => b.textContent)).toEqual([getModelDisplayName(model_id)]);
            expect(badges()[0].dataset).toMatchObject({ stageState: 'unknown', stageSource: 'model', stageId: model_id });
            expect(badges()[0].title).toContain('history unavailable');
            expect(badges()[0].className).not.toContain('emerald');
        });
    }
    it('renders raw API process/model keys readably without changing their identity or state', async () => {
        await mount(<JobStageProgress job={job({ execution_stages: [
            { id: 'RunPPIFlowGeneration', label: 'RunPPIFlowGeneration', state: 'unknown', source: 'plan' },
            { id: 'boltz2', label: 'boltz2', state: 'completed', source: 'recorded' },
            { id: 'caliby', label: 'caliby', state: 'unknown', source: 'model' },
        ] })} />);
        expect(badges().map(b => [b.textContent, b.dataset.stageId, b.dataset.stageState])).toEqual([
            ['PPIFlow Generation', 'RunPPIFlowGeneration', 'unknown'],
            ['Boltz-2', 'boltz2', 'completed'], ['Caliby', 'caliby', 'unknown'],
        ]);
    });
    it('honors an empty projection instead of resurrecting legacy history', async () => {
        await mount(<JobStageProgress job={job({ execution_stages: [], all_stages: ['boltz2'], completed_stages: ['boltz2'] })} />);
        expect(badges()).toHaveLength(0);
    });
    it('endpoint projection, including empty, takes precedence', async () => {
        await mount(<JobStageProgress job={job({ execution_stages: [{ id: 'old', label: 'Old', state: 'completed', source: 'recorded' }] })} stagePayload={{ execution_stages: [] }} />);
        expect(badges()).toHaveLength(0);
    });
    it('a legacy endpoint cannot replace the job projection', async () => {
        await mount(<JobStageProgress job={job({ execution_stages: [] })} stagePayload={{ all_stages: ['boltz2'] }} />);
        expect(badges()).toHaveLength(0);
    });
    for (const status of statuses) {
        it(`legacy explicit history: ${status} only colors observed completion`, async () => {
            await mount(<JobStageProgress job={job({ status, all_stages: ['rfantibody', 'fampnn', 'optional_validation'], current_stage: 'fampnn', completed_stages: ['rfantibody'] })} />);
            expect(badges().map(b => [b.dataset.stageId, b.dataset.stageState])).toEqual([
                ['rfantibody', 'completed'], ['fampnn', status === 'completed' ? 'unknown' : status === 'queued' ? 'planned' : status], ['optional_validation', ['completed', 'failed', 'cancelled'].includes(status) ? 'unknown' : 'planned'],
            ]);
        });
    }
    it('retains recorded stages outside a plan; terminal and queue labels are not stages', async () => {
        await mount(<JobStageProgress job={job({ all_stages: ['Complete', 'Queued', 'planned_only'], completed_stages: ['native_step', 'failed'], current_stage: 'Cancelled' })} />);
        expect(badges().map(b => [b.dataset.stageId, b.dataset.stageState])).toEqual([['planned_only', 'unknown'], ['native_step', 'completed']]);
    });
    it('legacy current-only history remains explicit, without a guessed plan', async () => {
        await mount(<JobStageProgress job={job({ current_stage: 'protenix', status: 'running' })} />);
        expect(badges().map(b => [b.textContent, b.dataset.stageState])).toEqual([['Protenix', 'running']]);
    });
    it('does not infer review stages from awaiting_stage alone', async () => {
        await mount(<JobStageProgress job={job({ status: 'awaiting_input', awaiting_stage: 'post_fampnn' })} />);
        expect(badges().map(b => b.dataset.stageId)).toEqual(['ppiflow']);
    });
});

for (const mobile of [false, true]) describe(`real dashboard ${mobile ? 'mobile' : 'desktop'} stage rendering`, () => {
    for (const status of statuses) it(`${status} uses the same labels and independent states`, async () => {
        vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: mobile, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })));
        const execution_stages: ExecutionStage[] = states.map(state => ({ id: state, label: `Native ${state}`, state, source: state === 'planned' ? 'plan' : 'recorded' }));
        await mount(<MemoryRouter><JobQueueTable jobs={[job({ status, execution_stages, parent_job_id: 'completed-parent', child_stage: 'validation' })]} loading={false} quickViewJobId={null}
            onCancel={vi.fn()} onResubmit={vi.fn()} onResume={vi.fn()} onViewLogs={vi.fn()} onViewQuick={vi.fn()} /></MemoryRouter>);
        expect(badges().map(b => [b.textContent, b.dataset.stageState])).toEqual(states.map(state => [`Native ${state}`, state]));
    });
});
