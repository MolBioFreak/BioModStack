import React from 'react'
import { createRoot } from 'react-dom/client'
import { MSAViewer } from 'react-msaview'

const { rows, token } = JSON.parse(document.getElementById('payload').textContent)
// Only opaque row keys enter FASTA. Labels are data passed to the viewer's
// canvas renderer; escaping controls makes line breaks visible on one line.
const display = value => JSON.stringify(value).slice(1, -1).replace(/[\u007f-\u009f\u2028\u2029]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`)
const fasta = rows.map((r, i) => `>row_${i}\n${r.aligned}`).join('\n') + '\n'
const rowData = Object.fromEntries(rows.map((r, i) => [`row_${i}`, { genome: `[${display(r.rowId)}] ${display(r.label)}` }]))
createRoot(document.getElementById('root')).render(
  <MSAViewer msa={fasta} rowData={rowData} drawTree={false} treeAreaWidth={260} hideHeader height={420} onCellClick={cell => {
    const m = /^row_(\d+)$/.exec(cell?.row || '')
    if (m && Number.isInteger(cell.column)) window.parent.postMessage({ channel: 'bms-jbrowse-inline-v1', token, index: Number(m[1]), column: cell.column - 1 }, '*')
  }} />
)
