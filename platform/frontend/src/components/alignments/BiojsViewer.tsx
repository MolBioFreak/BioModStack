import { useLayoutEffect, useRef, useState } from 'react'
import viewerCode from '../../../alignment-viewers/biojs/dist/vendor/msa.js?raw'
import viewerCss from '../../../alignment-viewers/biojs/dist/vendor/msa.css?raw'
import adapterCode from '../../../alignment-viewers/biojs/src.js?raw'
import { anchorAt } from '../../lib/alignmentCoordinates'
import type { AlignmentViewerProps } from './AlignmentWorkspace'

export default function BiojsViewer(props: AlignmentViewerProps) {
  const version = JSON.stringify([props.document.resultId, props.document.rows.map(({ rowId, label, aligned }) => [rowId, label, aligned])])
  return <BiojsFrame key={version} {...props} />
}
function BiojsFrame({ document, onSelect }: AlignmentViewerProps) {
  const frame = useRef<HTMLIFrameElement>(null)
  const current = useRef({ document, onSelect })
  current.current = { document, onSelect }
  const [token] = useState(() => crypto.randomUUID() + crypto.randomUUID())
  const [srcDoc] = useState(() => {
    const payload = JSON.stringify({ rows: document.rows.map(({ label, aligned }) => ({ label, aligned })), token }).replace(/</g, '\\u003c')
    const scripts = (viewerCode + '\n' + adapterCode).replace(/<\/script/gi, '<\\/script')
    const css = viewerCss.replace(/<\/style/gi, '<\\/style')
    return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${token}' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><style>${css}</style></head><body><div id="viewer"></div><script id="payload" type="application/json">${payload}</script><script nonce="${token}">${scripts}</script></body></html>`
  })
  useLayoutEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== 'bms-biojs-inline-v1' || event.data?.token !== token || !Number.isInteger(event.data.index)) return
      const row = current.current.document.rows[event.data.index]
      const anchor = row && anchorAt(current.current.document, row.rowId, event.data.column)
      if (anchor) current.current.onSelect(anchor)
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [token])
  return <section aria-label="BioJS MSAViewer"><p>Full alignment loaded; external selections, shared marks and saved views are not rendered.</p><iframe ref={frame} title="BioJS MSAViewer" srcDoc={srcDoc} sandbox="allow-scripts" style={{ width: '100%', height: 390, border: 0 }} /></section>
}
