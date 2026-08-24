import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
    getProteinLocalRedesignUiState,
    resolveProteinLocalRedesignSourcePath,
    selectResidueKeysFromRanges,
    summarizeChainsFromPdbContent,
} from '../src/components/proteinLocalRedesignUiState';

test('clone hydration resolves exact range selectors against the loaded design chain', () => {
    const residues = [
        { resNum: 1, iCode: '' },
        { resNum: 2, iCode: '' },
        { resNum: 3, iCode: '' },
        { resNum: 10, iCode: 'A' },
    ];
    assert.deepEqual(
        Array.from(selectResidueKeysFromRanges('A', residues, 'A2-3,A10A,B1')).sort(),
        ['A10A', 'A2', 'A3'],
    );
    assert.deepEqual(
        Array.from(selectResidueKeysFromRanges('A', residues, '2-3')).sort(),
        ['A2', 'A3'],
    );
});

test('native clone source hydration accepts input_structure with input_pdb precedence', () => {
    assert.equal(
        resolveProteinLocalRedesignSourcePath({ input_structure: 'inputs/native-clone.pdb' }),
        'inputs/native-clone.pdb',
    );
    assert.equal(
        resolveProteinLocalRedesignSourcePath({
            input_pdb: 'inputs/validated-clone.pdb',
            input_structure: 'inputs/native-clone.pdb',
        }),
        'inputs/validated-clone.pdb',
    );
});

test('classifies polymer chains without letting waters dominate the chain type', () => {
    const pdb = [
        'ATOM      1  CA  ALA A   1      11.000  12.000  13.000  1.00 20.00           C',
        'HETATM    2  O   HOH A 101      14.000  15.000  16.000  1.00 20.00           O',
        'HETATM    3  O   HOH A 102      15.000  16.000  17.000  1.00 20.00           O',
        'HETATM    4  O   HOH A 103      16.000  17.000  18.000  1.00 20.00           O',
        'ATOM      5  P    DA B   1      18.000  19.000  20.000  1.00 20.00           P',
        'HETATM    6  O   HOH B 101      21.000  22.000  23.000  1.00 20.00           O',
        'HETATM    7  O   HOH C 201      24.000  25.000  26.000  1.00 20.00           O',
        'END',
    ].join('\n');

    assert.deepEqual(summarizeChainsFromPdbContent(pdb), [
        { id: 'A', residueCount: 1, type: 'protein' },
        { id: 'B', residueCount: 1, type: 'dna' },
    ]);
});

test('binds modified polymer HETATM records through exact MODRES identity', () => {
    const pdb = [
        'MODRES 1ABC DAL A    2  ALA  D-ALANINE',
        'HETATM    1  CA  DAL A   2      11.000  12.000  13.000  1.00 20.00           C',
        'HETATM    2  CA  ALA Z   1      14.000  15.000  16.000  1.00 20.00           C',
        'HETATM    3  P    DA Y   1      17.000  18.000  19.000  1.00 20.00           P',
        'END',
    ].join('\n');

    assert.deepEqual(summarizeChainsFromPdbContent(pdb), [
        { id: 'A', residueCount: 1, type: 'protein' },
    ]);
});

test('keeps blank and named chain IDs distinct during MODRES binding', () => {
    const blankChainModres = 'MODRES 1ABC DAL      2  ALA  D-ALANINE';
    const namedChainModres = 'MODRES 1ABC DAL A    2  ALA  D-ALANINE';
    const blankChainHetatm = 'HETATM    1  CA  DAL     2      11.000  12.000  13.000                       C';
    const namedChainHetatm = 'HETATM    1  CA  DAL A   2      11.000  12.000  13.000  1.00 20.00           C';

    assert.deepEqual(
        summarizeChainsFromPdbContent([blankChainModres, namedChainHetatm, 'END'].join('\n')),
        [],
    );
    assert.deepEqual(
        summarizeChainsFromPdbContent([namedChainModres, blankChainHetatm, 'END'].join('\n')),
        [],
    );
});

test('native local redesign defaults to a compact sequence-design-disabled lane', () => {
    assert.deepEqual(
        getProteinLocalRedesignUiState(true, 'skip'),
        {
            sequenceDesignEnabled: false,
            showSequenceSampling: false,
            showLegacyOptionalStages: false,
            sequenceSectionLabel: 'Optional Sequence Redesign',
        },
    );
});

test('native local redesign ignores downstream sequence selection', () => {
    assert.deepEqual(
        getProteinLocalRedesignUiState(true, 'fampnn'),
        {
            sequenceDesignEnabled: false,
            showSequenceSampling: false,
            showLegacyOptionalStages: false,
            sequenceSectionLabel: 'Optional Sequence Redesign',
        },
    );
});

test('legacy local redesign keeps its existing downstream stage controls', () => {
    assert.deepEqual(
        getProteinLocalRedesignUiState(false, 'fampnn'),
        {
            sequenceDesignEnabled: true,
            showSequenceSampling: true,
            showLegacyOptionalStages: true,
            sequenceSectionLabel: 'Sequence Redesign',
        },
    );
});

test('queue stage labels identify each protein local model', () => {
    const source = readFileSync(
        fileURLToPath(new URL('../src/components/JobQueuePanel.tsx', import.meta.url)),
        'utf8',
    );
    assert.match(source, /'runrfd3': 'RFD3'/);
    assert.match(source, /'fampnn': 'FA-MPNN'/);
    assert.match(source, /'esmfold2frompdb': 'ESMFold2'/);
    assert.match(source, /'protenix': 'Protenix'/);
});
