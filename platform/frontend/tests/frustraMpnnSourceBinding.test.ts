import assert from 'node:assert/strict';
import test from 'node:test';
import {
    assertFrustraMpnnSourceBinding,
    assertTerminalSourceAuthority,
} from '../src/lib/frustraMpnnViewerAuthority.js';

const original = 'a'.repeat(64);
const normalized = 'b'.repeat(64);
const mapDigest = 'c'.repeat(64);
const wrong = 'd'.repeat(64);
const map = { source_sha256: original, normalized_pdb_sha256: normalized };
const resolution = { source_artifact_sha256: original, normalized_pdb_sha256: normalized, structure_map_sha256: mapDigest };

test('normalized execution input binds to its distinct original source', () => {
    assert.equal(assertFrustraMpnnSourceBinding(normalized, normalized, map, mapDigest, resolution), original);
});
test('original-source result uses the same binding path', () => {
    assert.equal(assertFrustraMpnnSourceBinding(original, normalized, map, mapDigest, resolution), original);
});
test('core source binding does not require optional expanded settings', () => {
    assert.equal(assertFrustraMpnnSourceBinding(normalized, normalized, map, mapDigest, null), original);
});
test('a result outside both bound source identities is rejected', () => {
    assert.throws(() => assertFrustraMpnnSourceBinding(wrong, normalized, map, mapDigest, resolution), /source_hash_conflict/);
});
test('a different normalized structure cannot receive the scores', () => {
    assert.throws(() => assertFrustraMpnnSourceBinding(normalized, wrong, map, mapDigest, resolution), /normalized_structure_hash_conflict/);
});
for (const field of ['source_artifact_sha256', 'normalized_pdb_sha256', 'structure_map_sha256'] as const) {
    test(`conflicting settings ${field} cannot replace the source binding`, () => {
        assert.throws(() => assertFrustraMpnnSourceBinding(normalized, normalized, map, mapDigest, { ...resolution, [field]: wrong }), /source_binding_conflict/);
    });
}
test('absent terminal source is not a core gate; a conflicting present one is rejected', () => {
    assert.doesNotThrow(() => assertTerminalSourceAuthority(null, normalized));
    assert.doesNotThrow(() => assertTerminalSourceAuthority({ sha256: normalized }, normalized));
    assert.throws(() => assertTerminalSourceAuthority({ sha256: original }, normalized), /terminal_source_hash_conflict/);
});
