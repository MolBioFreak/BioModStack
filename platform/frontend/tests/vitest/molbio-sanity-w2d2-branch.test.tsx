import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
    viewer: {} as any, navigate: null as any, revision: vi.fn(), panels: {} as Record<string, any>, summaries: vi.fn(), primer: {} as any, feature: {} as any, digest: {} as any, input: {} as any, header: {} as any, annotation: {} as any, visibility: {} as any, orfs: vi.fn(),
    branchExport: vi.fn(), branchImport: vi.fn(), create: vi.fn(), update: vi.fn(), get: vi.fn(), catalog: vi.fn(), products: vi.fn(), analyze: vi.fn(), tmOptions: vi.fn(), demos: vi.fn(), annotationDownload: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({SequenceViewer:(props:any)=>{mocks.viewer=props;return <div data-qa-sequence-viewer onContextMenu={props.onContextMenu}><button data-qa-select-range onClick={()=>props.onSelection({start:2,end:22})}>Select QA range</button></div>}}));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({VisibilityPanel:(props:any)=>{mocks.visibility=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/utils/orfs', () => ({findOpenReadingFrames:mocks.orfs}));
vi.mock('../../src/components/MolBioToolkit/GCContentTrack', () => ({GCContentTrack:()=> <div data-qa-analytics>Sequence analytics</div>}));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', async (original) => {const actual=await original<any>();return {SequenceHeader:(props:any)=>{mocks.header=props;return <actual.SequenceHeader {...props}/>}};});
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({MolecularInputModal:(props:any)=>{mocks.input=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({AutoAnnotatePanel:(props:any)=>{mocks.annotation=props;return null}}));
vi.mock('../../src/components/MolBioToolkit/panels', () => Object.fromEntries(['AlignmentPanel','AssemblyPanel','DigestPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name=>[name,(props:any)=>{mocks.panels[name]=props;return null}])));
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
vi.mock('../../src/lib/api', async (original) => ({...await original<any>(),fetchNucleotideSequences:vi.fn().mockResolvedValue({data:[]}),fetchNucleotideSequence:mocks.get,fetchMolecularRevision:mocks.revision,fetchMolecularRevisionSummaries:mocks.summaries,createNucleotideSequence:mocks.create,exportMolBioBranch:mocks.branchExport,importMolBioBranch:mocks.branchImport,updateNucleotideSequence:mocks.update,fetchPrimerTmOptions:mocks.tmOptions,calculatePrimerTm:vi.fn().mockResolvedValue({data:[{tm:62,gc_percent:50,algorithm:'nn_santalucia_hicks_2004'}]})}));
import { RECEIPT, SUMMARY } from './molBioRestrictionCatalogFixture';
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';
let root:Root, host:HTMLDivElement, client:QueryClient;
function RouteProbe(){const location=useLocation();mocks.navigate=useNavigate();return <output data-route-search>{location.search}</output>}
function deferred<T>() {let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>resolve=r);return {promise,resolve};}
const saved = {id:'saved-a',name:'A',sequence:'ACGT'.repeat(30),sequence_type:'dna',is_circular:false,features:[],primers:[],version:2};
async function create(name='A'){await act(async()=>mocks.input.onCreateSequence({name,sequence:'ACGT'.repeat(30),sequenceType:'dna',circular:false}));}
beforeEach(async()=>{
 localStorage.clear(); window.innerWidth=1400;mocks.revision.mockReset();mocks.branchExport.mockReset();mocks.branchImport.mockReset();
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

const REV_SEQ = 'ACGT'.repeat(30);
const revision = (n: number) => ({
  document_id: 'saved-a', sequence_id: 'saved-a', document_kind: 'plasmid', document_name: 'A', revision_id: `rev-${n}`, revision_number: n,
  parent_revision_id: null, change_kind: 'edit', relation: 'historical', snapshot: { name: 'A', sequence: REV_SEQ, sequence_type: 'dna', is_circular: false, features: [], primers: [] },
  content_sha256: 'f'.repeat(64), content_length: REV_SEQ.length, operation_id: null, provenance: {}, created_at: '2026-01-01T00:00:00Z', created_by: null,
  reopen_destination: { surface: 'molbio-sequence-revision', params: { sequence_id: 'saved-a', revision_id: `rev-${n}` } },
});
const tick = () => act(async () => new Promise((r) => setTimeout(r, 40)));
const branchButton = () => [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Branch (.molbio-branch.json)')) as HTMLButtonElement | undefined;
const openExport = async () => { const b = [...host.querySelectorAll('button')].find((x) => x.textContent?.trim() === 'Export')!; await act(async () => b.click()); };
const bundle = { format: 'molbio-branch', version: 1, selected: ['old-rev'], documents: [{ id: 'old-doc' }], revisions: [{ id: 'old-rev', document_id: 'old-doc' }], operations: [], inputs: [], outputs: [] };
const imported = (already: boolean) => ({ batch_id: 'b1', already_imported: already, id_map: { documents: { 'old-doc': 'saved-a' }, revisions: { 'old-rev': 'rev-2' }, operations: {} }, created: already ? { documents: 0, revisions: 0, operations: 0 } : { documents: 3, revisions: 4, operations: 2 }, selected: ['rev-2'] });
const file = (content: unknown, name: string) => ({ name, text: async () => JSON.stringify(content) }) as unknown as File;

it('Branch export posts the current revision id and downloads the bundle', async () => {
  mocks.summaries.mockResolvedValue([{ revision_id: 'rev-old', is_current: false }, { revision_id: 'rev-cur', is_current: true }]);
  mocks.branchExport.mockResolvedValue(bundle);
  let blob: Blob | undefined; let filename = '';
  URL.createObjectURL = vi.fn((b: Blob) => { blob = b; return 'blob:x'; }); URL.revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; });
  await act(async () => mocks.input.onSelectSequence('saved-a'));
  await openExport();
  await act(async () => branchButton()!.click()); await tick();
  expect(mocks.branchExport).toHaveBeenCalledWith(['rev-cur']);
  expect(filename).toBe('A.molbio-branch.json');
  const text = await new Promise<string>((resolve) => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob!); });
  expect(JSON.parse(text)).toEqual(bundle);
  click.mockRestore();
});

it('unsaved editor content disables the Branch option with a reason', async () => {
  await create();
  await openExport();
  expect(branchButton()!.disabled).toBe(true);
  expect(branchButton()!.title).toContain('Save');
});

it('importing a branch bundle posts it, reports counts and opens the first selected revision', async () => {
  mocks.branchImport.mockResolvedValue(imported(false));
  mocks.revision.mockResolvedValue(revision(2));
  await act(async () => mocks.input.onImportFile(file(bundle, 'x.molbio-branch.json'), 'preserve')); await tick(); await tick();
  expect(mocks.branchImport).toHaveBeenCalledWith(bundle);
  expect(host.textContent).toContain('Branch imported: 3 documents, 4 revisions, 2 operations created.');
  expect(mocks.revision).toHaveBeenCalledWith('saved-a', 'rev-2');
  expect(host.textContent).toContain('Read-only');
});

it('importing the same bundle again says nothing new was created', async () => {
  mocks.branchImport.mockResolvedValue(imported(true));
  mocks.revision.mockResolvedValue(revision(2));
  await act(async () => mocks.input.onImportFile(file(bundle, 'x.molbio-branch.json'), 'preserve')); await tick();
  expect(host.textContent).toContain('already imported');
  expect(host.textContent).toContain('nothing new created');
});

it('non-branch Toolkit JSON still follows the existing import path', async () => {
  vi.spyOn(window, 'alert').mockImplementation(() => {});
  await act(async () => mocks.input.onImportFile(file({ name: 'T', sequence: 'ACGT' }, 't.molbio.json'), 'preserve')); await tick();
  expect(mocks.branchImport).not.toHaveBeenCalled();
});
