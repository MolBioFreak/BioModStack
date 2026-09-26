import assert from 'node:assert/strict';
import test from 'node:test';
import { materializeLaProteinaMotifSelection as build } from '../src/lib/laProteinaMotifSelection';
const atom = (n: number, chain = 'A', insertion = '') => `ATOM      1  CA  ALA ${chain}${String(n).padStart(4)}${insertion || ' '}      7.000   2.000   3.000  1.00 20.00           C  `;
const pdb = [atom(1), atom(2), atom(3), atom(7, 'B'), 'END'].join('\n');
test('materializes only selected coordinates in explicit native order without inventing atoms', () => {
    const result = build(pdb, new Set(['A1', 'A3', 'B7']), '10/B7/2/A3/4/A1/10');
    assert.equal(result.contig, '10/B7/2/A3/4/A1/10');
    assert.equal(result.pdb, [atom(7, 'B'), atom(3), atom(1), 'END', ''].join('\n'));
});
test('does not silently infer missing selections, chain grouping or insertion-code mapping', () => {
    assert.throws(() => build(pdb, new Set(['A1', 'A3']), 'A1'), /every selected/);
    assert.throws(() => build(pdb, new Set(['A1', 'B7', 'A3']), 'A1/B7/A3'), /groups each chain/);
    assert.throws(() => build(atom(1, 'A', 'B'), new Set(['A1B']), 'A1B'), /insertion codes/);
    assert.throws(() => build(pdb, new Set(['A1']), 'A1/A1'), /more than once/);
});
test('refuses unsupported native syntax only for the explicit conversion action', () => {
    assert.throws(() => build(pdb, new Set(['A1']), ' A1 '), /explicit ordered/);
    assert.throws(() => build(pdb, new Set(['A1', 'A2']), 'A2-1'), /ascending/);
    assert.throws(() => build(pdb, new Set(['A1']), '5-2/A1'), /ascending/);
});
