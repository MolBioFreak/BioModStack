import { useEffect, useState } from 'react'
import { api } from '../../lib/api'

type Operation = {job_id: string; operation: string; status: string; queue_status: string | null; artifact_url: string | null; error_message: string | null}

/** Explicit metadata refresh: reopening never starts a polling loop or rebuilds the ZIP. */
export default function AlignmentExport({projectId, receiptId, sharedStateId, domain}: {projectId: string; receiptId: string; sharedStateId: string; domain: string}) {
  const base = `/api/family-msa/projects/${encodeURIComponent(projectId)}`
  const storageKey = `bms.alignment.export:${JSON.stringify([projectId, receiptId])}`
  const [jobId, setJobId] = useState('')
  const [operation, setOperation] = useState<Operation>()
  const [molecule, setMolecule] = useState<'protein' | 'dna' | 'rna'>('protein')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    const controller = new AbortController()
    setOperation(undefined); setJobId(''); setError('')
    try {
      const retained = sessionStorage.getItem(storageKey)
      if (retained) {
        setJobId(retained)
        void api.get<Operation>(`${base}/operations/${encodeURIComponent(retained)}`, {signal: controller.signal}).then(({data}) => { if (live) setOperation(data) }).catch(e => { if (live) setError(String(e)) })
      }
    } catch (e) { setError(String(e)) }
    return () => { live = false; controller.abort() }
  }, [base, storageKey])
  function received(data: Operation) {
    setOperation(data); setJobId(data.job_id)
    try { sessionStorage.setItem(storageKey, data.job_id) } catch { setError('Could not retain the export Job in this browser. Keep the Job ID to resume it.') }
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('')
    try { await action() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <section aria-label="Managed alignment export">
    <label>Export molecule <select value={molecule} onChange={e => setMolecule(e.target.value as typeof molecule)}><option value="protein">Protein</option><option value="dna">DNA</option><option value="rna">RNA</option></select></label>{' '}
    <button disabled={busy || !domain} onClick={() => void run(async () => received((await api.post<Operation>(`${base}/operations`, {
      operation: 'export', molecule, domain_experiment_id: domain, name: 'Alignment project bundle', inputs: {receipt_id: receiptId, shared_state_ids: [sharedStateId]},
    })).data))}>Export result and saved shared state</button>
    <p>Choose the destination domain above. Export runs as a managed Job and includes the saved revision, not unsaved edits. Import the ZIP from the Project alignment library.</p>
    <label>Export Job ID <input value={jobId} onChange={e => { setJobId(e.target.value); setOperation(undefined) }}/></label>{' '}
    <button disabled={busy || !jobId} onClick={() => void run(async () => received((await api.get<Operation>(`${base}/operations/${encodeURIComponent(jobId)}`)).data))}>Refresh / resume export</button>
    <p>Status refresh is manual; the last export Job is retained in this browser session.</p>
    {operation && <p role="status">Managed {operation.operation} Job {operation.job_id}: {operation.status}{operation.queue_status ? ` (${operation.queue_status})` : ''}. <a href={`/jobs/${encodeURIComponent(operation.job_id)}`}>Open export Job</a>{operation.operation === 'export' && operation.artifact_url && <> · <a href={`${base}/operations/${encodeURIComponent(operation.job_id)}/artifact`} download="alignment-project-bundle.zip">Download result and saved shared state</a></>}</p>}
    {(error || operation?.error_message) && <p role="alert">Export request: {error || operation?.error_message}</p>}
  </section>
}
