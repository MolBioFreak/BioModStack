import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import AlignmentWorkspace from '../../src/components/alignments/AlignmentWorkspace'
import {api} from '../../src/lib/api'

it('keeps the draft on a revision conflict and saves a selected artifact column', async () => {
  const state = {schema:'bms.family-msa.shared-state.v1', marks:[], views:[], comparisons:[]}
  const get = vi.spyOn(api, 'get').mockResolvedValue({data:{id:'dataset', head_generation:2, revision_id:'rev', state}})
  const put = vi.spyOn(api, 'put').mockRejectedValue(new Error('409 conflict'))
  const div = document.createElement('div'); document.body.appendChild(div); const root = createRoot(div)
  await act(async () => root.render(<AlignmentWorkspace projectId="p" receiptId="receipt" document={{resultId:'artifact', rows:[{rowId:'r', label:'row', aligned:'A-C'}]}}/>))
  const set = (el: HTMLInputElement | HTMLSelectElement, value: string) => { const prototype = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('change', {bubbles:true})); el.dispatchEvent(new Event('input', {bubbles:true})) }
  const button = (text: string) => [...div.querySelectorAll('button')].find(b => b.textContent === text)!
  const labels = [...div.querySelectorAll('label')]
  const input = (text: string) => labels.find(x => x.textContent?.includes(text))!.querySelector('input, select') as HTMLInputElement
  await act(async () => {set(input('Shared Dataset ID'), 'dataset'); set(input('Row'), 'r')})
  await act(async () => {button('Open shared state').click(); button('Inspect').click()})
  await act(async () => {button('Add selected column mark').click()})
  await act(async () => {button('Save shared revision').click()})
  expect(put).toHaveBeenCalledWith('/api/family-msa/projects/p/shared-state/dataset', expect.objectContaining({expected_head_generation:2, state: expect.objectContaining({marks:[expect.objectContaining({coordinate:{scope:'artifact_columns',artifact_id:'artifact',start:0,end:0}})]})}))
  expect(div.textContent).toContain('409 conflict')
  expect(div.textContent).toContain('artifact columns 0–0')
  await act(async () => root.unmount()); div.remove(); get.mockRestore(); put.mockRestore()
})
