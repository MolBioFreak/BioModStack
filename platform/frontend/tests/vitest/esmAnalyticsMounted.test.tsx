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

const path=process.env.BMS_ESM_ANALYTICS_WIRE;
const wire=path?JSON.parse(readFileSync(path,'utf8')):null;
let root:ReturnType<typeof createRoot>|undefined;
const adapter=api.defaults.adapter;
afterEach(async()=>{if(root)await act(async()=>root!.unmount());document.body.innerHTML='';api.defaults.adapter=adapter;});

test('published ESMFold2 scalars reach the dashboard with missingness and zero intact',async()=>{
    expect(wire,'requires the actual persisted API fixture').not.toBeNull();
    validateScientificEnvelope(wire);
    const requests:string[]=[];
    api.defaults.adapter=async config=>{requests.push(config.url!);return {config,status:200,statusText:'OK',headers:{},data:wire};};
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const host=document.createElement('div');document.body.append(host);root=createRoot(host);
    await act(async()=>root!.render(<QueryClientProvider client={client}><AnalyticsDashboard designs={wire.points as Design[]} jobId="job" jobName="Published ESMFold2"/></QueryClientProvider>));
    await settled(host);
    expect(requests).toContain('/api/designs/by-job/job/plotly-metrics');
    expect(host.textContent).toContain('plddt / model_token_mean / fraction');
    expect(host.textContent).toContain('ptm / model / dimensionless');
    expect(host.textContent).toContain('missing_native_scalar');
    expect(host.querySelector('[aria-label="Plotly Lab"]')).not.toBeNull();
    await change(host, '2D X metric', 'iptm');
    for (const metric of ['plddt', 'ptm']) {
        await change(host, '2D Y metric', metric);
        expect(scatter(host).data[0].x).toEqual([0]);
        expect(scatter(host).data[0].y).toEqual([metric === 'plddt' ? 0.005 : 0.7]);
        expect(host.textContent).toContain('1 plotted · 1 omitted');
    }
    await change(host, '2D X metric', 'plddt');
    expect(scatter(host)).toBeUndefined();
    expect(host.textContent).toContain('No complete finite pairs');
    expect(plots(host).some(plot => plot.data[0].type === 'histogram')).toBe(true);
});

test('a missing ESMFold2 value requires its stored reason',()=>{
    const bad=structuredClone(wire);
    const point=bad.points.find((p:{metric_states:{plddt:{state:string}}})=>p.metric_states.plddt.state==='unavailable');
    point.metric_states.plddt.reason_code=null;
    expect(()=>validateScientificEnvelope(bad)).toThrow();
});
