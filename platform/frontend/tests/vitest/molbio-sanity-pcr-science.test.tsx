import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PCRPanel } from '../../src/components/MolBioToolkit/panels/PCRPanel';
import { resolvePrimerBindings } from '../../src/components/MolBioToolkit/utils/nucleotides';
import * as api from '../../src/lib/api';
vi.mock('../../src/lib/api', () => ({ runPcrOperation: vi.fn(), fetchPcrExperimentRevision: vi.fn(), fetchPcrExperimentSummaryPage: vi.fn() }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: () => '/' }) }));
vi.mock('../../src/components/MolBioToolkit/PrimerTmSettingsPanel', () => ({ PrimerTmSettingsPanel: () => null }));
const T = 'AAGACTTCATTATGAGGCACAGAGCCACTTGGTTCGCACTTCGATGCCCCGAGATCTAGGTGGTATCCGATCATAAAAAACGTGTTTAGGCTGCGTTATTTGAAAGCAGTCTGCGTCTAC';
const settings = { algorithm:'nn_santalucia_hicks_2004', salt_correction:'owczarzy_2008', primer_concentration_nM:250, template_concentration_nM:0, na_mM:50, k_mM:0, tris_mM:0, mg_mM:1.5, dntps_mM:0.6, dmso_percent:0, formamide_percent:0, self_complementary:false };
const noop = () => {};
let host: HTMLDivElement, root: Root;
const response = (length = 80) => ({ product: { sequence:T.slice(100)+T.slice(0,60), length, start:100, end:60, wraps_origin:true, template_span:80, min_anneal_length:8, method:'exact-3prime-suffix-v2' }, sequence:null, message:'PCR sequence predicted', experiment_id:null, experiment_revision_id:null, operation_id:null, reused:false });
async function render(sequence = T, type: 'dna' | 'rna' = 'dna') { await act(async () => root.render(<MemoryRouter><PCRPanel sequenceData={{ name:'audit', sequence, circular:true, sequenceType:type, features:[], primers:[] }} sequenceId={null} onHighlight={noop} tmOptions={null} tmSettings={settings} onTmSettingsChange={noop} /></MemoryRouter>)); }
async function input(el: HTMLInputElement, value: string) { await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value); el.dispatchEvent(new Event('input',{bubbles:true})); }); }
async function primers(f = 'TGAAAGCAGTCTGCGTCTAC', r = 'CCTAGATCTCGGGGCATCGA') { const fields = host.querySelectorAll<HTMLInputElement>('input[placeholder="ATGC..."]'); await input(fields[0],f); await input(fields[1],r); }
async function tick() { await act(async () => vi.advanceTimersByTimeAsync(250)); }
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); host=document.createElement('div'); document.body.append(host); root=createRoot(host); vi.mocked(api.runPcrOperation).mockResolvedValue(response()); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });
it('uses authoritative positive circular product length and sends complete search settings', async () => {
 await render(); await primers(); await tick();
 expect(host.textContent).toContain('Predicted product: 80 bp'); expect(host.textContent).not.toContain('-40 bp');
 expect(api.runPcrOperation).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ schema_version:'pcr-v2', min_anneal_length:8, save:false, persist_experiment:false, forward_binding:null, reverse_binding:null }), expect.any(AbortSignal));
 await input(host.querySelector<HTMLInputElement>('input[type="number"]')!, '18'); await tick();
 expect(vi.mocked(api.runPcrOperation).mock.calls.at(-1)![0].min_anneal_length).toBe(18);
});
it('displays all qualifying sites and transmits the explicit selection without choosing the first silently', async () => {
 const f='AG'+T.slice(0,20), template=f+'TTTTTCC'+T.slice(0,20)+'GGGGG'+T.slice(20);
 expect(resolvePrimerBindings(template,f,{minAnnealLength:20}).map(b=>[b.start,b.annealLength])).toEqual([[0,22],[29,20]]);
 await render(template); await input(host.querySelector<HTMLInputElement>('input[type="number"]')!, '20'); await primers(f,'GTAGACGCAGACTGCTTTCA'); await tick();
 const choices=host.querySelector<HTMLSelectElement>('select[aria-label="Forward binding site"]')!;
 expect([...choices.options].map(o=>o.value)).toEqual(['','0:22','29:20']);
 await act(async()=>{choices.value='29:20';choices.dispatchEvent(new Event('change',{bubbles:true}));}); await tick();
 expect(vi.mocked(api.runPcrOperation).mock.calls.at(-1)![0].forward_binding).toEqual({start:29,anneal_length:20});
});
it('rejects obsolete previews after template edits and aborts the request',async()=>{
 let resolve!: (value: api.PcrOperationResponse)=>void;
 vi.mocked(api.runPcrOperation).mockReturnValueOnce(new Promise(r=>{resolve=r;}));
 await render(); await primers(); await tick(); const signal=vi.mocked(api.runPcrOperation).mock.calls[0][1]!;
 await render(T+'A'); expect(signal.aborted).toBe(true);
 await act(async()=>resolve(response(999))); expect(host.textContent).not.toContain('999 bp');
 await tick(); expect(host.textContent).toContain('80 bp');
});
it('does not calculate RNA as an RNA PCR product',async()=>{
 await render(T.replaceAll('T','U'),'rna'); await primers(); await tick();
 expect(api.runPcrOperation).not.toHaveBeenCalled(); expect(host.textContent).toContain('Convert RNA to cDNA');
 expect([...host.querySelectorAll('button')].find(b=>b.textContent==='Calculate PCR product')?.disabled).toBe(true);
});
