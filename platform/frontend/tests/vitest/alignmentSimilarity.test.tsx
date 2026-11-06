import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {expect, it, vi} from 'vitest'
import type {AlignmentDocument} from '../../src/lib/alignmentCoordinates'
import AlignmentWorkspace from '../../src/components/alignments/AlignmentWorkspace'
import AlignmentSimilaritySummary from '../../src/components/alignments/AlignmentSimilaritySummary'
import ProjectAlignments from '../../src/components/alignments/ProjectAlignments'
import {api} from '../../src/lib/api'

it('shows precise pair identity and undefined denominators without interpreting labels as identity', async () => {
  const rootElement = document.createElement('div'), root = createRoot(rootElement)
  await act(async () => root.render(<AlignmentSimilaritySummary document={{resultId: 'r', rows: [{rowId: 'a', label: 'First', aligned: 'A-'}, {rowId: 'b', label: 'Second', aligned: '-T'}], similarity: {policy: 'Exclude gaps', pairs_complete: true, pair_count: 1, pair_count_limit: 200, pair_cell_limit: 5000000, mean_pairwise_identity_percent: null, identical_columns: 0, compared_columns: 0, conserved_column_percent: null, pairs: [{left_row_id: 'a', right_row_id: 'b', identical: 0, compared: 0, identity_percent: null}]}}}/>))
  expect(rootElement.textContent).toContain('Identity at nongap positions: N/A')
  expect(rootElement.textContent).toContain('First ↔ Second')
  expect(rootElement.textContent).toContain('no comparable positions')
  expect(rootElement.textContent).toContain('100% identity here does not mean the full sequences are identical')
  expect(rootElement.textContent).toContain('Matching ambiguity symbols, such as N, do not confirm a known base')
  expect(rootElement.textContent).not.toContain('above 95%')
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


function comparisonFixture(): AlignmentDocument {
  const rows = Array.from({length: 8}, (_, i) => ({rowId: `r${i}`, label: `Sample ${i + 1}`, aligned: 'AC'}))
  return {resultId: 'eight', rows, similarity: {
    policy: 'Nongap positions', pairs_complete: true, pair_count: 28, pair_count_limit: 200,
    pair_cell_limit: 5000000, mean_pairwise_identity_percent: 100, identical_columns: 2,
    compared_columns: 2, conserved_column_percent: 100,
    pairs: rows.flatMap((a, i) => rows.slice(i + 1).map(b => ({left_row_id: a.rowId, right_row_id: b.rowId,
      identical: 2, compared: 2, identity_percent: 100, left_length: 2, right_length: 2,
      left_aligned_percent: 100, right_aligned_percent: 100,
      left_map: [[1, 0, 0], [1, 0, 0]], right_map: [[1, 0, 0], [1, 0, 0]],
    }))),
  }}
}

it('shows seven partners for eight sequences and reverses coverage/map when the reference changes', async () => {
  const el = document.createElement('div'), root = createRoot(el), doc = comparisonFixture()
  Object.assign(doc.similarity!.pairs[0], {left_length: 2, right_length: 4, right_aligned_percent: 50, right_map: [[1, 0, 1], [1, 0, 1]]})
  try {
    await act(async () => root.render(<AlignmentSimilaritySummary document={doc}/>))
    const table = () => el.querySelector('.alignment-comparison-table')!
    expect(table().querySelectorAll('tbody tr')).toHaveLength(7)
    expect(el.querySelector('h4')!.textContent).toBe('Reference: Sample 1 · 7 comparisons')
    expect([...table().querySelectorAll('tbody tr')[0].querySelectorAll('td')].slice(1).map(td => td.textContent)).toEqual(['100.00%', '100.00%', '50.00%', '2 / 2'])
    expect(el.querySelector('details')!.open).toBe(false)
    await act(async () => (el.querySelector('.alignment-reference-list button:nth-child(2)') as HTMLButtonElement).click())
    expect(el.querySelector('h4')!.textContent).toBe('Reference: Sample 2 · 7 comparisons')
    expect(table().querySelectorAll('tbody tr')).toHaveLength(7)
    expect([...table().querySelectorAll('tbody tr')[0].querySelectorAll('td')].slice(1).map(td => td.textContent)).toEqual(['100.00%', '50.00%', '100.00%', '2 / 2'])
    expect(table().querySelector('svg title')!.textContent).toContain('Position 1 to 4')
    expect([...table().querySelectorAll('.alignment-sequence-link')].map(b => b.textContent)).not.toContain('Sample 2')
  } finally { await act(async () => root.unmount()) }
})

it('uses occurrence ids for repeated labels and updates the reference from a viewer click', async () => {
  const el = document.createElement('div'), root = createRoot(el), doc = comparisonFixture()
  doc.rows[0].label = doc.rows[1].label = 'Same name'
  try {
    await act(async () => root.render(<AlignmentWorkspace document={doc} viewers={{jbrowse: props => <button onClick={() => props.onSelect({resultId: doc.resultId, rowId: 'r1', column: 0, residue: 0})}>Click viewer row two</button>}}/>))
    expect(el.querySelector('h4')!.textContent).toContain('Same name (row 1)')
    await act(async () => ([...el.querySelectorAll('button')].find(b => b.textContent === 'Click viewer row two')!).click())
    expect(el.querySelector('h4')!.textContent).toContain('Same name (row 2)')
    expect(el.querySelector('.alignment-comparison-table tbody tr')!.textContent).toContain('Same name (row 1)')
  } finally { await act(async () => root.unmount()) }
})
