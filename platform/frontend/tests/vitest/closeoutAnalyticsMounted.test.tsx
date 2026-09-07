import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {readFileSync} from 'node:fs';
import {afterEach,expect,test,vi} from 'vitest';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {api,type Design} from '../../src/lib/api';
import {AnalyticsDashboard} from '../../src/components/AnalyticsDashboard';
vi.mock('react-plotly.js',()=>({default:()=>null}));
const load=(env:string)=>{const path=process.env[env];if(!path)throw new Error(`${env} actual ASGI wire required`);return JSON.parse(readFileSync(path,'utf8'));};
const sortWire=load('BMS_SORT_ANALYTICS_WIRE'), unavailableWire=load('BMS_UNSUPPORTED_ANALYTICS_WIRE');
let root:ReturnType<typeof createRoot>;
const adapter=api.defaults.adapter;
afterEach(async()=>{await act(async()=>root?.unmount());document.body.replaceChildren();api.defaults.adapter=adapter;});
async function mount(wire:any){
    api.defaults.adapter=async config=>({config,status:200,statusText:'OK',headers:{},data:wire});
    const host=document.createElement('div');document.body.append(host);root=createRoot(host);
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    await act(async()=>root.render(<QueryClientProvider client={client}><AnalyticsDashboard designs={wire.points as Design[]} jobId="job" jobName="closeout"/></QueryClientProvider>));
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,40));});
    return host;
}
test('actual API points sort native filter_rmsd zero, five, missing without aliasing overall RMSD',async()=>{
    // Deliberately unsorted API-produced observations; never alter measurements.
    const wire=structuredClone(sortWire);
    wire.points.sort((a:any,b:any)=>(b.metrics.filter_rmsd??Infinity)-(a.metrics.filter_rmsd??Infinity));
    const host=await mount(wire);
    const select=host.querySelector('select[aria-label^="Sort metric for"]') as HTMLSelectElement;
    expect(select).not.toBeNull();
    expect([...select.options].map(o=>o.value)).toContain('filter_rmsd');
    expect([...select.options].map(o=>o.value)).not.toContain('rmsd_overall');
    await act(async()=>{select.value='filter_rmsd';select.dispatchEvent(new Event('change',{bubbles:true}));});
    const rows=[...host.querySelectorAll('tr')].filter(row=>row.children[1]?.textContent?.startsWith('filter_rmsd /'));
    expect(rows.map(row=>row.children[2].textContent)).toEqual(['0','5',expect.stringMatching(/unavailable|invalid/)]);
});
test('unsupported marked owner shows the API missing-publication reason',async()=>{
    const host=await mount(unavailableWire);
    expect(host.textContent).toContain('unavailable: missing_canonical_publication');
    expect(host.querySelectorAll('circle')).toHaveLength(0);
});
