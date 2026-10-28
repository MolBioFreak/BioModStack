import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import AlignmentLibrary from '../../src/components/alignments/AlignmentLibrary';
import { api } from '../../src/lib/api';

it('lists authorized Project receipts and links the exact retained result', async () => {
  const get = vi.spyOn(api, 'get').mockImplementation(async url => {
    if (String(url).includes('/projects/search')) return {data: {items: [{id: 'p', name: 'Project'}], next_cursor: null}};
    return {data: {items: [{receipt_id: 'r', entity_kind: 'family_msa_import', availability: 'available'}], next_cursor: null}};
  });
  const div = document.createElement('div'); const root = createRoot(div);
  await act(async () => { root.render(<MemoryRouter><AlignmentLibrary initialProject="p"/></MemoryRouter>); });
  await act(async () => { await new Promise(r => setTimeout(r, 30)); });
  expect(div.querySelector('a[href="/alignments/results/r?project_id=p"]')).not.toBeNull();
  expect(div.textContent).toContain('does not create a Job');
  await act(async () => root.unmount()); get.mockRestore();
});

it('uploads a selected alignment without exposing its replay key', async () => {
  const get = vi.spyOn(api, 'get').mockImplementation(async url => String(url).includes('/projects/search')
    ? {data: {items: [{id: 'p', name: 'Project'}], next_cursor: null}}
    : {data: {items: [], next_cursor: null}});
  const post = vi.spyOn(api, 'post').mockResolvedValue({data: {receipt_id: 'r'}});
  const div = document.createElement('div'); const root = createRoot(div);
  await act(async () => { root.render(<MemoryRouter><AlignmentLibrary initialProject="p"/></MemoryRouter>); });
  const file = new File(['>a\nAC\n'], 'aligned.fa');
  const input = div.querySelector<HTMLInputElement>('input[aria-label="Alignment file"]')!;
  Object.defineProperty(input, 'files', {value: [file], configurable: true});
  await act(async () => input.dispatchEvent(new Event('change', {bubbles: true})));
  const button = Array.from(div.querySelectorAll('button')).find(b => b.textContent === 'Import alignment')!;
  await act(async () => button.click());
  expect(post).toHaveBeenCalledWith('/api/family-msa/projects/p/imports/alignment', file,
    expect.objectContaining({params: expect.objectContaining({format: 'fasta', molecule: 'protein'})}));
  expect(div.textContent).not.toContain('Replay key');
  expect(div.querySelector('a[href="/alignments/results/r?project_id=p"]')).not.toBeNull();
  await act(async () => root.unmount()); get.mockRestore(); post.mockRestore();
});
