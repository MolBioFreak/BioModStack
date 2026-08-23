import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
    getProteinLocalRedesignUiState,
    resolveProteinLocalRedesignSourcePath,
    selectResidueKeysFromRanges,
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
