import { useState } from 'react'
import { api } from '../../lib/api'
import type { ResidueAnchor } from '../../lib/alignmentCoordinates'

type Coordinate = {scope: 'artifact_columns'; artifact_id: string; start: number; end: number} | {scope: 'source_positions'; source_member_id: string; occurrence_id: string; start: number; end: number}
type Mark = {id: string; label: string; coordinate: Coordinate; note: string; provenance: Record<string, unknown>}
type View = {id: string; name: string; artifact_id: string; hidden_row_ids: string[]; row_order: string[]; selected_marks: string[]; viewer: string | null}
type Comparison = {id: string; name: string; left_artifact_id: string; right_artifact_id: string; method: string; report: Record<string, unknown>; provenance: Record<string, unknown>}
type State = {schema: 'bms.family-msa.shared-state.v1'; marks: Mark[]; views: View[]; comparisons: Comparison[]}
type Revision = {id: string; head_generation: number; revision_id: string; state: State}
const empty = (): State => ({schema: 'bms.family-msa.shared-state.v1', marks: [], views: [], comparisons: []})
const id = () => crypto.randomUUID()

export default function AlignmentShared({projectId, receiptId, artifactId, selection, viewer, applyViewer}: {projectId: string; receiptId: string; artifactId: string; selection?: ResidueAnchor; viewer: string; applyViewer: (viewer: string) => void}) {
  const base = `/api/family-msa/projects/${encodeURIComponent(projectId)}`
  const [dataset, setDataset] = useState('')
  const [domain, setDomain] = useState('')
  const [revision, setRevision] = useState<Revision>()
  const [draft, setDraft] = useState<State>(empty)
  const [name, setName] = useState('')
  const [rightReceipt, setRightReceipt] = useState(receiptId)
  const [rightArtifact, setRightArtifact] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function run(fn: () => Promise<void>) { setBusy(true); setError(''); try { await fn() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  function received(value: Revision) { setRevision(value); setDataset(value.id); setDraft(value.state) }
  return <section aria-label="Project shared alignment state">
    <h2>Project shared alignment state</h2><p>Shared Project Dataset; coordinates are zero-based. Open or create a state before saving.</p>
    <label>Shared Dataset ID <input value={dataset} onChange={e => setDataset(e.target.value)}/></label>{' '}
    <button disabled={busy || !dataset} onClick={() => void run(async () => received((await api.get<Revision>(`${base}/shared-state/${encodeURIComponent(dataset)}`)).data))}>Open shared state</button>{' '}
    <label>Domain experiment ID <input value={domain} onChange={e => setDomain(e.target.value)}/></label>{' '}
    <button disabled={busy || !domain} onClick={() => void run(async () => received((await api.post<Revision>(`${base}/shared-state`, {name: name || 'Alignment shared state', domain_experiment_id: domain, state: empty()})).data))}>Create shared state</button>
    {revision && <>
      <p>Dataset {revision.id} · revision {revision.revision_id} · generation {revision.head_generation}</p>
      <label>Name <input value={name} onChange={e => setName(e.target.value)}/></label>{' '}
      <button disabled={!selection || selection.resultId !== artifactId} onClick={() => { if (selection) setDraft({...draft, marks: [...draft.marks, {id: id(), label: name, coordinate: {scope: 'artifact_columns', artifact_id: artifactId, start: selection.column, end: selection.column}, note: '', provenance: {}}]}) }}>Add selected column mark</button>{' '}
      <button disabled={!name} onClick={() => setDraft({...draft, views: [...draft.views, {id: id(), name, artifact_id: artifactId, hidden_row_ids: [], row_order: [], selected_marks: [], viewer}]})}>Save current viewer as view</button>
      <h3>Marks</h3><ul>{draft.marks.map(mark => <li key={mark.id}>{mark.label}: {mark.coordinate.scope === 'artifact_columns' ? `${mark.coordinate.artifact_id} columns ${mark.coordinate.start}–${mark.coordinate.end}` : `${mark.coordinate.source_member_id} / ${mark.coordinate.occurrence_id} positions ${mark.coordinate.start}–${mark.coordinate.end}`}</li>)}</ul>
      <h3>Saved views</h3><ul>{draft.views.map(view => <li key={view.id}>{view.name} ({view.artifact_id}) {view.artifact_id === artifactId && view.viewer && <button onClick={() => applyViewer(view.viewer!)}>Apply viewer</button>}</li>)}</ul>
      <h3>Compare retained alignments</h3><label>Right receipt ID <input value={rightReceipt} onChange={e => setRightReceipt(e.target.value)}/></label>{' '}<label>Right artifact ID <input value={rightArtifact} onChange={e => setRightArtifact(e.target.value)}/></label>{' '}
      <button disabled={busy || !rightReceipt || !rightArtifact || !name} onClick={() => void run(async () => { const result = (await api.post<{method: string; report: Record<string, unknown>}>(`${base}/compare`, {left_receipt_id: receiptId, left_artifact_id: artifactId, right_receipt_id: rightReceipt, right_artifact_id: rightArtifact})).data; setDraft(current => ({...current, comparisons: [...current.comparisons, {id: id(), name, left_artifact_id: artifactId, right_artifact_id: rightArtifact, method: result.method, report: result.report, provenance: {left_receipt_id: receiptId, right_receipt_id: rightReceipt}}]})) })}>Compare</button>
      <ul>{draft.comparisons.map(item => <li key={item.id}><details><summary>{item.name}: {item.left_artifact_id} ↔ {item.right_artifact_id}</summary><pre>{JSON.stringify(item.report, null, 2)}</pre></details></li>)}</ul>
      <button disabled={busy} onClick={() => void run(async () => received((await api.put<Revision>(`${base}/shared-state/${encodeURIComponent(revision.id)}`, {expected_head_generation: revision.head_generation, state: draft})).data))}>Save shared revision</button>
      <p>On a conflict your draft is retained. Reopen explicitly to discard it and load the latest revision.</p>
    </>}
    {error && <p role="alert">Shared state request failed (including revision conflicts): {error}</p>}
  </section>
}
