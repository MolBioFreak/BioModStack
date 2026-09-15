import React from 'react';
import {act,create,type ReactTestRenderer} from 'react-test-renderer';
import {afterEach,expect,it,vi} from 'vitest';
import {SequenceTrackExtension} from '../../src/structureViewer/extensions/sequence/SequenceTrackExtension';
import {canonicalSpatialRefKey} from '../../src/structureViewer/contracts/structureIdentity';

let mounted:ReactTestRenderer|undefined;
afterEach(async()=>{if(mounted)await act(async()=>mounted!.unmount());mounted=undefined;});
const options=()=>mounted!.root.findAllByProps({role:'option'});
const text=(node:any):string=>typeof node==='string'?node:(node.children??[]).map(text).join('');
it('bounds atom DOM, retains native order and exact identities, and recovers after filtering',async()=>{
    const points=Array.from({length:4746},(_,index)=>({residue:{documentId:'structure',authAsymId:'E',authSeqId:1,labelAtomId:`C${4746-index}`,authAtomId:`C${4746-index}`,element:'C'},label:`E:1:C${4746-index}`,value:0.25,displayValue:'25.0%'}));
    const selectedKeys=new Set([canonicalSpatialRefKey(points[3].residue)]);
    const onSelection=vi.fn();
    const render=(p=points)=><SequenceTrackExtension metricId="native-plddt" granularity="atom" points={p} selectedKeys={selectedKeys} onSelection={onSelection}/>;
    await act(async()=>{mounted=create(render());});
    expect(options()).toHaveLength(120);expect(text(options()[0])).toBe('E:1:C474625.0%');
    expect(options().filter(el=>el.props['aria-selected'])).toHaveLength(1);
    await act(async()=>options()[3].props.onClick());
    expect(onSelection).toHaveBeenLastCalledWith({metricId:'native-plddt',identities:[points[3].residue],origin:'sequence'});
    await act(async()=>mounted!.root.findByProps({role:'listbox'}).props.onScroll({currentTarget:{scrollLeft:4746*80}}));
    expect(options()).toHaveLength(120);expect(text(options().at(-1))).toBe('E:1:C125.0%');
    await act(async()=>mounted!.update(render(points.slice(0,5))));
    expect(options()).toHaveLength(5);expect(text(options()[0])).toBe('E:1:C474625.0%');
    await act(async()=>mounted!.update(render([])));
    expect(options()).toHaveLength(0);expect(text(mounted!.root)).toContain('window 0–0');
});
it('keeps the residue track identity, missingness and sequence ordering',async()=>{
    const points=[{residue:{documentId:'s',labelAsymId:'B',labelSeqId:2},label:'B:2',value:22},{residue:{documentId:'s',labelAsymId:'A',labelSeqId:1,insertionCode:'A',authAsymId:'A',authSeqId:100},label:'A:1A',value:0,missingness:'not-computed' as const}];
    const onSelection=vi.fn();
    await act(async()=>{mounted=create(<SequenceTrackExtension metricId="residue" points={points} onSelection={onSelection}/>);});
    expect(mounted!.root.findByProps({'aria-label':'Linked sequence track'})).toBeTruthy();
    expect(text(options()[0])).toBe('A:1Anot-computed');
    await act(async()=>options()[0].props.onClick());
    expect(onSelection.mock.calls[0][0].identities).toEqual([points[1].residue]);
});
