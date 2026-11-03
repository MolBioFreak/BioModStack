import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {expect, it, vi} from 'vitest'
import AlignmentSimilaritySummary from '../../src/components/alignments/AlignmentSimilaritySummary'
import ProjectAlignments from '../../src/components/alignments/ProjectAlignments'
import {api} from '../../src/lib/api'

it('shows precise pair identity and undefined denominators without interpreting labels as identity', async () => {
  const rootElement = document.createElement('div'), root = createRoot(rootElement)
  await act(async () => root.render(<AlignmentSimilaritySummary document={{resultId: 'r', rows: [{rowId: 'a', label: 'First', aligned: 'A-'}, {rowId: 'b', label: 'Second', aligned: '-T'}], similarity: {policy: 'Exclude gaps', pairs_complete: true, pair_count: 1, pair_count_limit: 200, pair_cell_limit: 5000000, mean_pairwise_identity_percent: null, identical_columns: 0, compared_columns: 0, conserved_column_percent: null, pairs: [{left_row_id: 'a', right_row_id: 'b', identical: 0, compared: 0, identity_percent: null}]}}}/>))
  expect(rootElement.textContent).toContain('Sequence identity: N/A')
  expect(rootElement.textContent).toContain('First ↔ Second')
  expect(rootElement.textContent).toContain('no comparable positions')
  await act(async () => root.unmount())
})

it('exposes retained imported alignments directly in the Project and preserves exact routing', async () => {
  const get = vi.spyOn(api, 'get').mockResolvedValue({data: {items: [{receipt_id: 'saved', entity_kind: 'family_msa_import', availability: 'available', label: 'Sequence A / Sequence B', row_count: 2, molecule: 'dna'}], next_cursor: null}})
  const el = document.createElement('div'), root = createRoot(el)
  try {
    await act(async () => root.render(<MemoryRouter><ProjectAlignments projectId="project"/></MemoryRouter>))
    expect(get).toHaveBeenCalledWith('/api/family-msa/projects/project/receipts')
    expect(el.querySelector('a[href="/alignments/results/saved?project_id=project"]')?.textContent).toBe('Sequence A / Sequence B')
    expect(el.textContent).toContain('2 sequences · DNA')
  } finally { await act(async () => root.unmount()); get.mockRestore() }
})
