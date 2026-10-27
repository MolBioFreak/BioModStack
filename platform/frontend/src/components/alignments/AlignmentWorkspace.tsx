import { useState, type ComponentType } from 'react'
import { anchorAt, type AlignmentDocument, type ResidueAnchor } from '../../lib/alignmentCoordinates'
import AlignmentShared, { type View } from './AlignmentShared'

export type AlignmentViewerId = 'jbrowse' | 'alignment-viewer-2' | 'biojs'
export interface AlignmentViewerProps {
  document: AlignmentDocument
  selection?: ResidueAnchor
  onSelect: (anchor: ResidueAnchor) => void
}
export interface AlignmentWorkspaceProps {
  importedStateIds?: string[];
  projectLabel?: string
  projectId?: string
  receiptId?: string
  document?: AlignmentDocument
  viewers?: Partial<Record<AlignmentViewerId, ComponentType<AlignmentViewerProps>>>
  onSelectionChange?: (anchor: ResidueAnchor) => void
  // Already-authorized artifact URLs supplied by the caller; this component never fetches external data.
  downloads?: { label: string; href: string }[]
}

const viewerNames: Record<AlignmentViewerId, string> = {
  jbrowse: 'JBrowseMSA', 'alignment-viewer-2': 'AlignmentViewer 2', biojs: 'BioJS MSAViewer',
}

/** Host for real viewer adapters. It never silently substitutes a fake renderer. */
export default function AlignmentWorkspace({ projectLabel, projectId, receiptId, document, viewers = {}, downloads = [], onSelectionChange, importedStateIds }: AlignmentWorkspaceProps) {
  const [viewerId, setViewerId] = useState<AlignmentViewerId>('jbrowse')
  const [selection, setSelection] = useState<ResidueAnchor>()
  const [rowId, setRowId] = useState('')
  const [column, setColumn] = useState('1')
  const [layouts, setLayouts] = useState<Record<string, {order: string[]; hidden: string[]}>>({})
  const layout = document ? layouts[document.resultId] : undefined
  const order = document ? [...(layout?.order ?? []).filter(id => document.rows.some(row => row.rowId === id)), ...document.rows.map(row => row.rowId).filter(id => !(layout?.order ?? []).includes(id))] : []
  const hidden = layout?.hidden ?? []
  const updateLayout = (order: string[], hidden: string[]) => { if (document) setLayouts(old => ({...old, [document.resultId]: {order, hidden}})) }
  const displayed = document && {...document, rows: order.filter(id => !hidden.includes(id)).map(id => document.rows.find(row => row.rowId === id)!)}
  const Viewer = viewers[viewerId]
  const localDownloads = downloads.filter(item => item.href.startsWith('/') && !item.href.startsWith('//') && !item.href.includes('\\'))
  const currentSelection = selection?.resultId === document?.resultId ? selection : undefined
  function select(anchor: ResidueAnchor) {
    setSelection(anchor)
    onSelectionChange?.(anchor)
  }
  return <main aria-label="Alignments workspace">
    <h1>Alignments</h1>
    <p>Project: {projectLabel || 'No project selected'}</p>
    {!document ? <p>No alignment result selected. Connect a retained result to view it.</p> : <>
      <p>Result: {document.resultId} · {document.rows.length} rows</p>
      <label htmlFor="alignment-renderer">Viewer</label>{' '}
      <select id="alignment-renderer" value={viewerId} onChange={event => setViewerId(event.target.value as AlignmentViewerId)}>
        {(Object.keys(viewerNames) as AlignmentViewerId[]).map(id => <option key={id} value={id}>{viewerNames[id]}{viewers[id] ? '' : ' (not installed)'}</option>)}
      </select>
      {Viewer ? <Viewer document={displayed!} selection={currentSelection} onSelect={select} /> : <p role="status">{viewerNames[viewerId]} is not connected. This result remains available for download.</p>}
      <fieldset><legend>Row order and visibility</legend>
        {order.map((id, index) => <div key={id}>{document.rows.find(row => row.rowId === id)?.label} ({id}){' '}
          <button type="button" onClick={() => updateLayout(order, hidden.includes(id) ? hidden.filter(x => x !== id) : [...hidden, id])}>{hidden.includes(id) ? 'Show' : 'Hide'}</button>{' '}
          <button type="button" disabled={index === 0} onClick={() => { const next = [...order]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; updateLayout(next, hidden) }}>Move up</button>
        </div>)}
      </fieldset>
      <fieldset>
        <legend>Inspect a position</legend>
        <label>Row <select value={rowId} onChange={event => { setRowId(event.target.value); setSelection(undefined) }}>
          <option value="">Choose a row</option>{document.rows.map(row => <option key={row.rowId} value={row.rowId}>{row.label} ({row.rowId})</option>)}
        </select></label>{' '}
        <label>Alignment column (1-based) <input type="number" min="1" value={column} onChange={event => setColumn(event.target.value)} /></label>{' '}
        <button type="button" onClick={() => { const anchor = anchorAt(document, rowId, Number(column) - 1); if (anchor) select(anchor) }}>Inspect</button>
      </fieldset>
      {currentSelection && <p>Row {currentSelection.rowId}, column {currentSelection.column + 1}, {currentSelection.residue === null ? 'gap' : `residue ${currentSelection.residue + 1}`}{currentSelection.sourcePosition === undefined ? '; source coordinate unknown' : `; source coordinate ${currentSelection.sourcePosition + 1}`}</p>}
      {projectId && receiptId && <AlignmentShared importedStateIds={importedStateIds} key={projectId} projectId={projectId} receiptId={receiptId} artifactId={document.resultId} selection={currentSelection} viewer={viewerId} rowOrder={order} hiddenRowIds={hidden} selectedRowId={document.rows.some(row => row.rowId === rowId) ? rowId : undefined} applyView={(view: View) => { if (view.viewer && view.viewer in viewerNames) setViewerId(view.viewer as AlignmentViewerId); updateLayout(view.row_order, view.hidden_row_ids); if (view.selected_row_id != null && view.selected_column != null) { const anchor = anchorAt(document, view.selected_row_id, view.selected_column); if (anchor) { setRowId(anchor.rowId); setColumn(String(anchor.column + 1)); select(anchor) } } else { setSelection(undefined); setRowId(view.selected_row_id ?? "") } }} applyViewer={value => { if (value in viewerNames) setViewerId(value as AlignmentViewerId) }}/>}
    </>}
    {localDownloads.length > 0 && <section aria-label="Downloads"><h2>Downloads</h2><ul>{localDownloads.map(item => <li key={`${item.label}:${item.href}`}><a href={item.href} download>{item.label}</a></li>)}</ul></section>}
  </main>
}
