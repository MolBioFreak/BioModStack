import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import ViewerFrame from '../../src/components/alignments/ViewerFrame'
import {projectMarks} from '../../src/components/alignments/viewerState'
import type {Mark} from '../../src/components/alignments/AlignmentShared'

const doc = {resultId: 'artifact', rows: [
  {rowId: 'b', label: 'same', aligned: 'A-C', sourceMemberId: 'source', occurrenceId: 'two', sourcePositions: [10, null, 12]},
  {rowId: 'a', label: 'same', aligned: 'A-C', sourceMemberId: 'source', occurrenceId: 'one', sourcePositions: [10, null, 12]},
  {rowId: 'ambiguous', label: 'same', aligned: 'ACC', sourceMemberId: 'source', occurrenceId: 'one', sourcePositions: [10, 12, 12]},
]}
const mark: Mark = {id: 'mark', label: 'mapped', coordinate: {scope: 'source_positions', source_member_id: 'source', occurrence_id: 'one', start: 12, end: 12}, note: '', provenance: {}}
it('projects marks only through verified occurrence maps after reorder; no duplicate/gap inference', () => {
  expect(projectMarks(doc, [mark])).toEqual([{id: 'mark', label: 'mapped', index: 1, start: 2, end: 2, residue: 1}])
  expect(projectMarks({...doc, rows: [doc.rows[0]]}, [mark])).toEqual([])
  expect(projectMarks(doc, [{...mark, coordinate: {scope: 'artifact_columns', artifact_id: 'other', start: 0, end: 2}}])).toEqual([])
})
it('sends restored selection, marks and renderer state to the authenticated frame without reloading rows', async () => {
  const el = document.createElement('div'); document.body.append(el); const root = createRoot(el)
  const onSelect = vi.fn(), onRendererStateChange = vi.fn()
  const render = (state: any) => root.render(<ViewerFrame document={doc} code="" channel="test" title="test" notice="test" rendererState={state} selection={{resultId: 'artifact', rowId: 'a', column: 2, residue: 1}} marks={[mark]} onSelect={onSelect} onRendererStateChange={onRendererStateChange}/>)
  await act(async () => render({scrollX: 30, colorScheme: 'taylor'}))
  const frame = el.querySelector('iframe')!
  const parsed = new DOMParser().parseFromString(frame.srcdoc, 'text/html')
  const payload = JSON.parse(parsed.querySelector('#payload')!.textContent!)
  expect(payload.selection).toEqual({index: 1, column: 2})
  expect(payload.marks).toEqual(projectMarks(doc, [mark]))
  const post = vi.spyOn(frame.contentWindow!, 'postMessage')
  const message = (data: any, source: Window | null = frame.contentWindow) => window.dispatchEvent(new MessageEvent('message', {source, data: {channel: 'test', token: payload.token, ...data}}))
  await act(async () => {message({type: 'ready'}); message({type: 'state', state: {scrollX: 99}})})
  expect(onRendererStateChange).toHaveBeenCalledWith({scrollX: 99})
  await act(async () => render({scrollX: 55, hiddenColumns: [1]}))
  expect(el.querySelector('iframe')).toBe(frame)
  expect(frame.srcdoc).toContain('"scrollX":30')
  expect(post.mock.calls.at(-1)?.[0]).toMatchObject({type: 'control', state: {scrollX: 55, hiddenColumns: [1]}, selection: {index: 1, column: 2}})
  expect(post.mock.calls.at(-1)?.[0]).not.toHaveProperty('rows')
  await act(async () => {message({index: 1, column: 2}, window); message({index: 1, column: 2, token: 'wrong'}); message({index: 1, column: 2})})
  expect(onSelect).toHaveBeenCalledTimes(1)
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({rowId: 'a', sourcePosition: 12, occurrenceId: 'one'}))
  await act(async () => root.unmount()); el.remove()
})
