import { useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import AlignmentExport from './AlignmentExport'
import { listGlobalExperiments, listDomainExperiments } from '../../lib/projectManager'
import type { RendererState } from './viewerState'
import { errorText } from './alignmentText'
import type { ResidueAnchor } from '../../lib/alignmentCoordinates'

type Coordinate = {scope: 'artifact_columns'; artifact_id: string; start: number; end: number} | {scope: 'source_positions'; source_member_id: string; occurrence_id: string; start: number; end: number}
export type Mark = {id: string; label: string; coordinate: Coordinate; note: string; provenance: Record<string, unknown>}
export type View = {id: string; name: string; artifact_id: string; hidden_row_ids: string[]; row_order: string[]; selected_marks: string[]; viewer: string | null; selected_row_id?: string | null; selected_column?: number | null; renderer_state?: RendererState}
type Agreement = {method: string; matched_rows: number; shared_mapped_residues: number; left_unmapped_residues: number; right_unmapped_residues: number; rows_with_unknown_mapping: number; left_aligned_pairs: number; right_aligned_pairs: number; shared_aligned_pairs: number; union_aligned_pairs: number; jaccard: number | null}
type ComparisonReport = {agreement?: Agreement; left_excluded: number; right_excluded: number; [key: string]: unknown}
type Comparison = {id: string; name: string; left_artifact_id: string; right_artifact_id: string; method: string; report: ComparisonReport; provenance: Record<string, unknown>}
type State = {schema: 'bms.family-msa.shared-state.v1'; marks: Mark[]; views: View[]; comparisons: Comparison[]}
type Revision = {id: string; head_generation: number; revision_id: string; state: State}
const empty = (): State => ({schema: 'bms.family-msa.shared-state.v1', marks: [], views: [], comparisons: []})
const id = () => crypto.randomUUID()

export default function AlignmentShared({projectId, receiptId, artifactId, selection, viewer, applyViewer, rowOrder = [], hiddenRowIds = [], selectedRowId, applyView, importedStateIds = [], rendererState, onMarksChange, selectedMarks = [], onSelectedMarksChange}: {projectId: string; receiptId: string; artifactId: string; selection?: ResidueAnchor; viewer: string; applyViewer: (viewer: string) => void; rowOrder?: string[]; hiddenRowIds?: string[]; selectedRowId?: string; applyView?: (view: View) => void; importedStateIds?: string[]; rendererState?: RendererState; onMarksChange?: (marks: Mark[]) => void; selectedMarks?: string[]; onSelectedMarksChange?: (ids: string[]) => void}) {
  const base = `/api/family-msa/projects/${encodeURIComponent(projectId)}`
  const [dataset, setDataset] = useState('')
  const [domain, setDomain] = useState('')
  const [domains, setDomains] = useState<{id: string; label: string}[]>([])
  useEffect(() => { let live = true; setDomain(''); setDomains([])
    void (async () => { try {
      const experiments = await listGlobalExperiments(projectId)
      const groups = await Promise.all(experiments.map(async experiment => (await listDomainExperiments(projectId, experiment.id)).map(item => ({id: item.id, label: `${experiment.name} / ${item.name}`}))))
      if (live) setDomains(groups.flat())
    } catch (e) { if (live) setError(errorText(e)) } })()
    return () => { live = false }
  }, [projectId])
  const [revision, setRevision] = useState<Revision>()
  const [draft, setDraft] = useState<State>(empty)
  useEffect(() => { onMarksChange?.(draft.marks) }, [draft.marks, onMarksChange])
  const draftRef = useRef(draft)
  const revisionRef = useRef(revision)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const storageKey = (datasetId: string) => `bms.alignment.draft:${JSON.stringify([projectId, datasetId])}`
  function persist(rev: Revision, state: State) {
    try { sessionStorage.setItem(storageKey(rev.id), JSON.stringify({revision: rev, draft: state})) }
    catch { setError('Couldn\'t keep unsaved changes in this tab; save before leaving the page.') }
  }
  function edit(update: (current: State) => State) {
    const next = update(draftRef.current)
    draftRef.current = next; setDraft(next)
    if (revisionRef.current) persist(revisionRef.current, next)
  }
  const [name, setName] = useState('')

  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function run(fn: () => Promise<void>) { setBusy(true); setError(''); try { await fn() } catch (e) { setError(errorText(e)) } finally { setBusy(false) } }
  function received(value: Revision) {
    let saved: {revision: Revision; draft: State} | undefined
    try { const raw = sessionStorage.getItem(storageKey(value.id)); if (raw) saved = JSON.parse(raw) } catch { setError('Couldn\'t read your unsaved changes.') }
    const rev = saved?.revision ?? value
    const state = saved?.draft ?? value.state
    revisionRef.current = rev; draftRef.current = state
    setRevision(rev); setDataset(value.id); setDraft(state)
  }
  return <section aria-label="Notes and saved views">
    <h2>Notes and saved views</h2><p>Open or create a notes set to add marks and saved views.</p>
    {importedStateIds.map((stateId, index) => <button key={stateId} disabled={busy} onClick={() => void run(async () => received((await api.get<Revision>(`${base}/shared-state/${encodeURIComponent(stateId)}`)).data))}>Open imported notes {index + 1}</button>)}
    <label>Notes ID <input value={dataset} onChange={e => setDataset(e.target.value)}/></label>{' '}
    <button disabled={busy || !dataset} onClick={() => void run(async () => received((await api.get<Revision>(`${base}/shared-state/${encodeURIComponent(dataset)}`)).data))}>Open notes</button>{' '}
    <label>Save to experiment <select value={domain} onChange={e => setDomain(e.target.value)}><option value="">Choose experiment</option>{domains.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>{' '}
    <button disabled={busy || !domain} onClick={() => void run(async () => received((await api.post<Revision>(`${base}/shared-state`, {name: name || 'Alignment shared state', domain_experiment_id: domain, state: empty()})).data))}>Create notes</button>
    {revision && <>
      <p title={`Dataset ${revision.id} · revision ${revision.revision_id}`}>Saved version {revision.head_generation}</p>
      <AlignmentExport key={`${projectId}:${receiptId}`} projectId={projectId} receiptId={receiptId} sharedStateId={revision.id} domain={domain}/>
      <label>Name <input value={name} onChange={e => setName(e.target.value)}/></label>{' '}
      <button disabled={!selection || selection.resultId !== artifactId} onClick={() => { if (selection) edit(current => ({...current, marks: [...current.marks, {id: id(), label: name, coordinate: {scope: 'artifact_columns', artifact_id: artifactId, start: selection.column, end: selection.column}, note: '', provenance: {}}]})) }}>Mark selected column</button>{' '}
      <button disabled={!selection || selection.resultId !== artifactId || selection.sourcePosition === undefined || !selection.sourceMemberId || !selection.occurrenceId} onClick={() => { if (selection?.sourcePosition !== undefined && selection.sourceMemberId && selection.occurrenceId) edit(current => ({...current, marks: [...current.marks, {id: id(), label: name, coordinate: {scope: 'source_positions', source_member_id: selection.sourceMemberId!, occurrence_id: selection.occurrenceId!, start: selection.sourcePosition!, end: selection.sourcePosition!}, note: '', provenance: {}}]})) }}>Mark selected sequence position</button>{' '}
      <button disabled={!name} onClick={() => edit(current => ({...current, views: [...current.views, {id: id(), name, artifact_id: artifactId, hidden_row_ids: hiddenRowIds, row_order: rowOrder, selected_marks: selectedMarks, ...(rendererState ? {renderer_state: rendererState} : {}), viewer, selected_row_id: selection?.rowId ?? (selectedRowId || null), selected_column: selection?.column ?? null}]}))}>Save current view</button>
      <h3>Marks</h3><ul>{draft.marks.map(mark => <li key={mark.id}><label><input type="checkbox" checked={selectedMarks.includes(mark.id)} onChange={e => onSelectedMarksChange?.(e.target.checked ? [...selectedMarks, mark.id] : selectedMarks.filter(id => id !== mark.id))} /> Draw {mark.label}</label>: <span title={mark.coordinate.scope === 'artifact_columns' ? mark.coordinate.artifact_id : `${mark.coordinate.source_member_id} / ${mark.coordinate.occurrence_id}`}>{mark.coordinate.scope === 'artifact_columns' ? 'columns' : 'sequence positions'} {mark.coordinate.start + 1}–{mark.coordinate.end + 1}</span></li>)}</ul>
      <p>A saved view keeps row order, hidden rows, selection, marks and viewer settings.</p><h3>Saved views</h3><ul>{draft.views.map(view => <li key={view.id}><span title={view.artifact_id}>{view.name}</span> {view.artifact_id === artifactId && view.viewer && <button onClick={() => { if (applyView) applyView(view); else applyViewer(view.viewer!) }}>Apply viewer</button>}</li>)}</ul>
      <h3>Comparisons</h3><p><a href={`/alignments/compare?project_id=${encodeURIComponent(projectId)}&shared_state_id=${encodeURIComponent(revision.id)}`}>Compare with another alignment →</a></p>
      <ul>{draft.comparisons.map(item => <li key={item.id}><details><summary title={`${item.left_artifact_id} ↔ ${item.right_artifact_id}`}>{item.name}</summary>{item.report.agreement && <p>Agreement: {item.report.agreement.jaccard === null ? '—' : item.report.agreement.jaccard.toFixed(3)} ({item.report.agreement.shared_aligned_pairs} of {item.report.agreement.union_aligned_pairs} pairs). {item.report.agreement.matched_rows} rows matched.</p>}</details></li>)}</ul>
      <button disabled={busy} onClick={() => void run(async () => {
        const submitted = draftRef.current
        const value = (await api.put<Revision>(`${base}/shared-state/${encodeURIComponent(revision.id)}`, {expected_head_generation: revision.head_generation, state: submitted})).data
        // A later mount may already be editing the persisted draft. Do not erase it.
        if (!mounted.current) return
        const current = draftRef.current === submitted ? value.state : draftRef.current
        revisionRef.current = value; draftRef.current = current
        setRevision(value); setDraft(current)
        if (current === value.state) { try { sessionStorage.removeItem(storageKey(value.id)) } catch { /* no retained draft */ } }
        else persist(value, current)
      })}>Save notes</button>
      <p>Unsaved changes are kept in this browser tab.</p>
    </>}
    {error && <p role="alert">Couldn't save or load notes: {error}. If someone else changed them, reload and try again.</p>}
  </section>
}
