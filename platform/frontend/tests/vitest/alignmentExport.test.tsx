import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach, expect, it, vi} from 'vitest'
import AlignmentShared from '../../src/components/alignments/AlignmentShared'
import {api} from '../../src/lib/api'

vi.mock('../../src/lib/projectManager', () => ({
  listGlobalExperiments: vi.fn().mockResolvedValue([{id:'experiment', name:'Experiment'}]),
  listDomainExperiments: vi.fn().mockResolvedValue([{id:'domain', name:'Domain'}]),
}))
afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear()})

it('submits a managed export with Project selection, resumes once, refreshes manually and offers the retained ZIP', async () => {
  sessionStorage.clear()
  const operation = {job_id:'export-job', operation:'export', status:'queued', queue_status:'pending', artifact_url:null, error_message:null}
  const revision = {id:'saved', head_generation:1, revision_id:'rev', state:{schema:'bms.family-msa.shared-state.v1', marks:[], views:[], comparisons:[]}}
  const get = vi.spyOn(api, 'get').mockImplementation(async url => ({data:String(url).includes('/operations/') ? operation : revision}))
  const post = vi.spyOn(api, 'post').mockResolvedValue({data:operation})
  const div = document.createElement('div'); document.body.appendChild(div)
  let root = createRoot(div)
  const render = () => root.render(<AlignmentShared projectId="p" receiptId="receipt" artifactId="a" viewer="jbrowse" applyViewer={() => {}} importedStateIds={['saved']}/> )
  const button = (text: string) => [...div.querySelectorAll('button')].find(b => b.textContent === text)!
  await act(async () => render())
  await act(async () => button('Open imported saved state 1').click())
  await act(async () => {
    const selects = div.querySelectorAll('select')
    selects[0].value = 'domain'; selects[0].dispatchEvent(new Event('change', {bubbles:true}))
    selects[1].value = 'rna'; selects[1].dispatchEvent(new Event('change', {bubbles:true}))
  })
  await act(async () => button('Export result and saved shared state').click())
  expect(post).toHaveBeenCalledExactlyOnceWith('/api/family-msa/projects/p/operations', {operation:'export', molecule:'rna', domain_experiment_id:'domain', name:'Alignment project bundle', inputs:{receipt_id:'receipt', shared_state_ids:['saved']}})
  expect(div.textContent).toContain('queued (pending)')
  await act(async () => root.unmount())
  root = createRoot(div)
  await act(async () => render())
  await act(async () => button('Open imported saved state 1').click())
  expect(get.mock.calls.filter(([url]) => String(url).includes('/operations/'))).toHaveLength(1)
  vi.useFakeTimers()
  await act(async () => vi.advanceTimersByTime(3600000))
  expect(get.mock.calls.filter(([url]) => String(url).includes('/operations/'))).toHaveLength(1)
  get.mockResolvedValueOnce({data:{...operation, status:'completed', queue_status:null, artifact_url:'/api/family-msa/projects/p/operations/export-job/artifact'}})
  await act(async () => button('Refresh / resume export').click())
  expect(div.querySelector('a[download]')?.getAttribute('href')).toBe('/api/family-msa/projects/p/operations/export-job/artifact')
  expect(div.querySelector('a[download]')?.getAttribute('download')).toBe('alignment-project-bundle.zip')
  get.mockResolvedValueOnce({data:{...operation, status:'failed', error_message:'Worker failed'}})
  await act(async () => button('Refresh / resume export').click())
  expect(div.textContent).toContain('Worker failed')
  expect(div.querySelector('a[download]')).toBeNull()
  expect(post).toHaveBeenCalledTimes(1)
  await act(async () => root.unmount()); div.remove()
})
