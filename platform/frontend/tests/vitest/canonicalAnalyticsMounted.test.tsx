import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {readFileSync} from 'node:fs';
import {expect, test, afterEach, vi} from 'vitest';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {api, type Design} from '../../src/lib/api';
import {AnalyticsDashboard} from '../../src/components/AnalyticsDashboard';
vi.mock('react-plotly.js',()=>({default:()=>null}));
import {parseMetricPoints, parseScientificCohorts, validateScientificEnvelope, type ScientificPoint} from '../../src/lib/scientificAnalytics';


const path = process.env.BMS_ANALYTICS_WIRE;
const wire = path ? JSON.parse(readFileSync(path,'utf8')) : null;
let root: ReturnType<typeof createRoot> | undefined;
afterEach(async () => {if (root) await act(async()=>root!.unmount()); document.body.innerHTML='';});

test('published SQLite API bytes reach mounted native scalar table and paired plot', async () => {
    expect(wire, 'BMS_ANALYTICS_WIRE must be produced by the API fixture').not.toBeNull();
    validateScientificEnvelope(wire);
    const points = parseMetricPoints(wire.points) as ScientificPoint[];
    parseScientificCohorts(wire.scientific_cohorts);
    const requests:string[]=[];
    api.defaults.adapter=async config=>{requests.push(config.url!);return {config,status:200,statusText:'OK',headers:{},data:wire};};
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const host=document.createElement('div');document.body.append(host);root=createRoot(host);
    await act(async()=>root!.render(<QueryClientProvider client={client}><AnalyticsDashboard designs={points as unknown as Design[]} jobId="job" jobName="Published native fixture"/></QueryClientProvider>));
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,30));});
    expect(requests).toContain('/api/designs/by-job/job/plotly-metrics');
    expect(host.textContent).toContain('complex_plddt / complex / fraction');
    expect(host.textContent).toContain('ptm / overall / dimensionless');
    expect(host.querySelectorAll('circle').length).toBe(points.length);
    for (const point of points) {
        const mark=host.querySelector(`circle[data-candidate-id="${point.id}"]`);
        expect(mark?.getAttribute('data-x')).toBe(String(point.metrics.complex_plddt));
        expect(mark?.getAttribute('data-y')).toBe(String(point.metrics.ptm));
    }
});

test('raw metric transport rejects a downgraded canonical marker', () => {
    expect(wire).not.toBeNull();
    const bad=structuredClone(wire.points);
    bad[0].contract_revision=null;
    expect(()=>parseMetricPoints(bad)).toThrow();
});

test.each(['source','descriptor','pair','unknown','boolean'])('rejects damaged actual API envelope: %s', damage => {
    expect(wire).not.toBeNull();
    const bad=structuredClone(wire);
    if (damage==='source') bad.points[0].metric_sources.ptm.artifact_sha256='foreign';
    if (damage==='descriptor') bad.scientific_cohorts[0].metrics.ptm.descriptor.producer_version='foreign';
    if (damage==='pair') bad.scientific_cohorts[0].pairs.complex_plddt_vs_ptm.points[0].id='foreign';
    if (damage==='unknown') bad.points[0].metric_descriptors.ptm.extra=true;
    if (damage==='boolean') bad.points[0].metric_states.ptm.value=true;
    expect(()=>validateScientificEnvelope(bad)).toThrow();
});
