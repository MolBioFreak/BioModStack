import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, expect, test, vi} from 'vitest';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {api} from '../../src/lib/api';
import {AnalyticsDashboard} from '../../src/components/AnalyticsDashboard';
import type {Design} from '../../src/lib/api';
vi.mock('react-plotly.js', () => ({default: () => null}));
Object.assign(globalThis,{React});
const descriptor = (key:string) => ({metric_id:key, source:'canonical_artifact',producer_version:'ui-fixture-v1',derivation_version:'ui-fixture-v1',scope:'overall',unit:key === 'plddt_overall' ? 'pLDDT' : 'angstrom',direction:key === 'plddt_overall' ? 'higher' : 'lower'});
const row = (id:string, rmsd:number|null) => ({id,name:id,contract_revision:1,source_job_id:'j',cohort_key:'v1:p:j',metrics:{plddt_overall:0,pae_overall:2,...(rmsd === null ? {} : {rmsd_overall:rmsd})}, metric_states:{plddt_overall:{state:'ok',value:0,reason_code:null},pae_overall:{state:'ok',value:2,reason_code:null},rmsd_overall:rmsd === null ? {state:'unavailable',value:null,reason_code:'not_reported'} : {state:'ok',value:rmsd,reason_code:null}},metric_sources:Object.fromEntries(['plddt_overall','pae_overall','rmsd_overall'].map(key=>[key,{artifact_sha256:'a'.repeat(64),candidate_id:id,document_id:id}])),metric_descriptors:Object.fromEntries(['plddt_overall','pae_overall','rmsd_overall'].map(key => [key,descriptor(key)]))});
let root: ReturnType<typeof createRoot>|undefined;
afterEach(async () => { if(root) await act(async () => root!.unmount()); document.body.innerHTML=''; });

test('UI-only canonical fixture: zero remains measured, missing sorts last; absent server pairs stay absent', async () => {
    const points = [row('missing',null),row('zero',0)];
    api.defaults.adapter = async config => ({config,status:200,statusText:'OK',headers:{},data:{job_id:'j',metric_keys:['plddt_overall','pae_overall','rmsd_overall'],points,total:2,scientific_cohorts:[]}});
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
    await act(async () => { root!.render(<QueryClientProvider client={client}><AnalyticsDashboard designs={points as unknown as Design[]} jobId="j" jobName="Test" /></QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve,30)); });
    expect(host.querySelectorAll('circle')).toHaveLength(0);
    expect(host.textContent).toContain('Paired statistics are unavailable for this response.');
    expect(host.textContent).toContain('0');
    expect(host.textContent).toContain('unavailable: not_reported');
    expect(host.textContent).toContain('pae_overall / overall / angstrom');
    await act(async () => { (host.querySelector('button') as HTMLButtonElement).click(); });
    expect(host.querySelector('tbody tr td')?.textContent).toBe('zero (zero)');
});
