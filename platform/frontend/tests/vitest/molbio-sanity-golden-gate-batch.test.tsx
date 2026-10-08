import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { api } from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { GoldenGateWorkflowControls } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateWorkflowControls';
import { designGoldenGateBatch, captureAlternative, editAlternative, type BatchRequest, type BatchEvent } from '../../src/lib/goldenGateBatch';
import type { AssembleTask, SaveDesignRequest } from '../../src/lib/goldenGateWorkflowTypes';
import { expandGoldenGateWire, projectGoldenGateWire } from '../../src/lib/goldenGateWorkflowWire';
import type { RestrictionRecord, RestrictionCatalogSummary } from '../../src/lib/restrictionAnalysis';
import native from '../fixtures/golden-gate/batch-receiving.json';
import workflow from '../fixtures/golden-gate/workflow-receiving.json';
import traffic from '../fixtures/molBioTrafficPairing.json';

let host: HTMLDivElement, root: Root, qc: QueryClient;
let calls: Array<{url: string; method: string; data: any; signal?: AbortSignal | null}>;
let stream: ReadableStreamDefaultController<Uint8Array> | undefined;
let template: any;
const adapter = api.defaults.adapter;
const loaded = vi.fn();
const encoder = new TextEncoder();
let hold = false;
let sampled = false;
const asset = JSON.parse(readFileSync('../api/config/molbio/restriction/restriction_enzyme_catalog_v1.json','utf8'));
const compact: RestrictionCatalogSummary[] = asset.records.map((r: RestrictionRecord) => ({
  enzyme_id:r.enzyme_id,canonical_name:r.canonical_name,aliases:r.aliases,site_iupac:r.recognition.site_iupac,
  site_alternatives_iupac:r.recognition.site_alternatives_iupac,palindromic:r.recognition.palindromic,
  cleavage_status:r.cleavage.status,overhang_kinds:[...new Set(r.cleavage.events.map(e=>e.overhang_kind))].sort(),
  nick_strand:r.cleavage.nick?.strand??null,enzyme_kind:r.enzyme_kind,analysis_capability:r.analysis_capability,
  golden_gate_compatible:traffic.catalog.golden_gate_compatible_ids.includes(r.enzyme_id),exclusion_reason:r.exclusion_reason,
  reported_commercial:r.supplier_provenance.reported_commercial,historical_supplier_codes:r.supplier_provenance.historical_supplier_codes,
}));
function emit(name: string, body: unknown) { if (process.env.BMS_GG_UI_EVIDENCE) { mkdirSync(process.env.BMS_GG_UI_EVIDENCE,{recursive:true}); writeFileSync(join(process.env.BMS_GG_UI_EVIDENCE,name+'.json'),JSON.stringify(body,null,2)); } }
function transport() {
 api.defaults.adapter = async config => {
  const data=typeof config.data==='string'?JSON.parse(config.data):config.data;
  const url=config.url!;calls.push({url,method:config.method!,data});
  const response=(data:unknown)=>({data,status:200,statusText:'OK',headers:{},config});
  if(url.endsWith('/golden-gate/options'))return response(workflow.options);
  if(url.endsWith('/primer-tm/options'))return response(workflow.tm_options);
  if(url==='/api/user-templates') { if(config.method==='post'){template={id:'batch-config',...data};return response(template);} return response([template]); }
  if(url==='/api/user-templates/batch-config')return response(template);
  if(url.endsWith('/design/save')){expect(config.params).toEqual({view:'normalized'});return response(projectGoldenGateWire(native.saved,'saved'));}
  if(url.endsWith('/export'))return response(new Blob(['Native ZIP contents qualified by ASGI test']));
  if(url.includes('/design/'))return response(projectGoldenGateWire(native.saved,'saved'));
  throw new Error('Unexpected HTTP '+url);
 };
 vi.stubGlobal('fetch',vi.fn(async (input: string, init?: RequestInit) => {
  const url=String(input);calls.push({url,method:init?.method??'GET',data:init?.body?JSON.parse(String(init.body)):null,signal:init?.signal});
  if(url.includes('/restriction/catalog?')) {
   const params=new URL(url,'http://test').searchParams;expect(params.get('response_view')).toBe('compact');
   const offset=Number(params.get('cursor')??0);
   return new Response(JSON.stringify({schema:'bms.molbio.restriction-catalog-browse-page.v1',catalog:traffic.catalog.receipt,items:compact.slice(offset,offset+200),next_cursor:offset+200<compact.length?String(offset+200):null}));
  }
  expect(url).toBe('/api/molbio/assembly/golden-gate/design/batch');
  return new Response(new ReadableStream<Uint8Array>({start(controller){
   stream=controller;const events=sampled?native.sampled:native.events;
   const delivered=hold?events.slice(0,2):events;
   // Split JSON across chunks, including inside fields; this is stream replay, not native computation.
   const text=delivered.map(e=>JSON.stringify(e)+'\n').join(''); const midpoint=Math.floor(text.length/2);
   controller.enqueue(encoder.encode(text.slice(0,midpoint)));controller.enqueue(encoder.encode(text.slice(midpoint)));
   if(!hold)controller.close();
   init?.signal?.addEventListener('abort',()=>controller.error(new DOMException('Aborted','AbortError')));
  }}),{headers:{'Content-Type':'application/x-ndjson'}});
 }));
}
async function settle(){await act(async()=>{await new Promise(r=>setTimeout(r,5));});}
async function waitFor(fn:()=>void){await vi.waitFor(async()=>{await settle();fn();},{timeout:5000});}
async function click(text:string){const button=[...host.querySelectorAll('button')].find(b=>b.textContent===text);expect(button,text).toBeTruthy();await act(async()=>button!.click());}
async function change(label:string,value:string){const f=host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement|HTMLSelectElement;expect(f,label).toBeTruthy();await act(async()=>{Object.getOwnPropertyDescriptor(f instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value')!.set!.call(f,value);f.dispatchEvent(new Event(f instanceof HTMLSelectElement?'change':'input',{bubbles:true}));});}
async function openSummary(text:string){const summary=[...host.querySelectorAll('summary')].find(s=>s.textContent===text);expect(summary,text).toBeTruthy();await act(async()=>{const d=summary!.parentElement as HTMLDetailsElement;d.open=true;d.dispatchEvent(new Event('toggle'));});await settle();}
async function mount(){
 calls=[];hold=false;sampled=false;loaded.mockClear();template={id:'batch-config',name:'Native batch',mode:'golden_gate_design',params:structuredClone(native.request)};transport();
 qc=new QueryClient({defaultOptions:{queries:{retry:false}}});host=document.createElement('div');document.body.append(host);root=createRoot(host);
 await act(async()=>root.render(<QueryClientProvider client={qc}><AssemblyPanel sequenceData={{name:'input',sequence:'ACGT'.repeat(30),sequenceType:'dna',circular:false,features:[]}} selection={null} selectedSequenceId={null} onLoadProduct={loaded} onLoadSavedWorkup={()=>{}}/></QueryClientProvider>));
 await click('Golden Gate');await click('Raw design / evaluate / optimize / split');
 await waitFor(()=>expect(host.querySelector('[aria-label="Golden Gate task"]')).toBeTruthy());
 await openSummary('User-defined standards / saved configurations');
 await waitFor(()=>expect(host.textContent).toContain('Load configuration Native batch'));
 await click('Load configuration Native batch');
 await openSummary('Combinatorial slot alternatives');
}
afterEach(async()=>{if(root)await act(async()=>root.unmount());host?.remove();qc?.clear();api.defaults.adapter=adapter;vi.unstubAllGlobals();vi.restoreAllMocks();});

describe('Golden Gate native batch receiving at actual Assembly entry',()=>{
 it('authors full slot alternatives and issues exactly one batch POST, then uses existing viewer, fixed Save, reopen and export',async()=>{
  await mount();expect(calls.filter(c=>c.url.endsWith('/batch'))).toHaveLength(0);
  await click('Edit alternative insert / insert-1');await change('insert name','reverse synthesis alternative');
  await click('Capture current part alternative');
  await click('Run combinatorial batch');
  await waitFor(()=>expect(host.textContent).toContain('Finished: full coverage'));
  const submitted=calls.filter(c=>c.url.endsWith('/batch'));expect(submitted).toHaveLength(1);
  expect(submitted[0].data.slots[1].alternatives.find((a:any)=>a.id==='insert-1').part.name).toBe('reverse synthesis alternative');
  expect(submitted[0].data.slots[1].alternatives[0].part.preparation).toEqual(native.request.slots[1].alternatives[0].part.preparation);
  expect(submitted[0].data.scope).toEqual({mode:'full'});expect(submitted[0].signal).toBeTruthy();
  expect(calls.some(c=>c.url.endsWith('/design'))).toBe(false);
  expect(host.textContent).toContain('total 4; selected 4; evaluated 4; completed 4; omitted by selection 0');
  expect(host.querySelectorAll('button')).toBeTruthy();
  await click('View batch combination 0: vector-0 / insert-0');
  expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy();
  await click('Save fixed selected candidate');await waitFor(()=>expect(host.textContent).toContain('Saved operation'));
  const wireSave=calls.find(c=>c.url.endsWith('/design/save'))!.data;
  const save=expandGoldenGateWire<SaveDesignRequest>(wireSave,'save');
  expect(save.selection.request).toEqual(native.events[1].event==='result'?native.events[1].result!.solutions[0].fixed_request:null);
  await click('Save fixed selected candidate');await settle();
  expect(calls.filter(c=>c.url.endsWith('/design/save')).at(-1)!.data).toEqual(wireSave);
  await change('Workup name','Changed batch metadata');await click('Save fixed selected candidate');await settle();
  const changed=expandGoldenGateWire<SaveDesignRequest>(calls.filter(c=>c.url.endsWith('/design/save')).at(-1)!.data,'save');
  expect(changed.idempotency_key).not.toBe(save.idempotency_key);
  expect(changed.name).toBe('Changed batch metadata');
  await click('Load selected product with annotations');expect(loaded).toHaveBeenCalledOnce();
  await click('Reopen retained workup');await settle();expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy();
  vi.stubGlobal('URL',class extends URL {static createObjectURL(){return 'blob:test';}static revokeObjectURL(){}});
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  await click('Export frozen workup ZIP');await settle();expect(calls.some(c=>c.url.endsWith('/export'))).toBe(true);
  emit('batch-ui-full',submitted[0].data);
 });
 it('shows explicitly sampled scope and retains completed results after cancellation, without claiming full coverage',async()=>{
  await mount();await change('Batch coverage','sampled');await change('Sample combination count','2');await change('Sample seed','17');sampled=true;
  await click('Run combinatorial batch');await waitFor(()=>expect(host.textContent).toContain('Finished: sampled coverage'));
  expect(host.textContent).toContain('total 4; selected 2; evaluated 2; completed 2; omitted by selection 2');
  expect(calls.find(c=>c.url.endsWith('/batch'))!.data.scope).toEqual({mode:'sampled',count:2,seed:17});
  emit('batch-ui-sampled',calls.find(c=>c.url.endsWith('/batch'))!.data);
  hold=true;sampled=false;await change('Batch coverage','full');await click('Run combinatorial batch');
  await waitFor(()=>expect(host.textContent).toContain('Delivered results: 1'));
  await click('Cancel batch delivery');expect(calls.filter(c=>c.url.endsWith('/batch')).at(-1)!.signal!.aborted).toBe(true);
  expect(host.textContent).toContain('incomplete coverage');expect(host.textContent).toContain('selected not yet evaluated 3');
  await click('View batch combination 0: vector-0 / insert-0');expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy();
  await click('Save fixed selected candidate');await settle();expect(calls.filter(c=>c.url.endsWith('/design/save'))).toHaveLength(1);
 });
 it('persists and explicitly loads named batch configuration without science and starts an independent immutable product stage',async()=>{
  await mount();await change('Configuration name','My named batch');await change('Batch coverage','sampled');await change('Sample combination count','2');await change('Sample seed','99');
  await click('Save named configuration');await waitFor(()=>expect(host.textContent).toContain('Saved configuration My named batch'));
  expect(template.params.scope).toEqual({mode:'sampled',count:2,seed:99});expect(template.params.slots).toEqual(native.request.slots);
  await change('Batch coverage','full');await waitFor(()=>expect(host.textContent).toContain('Load configuration My named batch'));await click('Load configuration My named batch');
  expect((host.querySelector('[aria-label="Sample seed"]') as HTMLInputElement).value).toBe('99');expect(calls.some(c=>c.url.endsWith('/batch'))).toBe(false);
  sampled=true;await click('Run combinatorial batch');await waitFor(()=>expect(host.textContent).toContain('Finished: sampled coverage'));
  const index=native.sampled.find(e=>e.event==='result')!;await click(`View batch combination ${index.index}: ${index.alternatives!.join(' / ')}`);
  await click('Save fixed selected candidate');await waitFor(()=>expect(host.textContent).toContain('Saved operation'));
  await click('Start next stage from saved immutable product');expect(host.textContent).toContain(native.saved.product_revision_id);
  await change('Catalog enzyme','BbsI');await change('Configuration name','Stage two');await click('Save named configuration');await settle();
  expect(template.params.sources).toEqual([{id:'previous-stage-product',source:{kind:'molecular_revision',revision_id:native.saved.product_revision_id}}]);
  expect(template.params.enzyme.enzyme_id).toBe('BbsI');expect(native.saved.result.requested.enzyme.enzyme_id).toBe('BsaI');
 });
 it('loads the entire shared compact catalog, offers every exact enzyme and reuses it on remount',async()=>{
  await mount();await openSummary('Explicit domestication proposals');await click('Add edit request insert');
  await waitFor(()=>expect(host.textContent).toContain(`Complete catalog: ${compact.length} enzymes`));
  const select=host.querySelector('[aria-label="Add unwanted catalog enzyme"]') as HTMLSelectElement;
  expect([...select.options].slice(1).map(o=>o.value).sort()).toEqual(compact.map(e=>e.enzyme_id).sort());
  const last=compact.at(-1)!;await change('Add unwanted catalog enzyme',last.enzyme_id);
  expect((host.querySelector('[aria-label="Unwanted enzyme 1 recognition sequence"]') as HTMLInputElement).value).toBe(last.site_iupac);
  const reads=calls.filter(c=>c.url.includes('/restriction/catalog?')).length;expect(reads).toBe(Math.ceil(compact.length/200));
  await act(async()=>root.render(<QueryClientProvider client={qc}><div/></QueryClientProvider>));
  await act(async()=>root.render(<QueryClientProvider client={qc}><GoldenGateWorkflowControls value={native.request.base as unknown as AssembleTask} onChange={()=>{}} sourceChoices={[]} enzymes={[]} datasets={[]} tmOptions={null}/></QueryClientProvider>));
  await openSummary('Explicit domestication proposals');await settle();expect(calls.filter(c=>c.url.includes('/restriction/catalog?'))).toHaveLength(reads);
  expect(host.textContent).toContain(`Complete catalog: ${compact.length} enzymes`);
 });
 it('preserves all alternative control objects without forking native science fields',()=>{
  const base=structuredClone(native.request.base) as unknown as AssembleTask;
  const a=captureAlternative(base,'insert','exact');const restored=editAlternative(base,a);
  expect(captureAlternative(restored,'insert','exact')).toEqual(a);
  expect(restored.primer_settings).toEqual(base.primer_settings);expect(restored.enzyme).toEqual(base.enzyme);
 });
 it('delivers results before EOF and reports a cut stream as incomplete without discarding them',async()=>{
  calls=[];template={};hold=true;sampled=false;transport();const delivered:BatchEvent[]=[];
  const pending=designGoldenGateBatch(native.request as unknown as BatchRequest,e=>delivered.push(e));
  await vi.waitFor(()=>expect(delivered).toHaveLength(2));stream!.close();await expect(pending).rejects.toThrow('before completion');expect(delivered[1].event).toBe('result');
 });
});
