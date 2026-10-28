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
  const control = () => ({state: current.current.rendererState ?? {}, selection: frameSelection(current.current.document, current.current.selection), marks: projectMarks(current.current.document, current.current.marks ?? [])})
  const [srcDoc] = useState(() => {
    const payload = JSON.stringify({rows: document.rows.map(({rowId, label, aligned}) => ({rowId, label, aligned})), token, ...control()}).replace(/</g, '\\u003c')
    return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${token}'${unsafeEval ? " 'unsafe-eval'" : ''}; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; worker-src ${blobWorkers ? 'blob:' : "'none'"}; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><style>${css.replace(/<\/style/gi, '<\\/style')}</style></head><body><div id="root"></div><div id="viewer"></div><script id="payload" type="application/json">${payload}</script><script nonce="${token}">${code.replace(/<\/script/gi, '<\\/script')}</script></body></html>`
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
  return <section aria-label={title}><p>Full alignment loaded. {notice}</p><iframe ref={frame} title={title} sandbox="allow-scripts" srcDoc={srcDoc} style={{width: '100%', height: 490, border: 0}} /></section>
}
