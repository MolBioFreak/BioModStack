import { Link } from 'react-router-dom'
export type AlignmentReceipt = {receipt_id: string; entity_kind: string; availability: string; label?: string; row_count?: number; molecule?: string; created_at?: string; summary_unavailable?: boolean}
export default function AlignmentResultList({rows, projectId}: {rows: AlignmentReceipt[]; projectId: string}) {
  return <ul className="space-y-2">{rows.map(row => <li key={row.receipt_id} className="min-w-0 break-words border-b border-border-primary py-2">
    <Link className="text-accent underline" to={`/alignments/results/${encodeURIComponent(row.receipt_id)}?project_id=${encodeURIComponent(projectId)}`}>{row.label || `${row.entity_kind === 'family_msa_import' ? 'Imported alignment' : 'Alignment result'} · ${row.receipt_id}`}</Link>
    <div className="text-xs text-content-secondary">{row.row_count != null && `${row.row_count} sequences · `}{row.molecule && `${row.molecule.toUpperCase()} · `}{row.entity_kind === 'family_msa_import' ? 'Imported' : 'Computed'} · {row.availability}{row.created_at && ` · ${new Date(row.created_at).toLocaleString()}`}{row.summary_unavailable && ' · Summary unavailable; open result for details'}</div>
  </li>)}</ul>
}
