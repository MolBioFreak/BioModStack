import type { AlignmentDocument } from '../../lib/alignmentCoordinates'

const percent = (value: number | null) => value === null ? 'N/A' : `${value.toFixed(2)}%`
export default function AlignmentSimilaritySummary({document}: {document: AlignmentDocument}) {
  const stats = document.similarity
  if (!stats) return null
  const label = (id: string) => document.rows.find(row => row.rowId === id)?.label || id
  return <section aria-label="Sequence similarity" className="alignment-similarity">
    <div><strong>Sequence identity: {percent(stats.mean_pairwise_identity_percent)}</strong>{' '}
      <span>{stats.pairs_complete ? '(mean across comparable pairs)' : '(pairwise calculation exceeds quick-summary limits)'}</span>{' · '}
      <span>Conserved columns: {percent(stats.conserved_column_percent)} ({stats.identical_columns.toLocaleString()} / {stats.compared_columns.toLocaleString()} gap-free columns)</span>
    </div>
    <details><summary>Pairwise identity and calculation details</summary>
      <p>{stats.policy} This measures sequence identity, not functional similarity. All rows are included, even when hidden in the viewer.</p>
      {!stats.pairs_complete && <p>Pairwise summary omitted: {stats.pair_count.toLocaleString()} pairs exceed the limit of {stats.pair_count_limit} pairs or {stats.pair_cell_limit.toLocaleString()} pair × column comparisons. Conserved columns are still calculated across every row.</p>}
      {stats.pair_count === 0 && <p>At least two rows are needed for pairwise identity.</p>}
      {!!stats.pairs.length && <table><thead><tr><th>Sequence pair</th><th>Identity</th><th>Identical / compared</th></tr></thead><tbody>{stats.pairs.map(pair => <tr key={JSON.stringify([pair.left_row_id, pair.right_row_id])}><td>{label(pair.left_row_id)} ↔ {label(pair.right_row_id)}</td><td>{percent(pair.identity_percent)}</td><td>{pair.identical.toLocaleString()} / {pair.compared.toLocaleString()}{pair.compared === 0 ? ' (no comparable positions)' : ''}</td></tr>)}</tbody></table>}
    </details>
  </section>
}
