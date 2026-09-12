import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
    input: {} as any, header: {} as any, annotation: {} as any, visibility: {} as any, orfs: vi.fn(),
    create: vi.fn(), update: vi.fn(), catalog: vi.fn(), products: vi.fn(), analyze: vi.fn(), tmOptions: vi.fn(), demos: vi.fn(), annotationDownload: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({SequenceViewer:()=>null}));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({VisibilityPanel:(props:any)=>{mocks.visibility=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/utils/orfs', () => ({findOpenReadingFrames:mocks.orfs}));
vi.mock('../../src/components/MolBioToolkit/GCContentTrack', () => ({GCContentTrack:()=>null}));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', () => ({SequenceHeader:(props:any)=>{mocks.header=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({MolecularInputModal:(props:any)=>{mocks.input=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({AutoAnnotatePanel:(props:any)=>{mocks.annotation=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/panels', () => Object.fromEntries(['AlignmentPanel','AssemblyPanel','DigestPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name=>[name,()=>null])));
vi.mock('../../src/components/MolBioToolkit/RnaStructureViewer', () => ({RnaStructureViewer:()=>null}));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({loadDemoPlasmids:mocks.demos}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({useGlobalExperimentContext:()=>({updateQueryParams:()=>{},contextHref:(p:string)=>p})}));
vi.mock('../../src/components/MolBioToolkit/utils/annotationSources', async (original) => ({...await original<any>(),fetchAnnotationSourceStatus:vi.fn().mockResolvedValue({}),retrieveNcbiAnnotationSource:mocks.annotationDownload}));
vi.mock('../../src/lib/restrictionAnalysis', async (original) => ({...await original<any>(),fetchRestrictionCatalog:mocks.catalog,fetchRestrictionProducts:mocks.products,fetchRestrictionAnalysisBatch:mocks.analyze}));
vi.mock('../../src/lib/api', async (original) => ({...await original<any>(),fetchNucleotideSequences:vi.fn().mockResolvedValue({data:[]}),createNucleotideSequence:mocks.create,updateNucleotideSequence:mocks.update,fetchPrimerTmOptions:mocks.tmOptions}));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';
let root:Root, host:HTMLDivElement, client:QueryClient;
function deferred<T>() {let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>resolve=r);return {promise,resolve};}
const saved = {id:'saved-a',name:'A',sequence:'ACGT'.repeat(30),sequence_type:'dna',is_circular:false,features:[],primers:[],version:2};
async function create(name='A'){await act(async()=>mocks.input.onCreateSequence({name,sequence:'ACGT'.repeat(30),sequenceType:'dna',circular:false}));}
beforeEach(async()=>{
 localStorage.clear(); window.innerWidth=1400;
 mocks.create.mockReset();mocks.update.mockReset();mocks.catalog.mockReset().mockResolvedValue({catalog:{catalog_id:'test'},items:[]});mocks.products.mockReset().mockResolvedValue({product_release:null});mocks.analyze.mockReset().mockResolvedValue({});mocks.orfs.mockReset().mockReturnValue([]);mocks.demos.mockReset().mockResolvedValue([]);mocks.annotationDownload.mockReset();
 mocks.tmOptions.mockReset().mockResolvedValue({data:{algorithms:[{id:'nn_santalucia_hicks_2004',sequence_types:['dna','rna']}],defaults:{}}});
 vi.stubGlobal('fetch',vi.fn().mockImplementation(async (url:string) => {
   if (url.includes('/restriction/products')) {
     expect(url).toBe('/api/molbio/restriction/products?limit=1');
     mocks.products();
     return {ok:true,json:async()=>({schema:'bms.molbio.restriction-products-page.v1',items:[],next_cursor:null,product_release:{
       release_id:'bms-restriction-products-permission-pending-v1',release_version:'1.0.0',content_sha256:'a'.repeat(64),raw_sha256:'b'.repeat(64),schema_raw_sha256:'c'.repeat(64),created_at:null,created_at_policy:'omitted_until_permissioned_evidence_release',source_policy:'no_runtime_scraping_written_redistribution_permission_required',redistribution_permission_state:'unavailable',permission_receipt:null,product_evidence_available:false,record_count:0,active_claim_count:0,core_catalog_digest_binding:'independent_no_binding',product_identity_policy:'supplier_id_and_catalog_number_nfkc_casefold_trim_v1'
     }})};
   }
   return {ok:true,json:async()=>({workups:[]})};
 }));
 host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 await act(async()=>root.render(<QueryClientProvider client={client}><MemoryRouter><MolBioToolkitV2/></MemoryRouter></QueryClientProvider>));
});
afterEach(async()=>{await act(async()=>root.unmount());client.clear();host.remove();vi.unstubAllGlobals();});
it('late save preserves subsequent edits and only clears dirty when submission is unchanged',async()=>{
 await create();const response=deferred<any>();mocks.create.mockReturnValueOnce(response.promise);
 let pending!:Promise<boolean>;await act(async()=>{pending=mocks.header.onSave()});
 await act(async()=>mocks.input.onAddPrimerToCurrentSequence({id:'post',name:'Post-submit primer',sequence:'ACGT',start:0,end:4,strand:1}));
 await act(async()=>response.resolve({data:saved}));
 expect(await pending).toBe(false);expect(mocks.header.sequenceData.primers).toHaveLength(1);expect(mocks.header.isDirty).toBe(true);expect(mocks.header.sequenceData.version).toBe(2);
});
it('save completion never steals another editable tab and retains inactive post-submit edits',async()=>{
 await create();const response=deferred<any>();mocks.create.mockReturnValueOnce(response.promise);
 let pending!:Promise<boolean>;await act(async()=>{pending=mocks.header.onSave()});
 await act(async()=>mocks.input.onAddPrimerToCurrentSequence({id:'post',name:'Post-submit primer',sequence:'ACGT',start:0,end:4,strand:1}));
 await create('B');await act(async()=>response.resolve({data:saved}));await pending;
 expect(mocks.header.sequenceData.name).toBe('B');expect(mocks.header.sequenceData.primers).toHaveLength(0);
 // Discard B through the actual dirty-tab transition, then inspect saved A.
 const tab=[...host.querySelectorAll('button')].find(b=>b.querySelector('.truncate')?.textContent?.trim()==='A *');expect(tab).toBeTruthy();
 await act(async()=>tab!.click());const discard=[...host.querySelectorAll('button')].find(b=>b.textContent?.includes('Discard and continue'));expect(discard).toBeTruthy();await act(async()=>discard!.click());
 expect(mocks.header.sequenceData.name).toBe('A');expect(mocks.header.sequenceData.primers).toHaveLength(1);expect(mocks.header.isDirty).toBe(true);
});
it('release fetch counts stay constant across construct edits and switch; hidden work and demos stay idle',async()=>{
 await create();await create('B');
 expect(mocks.catalog).toHaveBeenCalledTimes(1);expect(mocks.products).toHaveBeenCalledTimes(1);expect(mocks.tmOptions).toHaveBeenCalledTimes(1);expect(mocks.analyze).not.toHaveBeenCalled();expect(mocks.orfs).not.toHaveBeenCalled();expect(mocks.demos).not.toHaveBeenCalled();
 await act(async()=>mocks.visibility.onChange('translations'));expect(mocks.orfs).toHaveBeenCalledTimes(1);
 await act(async()=>mocks.visibility.onChange('cutsites'));expect(mocks.analyze).toHaveBeenCalledTimes(1);
 await act(async()=>mocks.input.onCreateSequence({name:'RNA',sequence:'ACGU'.repeat(30),sequenceType:'rna',circular:false}));
 expect(mocks.tmOptions).toHaveBeenCalledTimes(1);expect(mocks.catalog).toHaveBeenCalledTimes(1);expect(mocks.products).toHaveBeenCalledTimes(1);
 await act(async()=>mocks.input.onDemoIntent());expect(mocks.demos).toHaveBeenCalledTimes(1);
});
it('annotation retrieval is invalidated by edit generation before parsing or applying',async()=>{
 await create();const response=deferred<any>();mocks.annotationDownload.mockReturnValueOnce(response.promise);
 let result!:Promise<string>;await act(async()=>{result=mocks.annotation.onRetrieveNcbi('TEST');result.catch(()=>{})});
 await act(async()=>mocks.input.onAddPrimerToCurrentSequence({id:'post',name:'post',sequence:'ACGT',start:0,end:4,strand:1}));
 const staleRead=vi.fn().mockRejectedValue(new Error('Stale parser accessed'));
 await act(async()=>response.resolve({file:{arrayBuffer:staleRead},source:{}}));
 expect(staleRead).not.toHaveBeenCalled();
 await expect(result).rejects.toThrow('Workspace changed during annotation retrieval');expect(mocks.header.sequenceData.features).toHaveLength(0);
});
