import React, {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import {EnsembleSupport} from '../../src/components/alignments/AlignmentPage'
import {api} from '../../src/lib/api'

it('computes selected samples only on request and displays identities and denominators', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({data:{receipt_id:'r', reference_artifact_id:'a', report:{method:'retention.v1', interpretation:'not probability of alignment truth', denominator_policy:'missing mappings excluded', sample_count:1, numerator:2, denominator:3, fraction:2/3, samples:[{artifact_id:'b', logical_path:'ensemble.b.fasta', numerator:2, denominator:3, fraction:2/3, matched_rows:2, rows_with_unknown_mapping:1}]}}})
  const div = document.createElement('div'); document.body.appendChild(div); const root = createRoot(div)
  await act(async () => root.render(<EnsembleSupport projectId="p" receiptId="r" reference="a" artifacts={[{id:'a',logical_path:'ref'}, {id:'b',logical_path:'ensemble.b.fasta'}]}/>))
  expect(post).not.toHaveBeenCalled()
  await act(async () => (div.querySelectorAll('input')[1] as HTMLInputElement).click())
  await act(async () => [...div.querySelectorAll('button')].find(x => x.textContent === 'Compute ensemble support')!.click())
  expect(post).toHaveBeenCalledWith('/api/family-msa/projects/p/ensemble-support', {receipt_id:'r', reference_artifact_id:'a', member_artifact_ids:['b']})
  expect(div.textContent).toContain('ensemble.b.fasta (b): 2 / 3')
  expect(div.textContent).toContain('samples: 1')
  expect(div.textContent).toContain('not probability of alignment truth')
  await act(async () => root.unmount()); div.remove(); post.mockRestore()
})
