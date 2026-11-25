import assert from 'node:assert/strict';
import test from 'node:test';

import { DE_NOVO_MODIFICATION_MODE_CARDS, normalizeModificationMode } from '../src/components/proteinModificationModes.ts';
import { deNovoNavigation, deNovoSavedValues } from '../src/components/deNovoWorkflowRoute.ts';

test('De Novo Design exposes Generate, Redesign structure, and Resequence modes', () => {
    assert.deepEqual(
        DE_NOVO_MODIFICATION_MODE_CARDS.map((card) => card.id),
        ['de_novo_design', 'redesign_structure', 'resequence'],
    );

    const deNovo = DE_NOVO_MODIFICATION_MODE_CARDS[0];
    assert.equal(deNovo?.id, 'de_novo_design');
    assert.equal(deNovo?.label, 'Generate');
    assert.match(deNovo?.description ?? '', /native controls/i);

    const workbench = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'redesign_structure');
    assert.equal(workbench?.label, 'Redesign structure');
    assert.match(workbench?.description ?? '', /Mol\*/i);
    assert.match(workbench?.description ?? '', /RFD4-Proteina/i);
});

test('legacy saved draft or navigation carrying rfd3_iteration normalizes to redesign_structure (C3)', () => {
    assert.equal(normalizeModificationMode('rfd3_iteration'), 'redesign_structure');
    assert.equal(normalizeModificationMode('redesign_structure'), 'redesign_structure');
    assert.equal(deNovoNavigation({ modification_mode: 'rfd3_iteration' }).modification_mode, 'redesign_structure');
    assert.equal(deNovoSavedValues('protein_modification_experimental', null, { modification_mode: 'rfd3_iteration' }).modification_mode, 'redesign_structure');
    assert.equal(deNovoSavedValues('protein_local_redesign', null, {}).modification_mode, 'redesign_structure');
});
