import React from 'react';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import { PairMatrixExtension } from '../../src/structureViewer/extensions/pairMatrix/PairMatrixExtension';
import { parseScientificPae } from '../../src/lib/scientificViewerIdentity';
import type { MetricLayer } from '../../src/structureViewer/metrics/metricContracts';

// Written only by test_boltz_workflow_transport after literal offline Nextflow,
// real publication/ingestion/SQLite reload and actual ASGI serialization. No
// positive JSON is reconstructed here and database UUIDs are never normalized.
const path = process.env.BMS_TEST_BOLTZ_WIRE;
if (!path) throw Error('Run tests/test_boltz_workflow_transport.py: real published wire required');
const bytes = readFileSync(path);
expect(createHash('sha256').update(bytes).digest('hex')).toBe(process.env.BMS_TEST_BOLTZ_WIRE_SHA256);
const wire = JSON.parse(bytes.toString());
const state = vi.hoisted(() => ({select:vi.fn(), layer:undefined as MetricLayer | undefined}));
vi.mock('../../src/components/useThemeColors', () => ({useThemeColors:()=>({bgPrimary:'#000'})}));
vi.mock('react-plotly.js', () => ({default:()=>null}));
vi.mock('../../src/components/ReferenceSelector', () => ({default:()=>null}));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({StructureWorkbench: function Workbench(props: {metricLayers:MetricLayer[]}) {
    const layer=props.metricLayers.find(l=>l.descriptor.id==='pae'); state.layer=layer;
    return <div data-structure-visible>{layer && <PairMatrixExtension layer={layer} onSelection={state.select}/>}</div>;
}}));
let mounted: ReactTestRenderer | undefined;
let image: {data:Uint8ClampedArray; width:number; height:number};
const context={createImageData:(width:number,height:number)=>({data:new Uint8ClampedArray(width*height*4),width,height}),putImageData:(v:typeof image)=>{image=v;}};
const text=(node:any):string=>typeof node==='string'?node:(node.children??[]).map(text).join('');
function props(payload:unknown=wire.pae, design=wire.design) {
    return {selectedDesignId:design.id,selectedDesign:design,designs:[design],setSelectedDesignId:vi.fn(),colorMode:'default',setColorMode:vi.fn(),structureFormat:'pdb',activeJob:{id:design.job_id},getMetricColor:()=>'',viewerAnalyses:{paeMatrixData:payload,chainMetrics:{}}} as any;
}
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
async function mount(payload:unknown=wire.pae) {
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:false})));
    await act(async()=>{mounted=create(<QueryClientProvider client={client}><StructureViewerPane {...props(payload)}/></QueryClientProvider>,{createNodeMock:el=>el.type==='canvas'?{getContext:()=>context}:null});});
}
afterEach(async()=>{if(mounted) await act(async()=>mounted!.unmount());mounted=undefined;state.select.mockClear();vi.unstubAllGlobals();});

it('real published SQLite ASGI PAE mounts directed pixels and exact full ResidueRefs', async()=>{
    const parsed=parseScientificPae(wire.pae,wire.design.scientific_structure_document);
    expect(parsed.status).toBe('ok'); if(parsed.status!=='ok') throw Error(parsed.reason);
    expect(parsed.matrix).toBe(wire.pae.pae_matrix);
    expect([parsed.matrix[0][0],parsed.matrix[0][1],parsed.matrix[1][0],parsed.matrix[5][5]]).toEqual([0,1,6,35]);
    expect(parsed.rows.map(r=>r.authAsymId)).toEqual(['T','T','T','H','H','L']);
    await mount();
    expect(image.width).toBe(parsed.columns.length);expect(image.height).toBe(parsed.rows.length);
    expect([...image.data.slice(4,8)]).not.toEqual([...image.data.slice(parsed.columns.length*4,parsed.columns.length*4+4)]);
    await act(async()=>mounted!.root.findByProps({role:'grid'}).props.onKeyDown({key:'ArrowRight',preventDefault(){}}));
    await act(async()=>mounted!.root.findByProps({role:'grid'}).props.onKeyDown({key:'Enter',preventDefault(){}}));
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual({first:parsed.rows[0],second:parsed.columns[1]});
    const native=wire.pae.row_axis.residues[wire.pae.sampled_row_indices[0]];
    expect(parsed.rows[0]).toEqual({documentId:'primary',modelId:String(native.selected_model),
        authAsymId:native.auth_asym_id,authSeqId:native.auth_seq_id,insertionCode:native.insertion_code,
        componentId:native.residue_name,altLoc:native.selected_altloc,
        ...(native.source_entity_id===null?{}:{sourceEntityId:native.source_entity_id}),
        ...(native.entity_instance_id===null?{}:{sourceInstanceId:native.entity_instance_id}),
        ...(native.label_asym_id===null?{}:{labelAsymId:native.label_asym_id}),
        ...(native.label_seq_id===null?{}:{labelSeqId:native.label_seq_id})});
    await act(async()=>mounted!.root.findByType('canvas').props.onClick({clientX:1,clientY:21,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:120,height:120})}}));
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual({first:parsed.rows[1],second:parsed.columns[0]});
});
it.each(['extra','missing','boolean','infinite','foreign-candidate','foreign-source','axis-swap'])('real wire rejects %s without hiding structure',async damage=>{
    const p=structuredClone(wire.pae);
    if(damage==='extra') p.extra=1;
    if(damage==='missing') delete p.row_axis;
    if(damage==='boolean') p.pae_matrix[0][0]=true;
    if(damage==='infinite') p.pae_matrix[0][0]=Infinity;
    if(damage==='foreign-candidate') p.producer_binding.candidate_id='foreign';
    if(damage==='foreign-source') p.column_axis.source_sha256='f'.repeat(64);
    if(damage==='axis-swap') [p.row_axis.residues[0],p.row_axis.residues[1]]=[p.row_axis.residues[1],p.row_axis.residues[0]];
    expect(parseScientificPae(p,wire.design.scientific_structure_document).status).toBe('unavailable');
    await mount(p);
    expect(mounted!.root.findAllByType('canvas')).toHaveLength(0);
    expect(mounted!.root.findAllByProps({'data-structure-visible':true})).toHaveLength(1);
    expect(text(mounted!.root)).toContain('unavailable');
});
it('old candidate completion cannot mount against the newly selected document',async()=>{
    await mount();
    const newer={...wire.design,id:'new-selected',scientific_structure_document:{...wire.design.scientific_structure_document,candidateId:'new-selected'}};
    await act(async()=>mounted!.update(<QueryClientProvider client={client}><StructureViewerPane {...props(wire.pae,newer)}/></QueryClientProvider>));
    expect(mounted!.root.findAllByType('canvas')).toHaveLength(0);
    expect(text(mounted!.root)).toContain('unavailable');
});
