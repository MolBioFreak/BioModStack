import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import AlignmentResultList, { type AlignmentReceipt } from './AlignmentResultList'
type Page = {items: AlignmentReceipt[]; next_cursor: string | null}

export default function ProjectAlignments({projectId}: {projectId: string}) {
  const [rows, setRows] = useState<AlignmentReceipt[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let live = true
    setLoading(true); setError(''); setRows([]); setCursor(null)
    api.get<Page>(`/api/family-msa/projects/${encodeURIComponent(projectId)}/receipts`).then(({data}) => {
      if (live) { setRows(data.items); setCursor(data.next_cursor) }
    }).catch(e => { if (live) setError(String(e)) }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [projectId, reload])
  async function more() {
    setLoading(true); setError('')
    try {
      const {data} = await api.get<Page>(`/api/family-msa/projects/${encodeURIComponent(projectId)}/receipts`, {params: {after: cursor}})
      setRows(old => [...old, ...data.items]); setCursor(data.next_cursor)
    } catch (e) { setError(String(e)) } finally { setLoading(false) }
  }
  return <section aria-label="Project alignments" className="m-4 shrink-0 rounded-xl border border-border-primary bg-surface-secondary p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Alignments</h2><Link className="text-accent underline text-sm" to={`/alignments/library?project_id=${encodeURIComponent(projectId)}`}>Open alignment library / import</Link></div>
    {loading && <p role="status">Loading alignment results…</p>}
    {error && <p role="alert">Alignment results unavailable: {error} <button onClick={() => setReload(n => n + 1)}>Retry</button></p>}
    {!loading && !error && !rows.length && <p className="text-sm text-content-secondary">No saved alignments in this Project. Use “Save to Project” on a computed result, or import an alignment.</p>}
    <AlignmentResultList rows={rows} projectId={projectId}/>
    {cursor && <button disabled={loading} onClick={more}>Load more alignments</button>}
  </section>
}
