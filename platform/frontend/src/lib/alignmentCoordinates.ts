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

export function anchorAt(result: AlignmentDocument, rowId: string, column: number): ResidueAnchor | undefined {
  const row = result.rows.find(item => item.rowId === rowId)
  if (!row || !Number.isInteger(column) || column < 0 || column >= row.aligned.length) return undefined
  const symbol = row.aligned[column]
  const residue = symbol === '-' || symbol === '.' ? null : [...row.aligned.slice(0, column)].filter(c => c !== '-' && c !== '.').length
  const position = row.sourcePositions ? row.sourcePositions[column] : residue !== null && row.sourceOffset !== undefined ? row.sourceOffset + residue : undefined
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
    let residue = 0
    for (let column = 0; column < row.aligned.length; column++) {
      if (row.aligned[column] === '-' || row.aligned[column] === '.') continue
      const position = row.sourcePositions ? row.sourcePositions[column] : row.sourceOffset === undefined ? undefined : row.sourceOffset + residue
      if (position === sourcePosition) {
        const anchor = anchorAt(result, row.rowId, column)
        if (anchor) matches.push(anchor)
        break
      }
      residue++
    }
  }
  return matches
}
