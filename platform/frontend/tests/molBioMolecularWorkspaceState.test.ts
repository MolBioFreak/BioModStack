import assert from 'node:assert/strict';
import test from 'node:test';

import * as molecularWorkspaceState from '../src/components/MolBioToolkit/utils/molecularWorkspaceState.js';

import {
    deserializeMolecularWorkspaceIdentity,
    molecularWorkspaceId,
    resolveMolecularOpenRequest,
    runDirtyWorkspaceTransition,
    serializeMolecularWorkspaceIdentity,
    upsertStableMolecularWorkspace,
    type PersistedMolecularWorkspace,
} from '../src/components/MolBioToolkit/utils/molecularWorkspaceState.js';

test('exact molecular authority requires approval unless an exact lens is already active', () => {
    const resolveAuthority = (molecularWorkspaceState as Record<string, unknown>).resolveExactMolecularAuthority;
    assert.equal(typeof resolveAuthority, 'function');
    if (typeof resolveAuthority !== 'function') return;

    assert.equal(resolveAuthority(false, true, false), false);
    assert.equal(resolveAuthority(true, true, false), true);
    assert.equal(resolveAuthority(false, false, true), true);
});

test('molecular query routing distinguishes current, exact, and revision-only recovery', () => {
    assert.deepEqual(resolveMolecularOpenRequest(new URLSearchParams('molbio_sequence_id=seq-1')), {
        kind: 'current',
        sequenceId: 'seq-1',
    });
    assert.deepEqual(resolveMolecularOpenRequest(new URLSearchParams('molbio_sequence_id=seq-1&molbio_revision_id=rev-2')), {
        kind: 'exact',
        sequenceId: 'seq-1',
        revisionId: 'rev-2',
    });
    assert.deepEqual(resolveMolecularOpenRequest(new URLSearchParams('molbio_revision_id=rev-2')), {
        kind: 'invalid',
        reason: 'revision_without_sequence',
    });
    assert.deepEqual(resolveMolecularOpenRequest(new URLSearchParams('sequence_id=legacy&revision_id=legacy-rev')), {
        kind: 'none',
    });
});

test('one stable workspace identity is reused when a sequence changes lens', () => {
    const current = {
        id: molecularWorkspaceId('seq-1'),
        sequenceId: 'seq-1',
        lens: 'current' as const,
    };
    const historical = {
        id: molecularWorkspaceId('seq-1'),
        sequenceId: 'seq-1',
        lens: 'historical' as const,
        exactRevisionId: 'rev-2',
    };

    assert.equal(current.id, historical.id);
    assert.deepEqual(upsertStableMolecularWorkspace([current], historical), [historical]);
});

test('dirty transitions continue only after discard or a successful save', async () => {
    let saves = 0;
    assert.equal(await runDirtyWorkspaceTransition(true, 'stay', async () => true), false);
    assert.equal(await runDirtyWorkspaceTransition(true, 'discard', async () => true), true);
    assert.equal(await runDirtyWorkspaceTransition(true, 'save', async () => {
        saves += 1;
        return false;
    }), false);
    assert.equal(await runDirtyWorkspaceTransition(true, 'save', async () => {
        saves += 1;
        return true;
    }), true);
    assert.equal(saves, 2);
});

test('identity restore strips sequence payload and unsaved fields before clean serialization', () => {
    const identity: PersistedMolecularWorkspace = {
        id: 'molecular_sequence_seq-1', sequenceId: 'seq-1', lens: 'historical',
        exactRevisionId: 'rev-2',
        viewContext: { activePanel: 'history', viewMode: 'both', displayStrand: 'minus' },
    };
    // Exercise the receiving parser, not a test-side projection of clean input.
    // The serializer itself is not a sanitizer, nor is this a mounted caller test.
    const restored = deserializeMolecularWorkspaceIdentity(JSON.stringify({
        version: 1, activeWorkspaceId: identity.id,
        tabs: [{ ...identity, sequenceData: { sequence: 'ACTG' },
            historyState: { unsaved: 'ACTG' }, dirty: true,
            viewContext: { ...identity.viewContext, unsaved: 'ACTG' } }],
    }));
    assert.deepEqual(restored, {
        tabs: [identity], activeWorkspaceId: 'molecular_sequence_seq-1',
        invalidCount: 0, notice: null,
    });
    const serialized = serializeMolecularWorkspaceIdentity(restored.tabs, restored.activeWorkspaceId);
    assert.doesNotMatch(serialized, /ACTG|sequenceData|historyState|dirty|unsaved/i);
    assert.deepEqual(JSON.parse(serialized), {
        version: 1, activeWorkspaceId: 'molecular_sequence_seq-1', tabs: [identity],
    });
});
test('restore deduplicates invalid identities and reports one notice', () => {
    const validId = molecularWorkspaceId('seq-1');
    const restored = deserializeMolecularWorkspaceIdentity(JSON.stringify({
        version: 1,
        activeWorkspaceId: 'missing',
        tabs: [
            { id: validId, sequenceId: 'seq-1', lens: 'current', viewContext: { activePanel: 'view', viewMode: 'linear', displayStrand: 'plus' } },
            { id: validId, sequenceId: 'seq-1', lens: 'historical', exactRevisionId: 'rev-duplicate', viewContext: {} },
            { id: molecularWorkspaceId('seq-2'), sequenceId: 'seq-2', lens: 'historical', viewContext: {} },
            { id: 'forged', sequenceId: 'seq-3', lens: 'current', viewContext: {} },
        ],
    }));

    assert.equal(restored.tabs.length, 1);
    assert.equal(restored.activeWorkspaceId, validId);
    assert.equal(restored.invalidCount, 4);
    assert.equal(restored.notice, 'Some saved molecular workspaces could not be restored and were skipped.');
});
