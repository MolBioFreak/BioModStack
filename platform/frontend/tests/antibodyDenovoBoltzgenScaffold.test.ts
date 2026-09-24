import assert from 'node:assert/strict';
import test from 'node:test';

import {
    deriveBoltzgenScaffoldSelectionUpdate,
    resolveBoltzgenReferencePreviewEnabled,
} from '../src/components/antibodyDenovoBoltzgenScaffold.js';

const SABDAB_FRAMEWORK = {
    id: '1abc_HL_A',
    name: 'Example nanobody scaffold',
    pdbCode: '1abc',
    sequence: 'QVQLVESGGGLVQAGGSLRLSCAASGFTFSSYAMGWFRQAPGKEREFVAAISWSGGSTYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAAKDLSGYYYYWGQGTQVTVSS',
    cdrH3Length: 12,
};

test('keeps scaffold preview disabled by default while still copying the selected sequence into the BoltzGen input', () => {
    const result = deriveBoltzgenScaffoldSelectionUpdate({
        framework: SABDAB_FRAMEWORK,
        viewReferenceStructure: false,
    });

    assert.equal(result.nextFrameworkSequence, SABDAB_FRAMEWORK.sequence);
    assert.equal(result.nextCdrH3Length, '9-15');
    assert.equal(result.shouldOpenReferencePreview, false);
    assert.equal(result.referencePdbUrl, null);
});

test('uses the existing RCSB linkage when the user enables reference-structure viewing', () => {
    const result = deriveBoltzgenScaffoldSelectionUpdate({
        framework: SABDAB_FRAMEWORK,
        viewReferenceStructure: true,
    });

    assert.equal(result.nextFrameworkSequence, SABDAB_FRAMEWORK.sequence);
    assert.equal(result.shouldOpenReferencePreview, true);
    assert.equal(result.referencePdbUrl, 'https://files.rcsb.org/download/1ABC.pdb');
});

test('treats missing saved preview preference as disabled by default', () => {
    assert.equal(resolveBoltzgenReferencePreviewEnabled(undefined), false);
    assert.equal(resolveBoltzgenReferencePreviewEnabled({}), false);
    assert.equal(resolveBoltzgenReferencePreviewEnabled({ boltzgen_view_reference_structure: true }), true);
});


test('binder launcher lists four initial generators and leaves RFD3 in its separate product', async () => {
    const { launcherWorkflowTemplates, launcherExperimentalTemplates } = await import('../src/lib/launcherCatalog.js');
    const binder = launcherWorkflowTemplates.find(template => template.id === 'antibody_denovo')!;
    assert.equal(binder.stages[0].tool, 'BindCraft2 / BoltzGen / PPIFlow / RFantibody');
    assert.doesNotMatch(binder.description, /seeded-only|seeded PPIFlow/);
    assert.match(launcherExperimentalTemplates.find(template => template.id === 'protein_modification_experimental')!.description, /RFD3/);
});


test('initial native PPIFlow modes cannot be silently submitted as legacy partial flow', async () => {
    const { resolveExistingDeNovoGenerator } = await import('../src/components/deNovoGeneratorSelection.js');
    for (const mode of ['protein_binder', 'antibody_binder', 'nanobody_binder']) {
        assert.equal(resolveExistingDeNovoGenerator({ model_id: 'ppiflow', mode, denovo_generator: 'ppiflow' }), null);
    }
    assert.equal(resolveExistingDeNovoGenerator({ mode: 'generator_backbone_refine', denovo_generator: 'ppiflow' }), 'ppiflow');
});
