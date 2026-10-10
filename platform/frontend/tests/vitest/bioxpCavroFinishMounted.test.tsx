import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFileSync, appendFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { BioXpPipetteResults } from '../../src/components/BioXpPipetteResults';
import { BioXpPipetteNativeTools } from '../../src/components/BioXpPipetteNativeTools';
import { BioXpCalibrationAnalysis } from '../../src/components/BioXpCalibrationAnalysis';
import { BioXpPressureCapture } from '../../src/components/BioXpPressureCapture';
import { BioXpWorkflowJobMonitor } from '../../src/components/BioXpWorkflowJobMonitor';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { api } from '../../src/lib/api';
import { methodBindingsSchema } from '../../src/components/BioXpMethodFields';
import { methodsPost, type MethodCompile } from '../../src/lib/bioxpMethods';
const task = process.env.BIOXP_CAVRO_TASK;
// This receiving suite requires explicit retained producers and an isolated real API checkout.
let host: HTMLDivElement, root: Root, client: QueryClient, child: ChildProcessWithoutNullStreams;
let pending: ((value: any) => void)[], calls: any[], errors: string;
const original = api.defaults.adapter;
const load = (file: string) => JSON.parse(readFileSync(`${task}/${file}`, 'utf8'));
async function settle() { for(let pass=0;pass<3;pass++) await act(async () => { await new Promise(r=>setTimeout(r,30)); for (let i=0;i<500 && pending.length;i++) await new Promise(r=>setTimeout(r,20)); }); expect(pending.length,errors).toBe(0); }
async function mount(element: React.ReactNode) { await act(async()=>root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); await settle(); }
async function click(text: string) { const el=[...host.querySelectorAll('button')].find(e=>e.textContent===text || e.getAttribute('aria-label')===text); expect(el,text).toBeTruthy(); await act(async()=>el!.click()); await settle(); }
async function input(label: string, value: string) { const el=[...host.querySelectorAll<HTMLInputElement|HTMLSelectElement>('[aria-label]')].find(e=>e.getAttribute('aria-label')===label); expect(el,label).toBeTruthy(); await act(async()=>{ Object.getOwnPropertyDescriptor(el!.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value')!.set!.call(el,value); el!.dispatchEvent(new Event(el!.tagName==='SELECT'?'change':'input',{bubbles:true})); }); }
function resolve(schema: any, root: any): any { while(schema.$ref) schema = schema.$ref.slice(2).split('/').reduce((v:any,k:string)=>v[k],root); return schema; }
/** Drive ordinary published controls. Only test inputs come from retained native producers. */
async function fill(label: string, raw: any, value: any, rootSchema: any): Promise<void> {
 let schema=resolve(raw,rootSchema);
 const flatten=(s:any):any[]=>{const r=resolve(s,rootSchema);return r.oneOf||r.anyOf?(r.oneOf??r.anyOf).flatMap(flatten):[r];};
 const choices=schema.oneOf||schema.anyOf?flatten(schema):undefined;
 if(choices) {
  const index=choices.findIndex((c:any)=>{const s=resolve(c,rootSchema); if(value===null)return s.type==='null'; if(Array.isArray(value))return s.type==='array'; if(typeof value==='object')return (s.type==='object'||s.properties)&&Object.entries(s.properties??{}).every(([k,v])=>{const field=resolve(v,rootSchema);return (!Object.hasOwn(field,'const')||field.const===value[k])&&(!field.enum||field.enum.includes(value[k]));}); return s.type===typeof value || (typeof value==='number'&&s.type==='integer');});
  expect(index,`${label} union`).toBeGreaterThanOrEqual(0); await input(`${label} variant`,String(index)); schema=resolve(choices[index],rootSchema);
 }
 if(schema.enum || Object.hasOwn(schema,'const')) { const values=schema.enum??[schema.const]; await input(label,String(values.findIndex((v:any)=>v===value))); return; }
 if(value===null)return;
 if(Array.isArray(value)){for(const [i,row] of value.entries()){await click(`Add ${label} item`);await fill(`${label}[${i}]`,schema.items,row,rootSchema);}return;}
 if(typeof value==='object'){
  for(const [k,v] of Object.entries(value)){
   if(!schema.properties?.[k]){await input(`${label} property name`,k); await click(`Add ${label.replaceAll('_',' ')} property`);}
   const presence=[...host.querySelectorAll('[aria-label]')].find(e=>e.getAttribute('aria-label')===`${label}.${k} presence`) as HTMLSelectElement;
   if(presence?.value==='omitted')await input(`${label}.${k} presence`,'value');
   await fill(`${label}.${k}`,schema.properties?.[k]??schema.additionalProperties??{},v,rootSchema);
  }return;
 }
 if(typeof value==='boolean'){const e=[...host.querySelectorAll<HTMLInputElement>('input')].find(e=>e.getAttribute('aria-label')===label)!;if(e.checked!==value)await act(async()=>e.click());return;}
 await input(label,String(value));
}
describe('actual facade → typed forms → real compiler / exact scratch revisions',()=>{
 beforeEach(({task:test})=>{
  vi.stubGlobal('crypto',webcrypto);localStorage.clear();calls=[];pending=[];errors='';
  child=spawn(process.env.BMS_TEST_PYTHON!,['tests/fixtures/bioxpCavroFacadeBridge.py'],{env:{...process.env,BIOXP_CAVRO_REVIEW_LEASE_REFUSAL:test.name.includes('explicit lease nondelivery')?'1':'0'},cwd:process.cwd(),stdio:'pipe'});
  child.stderr.on('data',chunk=>{errors+=String(chunk);if(process.env.BIOXP_CAVRO_UI_EVIDENCE)appendFileSync(process.env.BIOXP_CAVRO_UI_EVIDENCE+'.stderr',chunk);});
  const localPending = pending;
  createInterface({input:child.stdout}).on('line',line=>{try{const value=JSON.parse(line);if(value.bridge_id!==undefined)localPending.shift()?.(value);}catch{errors+=line;}});
  child.on('exit',code=>{while(pending.length)pending.shift()!({status:599,error:`bridge ${code}: ${errors}`});});
  api.defaults.adapter=async config=>{
   const request={bridge_id:String(calls.length)+':'+pending.length,method:config.method,url:config.url,body:config.data?JSON.parse(config.data):undefined,params:config.params};
   const response:any=await new Promise(resolve=>{pending.push(resolve);child.stdin.write(JSON.stringify(request)+'\n');});calls.push({request,response});
   if(process.env.BIOXP_CAVRO_UI_EVIDENCE && !/catalog|schema|examples|starters/.test(request.url??''))appendFileSync(process.env.BIOXP_CAVRO_UI_EVIDENCE,JSON.stringify({request,response})+'\n');
   if(response.status>=400)throw new AxiosError(response.error??'Real facade error','ERR_BAD_RESPONSE',config,undefined,{config,status:response.status,statusText:'error',headers:{},data:response.data});
   return {config,status:response.status,statusText:'actual isolated ASGI',headers:{},data:response.data};
  };
  host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});
 });
 afterEach(async()=>{await act(async()=>root.unmount());client.clear();host.remove();await settle();child.stdin.end();await new Promise(resolve=>child.once('exit',resolve));api.defaults.adapter=original;vi.unstubAllGlobals();});
 for(const id of ['pipette_volume_calibration','pipette_height_calibration','rc_calibration','oc_calibration','tc_calibration'])it(`${id}: ordinary unbound authoring Save / exact GET / cold Open, no run`,async()=>{
  await mount(<BioXpMethodsWorkspace generation={77} connected={false} controlsEnabled={false}/>);
  const examples=calls.find(c=>c.request.url.endsWith('/examples')).response.data;
  const index=examples.findIndex((e:any)=>e.id===id);expect(index).toBeGreaterThanOrEqual(0);
  await input('Scientific example',String(index));await settle();await click('Save');
  const save=calls.find(c=>c.request.method==='post'&&c.request.url.endsWith('/library'));expect(save.response.status).toBe(201);
  expect(save.request.body.method.steps).toEqual(examples[index].method.steps);
  expect(host.textContent).toContain('Saved exact revision 1.');
  await act(async()=>root.unmount());client.clear();root=createRoot(host);
  await mount(<BioXpMethodsWorkspace generation={77} connected={false} controlsEnabled={false}/>);
  await input('Library entry',save.response.data.id);await click('Open');
  expect(host.textContent).toContain('Opened exact revision 1.');
  const exact=calls.filter(c=>c.request.method==='get'&&c.request.url.endsWith(`/library/${save.response.data.id}/revisions/1`)).at(-1);
  expect(exact?.response.data.method).toEqual(save.request.body.method);
  expect(exact?.response.data).toEqual(save.response.data);
  expect((host.querySelector('[aria-label="Method name"]') as HTMLInputElement).value).toBe(save.request.body.method.name);
  expect(calls.flatMap(c=>c.response.native_calls??[])).toEqual([]);
  expect(calls.some(c=>/\/runs$|quick-runs$/.test(c.request.url))).toBe(false);
 });
 for(const id of ['pipette_volume_calibration','pipette_height_calibration','rc_calibration','oc_calibration','tc_calibration'])it(`${id}: ordinary starter binding controls → real compiler (inert authored companion)`,async()=>{
  await mount(<BioXpMethodsWorkspace generation={77} connected={false} controlsEnabled={false}/>);
  const examples=calls.find(c=>c.request.url.endsWith('/examples')).response.data,catalog=calls.find(c=>c.request.url.endsWith('/catalog')).response.data;
  const index=examples.findIndex((e:any)=>e.id===id),companion=load(`bms-model-finish/exports/${id}-bound.json`).request;
  await input('Scientific example',String(index));await settle();
  const schema=methodBindingsSchema(examples[index].method,catalog);
  await fill('Bindings',schema,companion.bindings,schema);
  await click('Compile');
  const compile=calls.filter(c=>c.request.url.endsWith('/compile')).at(-1);expect(compile.response.data.document,JSON.stringify(compile.response.data.issues)).toBeTruthy();
  expect(compile.response.data.document.stages).toEqual(load(`bms-model-finish/exports/${id}-bound.json`).result.document.stages);
  expect(calls.flatMap(c=>c.response.native_calls??[])).toEqual([]);
 });
 it('pure analysis typed request → real relay → exact returned fit → deliberate immutable class pin',async()=>{
  const producer=load('native-science-finish/exports/analysis-to-recipe.json').analysis;
  await mount(<BioXpCalibrationAnalysis visible connected generation={77}/>);await click('Measurement analysis');
  const schema=calls.find(c=>c.request.url.endsWith('/calibration/schema')).response.data.analysis_request;
  await fill('Analysis',schema,producer.requested,schema);await click('Analyze measurements (no device call)');
  expect(host.textContent).toContain(producer.correction.scale);expect(host.textContent).toContain('Repeatability (sample SD)');
  expect(calls.filter(c=>c.request.method==='post')).toHaveLength(1);
  await click('Select correction for a liquid-class copy');
  await input('Pinned class name','UI native fit copy');await input('Correction calibration ID','inert-fit');await input('Correction calibration revision','r1');
  await click('Pin correction as new liquid class');
  const saved=calls.find(c=>c.request.method==='post'&&c.request.url.endsWith('/liquid-classes'));
  expect(saved.response.status).toBe(201);expect(saved.response.data.method.authored_settings.selected_correction.function).toEqual(producer.correction);
  expect(saved.response.data.method.calibration_analysis).toEqual(producer);expect(host.textContent).toContain('Pinned class UI native fit copy');
  expect(calls.some(c=>c.request.url.endsWith(`/liquid-classes/${saved.response.data.id}/revisions/1`))).toBe(true);
 });
 it('bounded pressure reader uses real relay / retained native counts, never starts acquisition',async()=>{
  const capture=load('native-science-finish/exports/pressure-public-receiving.json').capture;
  await mount(<BioXpPressureCapture visible connected generation={77}/>);await click('Pressure capture result');await input('Pressure stream session ID',capture.stream.stream_session_id);await click('Read retained capture');
  expect(host.textContent).toContain(capture.stream.stream_session_id);expect(host.textContent).toContain('controller_pressure_counts');
  expect(host.textContent).toContain('Lost samplesUnknown');expect(host.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  expect(calls.every(c=>c.request.method==='get')).toBe(true);await click('Pressure capture result');expect(host.querySelector('[aria-label="Retained pressure result"]')?.closest('[hidden]')).toBeNull();
 });
 for(const [file,state] of [['pressure-public-receiving','completed'],['review-pressure-readback-None','interrupted'],['failed-capture-ValueError','failed']])it(`${state} retained pressure: exact native samples and outcome, no acquisition`,async()=>{
  const capture=load(`native-receipts-finish/committed/exports/${file}.json`).capture;
  await mount(<BioXpPressureCapture visible connected generation={77}/>);await click('Pressure capture result');await input('Pressure stream session ID',capture.stream.stream_session_id);await click('Read retained capture');
  expect(host.textContent).toContain(state);
  expect(calls.at(-1).response.data).toEqual(capture);
  const expected=capture.chunks.flatMap((chunk:any)=>chunk.scientific_result.samples);
  const rows=[...host.querySelectorAll('tbody tr')].map(row=>[...row.querySelectorAll('td')].map(cell=>cell.textContent));
  expect(rows).toEqual(expected.map((sample:any)=>['sample_sequence','raw_pressure','robot_receive_time','controller_timestamp','receive_sequence','packet_sample_index'].map(key=>String(sample[key]??'Unknown'))));
  expect(calls.every(c=>c.request.method==='get')).toBe(true);
 });
 for(const methodsFacade of [false,true])for(const invalid of ['', 'not-a-number', 'explicit lease nondelivery'])it(`non-dispatched measurement validation ${JSON.stringify(invalid)} facade=${methodsFacade}: edit and deliberately resubmit without reloading`,async()=>{
  const rejected=load('native-science-finish/exports/review-first-fail-same-key-rejected.json');
  const leaseRefusal=invalid==='explicit lease nondelivery';
  await mount(<BioXpWorkflowJobMonitor methodsFacade={methodsFacade} jobId={rejected.still_held.job_id} generation={77} connected controlsEnabled busyRef={{current:false}} onBusyChange={()=>{}}/>);
  async function field(label:string,value:string){
   const el=[...host.querySelectorAll('label')].find(e=>e.textContent?.startsWith(label))!.querySelector('input')!;
   await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));});
  }
  await field('Reviewer',rejected.request.reviewer);
  for(const [i,value] of rejected.request.measurements.probe.entries())await field(`External probe reading ${i+1}`,leaseRefusal?String(value):invalid);
  const before=calls.at(-1).response.native_calls.length;
  await click('Acknowledge protocol review');
  const first=calls.filter(c=>c.request.url.endsWith('/review')).at(-1);
  expect(first.response.status).toBe(leaseRefusal?409:422);
  if(leaseRefusal)expect(first.response.data.detail).toEqual({delivery:'not_submitted',dispatch_state:'not_dispatched',native_reason:'Injected pre-dispatch lease refusal'});
  else expect(first.response.data.detail).toEqual([expect.objectContaining({type:'value_error',loc:['body','measurements'],input:first.request.body.measurements})]);
  expect(first.response.native_calls).toHaveLength(before);
  expect(first.response.native_calls.filter(([route]:[string])=>route==='protocol_review')).toEqual([]);
  expect(host.textContent).toContain('Response not submitted');
  for(const [i,value] of rejected.request.measurements.probe.entries())await field(`External probe reading ${i+1}`,String(value));
  await field('Review note','Deliberately edited after nondelivery');
  await click('Acknowledge protocol review');
  const posts=calls.filter(c=>c.request.url.endsWith('/review'));
  expect(posts).toHaveLength(2);
  expect(posts[1].request.body.measurements).toEqual(Object.fromEntries(Object.entries(rejected.request.measurements).map(([k,v])=>[k,(v as any[]).map(String)])));
  expect(posts[1].request.body.idempotency_key).not.toBe(first.request.body.idempotency_key);
  expect(posts[1].request.body.note).toBe('Deliberately edited after nondelivery');
  expect(posts[1].response.native_calls).toHaveLength(before+1);
  // The retained native producer deliberately fails persistence on its first delivery.
  expect(posts[1].response.status).toBe(500);
 });
 for(const fault of ['bare 422','unrelated validation','different input','unmatched command','unmatched generation','unmatched key'])it(`injected ${fault} is not nondelivery or matched rejection: retain frozen response`,async()=>{
  const rejected=load('native-science-finish/exports/review-first-fail-same-key-rejected.json');
  const adapter=api.defaults.adapter as Function;
  let first=true;
  api.defaults.adapter=async config=>{
   try{return await adapter(config);}catch(error){
    if(first && config.url?.endsWith('/review')){
     first=false;const body=JSON.parse(config.data);
     const control={accepted:false,command_id:body.command_id,ownership_generation:body.expected_ownership_generation,idempotency_key:body.idempotency_key};
     if(fault==='unmatched command')control.command_id='another-job';
     if(fault==='unmatched generation')control.ownership_generation++;
     if(fault==='unmatched key')control.idempotency_key='another-response';
     const detail=fault==='bare 422'?'Native refusal without delivery evidence':fault==='unrelated validation'?[{type:'value_error',loc:['body','note'],input:body.measurements}]:fault==='different input'?[{type:'value_error',loc:['body','measurements'],input:{probe:['different']}}]:{error:'workflow_review_rejected',control};
     const status=fault.startsWith('unmatched')?409:422;
     if(process.env.BIOXP_CAVRO_UI_EVIDENCE)appendFileSync(process.env.BIOXP_CAVRO_UI_EVIDENCE,JSON.stringify({injected_client_fault:fault,status,detail})+'\n');
     throw new AxiosError('Injected receiving error','ERR_BAD_RESPONSE',config,undefined,{config,status,statusText:'injected',headers:{},data:{detail}});
    }throw error;
   }
  };
  await mount(<BioXpWorkflowJobMonitor jobId={rejected.still_held.job_id} generation={77} connected controlsEnabled busyRef={{current:false}} onBusyChange={()=>{}}/>);
  async function field(label:string,value:string){const el=[...host.querySelectorAll('label')].find(e=>e.textContent?.startsWith(label))!.querySelector('input')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));});}
  await field('Reviewer',rejected.request.reviewer);
  for(const [i,value] of rejected.request.measurements.probe.entries())await field(`External probe reading ${i+1}`,String(value));
  await click('Acknowledge protocol review');expect(host.textContent).toContain('Response outcome uncertain');
  await field('Review note','Must not replace an uncertain body');await field('External probe reading 1','32');
  await click('Acknowledge protocol review');
  const posts=calls.filter(c=>c.request.url.endsWith('/review'));expect(posts).toHaveLength(2);
  expect(posts[1].request.body).toEqual(posts[0].request.body);
 });
 it('actual native persistence failure / same-key rejection / deliberate fresh same-job response through BMS relay',async()=>{
  const rejected=load('native-science-finish/exports/review-first-fail-same-key-rejected.json'),fresh=load('native-science-finish/exports/review-deliberate-fresh-attempt.json');
  const uuid=vi.spyOn(webcrypto,'randomUUID').mockReturnValueOnce(rejected.request.idempotency_key).mockReturnValueOnce(fresh.request.idempotency_key);
  await mount(<BioXpWorkflowJobMonitor jobId={rejected.still_held.job_id} generation={77} connected controlsEnabled busyRef={{current:false}} onBusyChange={()=>{}}/>);
  const reviewer=[...host.querySelectorAll('label')].find(e=>e.textContent?.startsWith('Reviewer'))!.querySelector('input')!;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(reviewer,rejected.request.reviewer);reviewer.dispatchEvent(new Event('input',{bubbles:true}));});
  for(const [i,value] of rejected.request.measurements.probe.entries()){
   const el=[...host.querySelectorAll('label')].find(e=>e.textContent?.startsWith(`External probe reading ${i+1}`))!.querySelector('input')!;
   await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,String(value));el.dispatchEvent(new Event('input',{bubbles:true}));});
  }
  await click('Acknowledge protocol review');expect(host.textContent).toContain('Response outcome uncertain');
  const note=[...host.querySelectorAll('label')].find(e=>e.textContent?.startsWith('Review note'))!.querySelector('input')!;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(note,'Edit while uncertain');note.dispatchEvent(new Event('input',{bubbles:true}));});
  await click('Acknowledge protocol review');expect(host.textContent).toContain('Response rejected');
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(note,'');note.dispatchEvent(new Event('input',{bubbles:true}));});
  await click('Acknowledge protocol review');
  const posts=calls.filter(c=>c.request.url.endsWith('/review'));
  expect(posts.map(c=>c.response.status)).toEqual([500,409,200]);expect(posts[0].request.body).toEqual(posts[1].request.body);
  expect(posts[2].request.body).toEqual({...posts[0].request.body,idempotency_key:fresh.request.idempotency_key});
  // Same successful response/key may still be deliberately replayed through the relay.
  const replay=await api.post(posts[2].request.url,posts[2].request.body);
  expect(replay.status).toBe(200);expect(replay.data).toEqual(posts[2].response.data);
  expect(calls.filter(c=>c.request.url.endsWith('/review'))).toHaveLength(4);
  expect(uuid).toHaveBeenCalledTimes(2);uuid.mockRestore();
 });
 for(const failed of [false,true])it(`actual public diagnostic report / later channel failure ${failed}: retained answer is not completion`,async()=>{
  const producer=load(`native-runs/resumed-native-cross-owner-fixed/native-exports/integrated-public-diagnostic-${failed?'True':'False'}.json`);
  const action=producer.job.execution.runtime_state.action_results[0],event=action.pipette_result.events[0];
  await mount(<BioXpPipetteResults value={action}/>);
  const panel=host.querySelector('[aria-label="Device command reports"]')!;
  expect(panel).toBeTruthy();expect(panel.closest('details')).toBeNull();
  const channel=panel.querySelector('[aria-label="Device channel 0"]')!;
  const native=(failed?event.partial.pipette_partial_result:event.result).channels[0].result;
  const report=native.reports[0];
  expect([...channel.querySelectorAll('tbody td')].map(e=>e.textContent)).toEqual([report.command_index,report.answer_index,report.report_kind,report.value,report.units,report.parsed,report.attribution_unknown,report.response.receive_sequence,report.response.received_at].map(String));
  expect(channel.textContent).toContain(`Completion verified${failed?'No':'Yes'}`);
  expect(channel.textContent).toContain('Physical effect verifiedNo');
  if(failed)expect(panel.textContent).toContain('Failed channel: 1 · ack_timeout');
  expect(calls).toEqual([]);
 });
 for(const name of ['device','geometry','profile','pressure'])it(`${name}: empty typed controls → actual Methods compile`,async()=>{
  let compiled:MethodCompile|undefined;
  await mount(<BioXpPipetteNativeTools visible enabled run={async(action,inputs)=>{compiled=await methodsPost('compile',{method:{schema:'bms.bioxp-method.v1',name:`UI ${name}`,steps:[{type:'action',step_id:'typed',action,inputs}]}});}}/>);
  const catalog=calls.find(c=>c.request.url.endsWith('/catalog')).response.data;
  const action=name==='profile'?'profile_pickup':'cavro_application';
  const value=name==='profile'?load('native-application/exports/profile-10.json').request:{application:name==='device'?load('native-application/exports/device-instructions.json').requested:name==='geometry'?load('native-application/exports/geometry-None.json').application:load('native-runs/integrated-cross-owner-route/native-exports/pressure-public-receiving.json').application};
  await input('Native tool',action);
  const source=catalog.actions.find((a:any)=>a.action===action).inputs;
  const schema={...source,$defs:{...catalog.native_definitions,...source.$defs}};
  await fill(action,schema,value,schema);await click('Run selected native action');await settle();
  expect(compiled?.document,JSON.stringify(compiled?.issues)).toBeTruthy();
  expect(calls.flatMap(c=>c.response.native_calls??[])).toEqual([]);
 });
});
