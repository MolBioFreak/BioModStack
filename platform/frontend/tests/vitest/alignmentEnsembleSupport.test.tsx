import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {expect, it, vi} from 'vitest';
import {EnsembleSupport} from '../../src/components/alignments/AlignmentPage';
import {api} from '../../src/lib/api';

vi.mock('../../src/lib/projectManager', async importOriginal => ({...await importOriginal<object>(),
  listGlobalExperiments: vi.fn().mockResolvedValue([{id: 'e', name: 'Experiment'}]),
  listDomainExperiments: vi.fn().mockResolvedValue([{id: 'd', name: 'Domain'}]),
}));

it('queues selected samples only on request and displays managed report identities and denominators', async () => {
  const report = {receipt_id:'r', reference_artifact_id:'a', report:{method:'retention.v1', interpretation:'not probability of alignment truth', denominator_policy:'missing mappings excluded', sample_count:1, numerator:2, denominator:3, fraction:2/3, samples:[{artifact_id:'b', logical_path:'ensemble.b.fasta', numerator:2, denominator:3, fraction:2/3, matched_rows:2, rows_with_unknown_mapping:1}]}};
  const post = vi.spyOn(api, 'post').mockResolvedValue({data:{job_id: 'j'}});
  const get = vi.spyOn(api, 'get').mockImplementation(async url => ({data: String(url).endsWith('/artifact') ? report : {status: 'completed', artifact_url: '/api/artifact', error_message: null}}));
  const cache = new QueryClient({defaultOptions: {queries: {retry: false}}});
  const div = document.createElement('div'); document.body.appendChild(div); const root = createRoot(div);
  const settle = async () => { await act(async () => {await new Promise(resolve => setTimeout(resolve, 30));}); };
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><EnsembleSupport projectId="p" receiptId="r" reference="a" artifacts={[{id:'a',logical_path:'ref'}, {id:'b',logical_path:'ensemble.b.fasta'}]}/></QueryClientProvider>)); await settle();
    expect(post).not.toHaveBeenCalled();
    await act(async () => {const domain = div.querySelectorAll('select')[1]; domain.value = 'd'; domain.dispatchEvent(new Event('change', {bubbles: true})); (div.querySelectorAll('input')[1] as HTMLInputElement).click();});
    await act(async () => [...div.querySelectorAll('button')].find(x => x.textContent === 'Compute ensemble support')!.click()); await settle(); await settle();
    expect(post).toHaveBeenCalledWith('/api/family-msa/projects/p/operations', {operation: 'ensemble-support', molecule: 'protein', domain_experiment_id: 'd', name: 'Ensemble support', inputs: {receipt_id:'r', reference_artifact_id:'a', member_artifact_ids:['b']}});
    expect(div.textContent).toContain('ensemble.b.fasta (b): 2 / 3');
    expect(div.textContent).toContain('samples: 1');
    expect(div.textContent).toContain('not probability of alignment truth');
  } finally {await act(async () => root.unmount()); cache.clear(); div.remove(); post.mockRestore(); get.mockRestore();}
});
