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

async function save(id='saved-a') { mocks.create.mockResolvedValueOnce({data:{...saved,id,name:id}}); await create(id); await act(async()=>mocks.header.onSave()); await act(async()=>new Promise(r=>setTimeout(r,10))); }
async function click(label:string) { const button=[...host.querySelectorAll('button')].find(b=>b.textContent?.trim()===label); expect(button,label).toBeDefined(); await act(async()=>button!.click()); await act(async()=>new Promise(r=>setTimeout(r,10))); }
function workups(handler:(id:string,offset:number)=>any) {
 const old=vi.mocked(fetch).getMockImplementation()!;
 return vi.mocked(fetch).mockImplementation(async (input:any,options?:any)=>{
  const url=String(input);if(!url.includes('/ngs-workup')) return old(input,options);
  const parsed=new URL(url,'http://test');expect(parsed.searchParams.get('limit')).toBe('50');
  const id=decodeURIComponent(parsed.pathname.split('/')[4]);
  return {ok:true,json:async()=>handler(id,Number(parsed.searchParams.get('offset')))} as any;
 });
}
it('actual shell follows candidate offsets across empty-valid workup pages and preserves relation',async()=>{
 const requests=workups((id,offset)=>({sequence_id:id,workups:offset===0?[]:[{job_id:'older-job',scientific_status:'REVIEW',revision_relation:'historical',manifest_available:false}],has_more:offset===0,next_offset:offset===0?73:null}));
 await save();expect(host.textContent).toContain('No valid workups in the loaded page');expect(host.textContent).not.toContain('No revision-bound NGS evidence.');
 await click('Load more workups');expect(host.textContent).toContain('REVIEW · historical revision');expect(host.querySelector('a[href*="older-job"]')).not.toBeNull();
 expect(requests.mock.calls.filter(([url])=>String(url).includes('/ngs-workup')).map(([url])=>new URL(String(url),'http://test').searchParams.get('offset'))).toEqual(['0','73']);
 expect(host.textContent).not.toContain('Load more workups');
});
it('actual shell ignores stale workup completion after switching saved workspace',async()=>{
 const old=deferred<any>();
 workups((id)=>id==='saved-a'?old.promise:{sequence_id:id,workups:[],has_more:false,next_offset:null});
 await save();await save('saved-b');await act(async()=>old.resolve({sequence_id:'saved-a',workups:[{job_id:'STALE',scientific_status:'PASS',revision_relation:'current',manifest_available:true}],has_more:false,next_offset:null}));
 await act(async()=>new Promise(r=>setTimeout(r,10)));expect(mocks.header.sequenceData.name).toBe('saved-b');expect(host.querySelector('a[href*="STALE"]')).toBeNull();
});
it('actual shell keeps continuation retry after a later workup page fails',async()=>{
 let fail=true;workups((id,offset)=>{if(offset===50&&fail)throw new Error('later history failed');return {sequence_id:id,workups:[],has_more:offset===0,next_offset:offset===0?50:null};});
 await save();await click('Load more workups');expect(host.textContent).toContain('could not be loaded completely');fail=false;await click('Retry workup history');expect(host.textContent).not.toContain('Retry workup history');
});

it('mobile QC can continue an empty valid page without claiming no evidence',async()=>{
 workups((id,offset)=>({sequence_id:id,workups:offset===0?[]:[{job_id:'mobile-job',scientific_status:'FAIL',revision_relation:'current',manifest_available:true}],has_more:offset===0,next_offset:offset===0?64:null}));
 await save();vi.stubGlobal('matchMedia',vi.fn().mockImplementation((query:string)=>({matches:query==='(pointer: coarse)',addEventListener:()=>{},removeEventListener:()=>{}})));await act(async()=>{window.innerWidth=400;window.innerHeight=800;window.dispatchEvent(new Event('resize'));});
 await click('QC');expect(host.textContent).toContain('No valid workups in the loaded page');await click('Load more workups');expect(host.textContent).toContain('FAIL');expect(host.querySelector('article')?.textContent).toContain('mobile-job');expect(host.querySelector('article')?.textContent).toContain('Current revision');
});
