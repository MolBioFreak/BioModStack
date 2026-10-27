// Coordinates are zero-based; gaps have no residue coordinate.
export interface AlignmentRow {
  rowId: string
  label: string
  aligned: string
  // Unknown is deliberately distinct from a matching label or sequence.
  sourceMemberId?: string
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
  sourcePosition?: number
}

export function anchorAt(result: AlignmentDocument, rowId: string, column: number): ResidueAnchor | undefined {
  const row = result.rows.find(item => item.rowId === rowId)
  if (!row || !Number.isInteger(column) || column < 0 || column >= row.aligned.length) return undefined
  const symbol = row.aligned[column]
  const residue = symbol === '-' || symbol === '.' ? null : [...row.aligned.slice(0, column)].filter(c => c !== '-' && c !== '.').length
  return {
    resultId: result.resultId, rowId, column, residue,
    ...(row.sourceMemberId !== undefined ? { sourceMemberId: row.sourceMemberId } : {}),
    ...(residue !== null && row.sourceMemberId !== undefined && row.sourceOffset !== undefined
      ? { sourcePosition: row.sourceOffset + residue } : {}),
  }
}

// Match only an explicit source member and position; labels/sequence are not identity.
export function correspondingColumns(result: AlignmentDocument, sourceMemberId: string, sourcePosition: number): ResidueAnchor[] {
  const matches: ResidueAnchor[] = []
  for (const row of result.rows) {
    if (row.sourceMemberId !== sourceMemberId || row.sourceOffset === undefined) continue
    let position = row.sourceOffset
    for (let column = 0; column < row.aligned.length; column++) {
      if (row.aligned[column] === '-' || row.aligned[column] === '.') continue
      if (position === sourcePosition) {
        const anchor = anchorAt(result, row.rowId, column)
        if (anchor) matches.push(anchor)
        break
      }
      position++
    }
  }
  return matches
}
