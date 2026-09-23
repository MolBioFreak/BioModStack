import assert from 'node:assert/strict';
import test from 'node:test';
import { hydrateInitialStageSelection, resolveExistingDeNovoGenerator } from '../src/components/deNovoGeneratorSelection.js';
import { launcherWorkflowTemplates } from '../src/lib/launcherCatalog.js';

test('launcher retains every existing generator and describes exact supported modality', () => {
    const card = launcherWorkflowTemplates.find(item => item.id === 'antibody_denovo');
    assert.equal(card?.name, 'De Novo Binder Design');
    assert.match(card?.stages[0].tool ?? '', /RFantibody.*BoltzGen VHH.*seeded PPIFlow/);
    assert.match(card?.description ?? '', /antibody-specific/);
});

test('saved native generator mode restores its own route and unknown selectors refuse substitution', () => {
    assert.equal(resolveExistingDeNovoGenerator({ mode: 'nanobody_binder' }), 'boltzgen');
    assert.equal(resolveExistingDeNovoGenerator({ mode: 'generator_backbone_refine' }), 'ppiflow');
    assert.equal(resolveExistingDeNovoGenerator({ denovo_generator: 'rfantibody' }), 'rfantibody');
    assert.equal(resolveExistingDeNovoGenerator({}), 'rfantibody'); // legacy antibody request
    assert.equal(resolveExistingDeNovoGenerator({ denovo_generator: 'bindcraft2', mode: 'nanobody_binder' }), null);
    assert.equal(resolveExistingDeNovoGenerator({ generator: 'unknown' }), null);
    assert.equal(resolveExistingDeNovoGenerator({ mode: 'unknown' }), null);
});

test('explicit false overrides legacy true and saved standalone stage requests remain visible', () => {
    assert.deepEqual(hydrateInitialStageSelection({
        initial_orchestration_sequence_design: false,
        seq_design_fampnn: true,
        initial_orchestration_validation: true,
        run_structure_validation: false,
        initial_orchestration_qc: false,
        run_frustrampnn: true,
        initial_orchestration_ppiflow: true,
    }), { sequence_design: false, ppiflow: true, validation: true, qc: false });
    assert.deepEqual(hydrateInitialStageSelection({}), {
        sequence_design: false, ppiflow: false, validation: false, qc: false,
    });
});
