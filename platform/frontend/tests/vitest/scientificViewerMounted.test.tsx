import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import ChainDetailsPanel from '../../src/components/ChainDetailsPanel';
import { document, fixture } from '../fixtures/scientificViewerFixture';

vi.mock('../../src/components/useThemeColors', () => ({useThemeColors: () => ({bgPrimary:'#000'})}));
vi.mock('react-plotly.js', () => ({default: () => null}));
const lifetime = vi.hoisted(() => ({next:0}));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({StructureWorkbench: function Workbench(props: any) {
    const [instance] = React.useState(() => ++lifetime.next);
    return <div data-workbench={true} data-instance={instance} data-residue-points={JSON.stringify(props.residueMetricLayer?.points ?? [])} data-layers={JSON.stringify(props.metricLayers)} />;
}}));
vi.mock('../../src/components/ReferenceSelector', () => ({default: () => null}));
const metric = {length: 1, type: 'protein' as const, avg_plddt: 90, residue_numbers:[100], plddt:[90], sequence:'A'};
const design: any = { id:'candidate', name:'Candidate', job_id:'job', pdb_path:'candidate.pdb',
    review_profile_id:'structure_prediction', core_protein_scientific_contract:1,
    scientific_structure_document:document, chains_ptm:{'0':0, '1':null, '2':0.8} };
const props: any = { selectedDesignId:'candidate', selectedDesign:design, designs:[design], setSelectedDesignId:vi.fn(),
    colorMode:'default', setColorMode:vi.fn(), structureFormat:'pdb', activeJob:{id:'job'}, getMetricColor:()=>'',
    viewerAnalyses:{paeMatrixData:fixture(), chainMetrics:{H:metric,L:metric,T:metric}} };
let mounted: ReactTestRenderer | undefined;
afterEach(async () => { if (mounted) await act(async () => mounted!.unmount()); mounted=undefined; vi.unstubAllGlobals(); });
const wrap = (p: any) => <QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><StructureViewerPane {...p}/></QueryClientProvider>;
const layers = () => JSON.parse(mounted!.root.findByProps({'data-workbench':true}).props['data-layers']);

it('mounted API native axes reach the existing workbench without alphabetical reassignment', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:false})));
    await act(async () => { mounted=create(wrap(props)); });
    const pae = layers().find((l: any) => l.descriptor.id === 'pae');
    expect(pae.values[0].identity.first.authAsymId).toBe('T');
    expect(pae.values[5].identity.first.insertionCode).toBe('');
    expect(pae.values[10].identity.first.insertionCode).toBe('A');
    expect(pae.values[15].identity.first.insertionCode).toBe('B');
    expect(pae.values[20].value).toBe(20);
    expect(pae.values[0].identity.second.authAsymId).toBe('L');
    expect(pae.values[0].identity.first.labelSeqId).toBeUndefined();
});
it('marked missing map leaves structure mounted and an explicit reason instead of legacy metrics', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:false})));
    await act(async () => { mounted=create(wrap({...props, viewerAnalyses:{...props.viewerAnalyses, paeMatrixData:{pae_matrix:[[1]],size:1}}})); });
    expect(layers().some((l: any)=>l.descriptor.id==='pae')).toBe(false);
    expect(JSON.stringify(mounted!.toJSON())).toContain('PAE identity unavailable');
});
it('numeric chain metrics never become A/B/C or turn null into zero for marked designs', async () => {
    await act(async () => { mounted=create(<ChainDetailsPanel design={design} chainMetrics={{H:metric,L:metric,T:metric}}/>); });
    const json=JSON.stringify(mounted!.toJSON());
    expect(json).not.toContain('Chain A');
    expect(json).toContain('missing_producer_chain_identity_ledger');
});

const renderedText = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(renderedText).join('');
it.each(['numeric','native'])('maps %s provider chains through the explicit bound ledger; null is not zero', async mode => {
    const keys = mode === 'numeric' ? ['0','1','2'] : ['T','H','L'];
    const mappedDesign = {...design, chains_ptm:Object.fromEntries(keys.map((k,i)=>[k,[0,null,0.8][i]]))};
    const ledger = {document, provider_chains:Object.fromEntries(keys.map((key,i)=>[key,{documentId:document.documentId,authAsymId:['T','H','L'][i]}]))};
    await act(async () => { mounted=create(<ChainDetailsPanel design={mappedDesign} chainMetrics={{H:metric,L:metric,T:metric}} chainIdentity={ledger}/>); });
    const text=renderedText(mounted!.root);
    expect(text).toContain('Chain T');
    expect(text).toContain('Chain H');
    expect(text).not.toContain('Chain A');
    expect(text.match(/0\.000/g)).toHaveLength(1);
    expect(text).toContain('unavailable');
});
it('same-size alphabetical reassignment is rejected by the mounted consumer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:false})));
    const bad=fixture();
    bad.row_axis.residues.sort((a,b)=>a.chain_id.localeCompare(b.chain_id));
    await act(async () => { mounted=create(wrap({...props,viewerAnalyses:{...props.viewerAnalyses,paeMatrixData:bad}})); });
    expect(layers().some((l: any)=>l.descriptor.id==='pae')).toBe(false);
    expect(renderedText(mounted!.root)).toContain('native position ledger mismatch');
});
it('downsample uses declared native sample indexes instead of chain lengths', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:false})));
    const sampled=fixture(); const indexes=[0,2,4];
    sampled.sampled_row_indices=indexes; sampled.sampled_column_indices=indexes;
    sampled.pae_matrix=indexes.map(i=>indexes.map(j=>sampled.pae_matrix[i][j])); sampled.size=3;
    await act(async () => { mounted=create(wrap({...props,viewerAnalyses:{...props.viewerAnalyses,paeMatrixData:sampled}})); });
    const pae=layers().find((l:any)=>l.descriptor.id==='pae');
    expect(pae.values.map((v:any)=>v.value)).toEqual([0,2,4,10,12,14,20,22,24]);
    expect(pae.values[3].identity.first.insertionCode).toBe('A');
});

it('candidate switches remount the selection owner and reject stale candidate payloads', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:false})));
    await act(async () => { mounted=create(wrap(props)); });
    const before=mounted!.root.findByProps({'data-workbench':true}).props['data-instance'];
    const other={...design,id:'candidate-b',scientific_structure_document:{...document,candidateId:'candidate-b'}};
    await act(async () => mounted!.update(wrap({...props,selectedDesignId:'candidate-b',selectedDesign:other})));
    expect(mounted!.root.findByProps({'data-workbench':true}).props['data-instance']).not.toBe(before);
    expect(layers().some((l:any)=>l.descriptor.id==='pae')).toBe(false);
    expect(renderedText(mounted!.root)).toContain('candidate mismatch');
});
it('late residue response cannot overwrite the newer candidate, even without transport cancellation', async () => {
    let finishOld!: (value: unknown) => void;
    const old = new Promise(resolve=>{finishOld=resolve;});
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ok:true,json:()=>url.includes('/candidate/residue-metrics') ? old : Promise.resolve({plddt:[22],residue_numbers:[1]})})));
    const legacy={...design,core_protein_scientific_contract:undefined,confidence_metrics:{confornets_request:{chain_id:'H'}}};
    const p={...props,colorMode:'plddt',selectedDesign:legacy,viewerAnalyses:{}};
    await act(async () => { mounted=create(wrap(p)); });
    await act(async () => mounted!.update(wrap({...p,selectedDesignId:'candidate-b',selectedDesign:{...legacy,id:'candidate-b'}})));
    const points=()=>JSON.parse(mounted!.root.findByProps({'data-workbench':true}).props['data-residue-points']);
    expect(points().map((p:any)=>p.value)).toEqual([22]);
    await act(async () => finishOld({plddt:[99],residue_numbers:[1]}));
    expect(points().map((p:any)=>p.value)).toEqual([22]);
});
