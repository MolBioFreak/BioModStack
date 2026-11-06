import assert from 'node:assert/strict'
import { test } from 'node:test'
// Isolated native-renderer code intentionally uses its React 18 workspace.
// @ts-ignore the renderer's JS module has no public TypeScript declaration
import { displaySequence, projectHighlights } from '../alignment-viewers/av2/projection.mjs'

test('AV2 display folds ASCII case only without changing retained input or coordinates', () => {
  const rows = [{aligned: 'acgT-.nß'}]
  const projected = rows.map(row => ({...row, aligned: displaySequence(row.aligned)}))
  assert.equal(rows[0].aligned, 'acgT-.nß')
  assert.equal(projected[0].aligned, 'ACGT-.Nß')
  assert.equal(projected[0].aligned.length, rows[0].aligned.length)
})

test('AV2 no selection does not activate upstream dim-everything highlight mode', () => {
  assert.deepEqual(projectHighlights([{aligned: 'ACGT'}]), {omitted: 0, highlightMatches: undefined})
})

test('AV2 uses displayed sequence keys and unchanged columns for marks and selections', () => {
  const rows = ['ac-gt', 'actgt'].map(aligned => ({aligned: displaySequence(aligned)}))
  const result = projectHighlights(rows, [{index: 0, start: 1, end: 2}], {index: 1, column: 3})
  assert.equal(result.omitted, 0)
  assert.deepEqual(result.highlightMatches.posidxsMatchedPerSequence, {'AC-GT': [1, 2], ACTGT: [3]})
  assert.notEqual(result.highlightMatches.searchString, projectHighlights(rows, [], {index: 1, column: 2}).highlightMatches.searchString)
})

test('case-only duplicate native sequences still omit occurrence-specific highlights', () => {
  const rows = ['acgt', 'ACGT'].map(aligned => ({aligned: displaySequence(aligned)}))
  assert.deepEqual(projectHighlights(rows, [], {index: 0, column: 2}), {omitted: 1, highlightMatches: undefined})
  assert.deepEqual(projectHighlights(rows, [{start: 1, end: 1}]).highlightMatches.posidxsMatchedPerSequence, {ACGT: [1, 1]})
})
