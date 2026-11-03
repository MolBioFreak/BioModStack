import {memo, useState} from 'react'
import type { AlignmentDocument, AlignmentPairSimilarity } from '../../lib/alignmentCoordinates'

const percent = (value: number | null | undefined) => value == null ? 'N/A' : `${value.toFixed(2)}%`
const colors = ['#14b8a6', '#f59e0b', '#64748b']

function ComparisonMap({bins, length, label}: {bins: [number, number, number][]; length: number; label: string}) {
  if (!length || !bins.length) return <span>No sequence positions to map</span>
  let start = 0
  const blocks = bins.map(counts => {
    const size = counts.reduce((a, b) => a + b, 0)
    const block = {start, size, counts}
    start += size
    return block
  })
  return <svg className="alignment-comparison-map" viewBox="0 0 1000 24" preserveAspectRatio="none" role="img" aria-label={label}>
    <title>{label}. Position 1 to {length.toLocaleString()}, with alignment gaps removed. Each block shows the proportions of matches, mismatches, and gaps in the other sequence.</title>
    {colors.map((color, category) => <path key={color} fill={color} d={blocks.map(({start, size, counts}) => {
      const x = start / length * 1000, width = size / length * 1000
      const y = counts.slice(0, category).reduce((a, b) => a + b, 0) / size * 24
      const height = counts[category] / size * 24
      return height ? `M${x},${y}h${width}v${height}h${-width}Z` : ''
    }).join(' ')}/>)}
    {blocks.map(({start, size, counts}) => <rect key={start} x={start / length * 1000} y="0" width={size / length * 1000} height="24" fill="transparent"><title>Positions {start + 1}–{start + size}: {counts[0]} identical, {counts[1]} mismatched, {counts[2]} with a gap in the other sequence</title></rect>)}
  </svg>
}

function oriented(pair: AlignmentPairSimilarity, id: string) {
  const left = pair.left_row_id === id
  return {otherId: left ? pair.right_row_id : pair.left_row_id,
    selectedCoverage: left ? pair.left_aligned_percent : pair.right_aligned_percent,
    otherCoverage: left ? pair.right_aligned_percent : pair.left_aligned_percent,
    length: left ? pair.left_length : pair.right_length,
    otherLength: left ? pair.right_length : pair.left_length,
    bins: left ? pair.left_map : pair.right_map}
}

export default memo(function AlignmentSimilaritySummary({document, selectedRowId, onSelectRow}: {
  document: AlignmentDocument; selectedRowId?: string; onSelectRow?: (id: string) => void
}) {
  const [localSelection, setLocalSelection] = useState<{result: string; row: string}>()
  const stats = document.similarity
  if (!stats) return null
  const candidate = selectedRowId ?? (localSelection?.result === document.resultId ? localSelection.row : undefined)
  const selected = document.rows.find(row => row.rowId === candidate) ?? document.rows[0]
  const select = (id: string) => { setLocalSelection({result: document.resultId, row: id}); onSelectRow?.(id) }
  const label = (id: string) => {
    const index = document.rows.findIndex(row => row.rowId === id)
    if (index < 0) return id
    const name = document.rows[index].label
    return document.rows.filter(row => row.label === name).length > 1 ? `${name} (row ${index + 1})` : name
  }
  const pairs = selected ? stats.pairs.filter(pair => pair.left_row_id === selected.rowId || pair.right_row_id === selected.rowId) : []
  return <section aria-label="Sequence similarity" className="alignment-similarity">
    <div><strong>Identity at nongap positions: {percent(stats.mean_pairwise_identity_percent)}</strong>{' '}
      <span>{stats.pairs_complete ? '(mean across all comparable pairs)' : '(pairwise calculation exceeds quick-summary limits)'}</span>
    </div>
    <h3>Compare one sequence against the others</h3>
    <p>{document.rows.length} sequences · {stats.pair_count} unique sequence pairs</p>
    <p>Click a sequence name below or a base in the viewer to use that sequence as the reference.</p>
    <div className="alignment-reference-list" aria-label="Comparison reference">{document.rows.map(row => <button type="button" key={row.rowId} aria-pressed={selected?.rowId === row.rowId} onClick={() => select(row.rowId)}>{label(row.rowId)}</button>)}</div>
    {selected && <>
      <h4>Reference: {label(selected.rowId)} · {document.rows.length - 1} comparisons</h4>
      <p>Identity = matches ÷ paired positions. Aligned % = paired positions ÷ that sequence’s length, counting both matches and mismatches.</p>
      {!!pairs.length && <>
        <div className="alignment-map-legend"><span><i style={{background: colors[0]}}/>Identical</span><span><i style={{background: colors[1]}}/>Mismatch</span><span><i style={{background: colors[2]}}/>Gap in other sequence</span></div>
        <p className="alignment-map-note">Maps run from the start to the end of the selected sequence, with alignment gaps removed. Extra positions in the other sequence appear when you select it. These are views of the current alignment, not new pairwise alignments.</p>
        <div className="alignment-comparison-scroll"><table className="alignment-comparison-table"><thead><tr><th>Other sequence / map on reference</th><th>Identity</th><th>Reference aligned</th><th>Other aligned</th><th>Identical / paired</th></tr></thead><tbody>{pairs.map(pair => {
          const view = oriented(pair, selected.rowId)
          return <tr key={view.otherId}><td><button type="button" className="alignment-sequence-link" title="Use this sequence as the reference" onClick={() => select(view.otherId)}>{label(view.otherId)}</button>
            {view.bins && view.length != null ? <><ComparisonMap bins={view.bins} length={view.length} label={`${label(selected.rowId)} against ${label(view.otherId)}`}/><div className="alignment-map-axis"><span>{view.length ? '1' : '0'}</span><span>{view.length.toLocaleString()} positions</span></div></> : <span>Map unavailable</span>}
          </td><td>{percent(pair.identity_percent)}</td><td title={`${pair.compared.toLocaleString()} paired / ${view.length?.toLocaleString() ?? 'unknown'} reference positions`}>{percent(view.selectedCoverage)}</td><td title={`${pair.compared.toLocaleString()} paired / ${view.otherLength?.toLocaleString() ?? 'unknown'} other positions`}>{percent(view.otherCoverage)}</td><td>{pair.identical.toLocaleString()} / {pair.compared.toLocaleString()}{pair.compared === 0 ? ' (no comparable positions)' : ''}</td></tr>
        })}</tbody></table></div>
      </>}
    </>}
    {!stats.pairs_complete && <p role="status">Pairwise maps and percentages omitted: this alignment exceeds the quick-summary limit of {stats.pair_count_limit} pairs or {stats.pair_cell_limit.toLocaleString()} pair × column comparisons. No pairs have been sampled.</p>}
    {stats.pair_count === 0 && <p>At least two rows are needed for pairwise identity.</p>}
    <details><summary>All {stats.pair_count} unique pairs and calculation details</summary>
      <p>Each row compares two sequences, not a separate region. Each pair appears once; sequence names repeat because each sequence has several partners.</p>
      <p>{stats.policy} All rows are included, even when hidden in the viewer. Identity does not establish biological function or alignment quality.</p>
      <p>Conserved among columns with no gaps in any row: {percent(stats.conserved_column_percent)} ({stats.identical_columns.toLocaleString()} / {stats.compared_columns.toLocaleString()}).</p>
      {!!stats.pairs.length && <div className="alignment-comparison-scroll"><table><thead><tr><th>Sequence pair (A ↔ B)</th><th>Identity</th><th>A aligned</th><th>B aligned</th><th>Identical / paired</th></tr></thead><tbody>{stats.pairs.map(pair => <tr key={JSON.stringify([pair.left_row_id, pair.right_row_id])}><td>{label(pair.left_row_id)} ↔ {label(pair.right_row_id)}</td><td>{percent(pair.identity_percent)}</td><td>{percent(pair.left_aligned_percent)}</td><td>{percent(pair.right_aligned_percent)}</td><td>{pair.identical.toLocaleString()} / {pair.compared.toLocaleString()}</td></tr>)}</tbody></table></div>}
    </details>
  </section>
})
