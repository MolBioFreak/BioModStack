import React,{act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({units:vi.fn(),sequences:vi.fn(),versions:vi.fn(),receipt:vi.fn(),submit:vi.fn()}));
vi.mock('../../src/lib/api',async original=>({...await original<any>(),fetchOntBarcodeUnits:mocks.units,fetchNucleotideSequences:mocks.sequences,fetchMolBioSequenceRevisions:mocks.versions,issueMolBioNgsReceipt:mocks.receipt,submitOntBarcodeBatch:mocks.submit,fetchExecutionTargets:vi.fn().mockResolvedValue({data:[]})}));
import {BarcodeUnitsPanel} from '../../src/components/ngs/BarcodeUnitsPanel';
let host:HTMLDivElement,root:Root,client:QueryClient;
async function settle(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});}
async function click(label:string){await act(async()=>Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(node=>node.textContent===label)!.click());await settle();}
async function paste(value:string){const node=host.querySelector<HTMLTextAreaElement>('[aria-label="Bulk barcode plasmid mappings"]')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(node,value);node.dispatchEvent(new Event('input',{bubbles:true}));});}
beforeEach(async()=>{vi.resetAllMocks();mocks.units.mockResolvedValue({data:{units:[{unit_id:'barcode01',sample_alias:'sheet_A',read_count:1},{unit_id:'barcode02',sample_alias:null,read_count:2},{unit_id:'unclassified',read_count:3}]}});mocks.sequences.mockResolvedValue({data:[{id:'a',name:'A'},{id:'b',name:'B'}]});mocks.versions.mockImplementation(async(id:string)=>({data:[{id:`r-${id}`,is_current:true,revision_number:2},{id:`old-${id}`,is_current:false,revision_number:1}]}));mocks.receipt.mockImplementation(async(id:string)=>({data:{receipt_id:`receipt-${id}`}}));mocks.submit.mockResolvedValue({data:{child_job_ids:['one','two'],reference_set_id:'set'}});host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient({defaultOptions:{queries:{retry:false}}});await act(async()=>root.render(<QueryClientProvider client={client}><BarcodeUnitsPanel jobId="source" enabled/></QueryClientProvider>));await settle();});
afterEach(()=>{act(()=>root.unmount());host.remove();client.clear();});
it('pins bulk current and historical references and submits the existing exact barcode batch contract',async()=>{
    await paste('barcode01,A\nbarcode02,B,1');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Applied 2 exact');});await click('Submit mapped batch');
    await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledTimes(1));expect(mocks.receipt.mock.calls).toEqual([['a',{revision_id:'r-a'}],['b',{revision_id:'old-b'}]]);
    expect(mocks.submit.mock.calls[0][1].mappings).toEqual([{unit_id:'barcode01',sample_alias:'sheet_A',molbio_ngs_receipt_id:'receipt-a'},{unit_id:'barcode02',sample_alias:null,molbio_ngs_receipt_id:'receipt-b'}]);
});
it('does not partially overwrite mappings when one pasted barcode is invalid',async()=>{
    await paste('barcode01,A\nunclassified,B');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Unknown or repeated');});
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Saved sequence for barcode01"]')!.value).toBe('');expect(mocks.receipt).not.toHaveBeenCalled();expect(mocks.submit).not.toHaveBeenCalled();
});
