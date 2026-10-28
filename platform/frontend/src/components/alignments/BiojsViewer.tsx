import viewerCode from '../../../alignment-viewers/biojs/dist/vendor/msa.js?raw'
import viewerCss from '../../../alignment-viewers/biojs/dist/vendor/msa.css?raw'
import adapterCode from '../../../alignment-viewers/biojs/src.js?raw'
import type { AlignmentViewerProps } from './AlignmentWorkspace'
import ViewerFrame from './ViewerFrame'

export default function BiojsViewer(props: AlignmentViewerProps) {
  const key = JSON.stringify([props.document.resultId, props.document.rows.map(r => [r.rowId, r.label, r.aligned])])
  return <ViewerFrame key={key} {...props} code={viewerCode + "\n" + adapterCode} css={viewerCss} unsafeEval channel="bms-biojs-inline-v1" title="BioJS MSAViewer" notice="Native viewport, colors, hidden columns and selection restore. Marks use native selection outlines; mark labels remain in the shared-state list." />
}
