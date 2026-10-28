import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, expect, it, vi} from 'vitest';
import AlignmentComparison from '../../src/components/alignments/AlignmentComparison';
import {api} from '../../src/lib/api';

vi.mock('../../src/lib/projectManager', () => ({
  searchProjects: vi.fn().mockResolvedValue({items: [{id: 'p', name: 'Project'}, {id: 'other', name: 'Other'}], next_cursor: null}),
  listGlobalExperiments: vi.fn().mockResolvedValue([{id: 'e', name: 'Experiment'}]),
  listDomainExperiments: vi.fn().mockResolvedValue([{id: 'd', name: 'Domain'}]),
}));
afterEach(() => vi.restoreAllMocks());
const report = {method: 'explicit-source-occurrence.v1', report: {rows: [{source_member_id: 'member', occurrence_id: 'repeat', left_row_id: 'lr', right_row_id: 'rr', positions: [{source: 5, left_column: 0, right_column: 2}], left_only_positions: [], right_only_positions: [6], left_unmapped_columns: [1], right_unmapped_columns: [], mapping_unknown: true}], left_excluded: 1, right_excluded: 2, agreement: {method: 'pairs', matched_rows: 2, shared_mapped_residues: 6, left_unmapped_residues: 1, right_unmapped_residues: 2, rows_with_unknown_mapping: 1, left_only_mapped_residues_known: 0, right_only_mapped_residues_known: 2, left_aligned_pairs: 4, right_aligned_pairs: 3, shared_aligned_pairs: 2, union_aligned_pairs: 5, jaccard: 0.4}}};

it('selects retained members, runs managed work, saves and reads back the exact revision', async () => {
  let saved: unknown;
  const get = vi.spyOn(api, 'get').mockImplementation(async (url, options) => {
    if (String(url).endsWith('/receipts')) return {data: options?.params?.after ? {items: [{receipt_id: 'r', entity_kind: 'family_msa_import', availability: 'available'}], next_cursor: null} : {items: [{receipt_id: 'l', entity_kind: 'family_msa_job_result', availability: 'available'}], next_cursor: 'page2'}};
    if (String(url).endsWith('/receipts/l')) return {data: {artifacts: [{id: 'log', role: 'log'}, {id: 'a', role: 'alignment', logical_path: 'left.fasta'}]}};
    if (String(url).endsWith('/receipts/r')) return {data: {artifacts: [{id: 'b', role: 'alignment', logical_path: 'right.fasta'}]}};
    if (String(url).endsWith('/operations/j')) return {data: {job_id: 'j', status: 'completed', artifact_url: '/api/artifact', error_message: null}};
    if (String(url).endsWith('/operations/j/artifact')) return {data: report};
    if (String(url).endsWith('/shared-state/s')) return {data: saved};
    throw new Error(String(url));
  });
  const post = vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
    if (String(url).endsWith('/operations')) return {data: {job_id: 'j'}};
    saved = {id: 's', revision_id: 'rev', state: (body as {state: unknown}).state};
    return {data: saved};
  });
  const div = document.createElement('div'); document.body.appendChild(div); const root = createRoot(div);
  const cache = new QueryClient({defaultOptions: {queries: {retry: false}}});
  const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); };
  const select = async (label: string, value: string) => {
    await act(async () => { const el = [...div.querySelectorAll('label')].find(l => l.textContent?.toLowerCase().startsWith(label.toLowerCase()))!.querySelector('select')!; el.value = value; el.dispatchEvent(new Event('change', {bubbles: true})); }); await settle();
  };
  const click = async (text: string) => { await act(async () => [...div.querySelectorAll('button')].find(b => b.textContent === text)!.click()); await settle(); };
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><MemoryRouter><AlignmentComparison initialProject="p"/></MemoryRouter></QueryClientProvider>)); await settle();
    expect(post).not.toHaveBeenCalled();
    await select('Destination domain', 'd');
    await select('Left result', 'l'); await select('Left member', 'a');
    await select('Right result', 'r'); await select('Right member', 'b');
    expect(div.querySelector('option[value="log"]')).toBeNull();
    await click('Compare alignments'); await settle(); await settle();
    expect(post).toHaveBeenCalledWith('/api/family-msa/projects/p/operations', {operation: 'compare', molecule: 'protein', domain_experiment_id: 'd', name: 'Alignment comparison', inputs: {left_receipt_id: 'l', left_artifact_id: 'a', right_receipt_id: 'r', right_artifact_id: 'b'}});
    expect(div.textContent).toContain('2 / 5'); expect(div.textContent).toContain('not biological accuracy');
    expect(div.textContent).toContain('excluded rows: left 1, right 2');
    await select('Mapped occurrence', '1');
    expect(div.querySelector('tbody')?.textContent).toBe('613shifted');
    expect(div.textContent).toContain('Left unmapped columns (1; first 100): 2');
    await click('Save comparison');
    expect(get).toHaveBeenCalledWith('/api/family-msa/projects/p/shared-state/s', {params: {revision_id: 'rev'}});
    expect(div.textContent).toContain('Saved revision rev');
    await select('Left result', 'r');
    expect(div.querySelector('select[aria-label="left member"]')).toHaveProperty('value', '');
    expect(div.textContent).not.toContain('Aligned-pair intersection');
    await select('Comparison Project', 'other');
    expect(div.querySelector('select[aria-label="right result"]')).toHaveProperty('value', '');
  } finally { await act(async () => root.unmount()); cache.clear(); div.remove(); }
});

it('resumes a managed comparison without submitting another Job', async () => {
  vi.spyOn(api, 'get').mockImplementation(async url => ({data: String(url).endsWith('/artifact') ? report : String(url).includes('/operations/') ? {status: 'completed', artifact_url: '/artifact', error_message: null} : {items: [], next_cursor: null}}));
  const post = vi.spyOn(api, 'post');
  const div = document.createElement('div'); const root = createRoot(div); const cache = new QueryClient({defaultOptions: {queries: {retry: false}}});
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><MemoryRouter><AlignmentComparison initialProject="p" operationJob="retained-job"/></MemoryRouter></QueryClientProvider>));
    for (let i = 0; i < 3; i++) await act(async () => {await new Promise(resolve => setTimeout(resolve, 30));});
    expect(div.textContent).toContain('Managed comparison Job retained-job: completed');
    expect(div.textContent).toContain('0.4000'); expect(post).not.toHaveBeenCalled();
    expect([...div.querySelectorAll('button')].find(b => b.textContent === 'Save comparison')?.disabled).toBe(true);
  } finally { await act(async () => root.unmount()); cache.clear(); }
});

it('reopens stored comparison reports without recomputation', async () => {
  const get = vi.spyOn(api, 'get').mockImplementation(async url => ({data: String(url).includes('/shared-state/') ? {id: 's', revision_id: 'saved-rev', state: {comparisons: [{id: 'c', name: 'Saved comparison', left_artifact_id: 'a', right_artifact_id: 'b', provenance: {}, ...report}]}} : {items: [], next_cursor: null}}));
  const post = vi.spyOn(api, 'post');
  const div = document.createElement('div'); const root = createRoot(div); const cache = new QueryClient({defaultOptions: {queries: {retry: false}}});
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><MemoryRouter><AlignmentComparison initialProject="p" savedState="s"/></MemoryRouter></QueryClientProvider>));
    await act(async () => {await new Promise(resolve => setTimeout(resolve, 30));});
    expect(div.textContent).toContain('Saved comparison'); expect(div.textContent).toContain('0.4000');
    expect(post).not.toHaveBeenCalled(); expect(get).toHaveBeenCalledWith('/api/family-msa/projects/p/shared-state/s', expect.anything());
  } finally { await act(async () => root.unmount()); cache.clear(); }
});
