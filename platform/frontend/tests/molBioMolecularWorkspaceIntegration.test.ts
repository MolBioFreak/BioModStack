import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/MolBioToolkitV2.tsx'), 'utf8');

test('MolBioToolkit uses the molecular request classifier without aliasing molecular IDs into legacy openers', () => {
    assert.match(source, /resolveMolecularOpenRequest\(queryParams\)/);
    assert.match(source, /queryParams\.get\('sequence_id'\)\?\.trim\(\) \|\| null/);
    assert.match(source, /queryParams\.get\('revision_id'\)\?\.trim\(\) \|\| null/);
    assert.doesNotMatch(source, /queryParams\.get\('sequence_id'\)\?\.trim\(\) \|\| requestedMolecularSequenceId/);
    assert.doesNotMatch(source, /queryParams\.get\('revision_id'\)\?\.trim\(\) \|\| requestedMolecularRevisionId/);
});

test('MolBioToolkit wires stable identity-only restore and persistence before direct URL opening', () => {
    assert.match(source, /MOLECULAR_WORKSPACE_STORAGE_KEY/);
    assert.match(source, /deserializeMolecularWorkspaceIdentity/);
    assert.match(source, /serializeMolecularWorkspaceIdentity/);
    assert.match(source, /molecularWorkspaceId\(/);
    assert.match(source, /fetchMolecularRevision\(persisted\.sequenceId, persisted\.exactRevisionId/);
    assert.doesNotMatch(source, /if \(molecularOpenRequest\.kind !== 'none'\)/);
    assert.match(source, /if \(!workspaceRestoreComplete \|\| !molecularOpenRequestApproved\) return;/);
    assert.match(source, /setApprovedMolecularOpenRequestKey\(molecularOpenRequestKey\)/);
    assert.match(source, /assertExactMolecularRevisionIdentity\(/);
});

test('MolBioToolkit guards dirty close, switch, route leave, and browser leave', () => {
    assert.match(source, /beforeunload/);
    assert.match(source, /Save and continue/);
    assert.match(source, /Discard and continue/);
    assert.match(source, />\s*Stay\s*</);
    assert.match(source, /requestWorkspaceTransition/);
    assert.match(source, /handleGuardedRouteClick/);
});

test('save targets the dirty workspace and clears only that tab after success', () => {
    assert.match(source, /const saveWorkspace = useCallback\(async \(workspaceId: string\): Promise<boolean>/);
    assert.match(source, /workspaceId: options\?\.workspaceId \?\? activeWorkspaceId/);
    assert.match(source, /saveWorkspace\(pendingWorkspaceTransition\.workspaceId\)/);
    assert.match(source, /tab\.id === workspaceId[\s\S]*dirty: false/);
    assert.match(source, /if \(!savedRecord\) return false;/);
});

test('closing the final saved tab creates a neutral workspace identity', () => {
    assert.match(source, /const emptyWorkspaceId = nextWorkspaceId\(\)/);
    assert.match(source, /id: emptyWorkspaceId/);
    assert.match(source, /setActiveWorkspaceId\(emptyWorkspaceId\)/);
});

test('tab activation and close keep the molecular URL bound to the active lens', () => {
    assert.match(source, /const setMolecularQueryForWorkspace = useCallback/);
    assert.match(source, /setMolecularQueryForWorkspace\(workspace\)/);
    assert.match(source, /setMolecularQueryForWorkspace\(nextWorkspace\)/);
    assert.match(source, /setMolecularQueryForWorkspace\(null\)/);
    assert.match(source, /approveMolecularOpenRequest\(\{ kind: 'current', sequenceId: id \}\)/);
});

test('discarding a dirty same-sequence reopen forces a fresh server-backed load', () => {
    assert.match(source, /loadSequence\(id, \{ forceReload: true \}\)/);
    assert.match(source, /existing && !options\?\.forceReload/);
});
