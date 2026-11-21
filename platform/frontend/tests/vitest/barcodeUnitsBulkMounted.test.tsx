import React,{act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({units:vi.fn(),detail:vi.fn(),sequences:vi.fn(),versions:vi.fn(),receipt:vi.fn(),submit:vi.fn()}));
vi.mock('../../src/lib/api',async original=>({...await original<any>(),fetchNucleotideSequence:mocks.detail,fetchOntBarcodeUnits:mocks.units,fetchNucleotideSequences:mocks.sequences,fetchMolBioSequenceRevisions:mocks.versions,issueMolBioNgsReceipt:mocks.receipt,submitOntBarcodeBatch:mocks.submit,fetchExecutionTargets:async()=>({data:[]})}));
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
it('submits the barcodes that have a sequence and lists the one left without',async()=>{
    mocks.submit.mockResolvedValue({data:{child_job_ids:['one'],reference_set_id:'set',skipped_unit_ids:['barcode02']}});
    await paste('barcode01,A');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.querySelector<HTMLSelectElement>('[aria-label="Saved sequence for barcode01"]')!.value).toBe('a');});
    await click('Submit mapped batch');await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledTimes(1));
    expect(mocks.submit.mock.calls[0][1].mappings).toEqual([{unit_id:'barcode01',sample_alias:'sheet_A',molbio_ngs_receipt_id:'receipt-a'}]);
    await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Skipped, no sequence chosen: barcode02. Their reads are kept.');});
});
it('asks for at least one sequence when no barcode has one',async()=>{
    await click('Submit mapped batch');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Choose a saved sequence for at least one barcode');});
    expect(mocks.submit).not.toHaveBeenCalled();
});
it('does not partially overwrite mappings when one pasted barcode is invalid',async()=>{
    await paste('barcode01,A\nunclassified,B');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Unknown or repeated');});
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Saved sequence for barcode01"]')!.value).toBe('');expect(mocks.receipt).not.toHaveBeenCalled();expect(mocks.submit).not.toHaveBeenCalled();
});
it('offers methylation tables only for a run called with modifications and sends the choice with the batch',async()=>{
    expect(host.querySelector('[data-testid="ont-barcode-methylation"]')).toBeNull();
    await act(async()=>root.render(<QueryClientProvider client={client}><BarcodeUnitsPanel jobId="source" enabled modifiedBases="5mC_5hmC 6mA"/></QueryClientProvider>));await settle();
    const box=host.querySelector<HTMLInputElement>('input[aria-label="Generate methylation tables"]')!;expect(box.checked).toBe(false);
    await act(async()=>box.click());await settle();await click('Strict 0.70');
    await paste('barcode01,A\nbarcode02,B');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Applied 2 exact');});await click('Submit mapped batch');
    await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledTimes(1));
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({run_modkit:true,modkit_filter_threshold:0.7});
});
it('sends no methylation fields when the box is left off',async()=>{
    await act(async()=>root.render(<QueryClientProvider client={client}><BarcodeUnitsPanel jobId="source" enabled modifiedBases="6mA"/></QueryClientProvider>));await settle();
    await paste('barcode01,A\nbarcode02,B');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Applied 2 exact');});await click('Submit mapped batch');
    await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledTimes(1));
    expect(mocks.submit.mock.calls[0][1]).not.toHaveProperty('run_modkit');
});
it('gives a barcode several plasmids from repeated rows and sends one mapping per plasmid',async()=>{
    await paste('barcode,plasmid\nbarcode01,A\nbarcode02,B\nbarcode02,A');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('Applied 3 exact');});
    expect(host.querySelector('[data-testid="barcode-mapping-row-barcode02-extra-1"]')).not.toBeNull();
    await vi.waitFor(async()=>{await settle();expect(host.querySelector('[data-testid="barcode-mapping-status-barcode02"]')!.textContent).toContain('Mapped · 2 sequences');});
    expect(host.querySelector('[data-testid="barcode-mapping-status-barcode01"]')!.textContent).toContain('Mapped');
    await click('Submit mapped batch');await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledTimes(1));
    expect(mocks.submit.mock.calls[0][1].mappings).toEqual([
        {unit_id:'barcode01',sample_alias:'sheet_A',molbio_ngs_receipt_id:'receipt-a'},
        {unit_id:'barcode02',sample_alias:null,molbio_ngs_receipt_id:'receipt-b',label:'B'},
        {unit_id:'barcode02',sample_alias:null,molbio_ngs_receipt_id:'receipt-a',label:'A'},
    ]);
});
it('adds and removes a further sequence row by hand and refuses a repeated plasmid on one barcode',async()=>{
    await act(async()=>host.querySelector<HTMLButtonElement>('[data-testid="barcode-add-sequence-barcode01"]')!.click());await settle();
    expect(host.querySelector('[data-testid="barcode-mapping-row-barcode01-extra-1"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="barcode-mapping-status-barcode01"]')!.textContent).toContain('Needs a sequence');
    await act(async()=>host.querySelector<HTMLButtonElement>('[aria-label="Remove sequence 2 from barcode01"]')!.click());await settle();
    expect(host.querySelector('[data-testid="barcode-mapping-row-barcode01-extra-1"]')).toBeNull();
    await paste('barcode01,A\nbarcode01,A\nbarcode02,B');await click('Apply barcode mappings');await vi.waitFor(async()=>{await settle();expect(host.textContent).toContain('barcode01 lists A twice.');});
});

it('submits an explicitly selected reference-free assembly beside an off-page exact revision', async () => {
    const id = '4588e09d-f644-46c2-b324-09feb21eda01';
    mocks.detail.mockResolvedValue({data:{id,name:'Library plasmid',sequence_type:'dna'}});
    await paste(`barcode02,${id},1`); await click('Apply barcode mappings');
    await vi.waitFor(async()=>{await settle();expect(host.querySelector<HTMLSelectElement>('[aria-label="Saved sequence for barcode02"]')!.value).toBe(id);});
    const target = host.querySelector<HTMLSelectElement>('[aria-label="Batch workflow"]')!;
    await act(async()=>{target.value='wf_clone_validation';target.dispatchEvent(new Event('change',{bubbles:true}));});
    await act(async()=>host.querySelector<HTMLInputElement>('[aria-label="Assemble barcode01 without a reference"]')!.click());
    await click('Submit mapped batch'); await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledOnce());
    expect(mocks.receipt.mock.calls).toEqual([[id,{revision_id:`old-${id}`}]]);
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({target_workflow:'wf_clone_validation',mappings:[{unit_id:'barcode01',sample_alias:'sheet_A'},{unit_id:'barcode02',molbio_ngs_receipt_id:`receipt-${id}`}]});
});
