import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import AlignmentShared from '../../src/components/alignments/AlignmentShared'
import {api} from '../../src/lib/api'

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
