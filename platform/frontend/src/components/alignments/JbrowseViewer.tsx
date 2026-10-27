import { useLayoutEffect, useRef, useState } from 'react'
import viewerCode from '../../../alignment-viewers/jbrowse/dist/viewer.js?raw'
import { anchorAt } from '../../lib/alignmentCoordinates'
import type { AlignmentViewerProps } from './AlignmentWorkspace'

/** Experimental: all document data is embedded once in the initial opaque srcdoc. */
export default function JbrowseViewer(props: AlignmentViewerProps) {
  const key = JSON.stringify([props.document.resultId, props.document.rows.map(r => [r.rowId, r.label, r.aligned])])
  return <Frame key={key} {...props} />
}

function Frame({ document, onSelect }: AlignmentViewerProps) {
  const frame = useRef<HTMLIFrameElement>(null)
  const current = useRef({ document, onSelect })
  current.current = { document, onSelect }
  const [token] = useState(() => crypto.randomUUID() + crypto.randomUUID())
  const [srcDoc] = useState(() => {
    const payload = JSON.stringify({ rows: document.rows.map(({ rowId, label, aligned }) => ({ rowId, label, aligned })), token }).replace(/</g, '\\u003c')
    // The host sends no rows after frame navigation. This does NOT prevent a
    // compromised viewer script from exfiltrating rows by self-navigation.
    const code = viewerCode.replace(/<\/script/gi, '<\\/script')
    return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${token}'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"></head><body><div id="root"></div><script id="payload" type="application/json">${payload}</script><script nonce="${token}">${code}</script></body></html>`
  })
  useLayoutEffect(() => {
    const receive = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow || e.data?.channel !== 'bms-jbrowse-inline-v1' || e.data?.token !== token || !Number.isInteger(e.data.index)) return
      const row = current.current.document.rows[e.data.index]
      const anchor = row && anchorAt(current.current.document, row.rowId, e.data.column)
      if (anchor) current.current.onSelect(anchor)
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [token])
  return <section aria-label="JBrowseMSA"><p>Experimental viewer: row labels include their row IDs; control characters are escaped. External selections and marks are not displayed.</p><iframe ref={frame} title="JBrowseMSA" sandbox="allow-scripts" srcDoc={srcDoc} style={{ width: '100%', height: 460, border: 0 }} /></section>
}
