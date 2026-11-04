import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AlignmentEditor, { fastaRoster } from '../../src/components/alignments/AlignmentEditor';
const submit = vi.hoisted(() => vi.fn());
vi.mock('../../src/lib/api', async original => ({...(await original<Record<string,unknown>>()), submitJob: submit}));

describe('family alignment authoring', () => {
    it('retains duplicate headers and sequence bytes with unique local row ids', () => {
        const roster = fastaRoster('>same\nAa\n>same\nAa\n', 'protein');
        expect(roster.rows).toEqual([{row_id:'row_00000001',header:'same',sequence:'Aa'},{row_id:'row_00000002',header:'same',sequence:'Aa'}]);
        expect(() => fastaRoster('ABC', 'protein')).toThrow('Sequence before FASTA header');
    });
    it('sends actual job API payload and shows the returned id', async () => {
        submit.mockResolvedValueOnce({data:{id:'job-real'}});
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async()=>root.render(<QueryClientProvider client={client}><MemoryRouter><AlignmentEditor model="family_msa_protein"/></MemoryRouter></QueryClientProvider>));
        const textarea = div.querySelector('textarea[aria-label="FASTA roster"]') as HTMLTextAreaElement;
        await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(textarea, '>a\nAA\n>b\nBB'); textarea.dispatchEvent(new Event('input',{bubbles:true}));});
        const button = [...div.querySelectorAll('button')].find(x=>x.textContent === 'Start run')!;
        await act(async()=>{button.click();});
        expect(submit).toHaveBeenCalledWith(expect.objectContaining({model_id:'family_msa_protein',mode:'family_msa',params:expect.objectContaining({roster:expect.objectContaining({rows:expect.arrayContaining([expect.objectContaining({header:'a',sequence:'AA'})])}),settings:expect.objectContaining({engine:'mafft',threads:1})})}));
        expect(div.querySelector('a[href="/alignments/jobs/job-real"]')?.getAttribute('title')).toBe('job-real');
        await act(async()=>root.unmount()); client.clear();
    });
});
it('keeps MUSCLE Align runnable and explains its molecule detection and DNA accuracy note', async () => {
 const div = document.createElement('div'), root = createRoot(div), client = new QueryClient();
 await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><AlignmentEditor model="family_msa_nucleic"/></MemoryRouter></QueryClientProvider>));
 try {
  const engine = div.querySelector('[aria-label="Engine"]') as HTMLSelectElement;
  await act(async () => { engine.value='muscle'; engine.dispatchEvent(new Event('change', {bubbles:true})); });
  const mode = div.querySelector('[data-setting="mode"] select') as HTMLSelectElement;
  expect(mode.value).toBe('align');
  expect(div.textContent).toContain('decides for itself whether the sequences are DNA or protein');
  expect(div.textContent).toContain('misplaced one single-base deletion');
  const start = [...div.querySelectorAll('button')].find(b => b.textContent === 'Start run')!;
  expect(start.disabled).toBe(false);
  await act(async () => { mode.value='super5'; mode.dispatchEvent(new Event('change', {bubbles:true})); });
  expect(start.disabled).toBe(false);
  expect(div.textContent).not.toContain('decides for itself');
  expect(div.textContent).toContain('misplaced one single-base deletion');
 } finally { await act(async () => root.unmount()); client.clear(); }
});
