import assert from 'node:assert/strict';
import test from 'node:test';

import { classifyShapeStructureFiles } from '../src/lib/shapeResultPhases.js';

const file = (path: string) => ({
    name: path.split('/').at(-1)!.replace(/\.(pdb|cif)$/u, ''),
    filename: path.split('/').at(-1)!,
    path,
    type: path.endsWith('.pdb') ? 'pdb' as const : 'cif' as const,
    size_bytes: 100,
});

test('Shape result phases expose canonical outputs and suppress duplicate internal copies', () => {
    const files = [
        file('bms_results/job/run/shape_sequences/fampnn/input/raw.pdb'),
        file('bms_results/job/run/shape_sequences/fampnn/runtime/samples/fa_sample.pdb'),
        file('bms_results/job/run/shape_sequences/fampnn/source_backbone.pdb'),
        file('bms_results/job/run/shape_sequences/proteinmpnn/source_backbone.pdb'),
        file('bms_results/job/pdb_files/esmfold2_results/item__fampnn__000_000.cif'),
        file('bms_results/job/final/esmfold2/item__fampnn__000/esmfold2_results/item__fampnn__000_000.cif'),
        file('bms_results/job/results/shape_candidates/item__fampnn__000__abc.cif'),
        file('bms_results/job/results/shape_candidates/item__fampnn__000__abc.source.pdb'),
        file('bms_results/job/results/shape_candidates/item__proteinmpnn__001__def.cif'),
        file('bms_results/job/results/shape_candidates/item__proteinmpnn__001__def.source.pdb'),
    ];

    const phases = classifyShapeStructureFiles(files);
    assert.deepEqual(phases.map((phase) => phase.key), [
        'backbone_generation', 'sequence_design', 'structure_validation',
    ]);
    assert.deepEqual(phases.map((phase) => phase.artifacts.length), [1, 1, 2]);
    assert.equal(phases[0].artifacts[0].engine, 'RFD3');
    assert.equal(phases[0].artifacts[0].role, 'Generated backbone');
    assert.equal(phases[1].artifacts[0].engine, 'FAMPNN');
    assert.equal(phases[1].artifacts[0].role, 'Sequence-designed structure');
    assert.deepEqual(phases[2].artifacts.map((artifact) => artifact.engine), ['FAMPNN → ESMFold2', 'ProteinMPNN → ESMFold2']);
    assert.ok(phases.flatMap((phase) => phase.artifacts).every((artifact) => !artifact.path.includes('/final/')));
    assert.ok(phases.flatMap((phase) => phase.artifacts).every((artifact) => !artifact.path.includes('/pdb_files/')));
});
