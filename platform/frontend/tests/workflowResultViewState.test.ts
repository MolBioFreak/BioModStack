import assert from 'node:assert/strict';
import test from 'node:test';


test('workflow result model and scope are URL-owned with workflow-primary defaults', async () => {
    const module = await import('../src/components/frustrampnn/workflowResultViewState.js').catch(() => null);
    assert.ok(module, 'workflow result view state contract is missing');

    assert.deepEqual(
        module.parseWorkflowResultViewState('', {
            frustraMpnnAvailable: true,
            directFrustraMpnnJob: false,
        }),
        { model: 'workflow', scope: 'this-job' },
    );
    assert.deepEqual(
        module.parseWorkflowResultViewState('', {
            frustraMpnnAvailable: true,
            directFrustraMpnnJob: true,
        }),
        { model: 'frustrampnn', scope: 'this-job' },
    );
    assert.deepEqual(
        module.parseWorkflowResultViewState(
            '?result_model=frustrampnn&frustrampnn_scope=whole-experiment',
            { frustraMpnnAvailable: true, directFrustraMpnnJob: false },
        ),
        { model: 'frustrampnn', scope: 'whole-experiment' },
    );
    assert.deepEqual(
        module.parseWorkflowResultViewState(
            '?result_model=frustrampnn&frustrampnn_scope=whole-experiment',
            { frustraMpnnAvailable: false, directFrustraMpnnJob: false },
        ),
        { model: 'workflow', scope: 'this-job' },
    );

    const next = module.updateWorkflowResultViewSearch(
        '?frustrampnn_invocation_id=inv-2&other=kept',
        { model: 'frustrampnn', scope: 'whole-experiment' },
    );
    assert.equal(
        next,
        '?frustrampnn_invocation_id=inv-2&other=kept&result_model=frustrampnn&frustrampnn_scope=whole-experiment',
    );

    assert.equal(typeof module.parseFrustraMpnnExperimentContext, 'function');
    assert.equal(
        module.parseFrustraMpnnExperimentContext('?workspace_id=project-1&global_experiment_id=experiment-1'),
        null,
    );
    assert.deepEqual(
        module.parseFrustraMpnnExperimentContext(
            '?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1',
        ),
        {
            projectId: 'project-1',
            globalExperimentId: 'experiment-1',
            domainExperimentId: 'domain-1',
        },
    );
});
