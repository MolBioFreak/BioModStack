import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { errorText, statusText } from './alignmentText'
import type { AlignmentMolecule } from '../../lib/alignmentCoordinates'

type Operation = {job_id: string; operation: string; status: string; queue_status: string | null; artifact_url: string | null; error_message: string | null}

/** Explicit metadata refresh: reopening never starts a polling loop or rebuilds the ZIP. */
export default function AlignmentExport({projectId, receiptId, sharedStateId, domain, molecule}: {projectId: string; receiptId: string; sharedStateId: string; domain: string; molecule?: AlignmentMolecule}) {
  const base = `/api/family-msa/projects/${encodeURIComponent(projectId)}`
  const storageKey = `bms.alignment.export:${JSON.stringify([projectId, receiptId])}`
  const [jobId, setJobId] = useState('')
  const [operation, setOperation] = useState<Operation>()
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
        void api.get<Operation>(`${base}/operations/${encodeURIComponent(retained)}`, {signal: controller.signal}).then(({data}) => { if (live) setOperation(data) }).catch(e => { if (live) setError(errorText(e)) })
      }
    } catch (e) { setError(errorText(e)) }
    return () => { live = false; controller.abort() }
  }, [base, storageKey])
  function received(data: Operation) {
    setOperation(data); setJobId(data.job_id)
    try { sessionStorage.setItem(storageKey, data.job_id) } catch { setError('Couldn\'t remember this export in the browser; open it from Jobs later.') }
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('')
    try { await action() } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <section aria-label="Alignment export">
    {molecule ? <p>Export molecule: {molecule === 'protein' ? 'Protein' : molecule.toUpperCase()} · from the saved alignment</p> : <p role="alert">The saved alignment's molecule is unavailable. Reload the alignment before exporting.</p>}
    <button disabled={busy || !domain || !molecule} onClick={() => void run(async () => received((await api.post<Operation>(`${base}/operations`, {
      operation: 'export', molecule, domain_experiment_id: domain, name: 'Alignment project bundle', inputs: {receipt_id: receiptId, shared_state_ids: [sharedStateId]},
    })).data))}>Export ZIP</button>
    <p>Exports the last saved version. Choose an experiment above first.</p>
    <button disabled={busy || !jobId} onClick={() => void run(async () => received((await api.get<Operation>(`${base}/operations/${encodeURIComponent(jobId)}`)).data))}>Refresh export status</button>
    <details><summary>Resume another export</summary><label>Export job ID <input value={jobId} onChange={e => { setJobId(e.target.value); setOperation(undefined) }}/></label></details>
    {operation && <p role="status">Export: {statusText(operation.status)}{operation.queue_status && statusText(operation.queue_status) !== statusText(operation.status) ? ` (${statusText(operation.queue_status)})` : ''}. <a href={`/jobs/${encodeURIComponent(operation.job_id)}`} title={operation.job_id}>Open job</a>{operation.operation === 'export' && operation.artifact_url && <> · <a href={`${base}/operations/${encodeURIComponent(operation.job_id)}/artifact`} download="alignment-project-bundle.zip">Download ZIP</a></>}</p>}
    {(error || operation?.error_message) && <p role="alert">Export failed: {error || operation?.error_message}</p>}
  </section>
}
