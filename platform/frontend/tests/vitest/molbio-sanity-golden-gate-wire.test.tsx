import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach,expect,it} from 'vitest';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {api} from '../../src/lib/api';
import {designGoldenGate,saveGoldenGateDesign,readGoldenGateDesign,exportGoldenGateDesign,importGoldenGateDesign,freezeGoldenGateSelection} from '../../src/lib/goldenGateWorkflow';
import {expandGoldenGateWire} from '../../src/lib/goldenGateWorkflowWire';
import type {WorkflowRequest,WorkflowResult,SaveDesignRequest} from '../../src/lib/goldenGateWorkflowTypes';
import fixtureJson from '../fixtures/golden-gate/wire-receiving.json?raw';
import browserRequestsJson from '../fixtures/golden-gate/wire-browser-requests.json?raw';
// Parse actual JSON bytes: Vite's JSON-to-object-literal transform treats
// __proto__ specially, unlike the real Axios JSON receiver.
const fixture=JSON.parse(fixtureJson);
const original=api.defaults.adapter;
afterEach(()=>{api.defaults.adapter=original;});

it('mounted action sends one normalized POST and consumes real ASGI preview/save/GET/import bodies',async()=>{
  const calls:Array<{url:string;method:string;body:any;params:any;signal:any}>=[];
  const controller=new AbortController();
  api.defaults.adapter=async config=>{
    const url=config.url!, body=typeof config.data==='string'?JSON.parse(config.data):config.data;
    calls.push({url,method:config.method!,body,params:config.params,signal:config.signal});
    let data:unknown;
    if(url.endsWith('/design'))data=fixture.normalized;
    else if(url.endsWith('/save'))data=fixture.saved_normalized;
    else if(url.endsWith('/import'))data=fixture.imported;
    else if(url.endsWith('/export'))data=new Blob(['ZIP bytes are verified by native receiving tests']);
    else data=fixture.saved_normalized;
    return {data,status:200,statusText:'OK',headers:{},config};
  };
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  let done:Promise<void>|undefined;
  const save=fixture.save_request as unknown as SaveDesignRequest;
  async function action(){
    const preview=await designGoldenGate(fixture.request as unknown as WorkflowRequest,controller.signal);
    expect(preview.data).toEqual(fixture.preview);
    expect(freezeGoldenGateSelection(preview.data,'fixed')).toEqual(save.selection);
    const saved=await saveGoldenGateDesign(save);
    expect(saved.data).toEqual(fixture.saved);
    const reopened=await readGoldenGateDesign(saved.data.operation_id,controller.signal);
    expect(reopened.data).toEqual(fixture.saved);
    await exportGoldenGateDesign(preview.data);
    await exportGoldenGateDesign(saved.data.result,saved.data.operation_id);
    expect((await importGoldenGateDesign(fixture.document)).data).toEqual(fixture.saved.result);
  }
  try{
    await act(async()=>root.render(<button onClick={()=>{done=action();}}>Explicit workflow action</button>));
    expect(calls).toHaveLength(0);
    await act(async()=>{host.querySelector('button')!.click();await done;});
    expect(calls).toHaveLength(6);
    expect(calls.filter(c=>c.url.endsWith('/design'))).toHaveLength(1);
    expect(calls[0].signal).toBe(controller.signal);
    expect(calls[1].signal).toBeUndefined();
    expect(calls[2].signal).toBe(controller.signal);
    for(const i of [0,1,2,5])expect(calls[i].params).toEqual({view:'normalized'});
    for(const i of [0,1,3,5])expect(calls[i].body.schema_version).toBe('bms.golden-gate-wire.v1');
    expect(expandGoldenGateWire(calls[0].body,'request')).toEqual(fixture.request);
    expect(expandGoldenGateWire(calls[1].body,'save')).toEqual(fixture.save_request);
    expect(expandGoldenGateWire<WorkflowResult>(calls[3].body,'result')).toEqual(fixture.preview);
    expect(expandGoldenGateWire(calls[5].body,'portable')).toEqual(fixture.document);
    // This exact wrapper output is replayed by test_golden_gate_wire_receiving;
    // changing either producer requires updating both real receiving proofs.
    const captured=JSON.parse(browserRequestsJson);
    expect(calls.map(({signal,...c})=>({...c,hasSignal:!!signal}))).toEqual(captured);
    if(process.env.BMS_GG_WIRE_EVIDENCE){
      mkdirSync(process.env.BMS_GG_WIRE_EVIDENCE,{recursive:true});
      writeFileSync(join(process.env.BMS_GG_WIRE_EVIDENCE,'browser-requests.json'),JSON.stringify(calls.map(({signal,...c})=>({...c,hasSignal:!!signal})),null,2));
    }
  }finally{await act(async()=>root.unmount());host.remove();}
});
