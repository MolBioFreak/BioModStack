import { useLayoutEffect, useRef, useState } from 'react'
import { anchorAt } from '../../lib/alignmentCoordinates'
import type { AlignmentViewerProps } from './AlignmentWorkspace'
import { frameSelection, projectMarks } from './viewerState'

export default function ViewerFrame(props: AlignmentViewerProps & {code: string; css?: string; channel: string; title: string; notice: string; unsafeEval?: boolean; blobWorkers?: boolean}) {
  const {document, code, css = '', channel, title, notice, unsafeEval, blobWorkers} = props
  const frame = useRef<HTMLIFrameElement>(null)
  const current = useRef(props); current.current = props
  const ready = useRef(false)
  const [token] = useState(() => crypto.randomUUID() + crypto.randomUUID())
  // Fill the remaining viewport, so the matrix rather than page chrome dominates.
  const [viewerHeight] = useState(() => Math.round(Math.min(900, Math.max(420, (typeof window === 'undefined' ? 0 : window.innerHeight) - 360))))
  const control = () => ({state: current.current.rendererState ?? {}, selection: frameSelection(current.current.document, current.current.selection), marks: projectMarks(current.current.document, current.current.marks ?? [])})
  const [srcDoc] = useState(() => {
    const payload = JSON.stringify({rows: document.rows.map(({rowId, label, aligned}) => ({rowId, label, aligned})), token, viewerHeight, ...control()}).replace(/</g, '\\u003c')
    return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${token}'${unsafeEval ? " 'unsafe-eval'" : ''}; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; worker-src ${blobWorkers ? 'blob:' : "'none'"}; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><style>html,body{margin:0;background:#fff;color:#1f2937;color-scheme:light;font-family:system-ui,sans-serif}.metadata-title[style*="position-axis-metadata"]{visibility:hidden}.biojs_msa_labels{overflow:hidden}.biojs_msa_labels>span+span{display:inline-block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;vertical-align:top;padding-right:6px;box-sizing:border-box}${css.replace(/<\/style/gi, '<\\/style')}</style></head><body><div id="root"></div><div id="viewer"></div><script id="payload" type="application/json">${payload}</script><script nonce="${token}">${code.replace(/<\/script/gi, '<\\/script')}</script></body></html>`
  })
  useLayoutEffect(() => {
    const receive = (event: MessageEvent) => {
      const data = event.data
      if (event.source !== frame.current?.contentWindow || data?.channel !== channel || data?.token !== token) return
      if (data.type === 'ready') { ready.current = true; frame.current?.contentWindow?.postMessage({channel, token, type: 'control', ...control()}, '*'); return }
      if (data.type === 'state') { current.current.onRendererStateChange?.(data.state); return }
      if (!Number.isInteger(data.index)) return
      const row = current.current.document.rows[data.index]
      const anchor = row && anchorAt(current.current.document, row.rowId, data.column)
      if (anchor) current.current.onSelect(anchor)
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [channel, token])
  useLayoutEffect(() => {
    if (ready.current) frame.current?.contentWindow?.postMessage({channel, token, type: 'control', ...control()}, '*')
  }, [props.rendererState, props.selection, props.marks, channel, token])
  return <section aria-label={title} className="alignment-viewer"><iframe ref={frame} title={title} sandbox="allow-scripts" srcDoc={srcDoc} style={{width: '100%', height: viewerHeight + 70, border: 0, borderRadius: 8, background: '#fff'}} /><details className="alignment-viewer-notes"><summary>About this viewer</summary><p>Full alignment loaded. {notice}</p></details></section>
}
