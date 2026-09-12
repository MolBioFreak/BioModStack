import './preparedRemoteAdmissionMounted.test';
import React, { act } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, submitJob } from '../../src/lib/api';
import type { ExecutionPlanPreview } from '../../src/components/ExecutionPlanApproval';

const preview: ExecutionPlanPreview = {
    schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true,
    request: { model_id: 'boltz2', mode: 'predict', execution_target_id: 'vast:one' },
    plan: { requested_json: { sequence: 'ACDE', boltz_use_msa: true },
        effective_json: { sequence: 'ACDE', boltz_use_msa: true, msa_provider: 'colabfold_api' },
        source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) },
        metadata: { static_components: [{ component_key: 'Boltz2Predict' }], dynamic_templates: [],
            external_services: [{ logical_id: 'boltz2:msa', provider: 'colabfold_api', state: 'planned' }] } },
    deferred_preparation: ['boltz2:msa'], blockers: [],
};
const settle = async () => {
    for (let attempt = 0; attempt < 150 && !button('Approve and submit'); attempt++) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
    }
};
const button = (text: string) => [...document.querySelectorAll('button')].find(row => row.textContent === text)!;
afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ''; });

it('actual shared submitJob waits for visible operator review and freezes science/placement', async () => {
    const post = vi.spyOn(api, 'post').mockImplementation(async (url) => ({ data: url.endsWith('/preview') ? preview : { id: 'queued' } }));
    const request = { name: 'test', model_id: 'boltz2', mode: 'predict', execution_target_id: 'vast:one', params: { sequence: 'ACDE', boltz_use_msa: true } };
    const pending = submitJob(request, { launchContext: false });
    await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe('/api/jobs/execution-plan/preview');
    expect(document.body.textContent).toContain('colabfold_api');
    expect(document.body.textContent).toContain('does not contact an MSA provider');
    expect(document.body.textContent).toContain(preview.approval_digest);
    act(() => button('Show all settings').click());
    expect(document.body.textContent).toContain('ACDE');
    request.params.sequence = 'CHANGED'; request.execution_target_id = 'vast:two';
    await act(async () => { button('Approve and submit').click(); await pending; });
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][1]).toMatchObject({ execution_plan_approval: preview.approval_digest,
        execution_target_id: 'vast:one', params: { sequence: 'ACDE' } });
}, 15000);

it('cancel does not enqueue and unsupported plans cannot be approved', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { ...preview, admissible: false, blockers: [{ reason: 'Unsupported selected component' }] } });
    const pending = submitJob({ ...preview.request, name: 'test', params: {} }, { launchContext: false }).catch(error => error);
    await settle();
    expect(button('Approve and submit').disabled).toBe(true);
    expect(document.body.textContent).toContain('Unsupported selected component');
    await act(async () => { button('Cancel').click(); await pending; });
    expect(post).toHaveBeenCalledTimes(1);
});

it('agent-supplied approval is forwarded unchanged; explicit local launches do not preview', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    await submitJob({ ...preview.request, execution_plan_approval: preview.approval_digest }, { launchContext: false });
    expect(post.mock.calls[0][1]).toMatchObject({ execution_plan_approval: preview.approval_digest });
    await submitJob({ ...preview.request, execution_target_id: null }, { launchContext: false });
    expect(post.mock.calls.map(row => row[0])).toEqual(['/api/jobs', '/api/jobs']);
});
