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

test('RFD3-only editing and validated region redesign have distinct operator identities', () => {
    const localRedesign = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'rfd3_local_redesign');
    const regionRedesign = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'region_redesign');
    const template = source('components/ProteinLocalRedesignTemplate.tsx');

    assert.equal(localRedesign?.label, 'RFD3 Native Local Edit');
    assert.match(localRedesign?.description ?? '', /RFD3 only/i);
    assert.match(localRedesign?.description ?? '', /preserves the source sequence/i);
    assert.equal(regionRedesign?.label, 'Validated Region Redesign');
    assert.match(regionRedesign?.description ?? '', /FA-MPNN/i);
    assert.match(regionRedesign?.description ?? '', /ESMFold2 and Protenix V2/i);

    assert.match(template, /isNativeLocalRedesign \? 'RFD3 Native Local Edit' : 'Validated Region Redesign'/);
    assert.match(template, /isNativeLocalRedesign \? 'Launch RFD3 Native Edit' : 'Launch Validated Region Redesign'/);
});

test('the RFD3 workflow uses the available wide viewport instead of stacking its main sections', () => {
    const submission = source('components/JobSubmission.tsx');
    const template = source('components/ProteinLocalRedesignTemplate.tsx');

    assert.match(submission, /selectedTemplateId === 'protein_modification_experimental'\s*\? 'max-w-none'/);
    assert.match(template, /data-bms-rfd3-layout="wide"/);
    assert.match(template, /2xl:grid-cols-\[minmax\(0,1\.1fr\)_minmax\(0,1fr\)_minmax\(20rem,0\.75fr\)\]/);
    assert.match(template, /2xl:contents/);
});
