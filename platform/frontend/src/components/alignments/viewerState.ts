import { anchorAt, type AlignmentDocument, type ResidueAnchor } from '../../lib/alignmentCoordinates'
import type { Mark } from './AlignmentShared'

/** Renderer-owned settings, never a rewritten alignment or coordinate map. */
export interface RendererState {
  scrollX?: number; scrollY?: number; columnWidth?: number; rowHeight?: number
  colorScheme?: string; hiddenColumns?: number[]; hideGaps?: boolean; allowedGappyness?: number
  zoomLevel?: number
}
export interface ViewerMark { id: string; label: string; index?: number; start: number; end: number; residue?: number }
export function projectMarks(document: AlignmentDocument, marks: Mark[]): ViewerMark[] {
  return marks.flatMap(mark => {
    const c = mark.coordinate
    if (c.scope === 'artifact_columns') return c.artifact_id === document.resultId ? [{id: mark.id, label: mark.label, start: c.start, end: c.end}] : []
    const cells: ViewerMark[] = []
    document.rows.forEach((row, index) => {
      if (row.sourceMemberId !== c.source_member_id || row.occurrenceId !== c.occurrence_id) return
      row.sourcePositions?.forEach((position, column) => {
        if (position === null || position < c.start || position > c.end) return
        const anchor = anchorAt(document, row.rowId, column)
        if (anchor?.sourcePosition !== undefined && anchor.residue !== null) cells.push({id: mark.id, label: mark.label, index, start: column, end: column, residue: anchor.residue})
      })
    })
    return cells
  })
}
export function frameSelection(document: AlignmentDocument, selection?: ResidueAnchor) {
  if (selection?.resultId !== document.resultId) return undefined
  const index = document.rows.findIndex(row => row.rowId === selection.rowId)
  return index >= 0 ? {index, column: selection.column} : undefined
}
