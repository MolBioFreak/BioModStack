import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import { DE_NOVO_MODIFICATION_MODE_CARDS } from '../src/components/proteinModificationModes';

const source = (relativePath: string): string => readFileSync(join(process.cwd(), 'src', relativePath), 'utf8');

test('De Novo Design exposes the complete supported native request controls', () => {
    const template = source('components/ProteinModificationTemplate.tsx');

    for (const field of [
        'laproteina_motif_task_name',
        'laproteina_segment_order',
        'laproteina_atom_selection_mode',
        'laproteina_motif_min_length',
        'laproteina_motif_max_length',
        'disco_num_inference_seeds',
        'disco_seeds',
    ]) {
        assert.match(template, new RegExp(field));
    }

    assert.match(template, /data-bms-de-novo-form="complete"/);
    assert.match(template, /La-Proteina motif scaffolding requires an upstream motif task or a motif PDB with a contig string\./);
    assert.match(template, /DISCO ligand-conditioned design requires a ligand SDF\./);
    assert.match(template, /DISCO DNA\/RNA-conditioned design requires a nucleic-acid sequence\./);
    assert.doesNotMatch(template, /<option value="medium">Medium<\/option>/);
});

test('RFD3 native and validated execution depths share one operator workbench', () => {
    const workbench = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'rfd3_iteration');
    const template = source('components/ProteinLocalRedesignTemplate.tsx');

    assert.equal(workbench?.label, 'RFD3 Iteration Workbench');
    assert.match(workbench?.description ?? '', /native RFD3 output or downstream sequence design and validation/i);
    assert.match(template, /Native RFD3 only/);
    assert.match(template, /Continue through sequence design and validation/);
    assert.match(template, /isNativeLocalRedesign \? 'Launch Native RFD3' : 'Launch RFD3 \+ Sequence \+ Validation'/);
});

test('the RFD3 workflow uses the available wide viewport instead of stacking its main sections', () => {
    const submission = source('components/JobSubmission.tsx');
    const template = source('components/ProteinLocalRedesignTemplate.tsx');

    assert.match(submission, /selectedTemplateId === 'protein_modification_experimental'\s*\? 'max-w-none'/);
    assert.match(template, /data-bms-rfd3-layout="wide"/);
    assert.match(template, /2xl:grid-cols-\[minmax\(0,1\.1fr\)_minmax\(0,1fr\)_minmax\(20rem,0\.75fr\)\]/);
    assert.match(template, /2xl:contents/);
});
