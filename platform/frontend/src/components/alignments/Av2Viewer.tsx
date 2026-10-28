import viewerCode from '../../../alignment-viewers/av2/dist/viewer.js?raw'
import type { AlignmentViewerProps } from './AlignmentWorkspace'
import ViewerFrame from './ViewerFrame'

export default function Av2Viewer(props: AlignmentViewerProps) {
  const key = JSON.stringify([props.document.resultId, props.document.rows.map(r => [r.rowId, r.label, r.aligned])])
  return <ViewerFrame key={key} {...props} code={viewerCode} blobWorkers channel="bms-av2-inline-v1" title="AlignmentViewer 2" notice="Native viewport, color and zoom restore; selection and marks use native search highlighting. Upstream has no arbitrary column-hiding API. Row-specific highlights on identical sequences are omitted, not propagated to other occurrences." />
}
