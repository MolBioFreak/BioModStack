import React,{act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,describe,it,expect,vi} from 'vitest';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {api} from '../../src/lib/api';
import {AssemblyPanel} from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import {DigestPanel} from '../../src/components/MolBioToolkit/panels/DigestPanel';
import type {SequenceData} from '../../src/components/MolBioToolkit/types';
import fixture from '../fixtures/golden-gate/workflow-receiving.json';
import traffic from '../fixtures/molBioTrafficPairing.json';
import {parseRestrictionDigestSimulation} from '../../src/lib/restrictionAnalysis';
import {exportGoldenGateDesign,importGoldenGateDesign} from '../../src/lib/goldenGateWorkflow';
import {expandGoldenGateWire,type WorkflowWireKind} from '../../src/lib/goldenGateWorkflowWire';
import type {WorkflowResult} from '../../src/lib/goldenGateWorkflowTypes';
import {digestFragmentsForAssembly} from '../../src/lib/digestAssemblyTransfer';
import type {RestrictionDigestSimulation} from '../../src/lib/restrictionAnalysis';
let host:HTMLDivElement,root:Root,qc:QueryClient;
const original=api.defaults.adapter;
let calls:Array<{url:string;method:string;data:any;signal:any}>=[];
let pending:((value:any)=>void)|null=null;
let hold=false;
const loaded=vi.fn();
const empty:SequenceData={name:'source',sequence:fixture.split_target.request.target.source.sequence,sequenceType:'dna',circular:false,features:[]};
function evidence(name:string,payload:unknown){if(process.env.BMS_GG_UI_EVIDENCE){mkdirSync(process.env.BMS_GG_UI_EVIDENCE,{recursive:true});writeFileSync(join(process.env.BMS_GG_UI_EVIDENCE,name+'.json'),JSON.stringify(payload,null,2));}}
function transport(){api.defaults.adapter=async config=>{const url=config.url!;const wire=typeof config.data==='string'?JSON.parse(config.data):config.data;let data=wire;if(wire?.schema_version==='bms.golden-gate-wire.v1'){const kind:WorkflowWireKind=url.endsWith('/save')?'save':url.endsWith('/import')?'portable':url.endsWith('/export')?'result':'request';data=expandGoldenGateWire(wire,kind);if(!url.endsWith('/export'))expect(config.params).toEqual({view:'normalized'});}calls.push({url,method:config.method!,data,signal:config.signal});const response=(data:unknown)=>({data,status:200,statusText:'OK',headers:{},config});
 if(url.endsWith('/golden-gate/options'))return response(fixture.options);
 if(url.endsWith('/primer-tm/options'))return response(fixture.tm_options);
 if(url.endsWith('/design/save'))return response(fixture.saved);
 if(url.endsWith('/design/import'))return response(data.result);
 if(url.endsWith('/export'))return response(new Blob(['contract-only archive response']));
 if(url.endsWith('/design')){if(hold)return new Promise(resolve=>{pending=()=>resolve(response(fixture.assemble_parts.result));});return response(fixture[data.task as 'assemble_parts'].result);}
 if(url.includes('/golden-gate/design/'))return response(fixture.saved);
 if(url.endsWith('/assembly-workups'))return response(fixture.shelf);
 if(url===`/api/sequences/${fixture.saved.product_document_id}`)return response(fixture.product);
 if(url==='/api/user-templates')return response(config.method==='post'?{id:'configuration',...data}:[]);
 if(url==='/api/user-templates/configuration')return response({id:'configuration',name:'Configuration',mode:'golden_gate_design',params:fixture.assemble_parts.request});
 throw new Error('Unexpected HTTP '+url);
};}
async function settle(){await act(async()=>{await new Promise(r=>setTimeout(r,5));});}
async function waitFor(fn:()=>void){await vi.waitFor(async()=>{await settle();fn();},{timeout:5000});}
async function mount(saved=false){calls=[];hold=false;pending=null;loaded.mockClear();transport();qc=new QueryClient({defaultOptions:{queries:{retry:false}}});host=document.createElement('div');document.body.append(host);root=createRoot(host);await act(async()=>root.render(<QueryClientProvider client={qc}><AssemblyPanel sequenceData={saved?{...empty,operation:'golden_gate_design',operationParams:{operation_id:fixture.saved.operation_id}}:empty} selection={null} selectedSequenceId={null} onLoadProduct={loaded} onLoadSavedWorkup={()=>{}}/></QueryClientProvider>));}
async function click(text:string){const b=Array.from(host.querySelectorAll('button')).find(b=>b.textContent===text);expect(b,`button ${text}`).toBeTruthy();await act(async()=>b!.click());}
function field(label:string){const f=host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement;expect(f, label).toBeTruthy();return f;}
async function change(label:string,value:string){const f=field(label);await act(async()=>{const proto=f instanceof HTMLSelectElement?HTMLSelectElement.prototype:f instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value')!.set!.call(f,value);f.dispatchEvent(new Event(f instanceof HTMLSelectElement?'change':'input',{bubbles:true}));});}
async function check(label:string){await act(async()=>(field(label) as HTMLInputElement).click());}
async function enter(){await mount();expect(calls).toHaveLength(0);await click('Golden Gate');await click('Raw design / evaluate / optimize / split');await waitFor(()=>expect(host.querySelector('[aria-label="Golden Gate task"]')).toBeTruthy());}
afterEach(async()=>{if(root)await act(async()=>root.unmount());host?.remove();qc?.clear();api.defaults.adapter=original;vi.restoreAllMocks();});

describe('Assembly Golden Gate native receiving',()=>{
 it('keeps the same Save request after successful acknowledgements',async()=>{
  await mount(true);await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());
  for(let i=0;i<3;i++){
   await click('Save fixed selected candidate');await waitFor(()=>expect(host.textContent).toContain('Saved operation'));
   await settle();
  }
  const saves=calls.filter(c=>c.url.endsWith('/design/save'));
  expect(saves).toHaveLength(3);expect(saves[0].data.idempotency_key).toBeTruthy();
  expect(saves[1].data).toEqual(saves[0].data);expect(saves[2].data).toEqual(saves[0].data);
  expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(0);
 });
 it('starts a new Save intent for changed metadata while preserving each retry',async()=>{
  await mount(true);await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());
  const keys:string[]=[];
  for(const [field,value] of [['Workup name','First'],['Workup name','Second'],['Workup description','Changed notes']]){
   await change(field,value);
   await click('Save fixed selected candidate');await settle();
   await click('Save fixed selected candidate');await settle();
   const saves=calls.filter(c=>c.url.endsWith('/design/save'));
   expect(saves.at(-1)!.data).toEqual(saves.at(-2)!.data);
   keys.push(saves.at(-1)!.data.idempotency_key);
  }
  expect(new Set(keys).size).toBe(3);
 });

 it('mounts all four native tasks without automatic design; evaluates through actual HTTP wrapper',async()=>{await enter();expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(0);await change('Golden Gate task','evaluate_overhangs');await change('Intended junction pairs (one representative per physical junction)','GGAG TGAC');await change('Reference dataset','pryor2020-s002');await click('Run Golden Gate task');await waitFor(()=>expect(host.querySelector('[aria-label="Empirical fidelity"]')).toBeTruthy());const call=calls.find(c=>c.url.endsWith('/design'))!;expect(call.data).toEqual(fixture.evaluate_overhangs.request);expect(call.signal).toBeTruthy();evidence('evaluate',call.data);expect(host.textContent).toContain('not yield');expect(calls.filter(c=>c.url.endsWith('/golden-gate/options'))).toHaveLength(1);});
 it('authors deterministic optimize request and consumes actual search evidence',async()=>{await enter();await change('Golden Gate task','optimize_overhangs');await change('Candidate domain','AAAA AAAC AATG CCGT TGAC');await change('Reference dataset','pryor2020-s002');await click('Run Golden Gate task');await waitFor(()=>expect(host.querySelector('[aria-label="Native Golden Gate search results"]')).toBeTruthy());const call=calls.find(c=>c.url.endsWith('/design'))!;expect(call.data).toEqual(fixture.optimize_overhangs.request);evidence('optimize',call.data);await click('Select search candidate 1');expect((field('Golden Gate task') as HTMLSelectElement).value).toBe('evaluate_overhangs');expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(1);});
 it('authors split windows, preparation and native constraints then renders physical product',async()=>{await enter();await change('Golden Gate task','split_target');await change('Catalog enzyme',fixture.split_target.request.enzyme.enzyme_id);for(const [i,start] of [20,80].entries()){await click('Add cut window');await change(`Window ${i+1} start`,String(start));await change(`Window ${i+1} end`,String(start+1));await change(`Window ${i+1} fixed position (optional)`,String(start));}await change('Split clamp','TT');await change('Split spacer','A');await change('Linear terminal right fusion','TGAC');await change('Search ranking mode','lexicographic');await check('Suggest unique reverse-complement classes');await check('Exclude palindromes from suggestions');await click('Run Golden Gate task');await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());const call=calls.find(c=>c.url.endsWith('/design'))!;expect(call.data).toEqual({...fixture.split_target.request,target:{...fixture.split_target.request.target,id:'source'}});evidence('split',call.data);await click('Save fixed selected candidate');const save=calls.find(c=>c.url.endsWith('/design/save'))!;expect(save.data.selection.request.task).toBe('assemble_parts');expect(save.data.selection.request).toEqual(fixture.split_target.result.solutions[0].fixed_request);expect(save.data.selection.authored_request.task).toBe('split_target');evidence('split-save',save.data);});
 it('reopens through real GET, clones settings, fixed Saves without optimizer, and preserves product annotations',async()=>{await mount(true);await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(0);await click('Clone retained settings into editable design');await click('Run Golden Gate task');await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());const request=calls.find(c=>c.url.endsWith('/design'))!.data;expect(request).toEqual(fixture.assemble_parts.request);evidence('assemble',request);await click('Save fixed selected candidate');await waitFor(()=>expect(host.textContent).toContain('Saved operation'));const save=calls.find(c=>c.url.endsWith('/design/save'))!.data;evidence('assemble-save',save);expect(save.selection.request).toEqual(fixture.assemble_parts.result.solutions[0].fixed_request);expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(1);await click('Load selected product with annotations');expect(loaded).toHaveBeenCalledOnce();expect(loaded.mock.calls[0][0].features.length).toBeGreaterThan(0);expect(loaded.mock.calls[0][0].primers.length).toBe(2);await click('Reopen retained workup');await settle();expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(1);});
 it('exposes every automatic primer scalar and retains exact requested settings in a real component POST',async()=>{await mount(true);await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());await click('Clone retained settings into editable design');await check('Automatic primers insert');const values={primer_min_length:20,primer_max_length:20,product_min_length:60,product_max_length:60,flank_search_span:20,gc_min_percent:0,gc_max_percent:100,tm_target_c:62,tm_max_delta_c:100,gc_clamp_min:0,max_poly_x:100,max_pairs:8};for(const [k,v] of Object.entries(values))await change('insert '+k,String(v));await change('insert pair rank','1');await click('Run Golden Gate task');await settle();const data=calls.find(c=>c.url.endsWith('/design'))!.data;expect(data.automatic_primers).toEqual([{part_id:'insert',pair_rank:1,settings:values}]);evidence('automatic',data);});
 it('suppresses a superseded design and aborts its read signal without persistence cancellation',async()=>{await mount(true);await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());await click('Clone retained settings into editable design');hold=true;await click('Run Golden Gate task');await change('insert name','changed');const call=calls.find(c=>c.url.endsWith('/design'))!;expect(call.signal.aborted).toBe(true);await act(async()=>pending?.(null));await settle();expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeNull();expect(calls.some(c=>c.url.endsWith('/design/save'))).toBe(false);});
 it('reads shelf only on demand and opens exactly the selected operation without recomputation',async()=>{await enter();expect(calls.some(c=>c.url.endsWith('/assembly-workups'))).toBe(false);await click('Saved Golden Gate workups (not loaded)');await waitFor(()=>expect(host.textContent).toContain('Open Mounted Golden Gate'));await click('Open Mounted Golden Gate');await waitFor(()=>expect(host.querySelector('[aria-label="Selected product DNA"]')).toBeTruthy());expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(0);expect(calls.filter(c=>c.url.includes('/golden-gate/design/'))).toHaveLength(1);});
 it('uses the frozen preview/saved export and import contract (transport only; ZIP writer owned by native sibling)',async()=>{calls=[];transport();const r=fixture.evaluate_overhangs.result as unknown as WorkflowResult;await exportGoldenGateDesign(r);await exportGoldenGateDesign(r,'op/a');await importGoldenGateDesign({schema_version:'bms.golden-gate-workflow-portable.v1',result:r});expect(calls.map(c=>[c.method,c.url])).toEqual([['post','/api/molbio/assembly/golden-gate/design/export'],['get','/api/molbio/assembly/golden-gate/design/op%2Fa/export'],['post','/api/molbio/assembly/golden-gate/design/import']]);expect(calls[0].data).toEqual(r);});
 it('mounts Digest selection and transfers physical fragments to the receiving Assembly form',async()=>{await mount();const simulation=parseRestrictionDigestSimulation(JSON.parse(traffic.digest.simulation));let transferred:any;await act(async()=>root.render(<QueryClientProvider client={qc}><DigestPanel sequenceData={empty} sequenceId="source-document" onHighlight={()=>{}} catalog={simulation.catalog} catalogRecords={[]} analysis={null} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={()=>{}} onSimulateDigest={()=>{}} onUseInAssembly={fragments=>{transferred=fragments;root.render(<QueryClientProvider client={qc}><AssemblyPanel sequenceData={empty} selection={null} selectedSequenceId={null} transferredFragments={fragments} onLoadProduct={loaded} onLoadSavedWorkup={()=>{}}/></QueryClientProvider>);}}/></QueryClientProvider>));await check('Use digest fragment 0');await check('Use digest fragment 1');await click('Use selected fragments in assembly');expect(transferred).toHaveLength(2);expect(host.textContent).toContain('source digest 0');await click('Golden Gate');await settle();expect(host.querySelector('select[aria-label="Left end notation"]') || host.textContent).toBeTruthy();expect(transferred[0].right_end.overhang).toBe(simulation.fragments[0].right_end.overhang_sequence_5to3);evidence('mounted-digest-transfer',transferred);});
 it('preserves every physical digest end, ordered source segment and provenance in direct assembly transfer',()=>{const d=fixture.assemble_parts.result.solutions[0].design.digests[0];const sim={...fixture.assemble_parts.result.solutions[0].design,source:{name:'donor'},catalog:fixture.options.catalog,selected_enzyme_ids:[fixture.assemble_parts.request.enzyme.enzyme_id],fragments:d.fragments,simulation_sha256:'captured-design-digest'} as unknown as RestrictionDigestSimulation;const fragments=digestFragmentsForAssembly(sim,[0,1],'source-id','donor');expect(fragments).toHaveLength(2);for(const [i,f] of fragments.entries()){expect(f.sequence).toBe(d.fragments[i].top_strand_sequence);expect(f.left_end?.protruding_strand).toBe(d.fragments[i].left_end.protruding_strand);expect(f.right_end?.overhang).toBe(d.fragments[i].right_end.overhang_sequence_5to3??'');expect(f.metadata?.source_segments).toEqual(d.fragments[i].source_segments);}evidence('digest-transfer',fragments);});
});
