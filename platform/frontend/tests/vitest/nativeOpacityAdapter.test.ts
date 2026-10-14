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
