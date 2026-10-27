// Coordinates are zero-based; gaps have no residue coordinate.
export interface AlignmentRow {
  rowId: string
  label: string
  aligned: string
  // Unknown is deliberately distinct from a matching label or sequence.
  sourceMemberId?: string
  occurrenceId?: string
  sourcePositions?: (number | null)[]
  sourceOffset?: number
}

export interface AlignmentDocument {
  resultId: string
  rows: AlignmentRow[]
}

export interface ResidueAnchor {
  resultId: string
  rowId: string
  column: number
  residue: number | null
  sourceMemberId?: string
  occurrenceId?: string
  sourcePosition?: number
}

// API maps are projected; defend callers that construct documents directly.
function positionAt(row: AlignmentRow, column: number): number | undefined {
  const positions = row.sourcePositions
  if (!positions || positions.length !== row.aligned.length || '-.'.includes(row.aligned[column])) return undefined
  const position = positions[column]
  if (typeof position !== 'number' || !Number.isSafeInteger(position) || position < 0) return undefined
  let count = 0
  for (let i = 0; i < positions.length; i++) {
    if (!'-.'.includes(row.aligned[i]) && positions[i] === position) count++
  }
  return count === 1 ? position : undefined
}

export function anchorAt(result: AlignmentDocument, rowId: string, column: number): ResidueAnchor | undefined {
  const row = result.rows.find(item => item.rowId === rowId)
  if (!row || !Number.isInteger(column) || column < 0 || column >= row.aligned.length) return undefined
  const symbol = row.aligned[column]
  const residue = symbol === '-' || symbol === '.' ? null : [...row.aligned.slice(0, column)].filter(c => c !== '-' && c !== '.').length
  const position = positionAt(row, column)
  return {
    resultId: result.resultId, rowId, column, residue,
    ...(row.sourceMemberId !== undefined ? { sourceMemberId: row.sourceMemberId } : {}),
    ...(row.occurrenceId !== undefined ? { occurrenceId: row.occurrenceId } : {}),
    ...(residue !== null && position != null ? { sourcePosition: position } : {}),
  }
}

// Match only an explicit source member and position; labels/sequence are not identity.
export function correspondingColumns(result: AlignmentDocument, sourceMemberId: string, sourcePosition: number): ResidueAnchor[] {
  const matches: ResidueAnchor[] = []
  for (const row of result.rows) {
    if (row.sourceMemberId !== sourceMemberId) continue
    for (let column = 0; column < row.aligned.length; column++) {
      if (row.aligned[column] === '-' || row.aligned[column] === '.') continue
      if (row.sourcePositions?.[column] === sourcePosition && positionAt(row, column) === sourcePosition) {
        const anchor = anchorAt(result, row.rowId, column)
        if (anchor) matches.push(anchor)
        break
      }
    }
  }
  return matches
}
