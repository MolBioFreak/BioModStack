import React from 'react'
import { createRoot } from 'react-dom/client'
import { reaction, when } from 'mobx'
import { MSAViewer } from 'react-msaview'

const payload = JSON.parse(document.getElementById('payload').textContent)
const { rows, token } = payload
const channel = 'bms-jbrowse-inline-v1'
const send = data => window.parent.postMessage({channel, token, ...data}, '*')
const display = value => JSON.stringify(value).slice(1, -1).replace(/[\u007f-\u009f\u2028\u2029]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`)
const fasta = rows.map((r, i) => `>row_${i}\n${r.aligned}`).join('\n') + '\n'
const rowData = Object.fromEntries(rows.map((r, i) => [`row_${i}`, { genome: display(r.label || r.rowId) }]))
let model, ready = false, applying = false, control = payload, lastState
const snapshot = () => ({scrollX: model.scrollX, scrollY: model.scrollY, columnWidth: model.colWidth, rowHeight: model.rowHeight, colorScheme: model.colorSchemeName, hideGaps: model.hideGaps, allowedGappyness: model.allowedGappyness})
function apply() {
  if (!ready) return
  applying = true
  try {
    const s = control.state ?? {}
    // A host echo must not reset scroll after unrelated selection/mark changes.
    if (JSON.stringify(s) !== lastState) {
      if (s.colorScheme != null) model.setColorSchemeName(s.colorScheme)
      if (s.columnWidth != null) model.setColWidth(s.columnWidth)
      if (s.rowHeight != null) model.setRowHeight(s.rowHeight)
      if (s.hideGaps != null) model.setHideGaps(s.hideGaps)
      if (s.allowedGappyness != null) model.setAllowedGappyness(s.allowedGappyness)
      if (s.scrollX != null) model.setScrollX(s.scrollX)
      if (s.scrollY != null) model.setScrollY(s.scrollY)
    }
    model.setSelection(control.selection ? {rows: [`row_${control.selection.index}`], start: control.selection.column + 1, end: control.selection.column + 1} : undefined)
    model.setHighlights((control.marks ?? []).map(m => m.index == null ? {start: m.start + 1, end: m.end + 1, label: m.label} : {row: `row_${m.index}`, start: m.residue + 1, end: m.residue + 1, label: m.label}))
    lastState = JSON.stringify(snapshot())
  } finally { applying = false }
  send({type: 'state', state: snapshot()})
}
window.addEventListener('message', event => {
  if (event.source !== window.parent || event.data?.channel !== channel || event.data?.token !== token || event.data.type !== 'control') return
  control = event.data; apply()
})
createRoot(document.getElementById('root')).render(
  <MSAViewer msa={fasta} rowData={rowData} drawTree={false} treeAreaWidth={260} height={payload.viewerHeight ?? 420} onModel={value => {
    model = value
    when(() => model.viewInitialized && model.numColumns > 0, () => {
      ready = true; apply()
      reaction(() => JSON.stringify(snapshot()), value => {
        if (!applying) { lastState = value; send({type: 'state', state: JSON.parse(value)}) }
      })
      send({type: 'ready'})
    })
  }} onCellClick={cell => {
    const m = /^row_(\d+)$/.exec(cell?.row || '')
    if (m && Number.isInteger(cell.column)) send({index: Number(m[1]), column: cell.column - 1})
  }} />
)
