import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    applyModelIntegrationChoice,
    applyModelIntegrationDefault,
    createModelIntegrationSelection,
    getModelIntegrationDetails,
    type ModelIntegrationPresentation,
} from '../src/components/modelIntegrationControlState.js';

const integration: ModelIntegrationPresentation = {
    model_name: 'FrustraMPNN',
    checkpoint_label: 'MegaScale-trained checkpoint',
    model_summary: 'Maps residue-level energetic frustration.',
    workflows: {
        structure_prediction: {
            enabled_summary: 'Analyze each predicted structure for interpretation and mutation planning.',
        },
    },
};

test('model integration details stay hidden while disabled and show model, checkpoint, and workflow context while enabled', () => {
    assert.equal(getModelIntegrationDetails(false, integration, 'structure_prediction'), null);
    assert.deepEqual(getModelIntegrationDetails(true, integration, 'structure_prediction'), {
        modelName: 'FrustraMPNN',
        checkpointLabel: 'MegaScale-trained checkpoint',
        summary: 'Analyze each predicted structure for interpretation and mutation planning.',
    });

    const controlSource = readFileSync('src/components/ModelIntegrationControl.tsx', 'utf8');
    assert.match(controlSource, /integration\?\.operator_label \|\| fallbackLabel/);
    assert.match(controlSource, /const details = getModelIntegrationDetails/);
    assert.match(controlSource, /\{details && \(/);
});

test('a delayed configured default is applied once and cannot overwrite an explicit saved or operator choice', () => {
    const newSelection = createModelIntegrationSelection(undefined, true);
    const operatorChoice = applyModelIntegrationChoice(newSelection, false);
    const afterDelayedDefault = applyModelIntegrationDefault(operatorChoice, true);
    assert.equal(afterDelayedDefault.value, false);
    assert.equal(afterDelayedDefault.hasExplicitSelection, true);

    const configuredSelection = applyModelIntegrationDefault(
        createModelIntegrationSelection(undefined, true),
        false,
    );
    assert.equal(configuredSelection.value, false);
    assert.strictEqual(applyModelIntegrationDefault(configuredSelection, true), configuredSelection);

    const savedSelection = createModelIntegrationSelection(false, true);
    assert.strictEqual(applyModelIntegrationDefault(savedSelection, true), savedSelection);

    const templateSource = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');
    assert.match(templateSource, /applyModelIntegrationDefault/);
    assert.match(templateSource, /applyModelIntegrationChoice/);

    const antibodySource = readFileSync('src/components/AntibodyDenovoTemplate.tsx', 'utf8');
    assert.match(antibodySource, /workflowId="antibody_design"/);
    assert.match(antibodySource, /fallbackLabel="Frustration analysis"/);
    assert.doesNotMatch(antibodySource, /Optional QC pass/);

    const resultsSource = readFileSync('src/components/ResultsViewer.tsx', 'utf8');
    assert.match(resultsSource, /workflowId="antibody_design"/);
    assert.match(resultsSource, /Frustration analysis hotspots/);
});
