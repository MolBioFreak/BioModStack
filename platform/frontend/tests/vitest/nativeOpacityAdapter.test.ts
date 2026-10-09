import {expect,it,vi,afterEach} from 'vitest';
import {parsePDB} from 'molstar/lib/mol-io/reader/pdb/parser';
import {trajectoryFromPDB} from 'molstar/lib/mol-model-formats/structure/pdb';
import {Structure,StructureElement} from 'molstar/lib/mol-model/structure';
import {StateSelection} from 'molstar/lib/mol-state';
import {OrderedSet} from 'molstar/lib/mol-data/int';
import {MolstarDirectAdapter} from '../../src/structureViewer/adapters/MolstarDirectAdapter';

vi.mock('../../src/structureViewer/runtime/createDirectMolstarEngineOwner',()=>({createDirectMolstarEngineOwner:()=>({dispose(){}})}));
afterEach(()=>vi.restoreAllMocks());

it('a residue hover is bounded while exact atom annotations remain available and HTML stays escaped', async () => {
    const atoms = ['N', 'CA', 'C', 'O', 'CB', 'CG', 'CD1', 'CD2'];
    const pdb = atoms.map((name, i) => `ATOM  ${String(i + 1).padStart(5)} ${name.padStart(4)} LEU B${String(93).padStart(4)}    ${'0.000'.padStart(8)}${'0.000'.padStart(8)}${'0.000'.padStart(8)}  1.00 90.00           C`).join('\n') + '\nEND\n';
    const parsed = await parsePDB(pdb).run();
    if (parsed.isError) throw Error(parsed.message);
    const trajectory = await trajectoryFromPDB(parsed.result).run();
    const structure = Structure.ofModel(trajectory.representative);
    expect(structure.elementCount).toBe(atoms.length);
    const addProvider = vi.fn();
    const plugin: any = { managers: {
        structure: { hierarchy: { current: { structures: [{ cell: { obj: { data: structure } } }] } } },
        lociLabels: { addProvider, removeProvider: vi.fn() },
    } };
    const adapter = new MolstarDirectAdapter();
    (adapter as any).documentStructures.set(structure, 'selected-document');
    const selections = atoms.map((name, i) => ({ document_id: 'selected-document', atom_id: [i + 1], tooltip: `${name} <native>: 91.7 /100` }));
    await (adapter as any).applyTooltips(plugin, [...selections, { document_id: 'other-document', atom_id: [1], tooltip: 'Foreign sample' }]);
    const provider = addProvider.mock.calls[0][0];
    const all = StructureElement.Loci(structure, structure.units.map(unit => ({ unit, indices: OrderedSet.ofBounds(0, unit.elements.length) })));
    const label = provider.label(all);
    expect(label.split('<br/>')).toHaveLength(5);
    expect(label).toContain('4 more matching annotations');
    expect(label).toContain('N &lt;native&gt;: 91.7 /100');
    expect(label).not.toContain('<native>');
    expect(label).not.toContain('Foreign sample');
    // A native atom pick still exposes its own annotation, including an atom
    // omitted from the bounded residue preview. No aggregation or value loss.
    const last = StructureElement.Loci(structure, [{ unit: structure.units[0], indices: OrderedSet.ofSingleton(7) }]);
    expect(provider.label(last)).toBe('CD2 &lt;native&gt;: 91.7 /100');
    expect(selections).toHaveLength(8);
});


it('4746 exact atom colors commit once; opacity bursts reuse loci/overpaint and commit only the latest pending update',async()=>{
    const pdb=Array.from({length:4746},(_,i)=>`ATOM  ${String(i+1).padStart(5)}  CA  ALA A${String(i+1).padStart(4)}    ${'0.000'.padStart(8)}${'0.000'.padStart(8)}${'0.000'.padStart(8)}  1.00 90.00           C`).join('\n')+'\nEND\n';
    const parsed=await parsePDB(pdb).run();
    if(parsed.isError)throw Error(parsed.message);
    const trajectory=await trajectoryFromPDB(parsed.result).run();
    const structure=Structure.ofModel(trajectory.representative);
    expect(structure.elementCount).toBe(4746);
    const cells=new Map<string,any>();
    const writes:string[]=[];
    let commits=0;
    let release:(()=>void)|undefined;
    let block=false;
    vi.spyOn(StateSelection.Generators,'ofTransformer').mockImplementation((transform:any)=>({withTag:(tag:string)=>({tag,transform})}) as any);
    const builder:any={
        to(target:any){return {
            apply(_transform:any,params:any,{tags}:any){writes.push(tags);cells.set(tags,{transform:{ref:tags},params:{values:params}});return builder;},
            update(params:any){writes.push(target.transform.ref);target.params.values=params;return builder;},
        };},
        delete(ref:string){writes.push(`delete:${ref}`);cells.delete(ref);return builder;},
        async commit(){commits++;if(block){block=false;await new Promise<void>(resolve=>{release=resolve;});}},
    };
    const plugin:any={state:{data:{build:()=>builder,select:({tag}:any)=>cells.has(tag)?[cells.get(tag)]:[]}},managers:{
        structure:{hierarchy:{current:{structures:[{cell:{obj:{data:structure}},components:[{representations:[{cell:{transform:{ref:'repr'},obj:{data:{sourceData:structure}}}}]}]}]}}},
        camera:{focusLoci:vi.fn()},lociLabels:{addProvider:vi.fn(),removeProvider:vi.fn()},
    }};
    const adapter=new MolstarDirectAdapter();
    (adapter as any).plugin=plugin;
    (adapter as any).documentStructures.set(structure,'doc');
    const reload=vi.spyOn(adapter,'loadScene');
    const bundle=vi.spyOn(StructureElement.Bundle,'fromLoci');
    const selections=Array.from({length:4746},(_,i)=>({document_id:'doc',atom_id:[i+1],color:i%2?'#3b82f6':'#f97316',opacity:1}));
    const tooltips=[{document_id:'doc',atom_id:[1],tooltip:'Native atom'}];
    await adapter.applyPresentation({colorSelections:selections,tooltipSelections:tooltips});
    expect(commits).toBe(1);
    expect(writes).toEqual(['overpaint-controls']);
    const bundles=bundle.mock.calls.length;
    const original=cells.get('overpaint-controls').params.values;
    const paint=StructureElement.Bundle.toLoci(original.layers[0].bundle,structure);
    expect(StructureElement.Loci.size(paint)).toBe(2373);
    block=true;
    const first=adapter.applyPresentation({colorSelections:selections.map(s=>({...s,opacity:0.95})),tooltipSelections:tooltips});
    await vi.waitFor(()=>expect(release).toBeTypeOf('function'));
    const queued=Array.from({length:19},(_,i)=>adapter.applyPresentation({colorSelections:selections.map(s=>({...s,opacity:0.9-i*0.05})),tooltipSelections:tooltips}));
    release!(); await Promise.all([first,...queued]);
    expect(commits).toBe(3);
    expect(writes.filter(w=>w==='overpaint-controls')).toHaveLength(1);
    expect(cells.get('overpaint-controls').params.values).toBe(original);
    // Only the two merged transparency bundles are serialized; no per-atom rebuild.
    expect(bundle.mock.calls.length).toBe(bundles+2);
    expect(plugin.managers.lociLabels.addProvider).toHaveBeenCalledTimes(1);
    expect(cells.get('transparency-controls').params.values.layers[0].value).toBeCloseTo(1);
    expect(reload).not.toHaveBeenCalled();
    await adapter.applyPresentation({colorSelections:selections,tooltipSelections:tooltips});
    expect(cells.has('transparency-controls')).toBe(false);
    expect(writes.filter(w=>w==='overpaint-controls')).toHaveLength(1);
    // A changed exact selection still overrides the base metric (last layer wins).
    await adapter.applyPresentation({colorSelections:[...selections,{document_id:'doc',atom_id:[1],color:'#22d3ee',opacity:1,focus:true}],tooltipSelections:tooltips});
    expect(writes.filter(w=>w==='overpaint-controls')).toHaveLength(2);
    expect(plugin.managers.camera.focusLoci).toHaveBeenCalledTimes(1);
    const selected=cells.get('overpaint-controls').params.values.layers.find((layer:any)=>layer.color===0x22d3ee);
    expect(StructureElement.Loci.size(StructureElement.Bundle.toLoci(selected.bundle,structure))).toBe(1);
},30000);
