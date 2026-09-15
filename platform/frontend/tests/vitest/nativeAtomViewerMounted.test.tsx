import React from 'react';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {act,create,type ReactTestRenderer} from 'react-test-renderer';
import {afterEach,expect,it,vi} from 'vitest';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import StructureViewerHost from '../../src/structureViewer/StructureViewerHost';
import {parseScientificNativeMetric,parseScientificPae} from '../../src/lib/scientificViewerIdentity';
import {validateMetricLayer} from '../../src/structureViewer/metrics/MetricRegistry';
import {nativeAtomFixture,unavailable} from '../fixtures/nativeAtomViewerFixture';

const state=vi.hoisted(()=>({gpu:null as any,layers:[] as any[],exports:[] as any[]}));
vi.mock('../../src/components/useThemeColors',()=>({useThemeColors:()=>({bgPrimary:'#000'})}));
vi.mock('../../src/structureViewer/extensions/m6/M6WorkbenchPanel',()=>({M6WorkbenchPanel:(props:any)=>{state.exports=props.tableRows;return null;}}));
vi.mock('react-plotly.js',()=>({default:()=>null}));
vi.mock('../../src/components/ReferenceSelector',()=>({default:()=>null}));
vi.mock('../../src/components/MolstarViewerImpl',()=>({default:(props:any)=>{state.gpu=props;return <div data-engine/>;}}));
// Keep actual shared host, registry, palette, directed renderer and selection owner.
vi.mock('../../src/structureViewer/StructureWorkbench',()=>({StructureWorkbench:(props:any)=>{
    state.layers=props.metricLayers;return <StructureViewerHost {...props} showMetricWorkbench showSequenceTrack={false} showM6Workbench={true} showMeasurements={false}/>;
}}));
let mounted:ReactTestRenderer|undefined;
const text=(node:any):string=>typeof node==='string'?node:(node.children??[]).map(text).join('');
afterEach(async()=>{if(mounted)await act(async()=>mounted!.unmount());mounted=undefined;vi.unstubAllGlobals();state.layers=[];});
const settle=async()=>{await act(async()=>{await new Promise(resolve=>setTimeout(resolve,15));});};
async function mount(payload=nativeAtomFixture(),extra:any={}) {
    const design:any={id:payload.document.candidateId,name:'Candidate',job_id:'job',pdb_path:'candidate.cif',review_profile_id:'structure_prediction',scientific_structure_document:payload.document,...extra.design};
    const props:any={selectedDesignId:design.id,selectedDesign:design,designs:[design],setSelectedDesignId:vi.fn(),colorMode:'plddt',setColorMode:vi.fn(),structureFormat:'cif',activeJob:{id:'job',model_id:'protenix'},getMetricColor:()=>'',viewerAnalyses:{paeMatrixRun:{status:'missing',result:null},onRunPaeMatrix:vi.fn()},...extra.props};
    const fetcher=vi.fn(async (url:string)=>({ok:true,json:async()=>url.includes('residue-metrics')?payload.confidence:url.includes('/pae?')?payload.pae:unavailable('chain_metrics')}));
    vi.stubGlobal('fetch',extra.fetcher??fetcher);
    await act(async()=>{mounted=create(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><StructureViewerPane {...props}/></QueryClientProvider>,{createNodeMock:el=>el.type==='canvas'?{getContext:()=>({createImageData:(w:number,h:number)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}})}:null});});
    await settle(); await settle();
    return {fetcher,props};
}
it('mixed atom confidence reaches actual shared atom palette and exact tooltip/selection queries without a revision flag',async()=>{
    const {fetcher}=await mount();
    const atom=state.layers.find(l=>l.descriptor.dimension==='atom-scalar');
    expect(atom.values).toHaveLength(5); expect(atom.values[3].value).toBe(0.25);
    expect(validateMetricLayer(atom).status).toBe('ok');
    expect(state.gpu.alphafoldView).toBe(false);
    const q=state.gpu.scenePresentation.colorQueries;
    expect(q.filter((q:any)=>q.authAsymId==='E').map((q:any)=>q.authAtomIds)).toEqual([['C1'],['C2']]);
    expect(q[3].color).not.toEqual(q[4].color);
    expect(state.gpu.scenePresentation.tooltipQueries[3].tooltip).toContain('0.25 fraction');
    await act(async()=>mounted!.root.findByProps({'data-native-residue-index':3}).props.onClick());
    expect(state.gpu.scenePresentation.colorQueries.some((q:any)=>q.authAtomIds?.[0]==='C1'&&q.color.g===185)).toBe(true);
    expect(text(mounted!.root)).toContain('Native atom pLDDT');
    expect(text(mounted!.root)).not.toContain('Boltz verified');
    expect(fetcher.mock.calls.some(([url])=>url.includes('/pae?max_size=1024'))).toBe(true);
    expect(fetcher.mock.calls.some(([url])=>url.includes('/analyses'))).toBe(false);
});
it('full 588-token matrix uses dense shared storage and selects distinct ligand atoms',async()=>{
    await mount(nativeAtomFixture(588));
    const layer=state.layers.find(l=>l.descriptor.id==='pae');
    expect(layer.values).toHaveLength(0);expect(layer.dataset.matrix).toHaveLength(588);
    expect(validateMetricLayer(layer).status).toBe('ok');
    const selector=mounted!.root.findAllByType('select').find(el=>el.props.value==='native-plddt')!;
    await act(async()=>selector.props.onChange({target:{value:'pae'}}));
    const grid=()=>mounted!.root.findByProps({role:'grid'});
    expect(grid().props['aria-rowcount']).toBe(588);
    await act(async()=>mounted!.root.findByType('canvas').props.onClick({clientX:4.5,clientY:3.5,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:588,height:588})}}));
    const q=state.gpu.scenePresentation.colorQueries.filter((q:any)=>q.focus);
    expect(q.map((q:any)=>q.authAtomIds)).toEqual([['C1'],['C2']]);
    expect(text(mounted!.root)).toContain('E:1:C1 × E:1:C2');
});
it.each(['missing','queued','running','failed'])('unbound %s analysis is ordinary state, not a false identity alarm',async status=>{
    const p=nativeAtomFixture();p.confidence=unavailable('residue_plddt');
    const {props}=await mount(p,{design:{core_protein_scientific_contract:1,scientific_structure_document:null},props:{viewerAnalyses:{paeMatrixRun:{status,result:null},paeMatrixData:null}}});
    const rendered=text(mounted!.root);
    expect(rendered).toContain('unsupported_model_native_spatial_metric');
    expect(rendered).not.toContain('candidate mismatch');expect(rendered).not.toContain('invalid object');
    expect(state.gpu.alphafoldView).toBe(false);expect(props.viewerAnalyses.paeMatrixRun.status).toBe(status);
});
it.each(['loading','error'])('native metric %s transport state never becomes an identity alarm',async kind=>{
    const fetcher=kind==='loading' ? vi.fn(()=>new Promise(()=>{})) : vi.fn(async()=>({ok:false}));
    await mount(nativeAtomFixture(),{fetcher});
    const rendered=text(mounted!.root);
    expect(rendered).toContain(kind==='loading'?'Loading confidence':'Confidence request failed');
    expect(rendered).not.toContain('identity unavailable');expect(rendered).not.toContain('invalid object');
    expect(state.gpu.alphafoldView).toBe(false);
});
it('actual unavailable envelopes route by selected design without requiring a spatial document',()=>{
    for(const metric of ['residue_plddt','chain_metrics'] as const) {
        expect(parseScientificNativeMetric(unavailable(metric),null,metric,'candidate')).toEqual({status:'unavailable',reason:'unsupported_model_native_spatial_metric'});
        expect(parseScientificNativeMetric(unavailable(metric),null,metric,'foreign').reason).toContain('candidate mismatch');
    }
    expect(parseScientificPae(unavailable('pae'),null,'candidate').reason).toBe('unsupported_model_native_spatial_metric');
    expect(parseScientificPae(null,null).reason).toBe('PAE not loaded');
});
it.each(['candidate','structure','order','duplicate-atom','missing-atom-field','null-atom-field','unknown-field','atom-as-residue'])('rejects native %s contradictions',kind=>{
    const p=nativeAtomFixture();
    if(kind==='candidate')p.confidence.design_id='foreign';
    if(kind==='structure')p.confidence.document={...p.document,contentSha256:'0'.repeat(64)};
    if(kind==='order')p.confidence.axis.residues.reverse();
    if(kind==='duplicate-atom')Object.assign(p.confidence.axis.residues[4],{label_atom_id:'C1',auth_atom_id:'C1'});
    if(kind==='missing-atom-field')delete p.confidence.axis.residues[3].element;
    if(kind==='null-atom-field')p.confidence.axis.residues[3].element=null;
    if(kind==='unknown-field')p.confidence.axis.residues[3].unknown='field';
    if(kind==='atom-as-residue')p.confidence.metric='residue_plddt';
    expect(parseScientificNativeMetric(p.confidence,p.document,'residue_plddt').status).toBe('unavailable');
});

it('shared chain ledger accepts absent numeric output-asym identity without inventing it, and keeps numeric validation',()=>{
    const p=nativeAtomFixture();
    const {units:_units,values:_values,...base}=p.confidence;
    const keys=['0','1','2','3'];
    const chain:any={...base,metric:'chain_metrics',axis:p.pae.row_axis,
        chain_index_map:['A','B','C','E'].map((chain_id,i)=>({chain_id,native_asym_id:i,source_chain_index:i,output_asym_id:null,native_entity_id:i,native_sym_id:0})),
        chains_ptm:Object.fromEntries(keys.map(k=>[k,0.8])),pair_chains_iptm:Object.fromEntries(keys.map(k=>[k,Object.fromEntries(keys.map(j=>[j,0.7]))])),
        role_assignment:null,role_reason:'missing_role_assignment'};
    expect(parseScientificNativeMetric(chain,p.document,'chain_metrics').status).toBe('ok');
    chain.chain_index_map.forEach((c:any,i:number)=>{c.output_asym_id=i;});
    expect(parseScientificNativeMetric(chain,p.document,'chain_metrics').status).toBe('ok');
    for(const value of [true,-1,undefined,'0']) {
        chain.chain_index_map[0].output_asym_id=value;
        expect(parseScientificNativeMetric(chain,p.document,'chain_metrics').status).toBe('unavailable');
    }
});

it.skipIf(!process.env.BMS_TEST_NATIVE_WIRES)('retained actual native owner wire reaches mounted shared atom/588-token/chain consumers',async()=>{
    const root=process.env.BMS_TEST_NATIVE_WIRES!;
    const read=(name:string)=>JSON.parse(readFileSync(join(root,`${name}.json`),'utf8'));
    const payload={document:read('document'),confidence:read('atom'),pae:read('pae')};
    const chain=process.env.BMS_TEST_NATIVE_CHAIN_WIRE ? JSON.parse(readFileSync(process.env.BMS_TEST_NATIVE_CHAIN_WIRE,'utf8')) : read('chain');
    expect(chain.chain_index_map.every((c:any)=>c.output_asym_id===null)).toBe(true);
    const confidence=parseScientificNativeMetric(payload.confidence,payload.document,'residue_plddt');
    const matrix=parseScientificPae(payload.pae,payload.document);
    const chains=parseScientificNativeMetric(chain,payload.document,'chain_metrics');
    expect(confidence.status).toBe('ok');expect(matrix.status).toBe('ok');expect(chains.status).toBe('ok');
    if(confidence.status!=='ok'||matrix.status!=='ok'||chains.status!=='ok')return;
    expect(confidence.values).toHaveLength(4746);expect(matrix.rows).toHaveLength(588);expect(chains.chains).toHaveLength(5);
    const fetcher=vi.fn(async(url:string)=>({ok:true,json:async()=>url.includes('residue-metrics')?payload.confidence:url.includes('/pae?')?payload.pae:chain}));
    await mount(payload,{fetcher});
    const atom=state.layers.find(l=>l.descriptor.dimension==='atom-scalar');
    const pae=state.layers.find(l=>l.descriptor.id==='pae');
    expect(validateMetricLayer(atom).status).toBe('ok');expect(validateMetricLayer(pae).status).toBe('ok');
    expect(atom.values.map((p:any)=>p.value)).toEqual(payload.confidence.values);
    expect(pae.dataset.matrix).toEqual(payload.pae.pae_matrix);
    expect(state.exports.find(row=>row.metric_id==='pae').value).toEqual(payload.pae.pae_matrix);
    const ligand=matrix.rows.filter(r=>r.componentId==='GTP');
    expect(new Set(ligand.map(r=>r.labelAtomId)).size).toBe(32);
    const selector=mounted!.root.findAllByType('select').find(el=>el.props.value==='native-plddt')!;
    await act(async()=>selector.props.onChange({target:{value:'pae'}}));
    const row=matrix.rows.findIndex(r=>r.componentId==='GTP');
    const col=row+1;
    await act(async()=>mounted!.root.findByType('canvas').props.onClick({clientX:col+0.5,clientY:row+0.5,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:588,height:588})}}));
    expect(state.gpu.scenePresentation.colorQueries.filter((q:any)=>q.focus).map((q:any)=>q.labelAtomIds)).toEqual([[matrix.rows[row].labelAtomId],[matrix.columns[col].labelAtomId]]);
    expect(text(mounted!.root)).not.toContain('identity unavailable');
    expect(fetcher.mock.calls).toHaveLength(3);
});
