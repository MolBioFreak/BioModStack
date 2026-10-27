import React from 'react'
import { createRoot } from 'react-dom/client'
import { MSAViewer } from 'react-msaview'

const { rows, token } = JSON.parse(document.getElementById('payload').textContent)
const fasta = rows.map((r, i) => `>row_${i}\n${r.aligned}`).join('\n') + '\n'
createRoot(document.getElementById('root')).render(
  <MSAViewer msa={fasta} drawTree={false} hideHeader height={420} onCellClick={cell => {
    const m = /^row_(\d+)$/.exec(cell?.row || '')
    if (m && Number.isInteger(cell.column)) window.parent.postMessage({ channel: 'bms-jbrowse-inline-v1', token, index: Number(m[1]), column: cell.column - 1 }, '*')
  }} />
)
