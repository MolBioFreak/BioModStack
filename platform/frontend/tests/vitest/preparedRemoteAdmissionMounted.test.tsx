import React, { act } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, launchAntibodyIteration, launchManualMutagenesis, resumeJob } from '../../src/lib/api';

const button = (text: string) => [...document.querySelectorAll('button')].find(row => row.textContent === text)!;
afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ''; });
for (const action of ['custom', 'manual', 'interactive'] as const) {
    it(`${action} prepares once and requires mounted shared approval before canonical submission`, async () => {
        const prepared = { name: 'prepared', model_id: 'boltz2', mode: 'complex', execution_target_id: 'vast:one', params: { sequence: 'ACDE' } };
        const preview = { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true,
            request: prepared, plan: { requested_json: prepared.params, effective_json: prepared.params,
                source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] };
        const post = vi.spyOn(api, 'post').mockImplementation(async (url) => {
            if (url === '/api/jobs/execution-plan/preview') return { data: preview };
            if (url === '/api/jobs') return { data: { id: 'queued', name: 'prepared' } };
            throw { isAxiosError: true, response: { status: 409, data: { detail: {
                code: 'remote_prepared_job_review_required', job_request: prepared, response_context: { source_job_id: 'source' },
            } } } };
        });
        const pending = action === 'custom' ? launchAntibodyIteration({ source_job_id: 'source', design_ids: ['d'], action: 'validate_boltz2' })
            : action === 'manual' ? launchManualMutagenesis({ source_job_id: 'source', design_ids: ['d'], config: { mutation_sets: ['A1G'] } })
                : resumeJob('source');
        for (let attempt = 0; attempt < 150 && !button('Approve and submit'); attempt++) {
            await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
        }
        expect(post).toHaveBeenCalledTimes(2);
        expect(post.mock.calls[1][0]).toBe('/api/jobs/execution-plan/preview');
        await act(async () => { button('Approve and submit').click(); await pending; });
        expect(post.mock.calls.map(row => row[0]).filter(url => url === '/api/jobs')).toHaveLength(1);
        expect(post).toHaveBeenCalledTimes(3);
        expect(post.mock.calls[2][1]).toMatchObject({ ...prepared, execution_plan_approval: preview.approval_digest });
        expect((await pending).data).toMatchObject({ new_job_id: 'queued', launched_job: { id: 'queued' } });
    }, 15000);
}
