const { rows, token } = JSON.parse(document.getElementById('payload').textContent)
const viewer = window.msa({ el: document.getElementById('viewer'), seqs: rows.map((row, index) => ({ id: String(index), name: row.label, seq: row.aligned })), bootstrapMenu: false, conf: { dropImport: false, registerMouseClicks: true }, vis: { metaLinks: false }, zoomer: { alignmentHeight: 320, autoResize: true } })
viewer.g.on('residue:click', data => window.parent.postMessage({ channel: 'bms-biojs-inline-v1', token, index: Number(data.seqId), column: data.rowPos }, '*'))
viewer.render()
// Upstream binds residue selection to dblclick; translate a single canvas click.
const canvas = document.querySelector('.biojs_msa_seqblock')
canvas.addEventListener('click', event => {
  canvas.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: event.clientX, clientY: event.clientY, button: event.button, ctrlKey: event.ctrlKey, metaKey: event.metaKey, shiftKey: event.shiftKey, altKey: event.altKey }))
})
