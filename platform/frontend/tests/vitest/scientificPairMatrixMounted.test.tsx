import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import { PairMatrixExtension } from '../../src/structureViewer/extensions/pairMatrix/PairMatrixExtension';
import type { MetricLayer, MetricDatasetMetadata } from '../../src/structureViewer/metrics/metricContracts';
import { document, fixture } from '../fixtures/scientificViewerFixture';

type PairLayer = Extract<MetricLayer, {dataset?: MetricDatasetMetadata}>;
const state = vi.hoisted(() => ({ select: vi.fn(), transform: undefined as undefined | ((layer: PairLayer) => PairLayer), layer: undefined as PairLayer | undefined }));
vi.mock('../../src/components/useThemeColors', () => ({useThemeColors: () => ({bgPrimary:'#000'})}));
vi.mock('react-plotly.js', () => ({default: () => null}));
vi.mock('../../src/components/ReferenceSelector', () => ({default: () => null}));
// Replace only the GPU owner: the pane's real builder and real matrix remain mounted.
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({StructureWorkbench: function Workbench(props: {metricLayers: MetricLayer[]}) {
    const layer = props.metricLayers.find(l => l.descriptor.id === 'pae') as PairLayer | undefined;
    state.layer = layer;
    return <div data-structure-visible>{layer && <PairMatrixExtension layer={state.transform ? state.transform(layer) : layer} onSelection={state.select}/>}</div>;
}}));
let mounted: ReactTestRenderer | undefined;
let image: {data: Uint8ClampedArray; width: number; height: number};
const context = {createImageData: (width: number, height: number) => ({data:new Uint8ClampedArray(width*height*4),width,height}), putImageData: (value: typeof image) => {image=value;}};
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
async function mount(payload=fixture()) {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:false})));
    const design = {id:'candidate',name:'Candidate',job_id:'job',pdb_path:'candidate.pdb',review_profile_id:'structure_prediction',core_protein_scientific_contract:1,scientific_structure_document:document};
    const props: any = {selectedDesignId:'candidate',selectedDesign:design,designs:[design],setSelectedDesignId:vi.fn(),colorMode:'default',setColorMode:vi.fn(),structureFormat:'pdb',activeJob:{id:'job'},getMetricColor:()=>'',viewerAnalyses:{paeMatrixData:payload,chainMetrics:{}}};
    await act(async () => {mounted=create(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><StructureViewerPane {...props}/></QueryClientProvider>,{createNodeMock: el => el.type === 'canvas' ? {getContext:()=>context} : null});});
}
const key = async (key: string) => {await act(async () => mounted!.root.findByProps({role:'grid'}).props.onKeyDown({key,preventDefault(){}}));};
afterEach(async () => {if(mounted) await act(async()=>mounted!.unmount());mounted=undefined;state.transform=undefined;state.select.mockClear();vi.unstubAllGlobals();});

it('real pane builder preserves directed T/H/L axes, distinct pixels, readout, click and keyboard pairs', async () => {
    await mount();
    expect(state.layer?.dataset?.rowAxis?.map(r=>r.authAsymId)).toEqual(['T','H','H','H','L']);
    expect(state.layer?.dataset?.columnAxis?.map(r=>r.authAsymId)).toEqual(['L','T','H','H','H']);
    expect(text(mounted!.root)).toContain('T:1 × L:10: 0');
    expect(image.width).toBe(5); expect(image.height).toBe(5);
    expect([...image.data.slice(4,8)]).not.toEqual([...image.data.slice(20,24)]);
    await key('ArrowRight'); await key('Enter');
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual(state.layer!.values[1].identity);
    expect(text(mounted!.root)).toContain('T:1 × T:1: 1');
    await act(async()=>mounted!.root.findByType('canvas').props.onClick({clientX:1,clientY:21,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})}}));
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual(state.layer!.values[5].identity);
    expect(text(mounted!.root)).toContain('H:100 × L:10: 5');
    // Reverse biological pairs occupy independent positions under permuted axes.
    await key('ArrowUp'); await key('ArrowRight'); await key('ArrowRight'); await key('Enter');
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual(state.layer!.values[2].identity);
    expect(text(mounted!.root)).toContain('T:1 × H:100: 2');
    await act(async()=>mounted!.root.findByType('canvas').props.onClick({clientX:21,clientY:21,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})}}));
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual(state.layer!.values[6].identity);
    expect(text(mounted!.root)).toContain('H:100 × T:1: 6');
    expect([...image.data.slice(8,12)]).not.toEqual([...image.data.slice(24,28)]);
    expect(mounted!.root.findAllByProps({role:'row'})).toHaveLength(25);
});
it('provided independently sampled rectangular map retains both provider orders without reduction', async () => {
    const p=fixture(); p.sampled_row_indices=[0,2]; p.sampled_column_indices=[0,1,4]; p.size=2;
    p.pae_matrix=p.sampled_row_indices.map(i=>p.sampled_column_indices.map(j=>p.pae_matrix[i][j]));
    await mount(p);
    expect(image.width).toBe(3);expect(image.height).toBe(2);
    expect(mounted!.root.findByProps({role:'grid'}).props['aria-rowcount']).toBe(2);
    expect(mounted!.root.findByProps({role:'grid'}).props['aria-colcount']).toBe(3);
    await key('ArrowDown');await key('ArrowRight');await key('ArrowRight');await key('ArrowRight');await key('Enter');
    expect(state.select.mock.lastCall?.[0].identities[0]).toEqual(state.layer!.values[5].identity);
    expect(text(mounted!.root)).toContain('H:100A × H:100B: 14');
});
it.each(['foreign-axis','duplicate-axis','foreign-cell','duplicate-cell','conflicting-cell','shape','overflow','missing-cell','document','missing-axis'])('fails closed on %s without hiding structure', async kind => {
    state.transform = layer => {
        const dataset={...layer.dataset!}; let values=[...layer.values];
        if(kind==='foreign-axis') dataset.rowAxis=[{...dataset.rowAxis![0],documentId:'foreign'},...dataset.rowAxis!.slice(1)];
        if(kind==='duplicate-axis') dataset.columnAxis=[dataset.columnAxis![1],...dataset.columnAxis!.slice(1)];
        if(kind==='foreign-cell') values[0]={...values[0],identity:{...values[0].identity,first:{...values[0].identity.first,modelId:'foreign'}}};
        if(kind==='duplicate-cell' || kind==='conflicting-cell') values.push({...values[0],value:kind==='conflicting-cell'?999:values[0].value});
        if(kind==='shape') dataset.shape=[6,5];
        if(kind==='overflow') {dataset.rowAxis=Array.from({length:513},(_,i)=>({...dataset.rowAxis![0],authSeqId:i}));dataset.shape=[513,5];}
        if(kind==='missing-cell') values=values.slice(1);
        if(kind==='document') dataset.documentIds=['foreign'];
        if(kind==='missing-axis') dataset.columnAxis=undefined;
        return {...layer,dataset,values};
    };
    await mount();
    expect(text(mounted!.root)).toContain('unavailable');
    expect(mounted!.root.findAllByType('canvas')).toHaveLength(0);
    expect(mounted!.root.findAllByProps({'data-structure-visible':true})).toHaveLength(1);
    expect(state.select).not.toHaveBeenCalled();
    expect(text(mounted!.root.findByType(PairMatrixExtension).findByProps({role:'status'}))).toContain('Predicted aligned error unavailable:');
});

function largePayload(count: number) {
    const p=fixture();
    const residues=Array.from({length:count},(_,index)=>({...p.row_axis.residues[0],index,auth_seq_id:index+1}));
    p.row_axis.residues=residues; p.column_axis.residues=[...residues];
    p.native_shape=[count,count]; p.native_row_positions=residues.map(r=>r.index);p.native_column_positions=[...p.native_row_positions];
    p.sampled_row_indices=[...p.native_row_positions];p.sampled_column_indices=[...p.native_row_positions];p.size=count;
    p.pae_matrix=residues.map((_,i)=>residues.map((_,j)=>i+j));
    return p;
}
it('API-shaped MAX_AXIS overflow remains explicit unavailable through the builder', async () => {
    await mount(largePayload(513));
    expect(state.layer!.dataset!.shape).toEqual([513,513]);
    expect(state.layer!.values).toHaveLength(0);
    expect(text(mounted!.root.findByType(PairMatrixExtension).findByProps({role:'status'}))).toContain('declared axis exceeds 512');
    expect(mounted!.root.findAllByType('canvas')).toHaveLength(0);
});
it('provider sampled projection of oversized native source is supported without re-reducing', async () => {
    const p=largePayload(513);p.sampled_row_indices=[0,512];p.sampled_column_indices=[1,3,512];p.size=2;
    p.pae_matrix=p.sampled_row_indices.map(i=>p.sampled_column_indices.map(j=>p.pae_matrix[i][j]));
    await mount(p);
    expect(image.width).toBe(3);expect(image.height).toBe(2);
    expect(state.layer!.values.map(v=>v.value)).toEqual([1,3,512,513,515,1024]);
});
it('supported 512 axes pass every cell beyond the old 250000-pair bound', async () => {
    await mount(largePayload(512));
    expect(state.layer!.values).toHaveLength(512*512);
    expect(image.width).toBe(512);expect(image.height).toBe(512);
    expect([...image.data.slice(-4)]).toEqual([245,80,30,255]);
    expect(text(mounted!.root)).toContain('Table lists the first 1000 directed cells');
});
it('unmarked overflow keeps the existing bounded symmetric behavior', async () => {
    state.transform=layer=>({...layer,dataset:undefined,values:Array.from({length:513},(_,i)=>{
        const ref={...layer.values[0].identity.first,authSeqId:i};
        return {identity:{first:ref,second:ref},value:i};
    })});
    await mount();
    expect(image.width).toBe(512);expect(image.height).toBe(512);
    expect(text(mounted!.root)).toContain('Axis bounded to 512 residues');
});
it('unmarked legacy matrix retains union ordering and symmetric last-write rendering', async () => {
    state.transform=layer=>({...layer,dataset:undefined});
    await mount();
    expect(text(mounted!.root)).toContain('T:1 × T:1: 1');
    expect(mounted!.root.findAllByProps({role:'row'})).toHaveLength(15);
    expect([...image.data.slice(4,8)]).toEqual([...image.data.slice(20,24)]);
});
