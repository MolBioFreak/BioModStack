import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { parseMmCIF } from '../src/utils/pdbUtils';

const source = (relativePath: string): string => readFileSync(join(process.cwd(), 'src', relativePath), 'utf8');

test('mmCIF source conversion preserves protein, ssDNA, and ligand records as PDB lines', () => {
    const atomHeaders = [
        'group_PDB', 'type_symbol', 'label_atom_id', 'label_alt_id', 'label_comp_id',
        'label_asym_id', 'label_entity_id', 'label_seq_id', 'pdbx_PDB_ins_code',
        'auth_seq_id', 'auth_comp_id', 'auth_asym_id', 'auth_atom_id', 'B_iso_or_equiv',
        'Cartn_x', 'Cartn_y', 'Cartn_z', 'pdbx_PDB_model_num', 'id', 'occupancy',
    ];
    const cif = [
        'data_rfd3_complex',
        'loop_',
        ...atomHeaders.map((header) => `_atom_site.${header}`),
        'ATOM C CA . ALA A 1 1 . 1 ALA A CA 90.0 1.0 2.0 3.0 1 1 1.0',
        'ATOM P P . DA B 2 1 . 1 DA B P 80.0 4.0 5.0 6.0 1 2 1.0',
        'HETATM C C1 . LIG C 3 101 . 101 LIG C C1 70.0 7.0 8.0 9.0 1 3 1.0',
        '#',
    ].join('\n');

    const parsed = parseMmCIF(cif);
    const content = parsed.models[0]?.content || '';
    const bodyLines = content.split(/\r?\n/).filter((line) => line.startsWith('ATOM') || line.startsWith('HETATM'));

    assert.equal(bodyLines.length, 3);
    assert.match(content, /^ATOM.* DA B/m);
    assert.match(content, /^HETATM.*LIG C/m);
});

