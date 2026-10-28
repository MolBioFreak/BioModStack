const payload = JSON.parse(document.getElementById('payload').textContent)
const { rows, token } = payload
const channel = 'bms-biojs-inline-v1'
const send = data => window.parent.postMessage({channel, token, ...data}, '*')
const viewer = window.msa({ el: document.getElementById('viewer'), seqs: rows.map((row, index) => ({ id: String(index), name: row.label, seq: row.aligned })), bootstrapMenu: false, conf: { dropImport: false, registerMouseClicks: true }, vis: { metaLinks: false }, zoomer: { alignmentHeight: 320, autoResize: true } })
viewer.g.on('residue:click', data => send({index: Number(data.seqId), column: data.rowPos}))
viewer.render()
const canvas = document.querySelector('.biojs_msa_seqblock')
canvas.addEventListener('click', event => {
  canvas.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: event.clientX, clientY: event.clientY, button: event.button, ctrlKey: event.ctrlKey, metaKey: event.metaKey, shiftKey: event.shiftKey, altKey: event.altKey }))
})
let applying = false
const snapshot = () => ({scrollX: viewer.g.zoomer.get('_alignmentScrollLeft'), scrollY: viewer.g.zoomer.get('_alignmentScrollTop'), columnWidth: viewer.g.zoomer.get('columnWidth'), rowHeight: viewer.g.zoomer.get('rowHeight'), colorScheme: viewer.g.colorscheme.get('scheme'), hiddenColumns: viewer.g.columns.get('hidden')})
function report() { if (!applying) send({type: 'state', state: snapshot()}) }
function apply(control) {
  applying = true
  try {
    const s = control.state ?? {}
    if (s.colorScheme != null) viewer.g.colorscheme.set('scheme', s.colorScheme)
    if (s.hiddenColumns != null) viewer.g.columns.set('hidden', [...s.hiddenColumns].sort((a,b) => a-b))
    const zoom = {}
    for (const [key, target] of [['scrollX', '_alignmentScrollLeft'], ['scrollY', '_alignmentScrollTop'], ['columnWidth', 'columnWidth'], ['rowHeight', 'rowHeight']]) if (s[key] != null) zoom[target] = s[key]
    viewer.g.zoomer.set(zoom)
    const selections = (control.marks ?? []).map(m => ({type: m.index == null ? 'column' : 'pos', ...(m.index == null ? {} : {seqId: String(m.index)}), xStart: m.start, xEnd: m.end}))
    if (control.selection) selections.push({type: 'pos', seqId: String(control.selection.index), xStart: control.selection.column, xEnd: control.selection.column})
    viewer.g.selcol.resetJSON(selections)
  } finally { applying = false }
  report()
}
window.addEventListener('message', event => {
  if (event.source === window.parent && event.data?.channel === channel && event.data?.token === token && event.data.type === 'control') apply(event.data)
})
viewer.g.zoomer.on('change', report)
viewer.g.colorscheme.on('change', report)
viewer.g.columns.on('change', report)
// Small controls configure upstream models, not a replacement alignment matrix.
const controls = document.createElement('div')
controls.innerHTML = '<label>Colors <select><option>taylor</option><option>clustal</option><option>zappo</option><option>nucleotide</option></select></label> <button>Hide selected columns</button> <button>Show all columns</button>'
document.body.insertBefore(controls, document.getElementById('viewer'))
controls.querySelector('select').onchange = event => viewer.g.colorscheme.set('scheme', event.target.value)
controls.querySelectorAll('button')[0].onclick = () => viewer.g.columns.set('hidden', [...new Set([...viewer.g.columns.get('hidden'), ...viewer.g.selcol.getAllColumnBlocks({maxLen: viewer.seqs.getMaxLength(), withPos: true})])].sort((a,b) => a-b))
controls.querySelectorAll('button')[1].onclick = () => viewer.g.columns.set('hidden', [])
viewer.g.colorscheme.on('change', () => { controls.querySelector('select').value = viewer.g.colorscheme.get('scheme') })
apply(payload)
send({type: 'ready'})
