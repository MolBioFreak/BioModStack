// Patch the same Pixi CommonJS instance used by AV2, for CSP without eval.
import './node_modules/@pixi/unsafe-eval/lib/index.js'
import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AlignmentViewer, FastaAlignment, AminoAcidColorSchemes, NucleotideColorSchemes, getAlignmentFontDetails, reduxStore } from 'alignment-viewer-2'
import { setWorldOffset } from 'alignment-viewer-2/dist/js/redux/VirtualizationReducers.js'

const payload = JSON.parse(document.getElementById('payload').textContent)
const { rows, token } = payload
const channel = 'bms-av2-inline-v1'
const send = data => window.parent.postMessage({channel, token, ...data}, '*')
const fasta = rows.map((r, i) => `>row_${i}\n${r.aligned}`).join('\n') + '\n'
const alignment = FastaAlignment.fromFileContents('alignment', fasta, false)
const colorSchemes = alignment.getPredictedType().key === 'nucleotide-alignment' ? NucleotideColorSchemes : AminoAcidColorSchemes
const xId = `x_viewport_virtualization_${alignment.getUUID()}`
const yId = `y_viewport_virtualization_${alignment.getUUID()}`
window.addEventListener('bms-av2-cell', event => send(event.detail))
function App() {
  const [control, setControl] = useState(payload)
  const [style, setStyle] = useState({zoomLevel: payload.state?.zoomLevel ?? 12, colorScheme: payload.state?.colorScheme ?? colorSchemes.list[0].key})
  useEffect(() => {
    let pending = payload.state, applying = false, last = ''
    let settings = style
    const report = () => {
      if (applying) return
      const v = reduxStore.getState().virtualizations
      if (!v[xId]?.containerSizePx || !v[yId]?.containerSizePx) return
      if (pending) {
        const mono = getAlignmentFontDetails(settings.zoomLevel, true)
        const normal = getAlignmentFontDetails(settings.zoomLevel, false)
        if (v[xId].cellSizePx !== mono.width || v[yId].cellSizePx !== Math.max(mono.height, normal.height)) return
        applying = true
        const s = pending; pending = undefined
        if (s.scrollX != null) reduxStore.dispatch(setWorldOffset({virtualizationId: xId, worldOffsetPx: s.scrollX}))
        if (s.scrollY != null) reduxStore.dispatch(setWorldOffset({virtualizationId: yId, worldOffsetPx: s.scrollY}))
        applying = false
      }
      const axes = reduxStore.getState().virtualizations
      const state = {...settings, scrollX: axes[xId].worldOffsetPx, scrollY: axes[yId].worldOffsetPx}
      const key = JSON.stringify(state)
      if (key !== last) { last = key; send({type: 'state', state}) }
    }
    const receive = event => {
      if (event.source !== window.parent || event.data?.channel !== channel || event.data?.token !== token || event.data.type !== 'control') return
      setControl(event.data)
      const s = event.data.state ?? {}
      settings = {zoomLevel: s.zoomLevel ?? settings.zoomLevel, colorScheme: s.colorScheme ?? settings.colorScheme}
      setStyle(settings); pending = s
      // Let changed zoom props reach the native virtualization before scroll.
      requestAnimationFrame(report)
    }
    const styled = event => { settings = event.detail; setStyle(settings); report() }
    window.addEventListener('message', receive)
    window.addEventListener('bms-av2-style', styled)
    const unsubscribe = reduxStore.subscribe(report)
    report(); send({type: 'ready'})
    return () => { unsubscribe(); window.removeEventListener('message', receive); window.removeEventListener('bms-av2-style', styled) }
  }, [])
  const highlights = {}
  let omitted = 0
  const spans = [...(control.marks ?? []), ...(control.selection ? [{index: control.selection.index, start: control.selection.column, end: control.selection.column}] : [])]
  for (const span of spans) {
    if (span.index != null && rows.filter(row => row.aligned === rows[span.index]?.aligned).length !== 1) { omitted++; continue }
    for (const row of span.index == null ? rows : [rows[span.index]]) {
      if (!row) continue
      const positions = highlights[row.aligned] ??= []
      for (let p = span.start; p <= Math.min(span.end, row.aligned.length - 1); p++) positions.push(p)
    }
  }
  const update = next => window.dispatchEvent(new CustomEvent('bms-av2-style', {detail: {...style, ...next}}))
  return <><label>Colors <select value={style.colorScheme} onChange={e => update({colorScheme: e.target.value})}>{colorSchemes.list.map(s => <option key={s.key} value={s.key}>{s.commonName}</option>)}</select></label> <label>Zoom <input type="range" min="5" max="24" value={style.zoomLevel} onChange={e => update({zoomLevel: Number(e.target.value)})}/></label>{omitted > 0 && <p>{omitted} row-specific highlights omitted: upstream keys highlights by sequence, not occurrence.</p>}
    <div style={{height: 420, width: '100%'}}><AlignmentViewer alignment={alignment} zoomLevel={style.zoomLevel} aaColorScheme={AminoAcidColorSchemes.deserialize(style.colorScheme)} ntColorScheme={NucleotideColorSchemes.deserialize(style.colorScheme)} highlightPositionalMatches={{searchString: JSON.stringify(highlights), posidxsMatchedPerSequence: highlights, highlightColor: {red: 255, green: 165, blue: 0}}} disableSearch disableSearchKeyboardShortcut /></div></>
}
createRoot(document.getElementById('root')).render(<App />)
