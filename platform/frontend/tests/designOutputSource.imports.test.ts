import assert from 'node:assert/strict';
import test from 'node:test';

import {
    inferDesignAnalysisLens,
    getOutputSourceLabel,
    inferDesignOutputSource,
    inferJobOutputSource,
} from '../src/components/designOutputSource.js';

test('classifies imported boltz2 rows as imported data instead of validation', () => {
    const importedDesign = {
        name: 'external_import_001',
        stage_family: 'validation',
        stage_mode: 'bundle_import',
        artifact_class: 'imported_structure',
        is_imported: true,
        import_source: 'external',
        import_method: 'boltz2',
        import_label: 'Imported • Boltz2',
        confidence_metrics: {
            boltz2_ptm: 0.66,
        },
        provenance: {
            dataset_name: 'Selected submissions',
        },
    };

    assert.equal(inferDesignOutputSource(importedDesign), 'imported');
    assert.equal(getOutputSourceLabel(importedDesign), 'Imported');
});

test('defaults external-import jobs to the imported output source', () => {
    const importedJob = {
        name: 'Selected submissions import',
        mode: 'external_import',
        stage_family: 'validation',
        stage_mode: 'bundle_import',
        current_stage: 'bundle_import',
        params: {
            import_type: 'jsonl_bundle',
        },
        selection_source_type: 'saved_dataset',
        selection_dataset_name: 'Selected submissions',
        provenance: {
            import_source: 'external',
        },
    };

    assert.equal(inferJobOutputSource(importedJob), 'imported');
});

test('prefers backend import flags over validation-style heuristics for imported rows', () => {
    const backendTaggedImport = {
        name: 'external_validated_001',
        stage_family: 'validation',
        stage_mode: 'post_structure_validation',
        artifact_class: 'validated_complex',
        is_imported: true,
        import_source: 'external',
        import_method: 'boltz2',
        import_label: 'Imported • Boltz2',
        confidence_metrics: {
            ranking_score: 0.91,
        },
        provenance: {
            source: 'workflow',
        },
    };

    assert.equal(inferDesignOutputSource(backendTaggedImport), 'imported');
    assert.equal(inferDesignAnalysisLens(backendTaggedImport), null);
    assert.equal(getOutputSourceLabel(backendTaggedImport), 'Imported');
});

test('keeps native validation outputs tagged as validation', () => {
    const validationDesign = {
        name: 'variant_001_model_1',
        stage_family: 'validation',
        stage_mode: 'post_structure_validation',
        artifact_class: 'validated_complex',
        confidence_metrics: {
            ranking_score: 0.82,
        },
        provenance: {
            source: 'workflow',
        },
    };

    assert.equal(inferDesignOutputSource(validationDesign), 'validation');
    assert.equal(getOutputSourceLabel(validationDesign), 'Protenix');
});
