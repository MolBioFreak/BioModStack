import viewerCode from '../../../alignment-viewers/jbrowse/dist/viewer.js?raw'
import type { AlignmentViewerProps } from './AlignmentWorkspace'
import ViewerFrame from './ViewerFrame'

export default function JbrowseViewer(props: AlignmentViewerProps) {
  const key = JSON.stringify([props.document.resultId, props.document.rows.map(r => [r.rowId, r.label, r.aligned])])
  return <ViewerFrame key={key} {...props} code={viewerCode} channel="bms-jbrowse-inline-v1" title="JBrowseMSA" notice="Native selection, labeled marks, viewport, colors and gap-column filtering. Arbitrary column hiding is not exposed by this upstream API." />
}
