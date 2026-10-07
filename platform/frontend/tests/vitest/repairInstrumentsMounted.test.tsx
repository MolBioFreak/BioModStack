import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { readFileSync, readlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import { methodNumber } from '../../src/lib/bioxpMethodNumber';
import { duplicateCanvasNode } from '../../src/lib/bioxpMethodCanvas';
import type { MethodValue, MethodCompile } from '../../src/lib/bioxpMethods';
import fallbackCatalog from '../fixtures/bioxp_method_deck_catalog.json';
const catalog = process.env.BIOXP_METHOD_MODEL_CONTRACT ? JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT,'utf8')).catalog : fallbackCatalog;
let host: HTMLDivElement, root: Root, current: MethodValue, bindings: MethodValue;
const receipts: unknown[] = [];
async function mount(initial: MethodValue, initialBindings: MethodValue = {}, preview?: MethodCompile) {
 function Owner() { const [method,setMethod] = useState(initial), [values,setValues] = useState(initialBindings); current=method; bindings=values; return <BioXpMethodDeckWorkbench preview={preview} bindings={values} onBindingsChange={setValues} method={method} onChange={setMethod} catalog={catalog} rootSchema={{}} />; }
 root=createRoot(host); await act(async()=>root.render(<Owner/>));
}
const sheet=()=>host.querySelector<HTMLElement>('[aria-label="On-deck editor"]')!;
async function click(label:string) { const b=[...host.querySelectorAll<HTMLElement>('button,[role=button]')].find(e=>e.textContent===label || e.getAttribute('aria-label')===label); expect(b,label).toBeTruthy(); await act(async()=>b!.dispatchEvent(new MouseEvent('click',{bubbles:true}))); }
async function station(id:string, well?:string) { const target=host.querySelector<SVGElement>(`[data-station="${id}"]${well ? `[data-well="${well}"]` : ':not([data-well])'}`)!; expect(target).toBeTruthy(); await act(async()=>{target.focus();target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}); return target; }
async function input(label:string,value:string) {const el=sheet().querySelector<HTMLInputElement|HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el,label).toBeTruthy(); await act(async()=>{ Object.getOwnPropertyDescriptor(el.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value')!.set!.call(el,value); el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true})); });}
const segment=(t:string)=>({bank:'nest',target_temp_c:methodNumber(t),duration_s:methodNumber('0.1250'),start:'dispatch'});
const profile=(id='program')=>({type:'action',step_id:id,action:'thermal_profile',inputs:{repeat:methodNumber('2'),segments:['10','20','30','40'].map(segment)},required_capability:null,on_error:'pause_for_operator'});
const draft=(steps:unknown[])=>({schema:'bms.bioxp-method.v1',name:'Synthetic repair instrument probe',steps});
beforeEach(()=>{ vi.stubGlobal('crypto',webcrypto); host=document.createElement('div'); document.body.append(host); });
afterEach(async()=>{await act(async()=>root?.unmount());host.remove();vi.unstubAllGlobals();});
it('blank TC opens a non-mutating compact shell; first authored stage alone creates the AST',async()=>{
 await mount(draft([])); const original=structuredClone(current); await station('LOC_TC'); expect(current).toEqual(original); expect(sheet().hidden).toBe(false); expect(sheet().querySelector('h2')?.textContent).toBe('Thermal cycler'); expect(sheet().querySelector('[data-program-view="compact"]')).not.toBeNull();
 await click('Add stage'); expect((current.steps as any[])).toHaveLength(1); expect((current.steps as any[])[0].inputs).toEqual({segments:[{}]});
});
it.each(['0','2'])('whole composed program (%s passes) survives real TC close/reopen, exact edits, expansion, portal Escape and cold remount; real compiler receives selected subgroup',async passes=>{
 await mount(draft([profile()])); const origin=await station('LOC_TC'); await input('Stage 2 Target temperature (°C)','0021.2500'); await click('Expand editor');
 expect(sheet().querySelector('[aria-label="Programmed temperature scale"]')).not.toBeNull(); expect(sheet().textContent).toContain('At step start');
 expect(sheet().querySelectorAll(':scope > header')).toHaveLength(1); expect(sheet().querySelectorAll('.bioxp-method-thermal > header')).toHaveLength(0);
 await input('Repeat first step','1'); await input('Repeat last step','2'); await input('Selected group total passes',passes);
 const field=sheet().querySelector<HTMLInputElement>('[aria-label="Selected group total passes"]')!; field.focus(); await act(async()=>field.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))); expect(sheet().hidden).toBe(true); expect(document.activeElement).toBe(origin);
 await station('LOC_TC'); expect((sheet().querySelector('[aria-label="Selected group total passes"]') as HTMLInputElement).value).toBe(passes); await click('Repeat selected steps');
 expect((current.steps as any[])[0].steps.map((n:any)=>n.action)).toEqual(['thermal_hold','thermal_profile','thermal_hold']);
 await click('Close editor'); await station('LOC_TC'); expect(sheet().querySelector('[aria-label="Temperature program group"]')).not.toBeNull(); expect(sheet().querySelectorAll('.bioxp-thermal-stages > li')).toHaveLength(4);
 const cold=JSON.parse(JSON.stringify(current)); await act(async()=>root.unmount()); await mount(cold); await station('LOC_TC'); expect(sheet().querySelectorAll('.bioxp-thermal-stages > li')).toHaveLength(4); expect(current).toEqual(cold);
 const python=process.env.BMS_TEST_PYTHON; expect(python,'real compiler Python must be provided').toBeTruthy();
 const request={method:current,bindings:{},dependencies:{}};
 const run=spawnSync('unshare',['--user','--map-root-user','--net',python!,'-c','import tests.conftest; import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps(compile_method(json.load(sys.stdin))))'],{cwd:'../api',encoding:'utf8',input:JSON.stringify(request),timeout:10000,env:{...process.env,_BIOXP_PYTEST_NETNS:'1',_BIOXP_PYTEST_PARENT_NETNS:readlinkSync('/proc/self/ns/net')}});
 expect(run.status,run.stderr).toBe(0);const result=JSON.parse(run.stdout); expect(result.issues).toEqual([]); const actions=result.document.stages.flatMap((s:any)=>s.actions); expect(actions.map((a:any)=>a.kind)).toEqual(['thermal_hold','thermal_profile','thermal_hold']); expect(actions[1].params.repeat).toBe(Number(passes)); expect(actions[1].params.segments.map((s:any)=>[s.target_temp_c,s.duration_s])).toEqual([[21.25,0.125],[30,0.125]]); expect(actions[0].params.target_temp_c).toBe(10);expect(actions[2].params.target_temp_c).toBe(40);
 receipts.push({request,result}); if(process.env.BIOXP_INSTRUMENT_EXPORT) writeFileSync(process.env.BIOXP_INSTRUMENT_EXPORT,JSON.stringify(receipts,null,2));
});
it('multiple programs and nested duplicate local IDs retain explicit selected whole scope and do not swallow the door',async()=>{
 const first={type:'group',step_id:'first',steps:[profile('same')]}; const second={type:'group',step_id:'second',steps:[{type:'repeat',step_id:'repeat',count:methodNumber('2'),steps:[profile('same')]}]};
 await mount(draft([first,{type:'action',step_id:'door',action:'thermal_door',inputs:{door_command:'DO'}},second])); await station('LOC_TC'); await input('Temperature program','/steps/2'); await click('Close editor'); await station('LOC_TC'); expect((sheet().querySelector('[aria-label="Temperature program"]') as HTMLSelectElement).value).toBe('/steps/2'); expect(sheet().querySelectorAll('.bioxp-thermal-repeat-frame')).toHaveLength(2); expect(current.steps).toEqual([first,{type:'action',step_id:'door',action:'thermal_door',inputs:{door_command:'DO'}},second]); const cold=JSON.parse(JSON.stringify(current)); await act(async()=>root.unmount()); await mount(cold); await station('LOC_TC'); expect((sheet().querySelector('[aria-label="Temperature program"]') as HTMLSelectElement).value).toBe('/steps/2');
});
it.each(['rc','oc'])('reopens %s elapsed task and edits associated start in place with later wait outside group',async bank=>{
 const timer=bank+'-timer'; await mount(draft([{type:'group',step_id:'task',steps:[{type:'action',step_id:'cool',action:'chiller_setpoint',inputs:{bank,target_temp_c:methodNumber('004.00')}},{type:'action',step_id:'start',action:'timer_start',inputs:{timer_id:timer,seconds:methodNumber('000.2500')}}]},{type:'action',step_id:'other',action:'note',inputs:{message:'other work'}},{type:'action',step_id:'wait',action:'timer_wait',inputs:{timer_id:timer}}]));
 await station(bank==='rc'?'LOC_RC':'LOC_OC'); expect(sheet().textContent).toContain('Continue other steps; wait later'); expect(sheet().textContent).toContain('/steps/2'); expect((sheet().querySelector('[aria-label="Existing chiller elapsed seconds"]') as HTMLInputElement).value).toBe('000.2500'); await input('Existing chiller elapsed seconds','003.7500'); await click('Close editor'); await station(bank==='rc'?'LOC_RC':'LOC_OC'); expect((sheet().querySelector('[aria-label="Existing chiller elapsed seconds"]') as HTMLInputElement).value).toBe('003.7500'); expect((current.steps as any[])[0].steps).toHaveLength(2); expect((current.steps as any[])[2].inputs.timer_id).toBe(timer); expect(sheet().textContent).not.toContain('Add elapsed timer');
});
it('A7 well activation exposes only A7 contents, never the station first A1 Transfer',async()=>{
 await mount({...draft([{type:'action',step_id:'A1-transfer',action:'transfer',inputs:{source:{station:'LOC_RC',location_id:3,wells:['A1']}}}]),deck_plan:{labware:[{id:'rc',name:'Reagents',station:'LOC_RC'}],materials:[{id:'a',name:'A one'},{id:'b',name:'A seven'}],assignments:[{id:'a1',labware_id:'rc',well:'A1',material_id:'a',volume_ul:'01.00'},{id:'a7',labware_id:'rc',well:'A7',material_id:'b',volume_ul:'07.00'}]}});
 await station('LOC_RC','A7'); const material=sheet().querySelector('.bioxp-workflow-materials') ?? sheet(); expect(material.textContent).toContain('A seven'); expect(sheet().querySelector('[aria-label="Actions touching selected well"]')?.textContent).not.toContain('A1-transfer');
 const label=[...sheet().querySelectorAll('label')].find(l=>l.textContent?.includes('Association 2 planned amount'))!; const field=label.querySelector('input')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(field,'007.500');field.dispatchEvent(new Event('input',{bubbles:true}));});expect((current.deck_plan as any).assignments.map((a:any)=>a.volume_ul)).toEqual(['01.00','007.500']);
});

it('nested bound thermal leaf edits use the existing bindings owner and retain raw expressions',async()=>{
 const reference={expr:{version:1,op:'param',id:'bound'}};
 await mount({...draft([{type:'group',step_id:'outer',steps:[{type:'action',step_id:'hold',action:'thermal_hold',inputs:reference}]}]),parameters:[{id:'bound',type:'object'}]}, {bound:segment('003.7500')});
 await station('LOC_TC'); await input('Step Target temperature (°C)','004.5000'); expect(((current.steps as any[])[0].steps[0]).inputs).toEqual(reference); expect((bindings.bound as any).target_temp_c).toEqual(methodNumber('004.5000')); await click('Expand editor'); await click('Step 1 Duplicate'); const children=(current.steps as any[])[0].steps; expect(children).toHaveLength(2); expect(children[1].step_id).not.toBe('hold'); const copiedId=children[1].inputs.expr.id; expect(copiedId).not.toBe('bound'); expect(bindings[copiedId]).toEqual(bindings.bound); const second=sheet().querySelectorAll<HTMLInputElement>('[aria-label="Step Target temperature (°C)"]')[1]; await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(second,'005.25');second.dispatchEvent(new Event('input',{bubbles:true}));});expect((bindings.bound as any).target_temp_c).toEqual(methodNumber('004.5000'));expect((bindings[copiedId] as any).target_temp_c).toEqual(methodNumber('005.25'));
});
it('plotted child reorder, nested repeat and ungroup preserve authored IDs and envelopes',async()=>{
 const a={type:'action',step_id:'a',action:'thermal_hold',inputs:segment('10'),on_error:'pause_for_operator',extension:{keep:null}}, b={...a,step_id:'b',inputs:segment('20')};
 await mount(draft([{type:'group',step_id:'whole',steps:[a,b]}])); await station('LOC_TC'); await click('Expand editor'); await click('Step 2 Move earlier'); expect((current.steps as any[])[0].steps).toEqual([b,a]);
 await input('Repeat first child','0'); await input('Repeat last child','1'); await input('Selected children total passes','003'); await click('Repeat selected children'); expect((current.steps as any[])[0].steps[0].steps).toEqual([b,a]); expect(sheet().querySelectorAll('.bioxp-thermal-repeat-frame')).toHaveLength(1);
 await click('Ungroup repeat; keep steps'); expect((current.steps as any[])[0].steps[0].type).toBe('group'); expect((current.steps as any[])[0].steps[0].steps).toEqual([b,a]);
});
it('copied elapsed tasks receive independent timer identities; external links are retained',()=>{
 const node={type:'group',step_id:'task',steps:[{type:'action',step_id:'start',action:'timer_start',inputs:{timer_id:'original',seconds:methodNumber('000.1250')}},{type:'action',step_id:'wait',action:'timer_wait',inputs:{timer_id:'original'}},{type:'action',step_id:'external',action:'timer_wait',inputs:{timer_id:'outside'}}]};
 const copy=duplicateCanvasNode(draft([node]),node,{}).node as any; expect(copy.steps[0].inputs.timer_id).not.toBe('original'); expect(copy.steps[1].inputs.timer_id).toBe(copy.steps[0].inputs.timer_id); expect(copy.steps[2].inputs.timer_id).toBe('outside'); expect(node.steps[0].inputs.timer_id).toBe('original'); expect(copy.steps[0].inputs.seconds).toEqual(methodNumber('000.1250'));
});


function receive(method: MethodValue) {
 const request={method,bindings:{},dependencies:{}};
 const run=spawnSync('unshare',['--user','--map-root-user','--net',process.env.BMS_TEST_PYTHON!,'-c','import tests.conftest; import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps(compile_method(json.load(sys.stdin))))'],{cwd:'../api',encoding:'utf8',input:JSON.stringify(request),timeout:10000,env:{...process.env,_BIOXP_PYTEST_NETNS:'1',_BIOXP_PYTEST_PARENT_NETNS:readlinkSync('/proc/self/ns/net')}});
 expect(run.status,run.stderr).toBe(0); const result=JSON.parse(run.stdout); receipts.push({request,result}); if(process.env.BIOXP_INSTRUMENT_EXPORT) writeFileSync(process.env.BIOXP_INSTRUMENT_EXPORT,JSON.stringify(receipts,null,2)); expect(result.document,JSON.stringify(result.issues)).toBeTruthy(); return result;
}
const plannedPlate={labware:[{id:'plate',name:'Reaction plate',station:'LOC_MS',kind:'plate'}],materials:[],assignments:[]};
const manualTransfer={source:{station:'LOC_RC',location_id:3,wells:['A1']},destination:{station:'LOC_MS',location_id:0,wells:['A1'],labware_id:'plate'},volume_ul:'002.5000',channels:[0],aspirate_speed:'030.00',dispense_speed:'040.00',source_position_flag:'0',destination_position_flag:'0',source_lift_height_steps:null,destination_lift_height_steps:null};
it('occurrence and explicit endpoint update are beside the Transfer, preserve retained endpoints until chosen, and change actual compiler targets',async()=>{
 const original={...draft([{type:'action',step_id:'carry',action:'plate_move',inputs:{labware_id:'plate',plate_id:'PL_POOL',target_location:'LOC_TC'}},{type:'action',step_id:'add',action:'transfer',inputs:manualTransfer}]),deck_plan:plannedPlate};
 const before=receive(original); await mount(original,{},before); await click('Edit connection add');
 expect(sheet().querySelector('[aria-label="Endpoint placement context"]')).not.toBeNull(); expect(host.querySelector('.bioxp-tools-popover [aria-label="Endpoint placement context"]')).toBeNull();
 await input('Endpoint placement context',before.provenance.find((p:any)=>p.step_id==='carry').occurrence_id); expect((current.steps as any[])[1].inputs).toEqual(manualTransfer); await click('Update destination to planned plate location');
 expect((current.steps as any[])[1].inputs.destination).toEqual({...manualTransfer.destination,station:'LOC_TC',location_id:2}); const after=receive(current); expect(after.document.stages.flatMap((s:any)=>s.actions).filter((a:any)=>a.kind==='pipette_position' && a.params.operation==='move').map((a:any)=>a.params.location_id)).toEqual([3,2]);
});
it('plate preparation retains its grid while editing and exposes source/destination, channels and mode',async()=>{
 await mount({...draft([{type:'action',step_id:'add',label:'Addition A',action:'transfer',inputs:manualTransfer}]),deck_plan:plannedPlate}); await click('Prepare Reaction plate'); const preparation=sheet().querySelector('[aria-label="Plate preparation"]')!; expect(preparation.textContent).toContain('Reagent chiller · A1 → A1'); expect(preparation.textContent).toContain('0 · Manual speeds'); await click('Preparation well A2'); expect(sheet().querySelector('[aria-label="Plate preparation"]')).not.toBeNull(); expect((current.steps as any[])[0].inputs.destination.wells).toEqual(['A1','A2']);
});


it.each(['rc','oc'])('blank %s one-click timing authors wait-here, cold reopens and emits exactly one start/wait',async bank=>{
 await mount(draft([])); await station(bank==='rc'?'LOC_RC':'LOC_OC'); expect(current.steps).toEqual([]); expect(sheet().querySelector('[aria-label="Chiller elapsed seconds"]')).not.toBeNull();
 await input('Step Target temperature (°C)','004.1250'); await input('Chiller timer identity',bank+'-new'); await input('Chiller elapsed seconds','000.2500'); await click('Add elapsed timer');
 const cold=JSON.parse(JSON.stringify(current)); await act(async()=>root.unmount()); await mount(cold); await station(bank==='rc'?'LOC_RC':'LOC_OC'); expect(sheet().textContent).toContain('Wait here'); expect((sheet().querySelector('[aria-label="Existing chiller elapsed seconds"]') as HTMLInputElement).value).toBe('000.2500');
 await input('Existing chiller elapsed seconds','003.7500'); const result=receive(current); const actions=result.document.stages.flatMap((s:any)=>s.actions); expect(actions.map((a:any)=>a.kind)).toEqual(['chiller_setpoint','timer_start','timer_wait']); expect(actions[0].params).toMatchObject({bank,target_temp_c:4.125}); expect(actions[1].params).toMatchObject({timer_id:bank+'-new',seconds:3.75}); expect(actions[2].params.timer_id).toBe(bank+'-new');
});


it('well contents stays addressed to the explicitly selected labware when two plates share a station',async()=>{
 await mount({...draft([]),deck_plan:{labware:[{id:'one',name:'Plate One',station:'LOC_RC'},{id:'two',name:'Plate Two',station:'LOC_RC'}],materials:[{id:'a',name:'Only One'},{id:'b',name:'Only Two'}],assignments:[{id:'a',labware_id:'one',well:'A7',material_id:'a',volume_ul:'01'},{id:'b',labware_id:'two',well:'A7',material_id:'b',volume_ul:'02'}]}});
 await click('Prepare Plate Two'); await station('LOC_RC','A7'); expect(sheet().textContent).toContain('Only Two'); expect([...sheet().querySelectorAll('.bioxp-plan-row > strong')].map(e=>e.textContent)).toEqual(['Plate Two · A7 → Only Two']); expect([...sheet().querySelectorAll('label')].filter(l=>l.textContent?.includes('planned amount'))).toHaveLength(1);
});
it('single hold expands into a nonrepeating program only on explicit Add hold',async()=>{
 const hold={type:'action',step_id:'single',action:'thermal_hold',inputs:{...segment('0037.00'),unknown:null},on_error:'pause_for_operator',required_capability:null};
 await mount(draft([hold])); await station('LOC_TC'); expect(current.steps).toEqual([hold]); await click('Add hold'); const group=(current.steps as any[])[0];expect(group.type).toBe('group');expect(group.step_id).toBe('single');expect(group.steps[0]).toMatchObject({action:'thermal_hold',inputs:hold.inputs,on_error:'pause_for_operator',required_capability:null});expect(group.steps[1].inputs).toEqual({});await click('Close editor');await station('LOC_TC');expect(sheet().querySelectorAll('.bioxp-thermal-stages > li')).toHaveLength(2);
});


it('chiller task association survives other work inserted inside its explicit group without guessing ambiguous peers',async()=>{
 const cool={type:'action',step_id:'cool',action:'chiller_setpoint',inputs:{bank:'rc',target_temp_c:methodNumber('4')}};
 const start={type:'action',step_id:'start',action:'timer_start',inputs:{timer_id:'t',seconds:methodNumber('003.7500')}};
 const work={type:'action',step_id:'work',action:'note',inputs:{message:'Other work'}};
 await mount(draft([{type:'group',step_id:'task',steps:[cool,work,start]},{type:'action',step_id:'wait',action:'timer_wait',inputs:{timer_id:'t'}}]));await station('LOC_RC');expect((sheet().querySelector('[aria-label="Existing chiller elapsed seconds"]') as HTMLInputElement).value).toBe('003.7500');
 await act(async()=>root.unmount());const ambiguous=draft([{type:'group',step_id:'ambiguous',steps:[cool,work,start,{...start,step_id:'other-start',inputs:{timer_id:'other',seconds:methodNumber('1')}}]}]);await mount(ambiguous);await station('LOC_RC');expect(sheet().querySelector('[aria-label="Retained chiller timer links"]')?.textContent).toContain('No unique authored task association');expect(current).toEqual(ambiguous);
});


it.each(['Stage 1 Target temperature (°C)','Stage 1 Hold time (seconds)'])('keyboard activation and Escape from %s retain the draft and restore the actual origin',async label=>{
 await mount(draft([profile()]));const origin=host.querySelector<SVGElement>('[data-station="LOC_TC"]:not([data-well])')!;await act(async()=>{origin.focus();origin.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));});expect(sheet().hidden).toBe(false);await input(label,'003.7500');const saved=structuredClone(current);const field=sheet().querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;field.focus();await act(async()=>field.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));expect(sheet().hidden).toBe(true);expect(document.activeElement).toBe(origin);await station('LOC_TC');expect(current).toEqual(saved);const close=sheet().querySelector<HTMLButtonElement>('[aria-label="Close editor"]')!;close.focus();await act(async()=>close.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));expect(sheet().hidden).toBe(true);expect(document.activeElement).toBe(origin);
});
