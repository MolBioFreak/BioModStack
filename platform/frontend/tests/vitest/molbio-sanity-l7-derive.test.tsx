import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
    viewer: {} as any, navigate: null as any, revision: vi.fn(), panels: {} as Record<string, any>, summaries: vi.fn(), primer: {} as any, feature: {} as any, digest: {} as any, input: {} as any, header: {} as any, annotation: {} as any, visibility: {} as any, orfs: vi.fn(),
    create: vi.fn(), update: vi.fn(), get: vi.fn(), catalog: vi.fn(), products: vi.fn(), analyze: vi.fn(), tmOptions: vi.fn(), demos: vi.fn(), annotationDownload: vi.fn(),
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
vi.mock('../../src/lib/api', async (original) => ({...await original<any>(),fetchNucleotideSequences:vi.fn().mockResolvedValue({data:[]}),fetchNucleotideSequence:mocks.get,fetchMolecularRevision:mocks.revision,fetchMolecularRevisionSummaries:mocks.summaries,createNucleotideSequence:mocks.create,updateNucleotideSequence:mocks.update,fetchPrimerTmOptions:mocks.tmOptions,calculatePrimerTm:vi.fn().mockResolvedValue({data:[{tm:62,gc_percent:50,algorithm:'nn_santalucia_hicks_2004'}]})}));
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

const REV_SEQ = 'ACGT'.repeat(30);
const revision = (n: number, relation = 'historical') => ({
  document_id: 'saved-a', sequence_id: 'saved-a', document_kind: 'plasmid', document_name: 'A', revision_id: `rev-${n}`, revision_number: n,
  parent_revision_id: null, change_kind: 'edit', relation, snapshot: { name: 'A', sequence: REV_SEQ, sequence_type: 'dna', is_circular: false, features: [], primers: [] },
  content_sha256: 'f'.repeat(64), content_length: REV_SEQ.length, operation_id: null, provenance: {}, created_at: '2026-01-01T00:00:00Z', created_by: null,
  reopen_destination: { surface: 'molbio-sequence-revision', params: { sequence_id: 'saved-a', revision_id: `rev-${n}` } },
});
const click = async (label: string) => { const b = [...host.querySelectorAll('button')].find((x) => x.textContent?.trim() === label); expect(b, label).toBeDefined(); await act(async () => b!.click()); await act(async () => new Promise((r) => setTimeout(r, 20))); };
const search = () => host.querySelector('[data-route-search]')!.textContent!;

it('exact-revision view enables Digest, PCR and Assembly, seeds Assembly with the revision, and never writes it', async () => {
  mocks.revision.mockResolvedValue(revision(1));
  await act(async () => mocks.input.onSelectSequence('saved-a'));
  mocks.get.mockClear();
  await act(async () => mocks.navigate({ pathname: '/', search: '?molbio_sequence_id=saved-a&molbio_revision_id=rev-1' }));
  await act(async () => new Promise((r) => setTimeout(r, 50)));
  expect(host.textContent).toContain('Read-only');
  const tabs = Object.fromEntries([...host.querySelectorAll('.panel-tabs button')].map((b) => [b.textContent, (b as HTMLButtonElement).disabled]));
  expect(tabs).toMatchObject({ Digest: false, PCR: false, Assembly: false, History: false });
  expect(tabs.Edit).toBe(true); // still read-only for editing
  await click('Assembly');
  expect(mocks.panels.AssemblyPanel.transferredFragments).toHaveLength(1);
  expect(mocks.panels.AssemblyPanel.transferredFragments[0]).toMatchObject({ source_sequence_id: 'saved-a', source_revision: 1, sequence: REV_SEQ });
  await click('Digest');
  expect(mocks.panels.DigestPanel.sequenceData.sequence).toBe(REV_SEQ);
  await click('PCR');
  expect(mocks.panels.PCRPanel.sequenceData.sequence).toBe(REV_SEQ);
  expect(mocks.panels.PCRPanel.sequenceId).toBeNull(); // the viewed revision is not a Save target
  expect(mocks.update).not.toHaveBeenCalled();
  expect(mocks.create).not.toHaveBeenCalled();
});

it('saved clean molecule submits molecular_revision for Digest; unsaved edits submit inline_dna', async () => {
  mocks.catalog.mockResolvedValue({ catalog: RECEIPT, items: SUMMARY });
  mocks.summaries.mockResolvedValue([{ id: 'r', revision_id: 'rev-9', sequence_id: 'saved-a', revision_number: 3, change_kind: 'edit', content_sha256: 'e'.repeat(64), content_length: REV_SEQ.length, topology: 'linear', created_at: '2026-01-01T00:00:00Z', created_by: null, is_current: true }]);
  await act(async () => mocks.input.onSelectSequence('saved-a'));
  await click('Digest');
  await act(async () => new Promise((r) => setTimeout(r, 50)));
  const sources: any[] = [];
  mocks.analyze.mockImplementation(async (args: any) => { sources.push(args.source); return {}; });
  await act(async () => { mocks.panels.DigestPanel.onAnalyzeAll?.(); mocks.panels.DigestPanel.onDigestSelectionChange?.(['EcoRI']); });
  await act(async () => new Promise((r) => setTimeout(r, 50)));
  const clean = sources.at(-1);
  expect(clean).toBeDefined();
  expect(clean).toMatchObject({ kind: 'molecular_revision', sequence_id: 'saved-a', revision_id: 'rev-9', expected_content_sha256: 'e'.repeat(64) });
  // dirty editor content stays inline
  sources.length = 0;
  await click('Edit');
  await act(async () => mocks.panels.EditPanel.onSequenceChange({ ...mocks.panels.EditPanel.sequenceData, sequence: REV_SEQ + 'A' }, 'Append A'));
  await click('Digest');
  await act(async () => { mocks.panels.DigestPanel.onAnalyzeAll?.(); mocks.panels.DigestPanel.onDigestSelectionChange?.(['EcoRI']); });
  await act(async () => new Promise((r) => setTimeout(r, 50)));
  const dirty = sources.at(-1);
  expect(dirty).toBeDefined();
  expect(dirty.kind).toBe('inline_dna');
});

it('an operation-only deep link opens the product-role output through the exact-revision opener', async () => {
  mocks.revision.mockResolvedValue(revision(2));
  vi.mocked(fetch).mockImplementation(async (url: any) => {
    if (String(url).startsWith('/api/molbio/operations/op-1')) {
      return { ok: true, json: async () => ({ operation_id: 'op-1', operation_type: 'restriction_digest', inputs: [{ revision_id: 'rev-in' }], outputs: [
        { revision_id: 'rev-a', document_id: 'doc-a', role: 'digest_fragment' }, { revision_id: 'rev-2', document_id: 'saved-a', role: 'product' }] }) } as any;
    }
    return { ok: true, json: async () => ({ workups: [], schema: 'x', items: [], next_cursor: null, product_release: null }) } as any;
  });
  await act(async () => mocks.navigate({ pathname: '/', search: '?molbio_operation_id=op-1' }));
  await act(async () => new Promise((r) => setTimeout(r, 80)));
  expect(search()).toContain('molbio_sequence_id=saved-a');
  expect(search()).toContain('molbio_revision_id=rev-2');
  expect(mocks.revision).toHaveBeenCalledWith('saved-a', 'rev-2');
  expect(host.textContent).toContain('Read-only');
});
