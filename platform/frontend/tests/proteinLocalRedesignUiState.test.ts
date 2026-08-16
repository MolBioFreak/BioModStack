import assert from 'node:assert/strict';
import test from 'node:test';

import { getProteinLocalRedesignUiState } from '../src/components/proteinLocalRedesignUiState';

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
