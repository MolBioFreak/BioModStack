import React,{act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({preview:vi.fn(),commit:vi.fn(),library:vi.fn(),versions:vi.fn(),member:vi.fn(),attach:vi.fn(),project:vi.fn()}));
vi.mock('../../src/lib/api',()=>({previewMolBioSequenceImport:mocks.preview,commitMolBioSequenceImport:mocks.commit,fetchNucleotideSequences:mocks.library,fetchMolBioSequenceRevisions:mocks.versions}));
vi.mock('../../src/lib/molecularProjectMembership',()=>({addMolecularProjectMember:mocks.member,MOLECULAR_MEMBER_ADAPTER:'molecular-adapter'}));
vi.mock('../../src/lib/projectManager',()=>({attachExistingEntity:mocks.attach,getProject:mocks.project}));
vi.mock('../../src/components/experiments/GlobalExperimentContext',()=>({useGlobalExperimentContext:()=>({workspaceId:'project',globalExperimentId:'experiment',stateRevisionId:'state-1',selectedDomainExperiment:{domain_experiment_id:'domain'},availability:{canMutateDomain:true},contextHref:(path:string)=>path})}));
import {ReferenceSequenceInputs} from '../../src/components/ngs/ReferenceSequenceInputs';
let host:HTMLDivElement,root:Root,client:QueryClient;
const added=vi.fn();
const records=[{sequence_id:'a',revision_id:'ra',name:'A'},{sequence_id:'b',revision_id:'rb',name:'B'}];
const button=(text:string)=>Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(node=>node.textContent===text)!;
async function settle(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});}
async function until(check:()=>void){await vi.waitFor(async()=>{await settle();check();});}
async function click(text:string){await act(async()=>button(text).click());await settle();}
async function input(label:string,value:string){const node=host.querySelector<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>(`[aria-label="${label}"]`)!;await act(async()=>{Object.getOwnPropertyDescriptor(node instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:node instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value')!.set!.call(node,value);node.dispatchEvent(new Event(node instanceof HTMLSelectElement?'change':'input',{bubbles:true}));});await settle();}
beforeEach(async()=>{
    vi.resetAllMocks();mocks.preview.mockResolvedValue({data:{valid:true,errors:[],records:[{record_ordinal:1,name:'A',length:4,topology:'circular'},{record_ordinal:2,name:'B',length:4,topology:'circular'}]}});
    mocks.commit.mockResolvedValue({data:{records}});mocks.library.mockResolvedValue({data:records.map(record=>({id:record.sequence_id,name:record.name}))});mocks.versions.mockImplementation(async(id:string)=>({data:[{id:`r${id}`,is_current:true,revision_number:2},{id:`old-${id}`,is_current:false,revision_number:1}]}));
    let state=1;mocks.member.mockImplementation(async()=>({id:`state-${++state}`}));mocks.project.mockResolvedValue({head_generation:10});mocks.attach.mockResolvedValue({});
    client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});host=document.createElement('div');document.body.append(host);root=createRoot(host);
    await act(async()=>root.render(<QueryClientProvider client={client}><MemoryRouter><ReferenceSequenceInputs onAdded={added}/></MemoryRouter></QueryClientProvider>));
});
afterEach(()=>{act(()=>root.unmount());host.remove();client.clear();});
describe('expected-sequence entry',()=>{
    it('starts with a blank paste box and imports every FASTA record into the Project',async()=>{
        expect(host.querySelector('textarea')!.value).toBe('');await input('Paste expected sequences','>A\nACGT\n>B\nTGCA');await click('Check sequences');
        await until(()=>expect(button('Save and add to Project').disabled).toBe(false));await click('Save and add to Project');
        await until(()=>expect(added).toHaveBeenCalledWith(records,'state-3'));
        expect(mocks.commit).toHaveBeenCalledWith(expect.objectContaining({source_text:'>A\nACGT\n>B\nTGCA',source_format:'fasta',source_provider:'paste'}));
        expect(mocks.member.mock.calls.map(call=>call[3])).toEqual(['sequence_id=a&revision_id=ra','sequence_id=b&revision_id=rb']);
        expect(mocks.member.mock.calls.map(call=>call[4])).toEqual(['state-1','state-2']);expect(mocks.attach).toHaveBeenCalledTimes(2);
    });
    it('keeps raw DNA input intact and rejects invalid preview records before any save',async()=>{
        mocks.preview.mockResolvedValue({data:{valid:false,records:[],errors:[{message:'Invalid nucleotide'}]}});
        await input('Pasted sequence format','raw_dna');await input('Sequence name','Test');await input('Paste expected sequences','ACGT!');await click('Check sequences');
        await until(()=>expect(host.textContent).toContain('Invalid nucleotide'));expect(mocks.preview.mock.calls[0][0].raw_rows[0].sequence).toBe('ACGT!');
        expect(button('Save and add to Project').disabled).toBe(true);expect(mocks.commit).not.toHaveBeenCalled();
    });
    it('checks mixed GenBank and FASTA files without flattening them or dropping GenBank annotations',async()=>{
        await click('Browse sequence files');const files=[new File(['>A\nACGT'],'A.fasta'),new File(['LOCUS B\nORIGIN\n acgt\n//'],'B.gb')];
        files.forEach((file,index)=>Object.defineProperty(file,'text',{value:async()=>index===0?'>A\nACGT':'LOCUS B\nORIGIN\n acgt\n//'}));
        const node=host.querySelector<HTMLInputElement>('[aria-label="Sequence files"]')!;Object.defineProperty(node,'files',{value:files});await act(async()=>node.dispatchEvent(new Event('change',{bubbles:true})));await click('Check sequences');
        await until(()=>expect(mocks.preview).toHaveBeenCalledTimes(2));expect(mocks.preview.mock.calls.map(call=>call[0].source_format)).toEqual(['fasta','genbank']);expect(mocks.preview.mock.calls[1][0].source_text).toContain('LOCUS B');
        expect(mocks.commit).not.toHaveBeenCalled();
    });
    it('invalidates the checked payload when the source text changes',async()=>{
        await input('Paste expected sequences','>A\nACGT');await click('Check sequences');await until(()=>expect(button('Save and add to Project').disabled).toBe(false));await input('Paste expected sequences','>Other\nTGCA');expect(button('Save and add to Project').disabled).toBe(true);
    });
    it('adds selected saved sequences with exact current or historical versions',async()=>{
        await click('Saved MolBio sequences');await until(()=>expect(host.querySelector('[aria-label="Select A"]')).not.toBeNull());
        await act(async()=>{host.querySelector<HTMLInputElement>('[aria-label="Select A"]')!.click();host.querySelector<HTMLInputElement>('[aria-label="Select B"]')!.click();});
        await until(()=>expect(host.querySelector<HTMLSelectElement>('[aria-label="Version for B"]')!.options.length).toBe(3));await input('Version for B','old-b');await click('Add 2 saved sequences to Project');
        await until(()=>expect(added).toHaveBeenCalled());expect(mocks.member.mock.calls.map(call=>call[3])).toEqual(['sequence_id=a&revision_id=ra','sequence_id=b&revision_id=old-b']);expect(mocks.commit).not.toHaveBeenCalled();
    });
    it('retains successful file imports and retries only the failed file before attaching the complete batch',async()=>{
        await click('Browse sequence files');const files=['one.fa','two.fa'].map(name=>{const file=new File(['>A\nACGT'],name);Object.defineProperty(file,'text',{value:async()=>'>A\nACGT'});return file;});
        const node=host.querySelector<HTMLInputElement>('[aria-label="Sequence files"]')!;Object.defineProperty(node,'files',{value:files});await act(async()=>node.dispatchEvent(new Event('change',{bubbles:true})));await click('Check sequences');
        await until(()=>expect(button('Save and add to Project').disabled).toBe(false));
        mocks.commit.mockReset().mockResolvedValueOnce({data:{records:[records[0]]}}).mockRejectedValueOnce(new Error('Temporary save failure')).mockResolvedValueOnce({data:{records:[records[1]]}});
        await click('Save and add to Project');await until(()=>expect(host.textContent).toContain('Temporary save failure'));expect(mocks.member).not.toHaveBeenCalled();
        const firstKey=mocks.commit.mock.calls[0][0].idempotency_key;await click('Save and add to Project');await until(()=>expect(added).toHaveBeenCalledWith(records,'state-3'));
        expect(mocks.commit).toHaveBeenCalledTimes(3);expect(mocks.commit.mock.calls.filter(call=>call[0].idempotency_key===firstKey)).toHaveLength(1);
    });
    it('does not attach or navigate after leaving an import whose save is still pending',async()=>{
        let resolve!:(value:unknown)=>void;mocks.commit.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
        await input('Paste expected sequences','>A\nACGT');await click('Check sequences');await until(()=>expect(button('Save and add to Project').disabled).toBe(false));await click('Save and add to Project');
        await act(async()=>root.render(<div>Another workspace</div>));await act(async()=>resolve({data:{records}}));await settle();
        expect(mocks.member).not.toHaveBeenCalled();expect(mocks.attach).not.toHaveBeenCalled();expect(added).not.toHaveBeenCalled();
    });

    it('revalidates and preserves a per-record topology override in a mixed batch',async()=>{
        await input('Paste expected sequences','>A\nACGT\n>B\nTGCA');await click('Check sequences');await until(()=>expect(button('Save and add to Project').disabled).toBe(false));
        mocks.preview.mockResolvedValueOnce({data:{valid:true,errors:[],records:[{record_ordinal:1,name:'A',length:4,topology:'linear'},{record_ordinal:2,name:'B',length:4,topology:'circular'}]}});
        await input('Topology for A','linear');await until(()=>expect(button('Save and add to Project').disabled).toBe(false));await click('Save and add to Project');await until(()=>expect(mocks.commit).toHaveBeenCalled());
        expect(mocks.commit.mock.calls[0][0].topology_overrides).toEqual({'1':'linear'});
    });

    it('browses a CSV batch and submits the original file to authoritative preview and commit before attaching every reference',async()=>{
        await click('Browse sequence files');
        const source='name,sequence,topology\nA,ACGT,circular\nB,TGCA,linear\n';
        const file=new File([source],'references.CSV',{type:'text/csv'});
        Object.defineProperty(file,'text',{value:async()=>source});
        const node=host.querySelector<HTMLInputElement>('[aria-label="Sequence files"]')!;
        expect(node.accept.split(',')).toContain('.csv');
        Object.defineProperty(node,'files',{value:[file]});await act(async()=>node.dispatchEvent(new Event('change',{bubbles:true})));
        await click('Check sequences');await until(()=>expect(button('Save and add to Project').disabled).toBe(false));
        expect(mocks.preview).toHaveBeenCalledWith(expect.objectContaining({source_format:'csv',source_text:source,source_provider:'upload',source_id:'references.CSV'}));
        expect(mocks.commit).not.toHaveBeenCalled();
        await click('Save and add to Project');await until(()=>expect(added).toHaveBeenCalledWith(records,'state-3'));
        expect(mocks.commit.mock.calls[0][0]).toEqual(mocks.preview.mock.calls[0][0]);
        expect(mocks.member.mock.calls.map(call=>call[3])).toEqual(['sequence_id=a&revision_id=ra','sequence_id=b&revision_id=rb']);
    });
    it('accepts pasted CSV and blocks the complete batch when one CSV row fails validation',async()=>{
        mocks.preview.mockResolvedValue({data:{valid:false,records:[{record_ordinal:1,name:'A',length:4,topology:'circular'}],errors:[{record_ordinal:2,message:'CSV record 2 (line 3): invalid nucleotide'}]}});
        await input('Pasted sequence format','csv');await input('Paste expected sequences','name,sequence\nA,ACGT\nB,ACGT!');await click('Check sequences');
        await until(()=>expect(host.textContent).toContain('CSV record 2 (line 3): invalid nucleotide'));
        expect(mocks.preview.mock.calls[0][0]).toMatchObject({source_format:'csv',source_text:'name,sequence\nA,ACGT\nB,ACGT!',source_provider:'paste'});
        expect(button('Save and add to Project').disabled).toBe(true);expect(mocks.commit).not.toHaveBeenCalled();
    });

});
