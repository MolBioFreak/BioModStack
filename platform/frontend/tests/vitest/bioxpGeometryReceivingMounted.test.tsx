import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFileSync, appendFileSync, existsSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { BioXpMethodPreview, BioXpMethodProgress } from '../../src/components/BioXpMethodPreview';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { api } from '../../src/lib/api';
const task = process.env.BIOXP_CAVRO_TASK;
// This receiving suite requires explicit retained producers and an isolated real API checkout.
let host: HTMLDivElement, root: Root, client: QueryClient, child: ChildProcessWithoutNullStreams;
let pending: ((value: any) => void)[], calls: any[], errors: string;
const original = api.defaults.adapter;
const loadGeometry = (name: string) => JSON.parse(readFileSync(`${process.env.BIOXP_GEOMETRY_PRODUCER}/${name}.json`, 'utf8'));
async function settle() { for(let pass=0;pass<3;pass++) await act(async () => { await new Promise(r=>setTimeout(r,30)); for (let i=0;i<500 && pending.length;i++) await new Promise(r=>setTimeout(r,20)); }); expect(pending.length,errors).toBe(0); }
async function mount(element: React.ReactNode) { await act(async()=>root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); await settle(); }
async function click(text: string) { const el=[...host.querySelectorAll('button')].find(e=>e.textContent===text || e.getAttribute('aria-label')===text); expect(el,text).toBeTruthy(); await act(async()=>el!.click()); await settle(); }
async function input(label: string, value: string) { const el=[...host.querySelectorAll<HTMLInputElement|HTMLSelectElement>('[aria-label]')].find(e=>e.getAttribute('aria-label')===label); expect(el,label).toBeTruthy(); await act(async()=>{ Object.getOwnPropertyDescriptor(el!.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value')!.set!.call(el,value); el!.dispatchEvent(new Event(el!.tagName==='SELECT'?'change':'input',{bubbles:true})); }); }
describe('selected native context presentation without mirrored state', () => {
 let container: HTMLDivElement, view: Root;
 beforeEach(() => { container = document.createElement('div'); document.body.append(container); view = createRoot(container); });
 afterEach(async () => { await act(async () => view.unmount()); container.remove(); });
 it('renders only selected operands on demand and no panel for unrelated steps', async () => {
  const recipe = { accounting_context: { context_id: 'exact-context', initial_channels: [{ channel: 2, pneumatic: null, liquid_intent: [] }] }, before_liquid: [{ reference: { kind: 'vessel', vessel: { fill_ul: '16.000000000000000000001' }, fallback_position_steps: 999 } }], unrelated_label: 'not duplicated' };
  const result: any = { document: { stages: [{ actions: [{ action_id: 'geometry', kind: 'pipette', params: { recipe } }, { action_id: 'note', kind: 'note', params: { message: 'ordinary note' } }] }] }, provenance: [{ occurrence_id: 'first', native_action_ids: ['geometry'] }, { occurrence_id: 'second', native_action_ids: ['note'] }], simulation: {} };
  const before = JSON.stringify(result);
  await act(async () => view.render(<BioXpMethodPreview result={result} />));
  const panel = container.querySelector<HTMLDetailsElement>('[aria-label="Authored native geometry and accounting context"]')!;
  expect(panel).toBeTruthy(); expect(panel.querySelector('section')).toBeNull();
  await act(async () => { panel.open = true; panel.dispatchEvent(new Event('toggle')); });
  expect(panel.textContent).toContain('16.000000000000000000001');
  expect(panel.textContent).toContain('exact-context'); expect(panel.textContent).not.toContain('not duplicated');
  const select = container.querySelector<HTMLSelectElement>('[aria-label="Preview occurrence"]')!;
  await act(async () => { select.value = 'second'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(container.querySelector('[aria-label="Authored native geometry and accounting context"]')).toBeNull();
  expect(JSON.stringify(result)).toBe(before); expect(result.simulation).not.toHaveProperty('native_context');
 });
});
describe.skipIf(!task || !process.env.BIOXP_METHOD_API_DIR || !process.env.BMS_TEST_PYTHON || !process.env.BIOXP_GEOMETRY_PRODUCER)('actual facade → typed forms → real compiler / exact scratch revisions',()=>{
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

 for(const [name,action,field] of [['geometry-accounting-recipe','liquid_recipe','recipe'],['rack-True','cavro_application','application']])it(`${name}: typed edits → scratch Save / exact GET / cold Open / HTTP compile`,async()=>{
  const data=loadGeometry(name);
  const value=name==='rack-True'?data.request:data;
  // Seed only the real native software example via scratch HTTP, then edit
  // ordinary discovered controls in the existing Methods draft owner.
  const seeded=(await api.post('/api/bioxp/methods/library',{name:'Native software example',method:{schema:'bms.bioxp-method.v1',name:'Native software example',steps:[{type:'action',step_id:'geometry',action,inputs:{[field]:value}}]}})).data;
  await mount(<BioXpMethodsWorkspace generation={77} connected={false} controlsEnabled={false}/>);
  await input('Library entry',seeded.id);await click('Open');
  const selected=host.querySelector('[data-method-step]')!;
  await act(async()=>(selected as HTMLElement).click());await settle();
  const id=selected.getAttribute('data-method-step');
  // Edit actual native Number text, preserving spelling instead of browser float conversion.
  const path=field==='recipe'?`Inputs ${id}.recipe.before_liquid[1].reference.vessel.fill_ul`:`Inputs ${id}.application.operations[0].profile.length_mm`;
  await input(path,field==='recipe'?'16.000000000000000000000':'5.000000000000000000000');
  if(field==='recipe'){
    const p=`Inputs ${id}.recipe`;
    for(const [suffix,text] of [
      ['before_liquid[1].reference.vessel.area_points[1].area_mm2','6.000'],
      ['before_liquid[1].reference.fallback_position_steps','999'],
      ['before_dispense[0].reference.vessel.area_mm2','10.000'],
      ['before_liquid[0].rack_transform.origin_mm.x','10.000'],
      ['shared_head.mounted_tips[0].footprint.offset_mm.z','5.000'],
      ['accounting_context.context_id','typed-context'],
      ['aspiration_intent.channel_vessels.2','source'],
      ['dispense_segments[0].target_liquid_ul','4.000'],
      ['dispense_segments[0].accounting_intent.channel_vessels.2','destination'],
    ])await input(`${p}.${suffix}`,text);
    const initial=`${p}.accounting_context.initial_channels[0].pneumatic`;
    await click(`Add ${initial} item`);
    await input(`${initial}[0].kind presence`,'value');
    await input(`${initial}[0].kind`,'0');
    await input(`${initial}[0].volume_ul presence`,'value');
    await input(`${initial}[0].volume_ul`,'0.000');
  }else{
    await input(`Inputs ${id}.application.operations[0].calibration.rack_transform.origin_mm.x`,'10.000');
    await input(`Inputs ${id}.application.operations[0].calibration.pickup.reference.fallback_position_steps`,'999');
  }
  await input('Method name','Typed geometry '+name);await click('Save');
  const save=calls.filter(c=>c.request.body?.method&&c.request.url.includes('/library')).at(-1);
  expect(save?.response.status,JSON.stringify(save)).toBe(200);
  expect(host.textContent).toContain(`Saved exact revision ${save.response.data.revision}.`);
  await act(async()=>root.unmount());client.clear();root=createRoot(host);
  await mount(<BioXpMethodsWorkspace generation={77} connected={false} controlsEnabled={false}/>);
  await input('Library entry',save.response.data.id);await click('Open');
  const exact=calls.filter(c=>c.request.method==='get'&&c.request.url.endsWith(`/library/${save.response.data.id}/revisions/${save.response.data.revision}`)).at(-1);
  expect(exact.response.data.method).toEqual(save.request.body.method);
  await click('Compile');
  const compile=calls.filter(c=>c.request.url.endsWith('/compile')).at(-1);
  expect(compile.response.data.document,JSON.stringify(compile.response.data.issues)).toBeTruthy();
  expect(compile.request.body.method).toEqual(save.request.body.method);
  const emitted=compile.response.data.document.stages[0].actions[0].params[field];
  const authored=save.request.body.method.steps[0].inputs[field];
  // Native integer-only controls normalize at compile; exact Number strings do not.
  expect(field==='recipe'?emitted.before_liquid[1].reference.vessel.fill_ul:emitted.operations[0].profile.length_mm).toBe(field==='recipe'?'16.000000000000000000000':'5.000000000000000000000');
  expect(compile.response.data.simulation).not.toHaveProperty('native_context');
  expect(compile.response.data.resolved.occurrences[0].inputs[field]).toEqual(authored);
  expect(calls.flatMap(c=>c.response.native_calls??[])).toEqual([]);
  if(process.env.BIOXP_CAVRO_UI_EVIDENCE)appendFileSync(process.env.BIOXP_CAVRO_UI_EVIDENCE+'.compiled',JSON.stringify({name,request:compile.request.body,result:compile.response.data,authored})+'\n');
 });
 for(const name of ['finish-True-True','finish-True-False','finish-False-True','finish-False-None','finish-partial-http-device','finish-partial-http-missing','finish-partial-http-interrupt'])it(`${name}: genuine native HTTP report → mounted existing report owner`,async()=>{
  const source=loadGeometry(name).result;
  const report=(await api.get(`/api/bioxp/methods/runs/${source.job_id}/report`)).data;
  expect(report.action_results).toEqual(source.execution.runtime_state.action_results);
  await mount(<BioXpMethodProgress report={report}/>);
  const panel=host.querySelector('[aria-label="Native geometry and accounting"]')!;
  expect(panel).toBeTruthy();expect(panel.closest('details')).toBeNull();
  expect(panel.textContent).toContain('not measured surface');
  const summary=report.action_results[0].pipette_result.accounting_summary;
  expect(panel.textContent).toContain(`Incomplete application: ${summary.incomplete_application?'Yes':'No'}`);
  for(const [channel,raw] of Object.entries(summary.channels)){
    const rows=[...panel.querySelector(`[aria-label="Channel ${channel} compartment accounting"]`)!.querySelectorAll('tbody tr')];
    expect(rows.map(row=>[...row.querySelectorAll('td')].map(cell=>cell.textContent))).toEqual(['pneumatic','authored_liquid_intent'].map(key=>['occupancy_ul','peak_occupancy_ul','known_prefix_peak_occupancy_ul','cumulative_travel_ul','current_state_known'].map(field=>String((raw as any).occupancy[key][field]??'Unknown / not measured'))));
  }
  expect(calls.every(c=>c.request.method==='get')).toBe(true);
 });
 for (const name of ['review-lifetime-command', 'review-lifetime-program', 'review-lifetime-query',
  'review-lifetime-seed-manual', 'review-lifetime-absent', 'review-lifetime-empty_unknown',
  'review-projection-True', 'review-projection-False',
  'review-geometry-observation-unavailable', 'review-geometry-primary-failure'])
  it.skipIf(!existsSync(`${process.env.BIOXP_GEOMETRY_PRODUCER}/${name}.json`))(`${name}: repaired producer → exact report → selected action`, async () => {
   const source = loadGeometry(name).job;
   const before = JSON.stringify(source);
   const report = (await api.get(`/api/bioxp/methods/runs/${source.job_id}/report`)).data;
   expect(report.action_results).toEqual(source.execution.runtime_state.action_results);
   for (const action of report.action_results) {
    await mount(<BioXpMethodProgress report={{ ...report, action_results: [action] }} />);
    const pipette = action.pipette_result;
    const summary = pipette?.accounting_summary;
    const geometry = (pipette?.events ?? []).filter((event: any) => ['rack_geometry', 'surface_reference'].includes(event.operation));
    const panel = host.querySelector('[aria-label="Native geometry and accounting"]');
    if (!summary && !geometry.length && pipette?.liquid_settings?.accounting_labels === undefined) {
     expect(panel).toBeNull();
     continue;
    }
    expect(panel).toBeTruthy(); expect(panel!.closest('details')).toBeNull();
    if (summary) {
     expect(panel!.textContent).toContain(`Incomplete application: ${summary.incomplete_application ? 'Yes' : 'No'}`);
     expect(summary.actual_delivery_ul).toBeNull();
     expect(panel!.querySelectorAll('[aria-label^="Channel "]')).toHaveLength(Object.keys(summary.channels).length);
     for (const [channel, raw] of Object.entries(summary.channels)) {
      const rows = [...panel!.querySelector(`[aria-label="Channel ${channel} compartment accounting"]`)!.querySelectorAll('tbody tr')];
      expect(rows.map(row => [...row.querySelectorAll('td')].map(cell => cell.textContent))).toEqual(
       ['pneumatic', 'authored_liquid_intent'].map(key => ['occupancy_ul', 'peak_occupancy_ul', 'known_prefix_peak_occupancy_ul', 'cumulative_travel_ul', 'current_state_known'].map(field => String((raw as any).occupancy[key][field] ?? 'Unknown / not measured'))));
     }
    }
    expect(panel!.querySelectorAll('[aria-label^="Native geometry "]')).toHaveLength(geometry.length);
    for (const event of geometry) expect(panel!.textContent).toContain(`${event.operation.replaceAll('_', ' ')} · ${event.status ?? 'Unknown'}`);
   }
   expect(JSON.stringify(source)).toBe(before);
   expect(calls.length).toBeGreaterThan(0);
   expect(calls.every(call => call.request.method === 'get')).toBe(true);
  });
});
