import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildWorkflowModelResults,
    filterDesignsForResultModel,
} from '../src/components/frustrampnn/workflowModelResults.js';
import {
    parseFrustraMpnnExperimentContext,
    parseWorkflowResultViewState,
    updateWorkflowResultViewSearch,
} from '../src/components/frustrampnn/workflowResultViewState.js';

const designs = [
    { id: 'structure-1', provenance: { model_id: 'boltz2' }, artifact_class: 'predicted_complex' },
    { id: 'validated-1', provenance: { model_id: 'protenix' }, artifact_class: 'validated_complex' },
    { id: 'other-1', provenance: { model_id: 'thermompnn' }, artifact_class: 'sequence_designed_complex' },
];

test('structure workflow exposes primary and persisted real-model siblings in stable order', () => {
    const hierarchy = buildWorkflowModelResults({
        job: { model_id: 'structure_prediction', params: { structure_validator: 'protenix' } },
        designs,
        frustraMpnnAvailable: true,
    });
    assert.deepEqual(hierarchy.map(({ modelId, label }) => ({ modelId, label })), [
        { modelId: 'structure_prediction', label: 'Structure Prediction' },
        { modelId: 'protenix', label: 'Validator' },
        { modelId: 'frustrampnn', label: 'FrustraMPNN' },
        { modelId: 'boltz2', label: 'Boltz-2' },
        { modelId: 'thermompnn', label: 'ThermoMPNN' },
    ]);
    assert.deepEqual(filterDesignsForResultModel(designs, 'protenix').map((item) => item.id), ['validated-1']);
    assert.deepEqual(filterDesignsForResultModel(designs, 'thermompnn').map((item) => item.id), ['other-1']);
});

test('Boltz2 structure-prediction jobs keep the workflow result primary and expose only persisted model siblings', () => {
    const hierarchy = buildWorkflowModelResults({
        job: { model_id: 'boltz2', mode: 'structure_prediction', params: {} },
        designs: [{ id: 'boltz-structure', provenance: { model_id: 'boltz2' }, artifact_class: 'predicted_complex' }],
        frustraMpnnAvailable: true,
    });
    assert.deepEqual(hierarchy.map(({ modelId, label, kind }) => ({ modelId, label, kind })), [
        { modelId: 'structure_prediction', label: 'Structure Prediction', kind: 'primary' },
        { modelId: 'frustrampnn', label: 'FrustraMPNN', kind: 'frustrampnn' },
        { modelId: 'boltz2', label: 'Boltz-2', kind: 'model' },
    ]);
});


test('result_model selects an available real model and defaults to the workflow primary', () => {
    const availableModelIds = ['structure_prediction', 'protenix', 'frustrampnn'];
    assert.equal(parseWorkflowResultViewState('?result_model=protenix', {
        availableModelIds,
        primaryModelId: 'structure_prediction',
    }).model, 'protenix');
    assert.equal(parseWorkflowResultViewState('?result_model=unknown', {
        availableModelIds,
        primaryModelId: 'structure_prediction',
    }).model, 'structure_prediction');
    assert.equal(parseWorkflowResultViewState('', {
        availableModelIds,
        primaryModelId: 'structure_prediction',
    }).model, 'structure_prediction');
});

test('Project Manager context preserves exact selected revisions through deep links', () => {
    const search = '?workspace_id=project-1&global_experiment_id=global-1&domain_experiment_id=domain-1&global_experiment_revision_id=global-rev-4&domain_revision_id=domain-rev-7&result_model=frustrampnn&frustrampnn_scope=whole-experiment';
    assert.deepEqual(parseFrustraMpnnExperimentContext(search), {
        projectId: 'project-1',
        globalExperimentId: 'global-1',
        domainExperimentId: 'domain-1',
        globalExperimentRevisionId: 'global-rev-4',
        domainRevisionId: 'domain-rev-7',
    });
    const updated = updateWorkflowResultViewSearch(search, { model: 'protenix', scope: 'this-job' });
    const params = new URLSearchParams(updated);
    assert.equal(params.get('global_experiment_revision_id'), 'global-rev-4');
    assert.equal(params.get('domain_revision_id'), 'domain-rev-7');
});
