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

