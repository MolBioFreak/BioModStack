import React, {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import {webcrypto} from 'node:crypto';
import {beforeEach, afterEach, describe, expect, it, vi} from 'vitest';
import {BioXpWorkflowJobMonitor} from '../../src/components/BioXpWorkflowJobMonitor';
import {api} from '../../src/lib/api';
const directory = process.env.BIOXP_MEASUREMENT_PRODUCERS;
const cases = directory ? readdirSync(directory).filter(name=>/^thermal-.*\.json$/.test(name)).map(name=>({name, producer:JSON.parse(readFileSync(join(directory,name),'utf8'))})) : [];
let host:HTMLDivElement,root:Root,client:QueryClient;
const original=api.defaults.adapter;
beforeEach(()=>{vi.stubGlobal('crypto',webcrypto);host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});});
afterEach(async()=>{await act(async()=>root.unmount());client.clear();host.remove();api.defaults.adapter=original;vi.unstubAllGlobals();});
async function tick(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,25));});}
async function input(label:string,value:string){const el=[...host.querySelectorAll('label')].find(row=>row.textContent?.startsWith(label))?.querySelector('input');expect(el,label).toBeDefined();await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value);el!.dispatchEvent(new Event('input',{bubbles:true}));});}
// Actual native HTTP/SQLite exports, not invented sensor/thermal results. Axios transport
// alone is inert here; BMS relay acceptance is a separate integration obligation.
describe.skipIf(!directory)('native retained measurement producer → mounted existing review',()=>{
 it('contains all RC/OC/TC source zone exports',()=>expect(cases.map(row=>row.name).sort()).toEqual(['thermal-oc-oc.json','thermal-rc-rc.json','thermal-tc-lid.json','thermal-tc-nest.json','thermal-tc-pedestal.json']));
 it.each(cases)('$name survives cold mount and continues exact occurrences without heating replay',async({producer})=>{
  let point=0,current=producer.held[0];const posts:any[]=[];const busy={current:false};
  api.defaults.adapter=async config=>{
   if(config.method==='post'){
    expect(config.url).toBe(`/api/bioxp/protocols/jobs/${current.job_id}/review`);
    posts.push(JSON.parse(config.data));point++;current=producer.held[point]??producer.completed;
   }
   return {data:current,status:200,statusText:'real native payload via inert transport',headers:{},config};
  };
  for(let index=0;index<producer.held.length;index++){
   if(index){await act(async()=>root.unmount());client.clear();root=createRoot(host);}
   await act(async()=>root.render(<QueryClientProvider client={client}><BioXpWorkflowJobMonitor jobId={current.job_id} generation={77} connected controlsEnabled busyRef={busy} onBusyChange={()=>{}}/></QueryClientProvider>));await tick();
   const pending=producer.held[index].operator.pending_review;expect(host.textContent).toContain(pending.measurement_form.fields[0].target);
   await input('Reviewer','external-probe-fixture');
   for(const field of pending.measurement_form.fields){for(const [n,value] of producer.review_requests[index].measurements[field.field_id].entries())await input(`${field.label} reading ${n+1} (${field.unit})`,String(value));}
   await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent==='Acknowledge protocol review')!.click());await tick();
   expect(posts[index]).toMatchObject({command_id:producer.review_requests[index].command_id,stage_id:pending.stage_id,action_id:pending.action_id,source_occurrence_id:pending.source_occurrence_id,measurements:Object.fromEntries(Object.entries(producer.review_requests[index].measurements).map(([key,values])=>[key,(values as unknown[]).map(String)]))});
  }
  expect(posts).toHaveLength(producer.held.length);expect(host.textContent).toContain('Phase: terminal');
 });
});
