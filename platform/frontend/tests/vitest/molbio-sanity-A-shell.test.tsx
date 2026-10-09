import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
    navigate: null as any, revision: vi.fn(), primer: {} as any, feature: {} as any, digest: {} as any, input: {} as any, header: {} as any, annotation: {} as any, visibility: {} as any, orfs: vi.fn(),
    create: vi.fn(), update: vi.fn(), get: vi.fn(), catalog: vi.fn(), products: vi.fn(), analyze: vi.fn(), tmOptions: vi.fn(), demos: vi.fn(), annotationDownload: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({SequenceViewer:()=>null}));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({VisibilityPanel:(props:any)=>{mocks.visibility=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/utils/orfs', () => ({findOpenReadingFrames:mocks.orfs}));
vi.mock('../../src/components/MolBioToolkit/GCContentTrack', () => ({GCContentTrack:()=>null}));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', () => ({SequenceHeader:(props:any)=>{mocks.header=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({MolecularInputModal:(props:any)=>{mocks.input=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({AutoAnnotatePanel:(props:any)=>{mocks.annotation=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/panels', () => Object.fromEntries(['AlignmentPanel','AssemblyPanel','DigestPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name=>[name,(props:any)=>{if(name==='DigestPanel')mocks.digest=props;if(name==='PrimerPanel')mocks.primer=props;if(name==='FeaturePanel')mocks.feature=props;return null}])));
vi.mock('../../src/components/MolBioToolkit/RnaStructureViewer', () => ({RnaStructureViewer:()=>null}));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({loadDemoPlasmids:mocks.demos}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', async () => {
 const {useNavigate, useLocation} = await import('react-router-dom');
 return {useGlobalExperimentContext:()=>{
   const navigate=useNavigate(), location=useLocation();
   return {updateQueryParams:(updates:Record<string,string|null>)=>{
     const params=new URLSearchParams(location.search);
     for(const [key,value] of Object.entries(updates)) {if(value) params.set(key,value);else params.delete(key)}
     navigate({pathname:location.pathname,search:params.toString()});
   },contextHref:(p:string)=>p};
 }};
});
vi.mock('../../src/components/MolBioToolkit/utils/annotationSources', async (original) => ({...await original<any>(),fetchAnnotationSourceStatus:vi.fn().mockResolvedValue({}),retrieveNcbiAnnotationSource:mocks.annotationDownload}));
vi.mock('../../src/lib/restrictionAnalysis', async (original) => ({...await original<any>(),fetchRestrictionCatalogBrowse:mocks.catalog,fetchRestrictionProducts:mocks.products,fetchRestrictionAnalysisBatch:mocks.analyze}));
vi.mock('../../src/lib/api', async (original) => ({...await original<any>(),fetchNucleotideSequences:vi.fn().mockResolvedValue({data:[]}),fetchNucleotideSequence:mocks.get,fetchMolecularRevision:mocks.revision,createNucleotideSequence:mocks.create,updateNucleotideSequence:mocks.update,fetchPrimerTmOptions:mocks.tmOptions}));
import { RECEIPT, SUMMARY } from './molBioRestrictionCatalogFixture';
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';
let root:Root, host:HTMLDivElement, client:QueryClient;
function RouteProbe(){const location=useLocation();mocks.navigate=useNavigate();return <output data-route-search>{location.search}</output>}
function deferred<T>() {let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>resolve=r);return {promise,resolve};}
const saved = {id:'saved-a',name:'A',sequence:'ACGT'.repeat(30),sequence_type:'dna',is_circular:false,features:[],primers:[],version:2};
async function create(name='A'){await act(async()=>mocks.input.onCreateSequence({name,sequence:'ACGT'.repeat(30),sequenceType:'dna',circular:false}));}
beforeEach(async()=>{
 localStorage.clear(); window.innerWidth=1400;mocks.revision.mockReset();
 mocks.create.mockReset();mocks.update.mockReset();mocks.get.mockReset().mockResolvedValue({data:saved});mocks.catalog.mockReset().mockResolvedValue({catalog:{catalog_id:'test'},items:[]});mocks.products.mockReset().mockResolvedValue({product_release:null});mocks.analyze.mockReset().mockResolvedValue({});mocks.orfs.mockReset().mockReturnValue([]);mocks.demos.mockReset().mockResolvedValue([]);mocks.annotationDownload.mockReset();
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
 await act(async()=>root.render(<QueryClientProvider client={client}><MemoryRouter><RouteProbe/><MolBioToolkitV2/></MemoryRouter></QueryClientProvider>));
});
afterEach(async()=>{await act(async()=>root.unmount());client.clear();host.remove();vi.unstubAllGlobals();});

it('failed selection cannot publish a workspace or its URL', async () => {
 await act(async()=>mocks.input.onSelectSequence('saved-a'));
 const before=host.querySelector('[data-route-search]')!.textContent;
 const priorSequence = mocks.header.sequenceData.sequence;
 let reject!: (error: Error) => void;
 mocks.get.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
 let pending!: Promise<unknown>;
 await act(async()=>{ pending = mocks.input.onSelectSequence('missing'); });
 expect(host.querySelector('[data-route-search]')!.textContent).toBe(before);
 await act(async()=>{ reject(new Error('sequence unavailable')); await pending; });
 expect(mocks.header.sequenceData.name).toBe('A');
 expect(mocks.header.sequenceData.sequence).toBe(priorSequence);
 expect(host.querySelectorAll('button[title="Close workspace"]')).toHaveLength(1);
 expect(host.querySelector('[data-route-search]')!.textContent).toBe(before);
});
