import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import AlignmentWorkspace, {type AlignmentViewerProps} from '../../src/components/alignments/AlignmentWorkspace'
import {api} from '../../src/lib/api'

it('saves and reopens host view state across renderer switches and retains conflicts', async () => {
  sessionStorage.clear()
  const initial = {schema:'bms.family-msa.shared-state.v1', marks:[], views:[], comparisons:[]}
  let revision: any = {id:'ds', head_generation:1, revision_id:'one', state:initial}
  const get = vi.spyOn(api,'get').mockImplementation(async () => ({data:revision}))
  let conflict = false
  const put = vi.spyOn(api,'put').mockImplementation(async (_url, body: any) => {
    if (conflict) throw new Error('409 conflict')
    revision = {id:'ds', head_generation:2, revision_id:'two', state:body.state}; return {data:revision}
  })
  const Viewer = ({document}: AlignmentViewerProps) => <div data-testid="rows">{document.rows.map(r => r.rowId).join(',')}</div>
  const doc = {resultId:'art', rows:[{rowId:'a',label:'A',aligned:'A-C'}, {rowId:'b',label:'B',aligned:'ABC'}]}
  const div = document.createElement('div'); document.body.appendChild(div); let root = createRoot(div)
  const render = () => root.render(<AlignmentWorkspace projectId="p" receiptId="r" document={doc} viewers={{jbrowse:Viewer,biojs:Viewer}}/>)
  const input = (label:string,value:string) => { const el = [...div.querySelectorAll('label')].find(x => x.textContent?.includes(label))!.querySelector('input')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value); el.dispatchEvent(new Event('input',{bubbles:true})) }
  const select = (label:string,value:string) => { const el = label === 'Viewer' ? div.querySelector('#alignment-renderer') as HTMLSelectElement : [...div.querySelectorAll('label')].find(x => x.textContent?.includes(label))!.querySelector('select')!; el.value=value; el.dispatchEvent(new Event('change',{bubbles:true})) }
  const button = (text:string) => [...div.querySelectorAll('button')].find(x => x.textContent === text)!
  await act(async () => render())
  await act(async () => input('Shared Dataset ID','ds')); await act(async () => button('Open shared state').click())
  await act(async () => {select('Row','b'); input('Alignment column','2')}); await act(async () => button('Inspect').click())
  await act(async () => {const buttons = [...div.querySelectorAll('button')].filter(x => x.textContent === 'Move up'); buttons[1].click()})
  await act(async () => button('Hide').click())
  await act(async () => select('Viewer','biojs'))
  expect(div.querySelector('[data-testid="rows"]')!.textContent).toBe('a')
  await act(async () => input('Name','my view')); await act(async () => button('Save current viewer as view').click()); await act(async () => button('Save shared revision').click())
  expect(revision.state.views[0]).toMatchObject({row_order:['b','a'],hidden_row_ids:['b'],selected_row_id:'b',selected_column:1,viewer:'biojs'})
  await act(async () => root.unmount()); root = createRoot(div); await act(async () => render())
  await act(async () => input('Shared Dataset ID','ds')); await act(async () => button('Open shared state').click()); await act(async () => button('Apply viewer').click())
  expect(div.querySelector('[data-testid="rows"]')!.textContent).toBe('a'); expect(div.textContent).toContain('Row b, column 2')
  expect((div.querySelector('#alignment-renderer') as HTMLSelectElement).value).toBe('biojs')
  conflict = true; await act(async () => input('Name','conflict draft')); await act(async () => button('Save current viewer as view').click()); await act(async () => button('Save shared revision').click())
  expect(div.textContent).toContain('409 conflict'); expect(div.textContent).toContain('conflict draft')
  await act(async () => root.unmount()); div.remove(); get.mockRestore(); put.mockRestore(); sessionStorage.clear()
})
