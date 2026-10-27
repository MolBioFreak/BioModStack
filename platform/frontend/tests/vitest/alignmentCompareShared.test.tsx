import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import AlignmentShared from '../../src/components/alignments/AlignmentShared'
import {api} from '../../src/lib/api'

vi.mock('../../src/lib/projectManager', () => ({
  listGlobalExperiments: vi.fn().mockResolvedValue([{id:'experiment', name:'Experiment'}]),
  listDomainExperiments: vi.fn().mockResolvedValue([{id:'domain', name:'Domain'}]),
}))

it('requests a backend comparison and retains its returned report in the shared revision', async () => {
  const state = {schema:'bms.family-msa.shared-state.v1', marks:[], views:[], comparisons:[]}
  const get = vi.spyOn(api, 'get').mockResolvedValue({data:{id:'dataset', head_generation:2, revision_id:'rev', state}})
  const post = vi.spyOn(api, 'post').mockResolvedValue({data:{method:'explicit-source-occurrence.v1', report:{rows:[{source_member_id:'m'}]}}})
  const put = vi.spyOn(api, 'put').mockImplementation(async (_url, payload) => ({data:{id:'dataset', head_generation:3, revision_id:'new', state:(payload as {state: unknown}).state}}))
  const div = document.createElement('div'); document.body.appendChild(div); const root = createRoot(div)
  await act(async () => root.render(<AlignmentShared projectId="p" receiptId="l" artifactId="a" viewer="jbrowse" applyViewer={() => {}}/>))
  const set = (label: string, value: string) => { const el = [...div.querySelectorAll('label')].find(x => x.textContent?.includes(label))!.querySelector('input')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input',{bubbles:true})) }
  const button = (text: string) => [...div.querySelectorAll('button')].find(b => b.textContent === text)!
  await act(async () => set('Shared Dataset ID','dataset'))
  await act(async () => button('Open shared state').click())
  await act(async () => {set('Name','Comparison'); set('Right receipt ID','r'); set('Right artifact ID','b')})
  await act(async () => button('Compare').click())
  expect(post).toHaveBeenCalledWith('/api/family-msa/projects/p/compare', {left_receipt_id:'l', left_artifact_id:'a', right_receipt_id:'r', right_artifact_id:'b'})
  await act(async () => button('Save shared revision').click())
  expect(put).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({expected_head_generation:2, state:expect.objectContaining({comparisons:[expect.objectContaining({method:'explicit-source-occurrence.v1', report:{rows:[{source_member_id:'m'}]}})]})}))
  await act(async () => root.unmount()); div.remove(); get.mockRestore(); post.mockRestore(); put.mockRestore()
})


it('retains edits made during a save, switches datasets, and restores the draft after unmount', async () => {
  sessionStorage.clear()
  const state = {schema:'bms.family-msa.shared-state.v1', marks:[], views:[], comparisons:[]}
  const get = vi.spyOn(api, 'get').mockImplementation(async url => ({data:{id:String(url).split('/').pop(), head_generation:2, revision_id:'rev', state}}))
  const post = vi.spyOn(api, 'post').mockResolvedValue({data:{id:'created', head_generation:1, revision_id:'created-rev', state}})
  let finish!: (value: any) => void
  const put = vi.spyOn(api, 'put').mockImplementationOnce(() => new Promise(resolve => {finish = resolve}))
    .mockImplementation(async (_url, payload) => ({data:{id:'one', head_generation:4, revision_id:'next', state:(payload as {state: unknown}).state}}))
  const div = document.createElement('div'); document.body.appendChild(div); const root = createRoot(div)
  const render = (artifactId: string) => root.render(<AlignmentShared projectId="p" receiptId="l" artifactId={artifactId} viewer="jbrowse" applyViewer={() => {}}/>)
  const set = (label: string, value: string) => { const el = [...div.querySelectorAll('label')].find(x => x.textContent?.includes(label))!.querySelector('input')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input',{bubbles:true})) }
  const button = (text: string) => [...div.querySelectorAll('button')].find(b => b.textContent === text)!
  await act(async () => render('a'))
  await act(async () => set('Shared Dataset ID', 'one'))
  await act(async () => button('Open shared state').click())
  await act(async () => set('Name', 'first'))
  await act(async () => button('Save current viewer as view').click())
  await act(async () => button('Save shared revision').click())
  await act(async () => {set('Name', 'second'); render('b')})
  await act(async () => button('Save current viewer as view').click())
  const submitted = (put.mock.calls[0][1] as {state: unknown}).state
  await act(async () => finish({data:{id:'one', head_generation:3, revision_id:'new', state:submitted}}))
  expect(div.textContent).toContain('second (b)')
  await act(async () => {set('Shared Dataset ID', 'two')})
  await act(async () => button('Open shared state').click())
  expect(div.textContent).not.toContain('second (b)')
  await act(async () => {set('Shared Dataset ID', 'one')})
  await act(async () => button('Open shared state').click())
  expect(div.textContent).toContain('second (b)')
  expect(div.textContent).toContain('generation 3')
  await act(async () => {
    const select = [...div.querySelectorAll('label')].find(x => x.textContent?.includes('Destination domain'))!.querySelector('select')!
    select.value = 'domain'; select.dispatchEvent(new Event('change', {bubbles:true}))
  })
  await act(async () => button('Create shared state').click())
  expect(div.textContent).toContain('Dataset created')
  await act(async () => set('Shared Dataset ID', 'one'))
  await act(async () => button('Open shared state').click())
  expect(div.textContent).toContain('second (b)')
  await act(async () => root.unmount())
  const again = createRoot(div)
  await act(async () => again.render(<AlignmentShared projectId="p" receiptId="l" artifactId="b" viewer="jbrowse" applyViewer={() => {}}/>))
  await act(async () => set('Shared Dataset ID', 'one'))
  await act(async () => button('Open shared state').click())
  expect(div.textContent).toContain('second (b)')
  await act(async () => button('Save shared revision').click())
  expect(put.mock.calls[1][1]).toEqual(expect.objectContaining({expected_head_generation:3, state:expect.objectContaining({views:[expect.objectContaining({name:'first'}), expect.objectContaining({name:'second'})]})}))
  await act(async () => again.unmount()); div.remove(); get.mockRestore(); put.mockRestore(); post.mockRestore(); sessionStorage.clear()
})
