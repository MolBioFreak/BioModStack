import { describe, expect, it } from 'vitest'
import { anchorAt, correspondingColumns, type AlignmentDocument } from '../../src/lib/alignmentCoordinates'

const doc: AlignmentDocument = { resultId: 'result-1', rows: [
  { rowId: 'row-1', label: 'same', aligned: 'A-C.G', sourceMemberId: 'member-1', sourceOffset: 10 },
  { rowId: 'row-2', label: 'same', aligned: 'AC--G' },
] }

describe('alignment coordinates', () => {
  it('distinguishes gaps from residues without inferring offsets', () => {
    expect(anchorAt(doc, 'row-1', 1)).toEqual({ resultId: 'result-1', rowId: 'row-1', column: 1, residue: null, sourceMemberId: 'member-1' })
    expect(anchorAt(doc, 'row-1', 4)).toEqual({ resultId: 'result-1', rowId: 'row-1', column: 4, residue: 2, sourceMemberId: 'member-1' })
    expect(anchorAt(doc, 'row-2', 0)?.sourcePosition).toBeUndefined()
    expect(anchorAt(doc, 'row-1', 5)).toBeUndefined()
  })
  it('does not infer correspondence from a duplicate label', () => {
    expect(correspondingColumns(doc, 'member-1', 11)).toEqual([])
    expect(correspondingColumns(doc, 'same', 0)).toEqual([])
  })
  it('preserves explicit noncontiguous positions and occurrence', () => {
    const mapped: AlignmentDocument = {resultId: 'r', rows: [{rowId: 'a', label: 'same', aligned: 'A-CG', sourceMemberId: 'm', occurrenceId: 'o', sourcePositions: [8, null, 10, 13]}]}
    expect(anchorAt(mapped, 'a', 2)).toMatchObject({sourcePosition: 10, sourceMemberId: 'm', occurrenceId: 'o'})
    expect(anchorAt(mapped, 'a', 1)?.sourcePosition).toBeUndefined()
    expect(correspondingColumns(mapped, 'm', 13).map(x => x.column)).toEqual([3])
  })
  it('keeps duplicate, invalid and missing maps unknown while retaining column anchors', () => {
    const mapped: AlignmentDocument = {resultId: 'r', rows: [
      {rowId: 'a', label: 'x', aligned: 'A-CG', sourceMemberId: 'm', sourcePositions: [9, 9, 9, 0]},
      {rowId: 'b', label: 'x', aligned: 'A-CG', sourceMemberId: 'm', sourcePositions: [3, null]},
      {rowId: 'c', label: 'x', aligned: 'A-CG', sourceMemberId: 'm', sourcePositions: [-1, null, 1.5, 4]},
      {rowId: 'd', label: 'x', aligned: 'A-CG', sourceMemberId: 'm', sourcePositions: [8, null, 5, 2]},
    ]}
    expect(anchorAt(mapped, 'a', 0)).toMatchObject({column: 0, residue: 0})
    expect(anchorAt(mapped, 'a', 0)?.sourcePosition).toBeUndefined()
    expect(anchorAt(mapped, 'a', 1)?.sourcePosition).toBeUndefined()
    expect(anchorAt(mapped, 'b', 0)?.sourcePosition).toBeUndefined()
    expect(anchorAt(mapped, 'c', 0)?.sourcePosition).toBeUndefined()
    expect(anchorAt(mapped, 'c', 2)?.sourcePosition).toBeUndefined()
    expect(correspondingColumns(mapped, 'm', 9)).toEqual([])
    expect(correspondingColumns(mapped, 'm', 5).map(x => [x.rowId, x.column])).toEqual([['d', 2]])
  })
})
