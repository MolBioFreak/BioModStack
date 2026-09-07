import React from 'react';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import * as parser from '../../src/lib/scientificViewerIdentity';
import type { MetricLayer } from '../../src/structureViewer/metrics/metricContracts';
const path=process.env.BMS_TEST_BOLTZ_WIRE;
if(!path) throw Error('Real published SQLite ASGI wire required');
const bytes=readFileSync(path);
expect(createHash('sha256').update(bytes).digest('hex')).toBe(process.env.BMS_TEST_BOLTZ_WIRE_SHA256);
const wire=JSON.parse(bytes.toString());
const state=vi.hoisted(()=>({props:{} as any}));
vi.mock('../../src/components/useThemeColors',()=>({useThemeColors:()=>({bgPrimary:'#000'})}));
vi.mock('react-plotly.js',()=>({default:()=>null}));
vi.mock('../../src/components/ReferenceSelector',()=>({default:()=>null}));
vi.mock('../../src/structureViewer/StructureWorkbench',()=>({StructureWorkbench:(props:any)=>{state.props=props;return <div data-structure-visible/>;}}));
let mounted:ReactTestRenderer|undefined;
const text=(node:any):string=>typeof node==='string'?node:(node.children??[]).map(text).join('');
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
function props(design=wire.design){return {selectedDesignId:design.id,selectedDesign:design,designs:[design],setSelectedDesignId:vi.fn(),colorMode:'default',setColorMode:vi.fn(),structureFormat:'pdb',activeJob:{id:design.job_id},getMetricColor:()=>'',viewerAnalyses:{paeMatrixData:wire.pae,chainMetrics:{}}} as any;}
async function mount(residue=wire.residue,chains=wire.chains){
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>({ok:true,json:async()=>url.endsWith('/residue-metrics')?residue:chains})));
 await act(async()=>{mounted=create(<QueryClientProvider client={client}><StructureViewerPane {...props()}/></QueryClientProvider>);});
}
afterEach(async()=>{if(mounted)await act(async()=>mounted!.unmount());mounted=undefined;vi.unstubAllGlobals();});
it('native vectors and provider-chain map survive real transport into panel/chart/exact selection',async()=>{
 expect(typeof (parser as any).parseScientificNativeMetric).toBe('function');
 const residue=(parser as any).parseScientificNativeMetric(wire.residue,wire.design.scientific_structure_document,'residue_plddt');
 const chains=(parser as any).parseScientificNativeMetric(wire.chains,wire.design.scientific_structure_document,'chain_metrics');
 expect(residue.status).toBe('ok');expect(chains.status).toBe('ok');
 expect(residue.residues.map((r:any)=>r.authAsymId)).toEqual(['T','T','T','H','H','L']);
 expect(residue.values).toEqual(wire.residue.values);
 expect(chains.chains.map((c:any)=>c.chainId)).toEqual(['T','H','L']);
 await mount();
 expect(text(mounted!.root)).toContain('Chain T');expect(text(mounted!.root)).toContain('Chain H');expect(text(mounted!.root)).toContain('Chain L');
 expect(text(mounted!.root)).toContain('missing_role_assignment');
 const layer=state.props.metricLayers.find((l:MetricLayer)=>l.descriptor.id==='native-plddt');
 expect(layer.descriptor.units).toBe('fraction');expect(layer.values.map((v:any)=>v.value)).toEqual(wire.residue.values);
 const point=mounted!.root.findByProps({'data-native-residue-index':3});
 expect(point.props['data-display-percent']).toBe(wire.residue.values[3]*100);
 await act(async()=>mounted!.root.findByProps({'data-native-chart-index':3}).props.onClick());
 expect(state.props.residueSelections).toEqual([residue.residues[3]]);
 await act(async()=>point.props.onClick());
 expect(state.props.residueSelections).toEqual([residue.residues[3]]);
 const newer={...wire.design,id:'new',scientific_structure_document:{...wire.design.scientific_structure_document,candidateId:'new'}};
 await act(async()=>mounted!.update(<QueryClientProvider client={client}><StructureViewerPane {...props(newer)}/></QueryClientProvider>));
 expect(state.props.residueSelections).toEqual([]);
 expect(state.props.metricLayers.some((l:MetricLayer)=>l.descriptor.id==='native-plddt')).toBe(false);
 expect(mounted!.root.findAllByProps({'data-native-residue-index':3})).toHaveLength(0);
});
it.each(['dimension','boolean','string','extra','source','candidate','position','chain-map','chain-value','role'])('rejects damaged native %s without hiding structure',async damage=>{
 const r=structuredClone(wire.residue), c=structuredClone(wire.chains);
 if(damage==='dimension')r.values.pop();
 if(damage==='boolean')r.values[0]=true;
 if(damage==='string')r.values[0]='0.5';
 if(damage==='extra')r.extra=1;
 if(damage==='source')r.axis.source_sha256='f'.repeat(64);
 if(damage==='candidate')r.document.candidateId='foreign';
 if(damage==='position')r.native_positions.reverse();
 if(damage==='chain-map')c.chain_index_map[0].chain_id='Z';
 if(damage==='chain-value')c.chains_ptm[Object.keys(c.chains_ptm)[0]]=true;
 if(damage==='role')c.role_assignment={target:'T'};
 await mount(r,c);
 if(damage.startsWith('chain')||damage==='role')expect(text(mounted!.root)).toContain('Chain metrics unavailable');
 else {
  expect(state.props.metricLayers.some((l:MetricLayer)=>l.descriptor.id==='native-plddt')).toBe(false);
  expect(mounted!.root.findAllByProps({'data-native-chain':'T'})).toHaveLength(1);
 }
 expect(mounted!.root.findAllByProps({'data-structure-visible':true})).toHaveLength(1);
});
