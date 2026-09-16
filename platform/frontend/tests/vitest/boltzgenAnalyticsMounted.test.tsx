import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {readFileSync} from 'node:fs';
import {afterEach, expect, test, vi} from 'vitest';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {api, type Design} from '../../src/lib/api';
import {AnalyticsDashboard} from '../../src/components/AnalyticsDashboard';
import {validateScientificEnvelope} from '../../src/lib/scientificAnalytics';
vi.mock('react-plotly.js',()=>({default:()=>null}));
const directory=process.env.BMS_BOLTZGEN_ANALYTICS_WIRES;
if(!directory)throw new Error('Real published SQLite API wires required');
const load=(name:string)=>JSON.parse(readFileSync(`${directory}/${name}.json`,'utf8'));
let root:ReturnType<typeof createRoot>|undefined;
const adapter=api.defaults.adapter;
afterEach(async()=>{if(root)await act(async()=>root!.unmount());root=undefined;document.body.innerHTML='';api.defaults.adapter=adapter;});

test.each(['zero','csv_zero','missing','invalid','source_swapped','unknown_producer'])('published BoltzGen %s reaches the actual dashboard',async name=>{
    const wire=load(name);validateScientificEnvelope(wire);
    const requests:string[]=[];
    api.defaults.adapter=async config=>{requests.push(config.url!);return {config,status:200,statusText:'OK',headers:{},data:wire};};
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const host=document.createElement('div');document.body.append(host);root=createRoot(host);
    await act(async()=>root!.render(<QueryClientProvider client={client}><AnalyticsDashboard designs={wire.points as Design[]} jobId="job" jobName="Published BoltzGen"/></QueryClientProvider>));
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,30));});
    expect(requests).toContain('/api/designs/by-job/job/plotly-metrics');
    expect(host.textContent).toContain('design_ptm / native_design_chain_tokens / fraction');
    expect(host.textContent).toContain(`filter_rmsd / ${name==='csv_zero'?'native_refolded_complex_backbone':'native_filter_complex_alignment'} / angstrom`);
    expect(host.querySelectorAll('circle')).toHaveLength(name==='zero'?1:name==='csv_zero'?3:0);
    if(name==='zero'||name==='csv_zero'){
        for(const mark of host.querySelectorAll('circle')){
            expect(mark.getAttribute('data-x')).toBe('0');expect(mark.getAttribute('data-y')).toBe('0');
        }
    }else{
        expect(host.textContent).toContain(wire.points[0].metric_states.design_ptm.reason_code);
    }
    client.clear();
});

test('the consumer rejects a missing native value without its persisted reason',()=>{
    const bad=load('missing');bad.points[0].metric_states.design_ptm.reason_code=null;
    expect(()=>validateScientificEnvelope(bad)).toThrow();
});
