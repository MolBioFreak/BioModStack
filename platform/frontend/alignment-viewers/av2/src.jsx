// Patch the same Pixi CommonJS instance used by AV2, for CSP without eval.
import './node_modules/@pixi/unsafe-eval/lib/index.js'
import React from 'react'
import { createRoot } from 'react-dom/client'
import { AlignmentViewer, FastaAlignment } from 'alignment-viewer-2'

const { rows, token } = JSON.parse(document.getElementById('payload').textContent)
const fasta = rows.map((r, i) => `>row_${i}\n${r.aligned}`).join('\n') + '\n'
window.addEventListener('bms-av2-cell', event => {
  window.parent.postMessage({ channel: 'bms-av2-inline-v1', token, ...event.detail }, '*')
})
createRoot(document.getElementById('root')).render(
  <div style={{ height: 420, width: '100%' }}><AlignmentViewer alignment={FastaAlignment.fromFileContents('alignment', fasta, false)} disableSearch disableSearchKeyboardShortcut /></div>
)
