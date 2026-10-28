import React, {Suspense, act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
const loaded = vi.hoisted(() => ({jbrowse: 0, av2: 0, biojs: 0}))
vi.mock('../../src/components/alignments/JbrowseViewer', () => {loaded.jbrowse++; return {default: () => <p>jbrowse</p>}})
vi.mock('../../src/components/alignments/Av2Viewer', () => {loaded.av2++; return {default: () => <p>av2</p>}})
vi.mock('../../src/components/alignments/BiojsViewer', () => {loaded.biojs++; return {default: () => <p>biojs</p>}})
import {alignmentViewers} from '../../src/components/alignments/lazyViewers'
it('imports no renderer with the registry and only resolves the mounted choice', async () => {
  expect(loaded).toEqual({jbrowse: 0, av2: 0, biojs: 0})
  const el = document.createElement('div'); const root = createRoot(el)
  const JBrowse = alignmentViewers.jbrowse!, Biojs = alignmentViewers.biojs!
  const props = {document: {resultId: 'x', rows: []}, onSelect: () => {}}
  await act(async () => {root.render(<Suspense><JBrowse {...props}/></Suspense>)})
  expect(loaded).toEqual({jbrowse: 1, av2: 0, biojs: 0})
  await act(async () => {root.render(<Suspense><Biojs {...props}/></Suspense>)})
  expect(loaded).toEqual({jbrowse: 1, av2: 0, biojs: 1})
  await act(async () => root.unmount())
})
