import { useMemo } from 'react'
import viewerCode from '../../../alignment-viewers/av2/dist/viewer.js?raw'
import type { AlignmentViewerProps } from './AlignmentWorkspace'
import ViewerFrame from './ViewerFrame'

export default function Av2Viewer(props: AlignmentViewerProps) {
  const key = useMemo(() => JSON.stringify([props.document.resultId, props.document.rows.map(r => [r.rowId, r.label, r.aligned])]), [props.document.resultId, props.document.rows])
  return <ViewerFrame key={key} {...props} code={viewerCode} blobWorkers channel="bms-av2-inline-v1" title="AlignmentViewer 2" notice="Zoom, colors and position are saved. Column hiding isn't supported. AV2 displays residues in uppercase; original case is retained in the alignment and downloads." />
}
