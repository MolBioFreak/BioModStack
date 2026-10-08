import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {readFileSync} from 'node:fs';
import {afterEach, expect, test, vi} from 'vitest';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {api, type Design} from '../../src/lib/api';
import {AnalyticsDashboard} from '../../src/components/AnalyticsDashboard';
import {validateScientificEnvelope} from '../../src/lib/scientificAnalytics';
import { change, plots, scatter, settled } from './analyticsPlotHarness';
vi.mock('react-plotly.js',()=>import('./analyticsPlotHarness'));
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
    await settled(host);
    expect(requests).toContain('/api/designs/by-job/job/plotly-metrics');
    expect(host.textContent).toContain('design_ptm / native_design_chain_tokens / fraction');
    expect(host.textContent).toContain(`filter_rmsd / ${name==='csv_zero'?'native_refolded_complex_backbone':'native_filter_complex_alignment'} / angstrom`);
    if(name==='zero'||name==='csv_zero'){
        expect(host.querySelector('[aria-label="Plotly Lab"]')).not.toBeNull();
        await change(host, '2D X metric', 'design_ptm');
        await change(host, '2D Y metric', name === 'csv_zero' ? 'filter_rmsd' : 'affinity_probability');
        const count = wire.points.length;
        expect(scatter(host).data[0].x).toEqual(Array(count).fill(0));
        expect(scatter(host).data[0].y).toEqual(Array(count).fill(0));
        expect(scatter(host).layout.xaxis.title.text).toContain('(fraction)');
        expect(scatter(host).layout.yaxis.title.text).toContain(name === 'csv_zero' ? '(angstrom)' : '(fraction)');
    }else{
        if (Object.keys(wire.points[0].metrics).length) {
            expect(host.querySelector('[aria-label="Plotly Lab"]')).not.toBeNull();
            expect(plots(host).some(plot => plot.data[0].type === 'histogram')).toBe(true);
            expect([...host.querySelectorAll('select[aria-label="2D X metric"] option')].map(option => (option as HTMLOptionElement).value)).not.toContain('design_ptm');
        } else {
            expect(plots(host)).toHaveLength(0);
            expect(host.textContent).toContain('No finite numeric observations');
        }
        const reason = [...host.querySelectorAll('td')].find(td => td.textContent?.includes(wire.points[0].metric_states.design_ptm.reason_code));
        expect(reason?.closest('details')?.open).toBe(false);
    }
    client.clear();
});

test('the consumer rejects a missing native value without its persisted reason',()=>{
    const bad=load('missing');bad.points[0].metric_states.design_ptm.reason_code=null;
    expect(()=>validateScientificEnvelope(bad)).toThrow();
});
