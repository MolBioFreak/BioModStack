import { describe, expect, it } from 'vitest'
import { anchorAt, correspondingColumns, type AlignmentDocument } from '../../src/lib/alignmentCoordinates'

const doc: AlignmentDocument = { resultId: 'result-1', rows: [
  { rowId: 'row-1', label: 'same', aligned: 'A-C.G', sourceMemberId: 'member-1', sourceOffset: 10 },
  { rowId: 'row-2', label: 'same', aligned: 'AC--G' },
] }

describe('alignment coordinates', () => {
  it('distinguishes gaps from residues and uses source offsets', () => {
    expect(anchorAt(doc, 'row-1', 1)).toEqual({ resultId: 'result-1', rowId: 'row-1', column: 1, residue: null, sourceMemberId: 'member-1' })
    expect(anchorAt(doc, 'row-1', 4)).toEqual({ resultId: 'result-1', rowId: 'row-1', column: 4, residue: 2, sourceMemberId: 'member-1', sourcePosition: 12 })
    expect(anchorAt(doc, 'row-2', 0)?.sourcePosition).toBeUndefined()
    expect(anchorAt(doc, 'row-1', 5)).toBeUndefined()
  })
  it('does not infer correspondence from a duplicate label', () => {
    expect(correspondingColumns(doc, 'member-1', 11).map(a => [a.rowId, a.column])).toEqual([['row-1', 2]])
    expect(correspondingColumns(doc, 'same', 0)).toEqual([])
  })
  it('preserves explicit noncontiguous positions and occurrence', () => {
    const mapped: AlignmentDocument = {resultId: 'r', rows: [{rowId: 'a', label: 'same', aligned: 'A-CG', sourceMemberId: 'm', occurrenceId: 'o', sourcePositions: [8, null, 10, 13]}]}
    expect(anchorAt(mapped, 'a', 2)).toMatchObject({sourcePosition: 10, sourceMemberId: 'm', occurrenceId: 'o'})
    expect(anchorAt(mapped, 'a', 1)?.sourcePosition).toBeUndefined()
    expect(correspondingColumns(mapped, 'm', 13).map(x => x.column)).toEqual([3])
  })
})
