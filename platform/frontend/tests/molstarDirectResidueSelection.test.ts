import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import type { Structure as StructureType, StructureElement as StructureElementType } from 'molstar/lib/mol-model/structure';
import type { MolstarDirectResidueClick } from '../src/structureViewer/adapters/MolstarDirectAdapter';
// Mol* 4.5's extensionless ESM graph needs tsx's require hook in Node.
// Use the same graph as the actual adapter, not a second CommonJS model runtime.
const require = createRequire(import.meta.url);
const { CIF } = require('molstar/lib/mol-io/reader/cif') as typeof import('molstar/lib/mol-io/reader/cif');
const { trajectoryFromMmCIF } = require('molstar/lib/mol-model-formats/structure/mmcif') as typeof import('molstar/lib/mol-model-formats/structure/mmcif');
const { Queries, Structure, StructureElement, StructureProperties, StructureSelection } = require('molstar/lib/mol-model/structure') as typeof import('molstar/lib/mol-model/structure');
const { StructureQuery } = require('molstar/lib/mol-model/structure/query/query') as typeof import('molstar/lib/mol-model/structure/query/query');
const { StateSelection } = require('molstar/lib/mol-state') as typeof import('molstar/lib/mol-state');
const { MolstarDirectAdapter } = require('../src/structureViewer/adapters/MolstarDirectAdapter') as typeof import('../src/structureViewer/adapters/MolstarDirectAdapter');
const { StructureSelectionManager } = require('molstar/lib/mol-plugin-state/manager/structure/selection') as typeof import('molstar/lib/mol-plugin-state/manager/structure/selection');
const { InteractivityManager } = require('molstar/lib/mol-plugin-state/manager/interactivity') as typeof import('molstar/lib/mol-plugin-state/manager/interactivity');
const { PolymerSequenceWrapper } = require('molstar/lib/mol-plugin-ui/sequence/polymer') as typeof import('molstar/lib/mol-plugin-ui/sequence/polymer');

// Author chain Q deliberately differs from label chain L. The blank and A
// insertion residues share author number 42 but have distinct native label IDs.
const cif = `data_selection
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA L 1 1 ? 0 0 0 1 90 42 ALA Q CA 1
ATOM 2 C CA . GLY L 1 2 A 3 0 0 1 90 42 GLY Q CA 1
ATOM 3 C CA . SER L 1 3 ? 6 0 0 1 90 -7 SER Q CA 1
ATOM 4 C CA . ALA M 2 1 ? 0 3 0 1 90 42 ALA R CA 1
`;

async function rootStructure() {
    const parsed = await CIF.parseText(cif).run();
    if (parsed.isError) throw Error(parsed.message);
    const trajectory = await trajectoryFromMmCIF(parsed.result.blocks[0]).run();
    return Structure.ofModel(trajectory.representative);
}

function component(root: StructureType, labelChain = 'L') {
    const selection = StructureQuery.run(Queries.generators.atoms({
        chainTest: ctx => StructureProperties.chain.label_asym_id(ctx.element) === labelChain,
    }), root);
    return StructureSelection.unionStructure(selection);
}

// Replace only the UI/WebGL and state transaction boundaries. Adapter loading,
// click subscription, native property reads, queries, bundles and overpaint
// filtering all execute unchanged against parsed Mol* Structures.
async function harness(roots: StructureType[]) {
    const adapter = new MolstarDirectAdapter();
    let emit: (event: any) => void = () => {};
    const structures: any[] = [];
    const cells = new Map<string, any>();
    const commits: any[][] = [];
    let writes: any[] = [];
    const builder: any = {
        to(target: any) { return {
            apply(transform: any, params: any, options: any) {
                const cell = { transform: { ref: options.tags }, params: { values: params } };
                cells.set(options.tags, cell);
                writes.push({ transform, params, tag: options.tags });
                return builder;
            },
            update(params: any) { target.params.values = params; writes.push({ params }); return builder; },
        }; },
        delete(ref: string) { cells.delete(ref); return builder; },
        async commit() { commits.push(writes); writes = []; },
    };
    const plugin: any = {
        canvas3d: {},
        helpers: { substructureParent: {
            events: { removed: { subscribe() {} }, updated: { subscribe() {} } },
            get(structure: StructureType) {
                const index = roots.indexOf(structure.root);
                return index < 0 ? undefined : { transform: { ref: `structure-${index}` } };
            },
        } },
        behaviors: { interaction: { click: { subscribe(handler: any) { emit = handler; return { unsubscribe() {} }; } } } },
        state: { data: { build: () => builder, select: ({ tag }: any) => cells.has(tag) ? [cells.get(tag)] : [] } },
        managers: {
            interactivity: { setProps() {} },
            structure: { hierarchy: { current: { structures } } },
            camera: { focusLoci() {} },
            lociLabels: { addProvider() {}, removeProvider() {} },
        },
        async clear() { structures.length = 0; },
        builders: {
            data: { async download() { return {}; } },
            structure: {
                async parseTrajectory() { return {}; },
                hierarchy: { async applyPreset() {
                    const root = roots[structures.length];
                    const derived = component(root);
                    structures.push({ cell: { obj: { data: root } }, components: [{ representations: [{
                        cell: { transform: { ref: `repr-${structures.length}` }, obj: { data: { sourceData: derived } } },
                    }] }] });
                } },
            },
        },
    };
    plugin.managers.structure.selection = new StructureSelectionManager(plugin);
    plugin.managers.interactivity = new InteractivityManager(plugin);
    (adapter as any).owner.initialize = async () => ({ status: 'ok', plugin });
    adapter.setBackground = async () => {};
    await adapter.mount({} as HTMLElement);
    await adapter.loadScene(roots.map((_, i) => ({ id: `doc-${i}`, url: `/structure-${i}`, format: 'mmcif' })), {});
    return { adapter, plugin, cells, commits, emit: (loci: StructureElementType.Loci) => emit({ current: { loci } }) };
}

test('scene selection projection preserves exact atom identities and omitted versus explicit clear', () => {
    const { toDirectPresentation } = require('../src/structureViewer/runtime/MolstarDirectSceneEngineAdapter') as typeof import('../src/structureViewer/runtime/MolstarDirectSceneEngineAdapter');
    const state: import('../src/structureViewer/contracts/sceneState').StructureSceneState = {
        schemaVersion: 1, ref: { viewerId: 'v', sceneId: 's', generation: 1 },
        documents: [{ documentId: 'doc-0', sourceKind: 'mmcif', sourceUrl: '/source.cif' }], activeDocumentId: 'doc-0',
        provenance: { createdBy: 'test', createdAt: '2026-09-29T00:00:00Z' },
    };
    assert.equal(toDirectPresentation(state).selectionSelections, undefined);
    assert.deepEqual(toDirectPresentation({ ...state, presentation: { selection: [] } }).selectionSelections, []);
    const atom = { documentId: 'doc-0', authAsymId: 'Q', authSeqId: 42, insertionCode: '', labelAtomId: 'CA', authAtomId: 'CX', altLoc: 'B' };
    const projected = toDirectPresentation({ ...state, presentation: { selection: [{ selectionSetId: 'linked', label: 'Linked', residues: [atom] }] } });
    assert.equal(projected.selectionSelections?.length, 1);
    assert.deepEqual(projected.selectionSelections?.[0]?.atoms, ['CA']);
    assert.deepEqual(projected.selectionSelections?.[0]?.auth_atoms, ['CX']);
    assert.equal(projected.selectionSelections?.[0]?.auth_ins_code_id, '');
    assert.equal(projected.selectionSelections?.[0]?.alt_loc_id, 'B');
});

function selectors(t: any) {
    t.mock.method(StateSelection.Generators, 'ofTransformer', (_transform: any) => ({ withTag: (tag: string) => ({ tag }) }));
}

test('derived representation picks retain exact same-root document and native author identity', async () => {
    const root = await rootStructure();
    const otherRoot = await rootStructure();
    const h = await harness([root, otherRoot]);
    const clicks: MolstarDirectResidueClick[] = [];
    h.adapter.setResidueClickHandler(click => clicks.push(click));
    const derived = component(root);
    assert.notEqual(derived, root);
    assert.equal(derived.root, root);
    const loci = StructureElement.Loci.all(derived);
    h.emit(loci);
    assert.deepEqual(clicks[0], { documentId: 'doc-0', labelAsymId: 'L', authAsymId: 'Q', labelSeqId: 1, authSeqId: 42, insertionCode: '', sceneGeneration: 1 });
    const onlyInsertion = StructureQuery.run(Queries.generators.atoms({ residueTest: ctx => StructureProperties.residue.label_seq_id(ctx.element) === 2, chainTest: ctx => StructureProperties.chain.label_asym_id(ctx.element) === 'L' }), derived);
    h.emit(StructureSelection.toLociWithSourceUnits(onlyInsertion));
    assert.equal(clicks[1].insertionCode, 'A');
    assert.equal(clicks[1].authSeqId, 42);
    h.emit(StructureElement.Loci.all(component(otherRoot)));
    assert.equal(clicks[2].documentId, 'doc-1');
    // Identical bytes and chain IDs in an unregistered model are not authority.
    h.emit(StructureElement.Loci.all(component(await rootStructure())));
    assert.equal(clicks.length, 3);
    await h.adapter.loadScene([{ id: 'replacement', url: '/replacement', format: 'mmcif' }], {});
    h.emit(StructureElement.Loci.all(component(otherRoot)));
    assert.equal(clicks.length, 3);
});

test('auth-only selections paint exact insertion residue on a derived representation and clear on deselect', async t => {
    selectors(t);
    const root = await rootStructure();
    const h = await harness([root]);
    await h.adapter.applyPresentation({ colorSelections: [{ document_id: 'doc-0', auth_asym_id: 'Q', start_auth_residue_number: 42, end_auth_residue_number: 42, auth_ins_code_id: 'A', color: '#22d3ee' }], nonSelectedColor: '#808080' });
    const layers = h.cells.get('overpaint-controls').params.values.layers;
    const selected = layers.find((layer: any) => layer.color === 0x22d3ee);
    const selectedLoci = StructureElement.Bundle.toLoci(selected.bundle, root);
    assert.equal(StructureElement.Loci.size(selectedLoci), 1);
    const location = StructureElement.Loci.getFirstLocation(selectedLoci)!;
    assert.equal(StructureProperties.chain.auth_asym_id(location), 'Q');
    assert.equal(StructureProperties.chain.label_asym_id(location), 'L');
    assert.equal(StructureProperties.residue.auth_seq_id(location), 42);
    assert.equal(StructureProperties.residue.pdbx_PDB_ins_code(location), 'A');
    const background = layers.find((layer: any) => layer.color === 0x808080);
    assert.equal(StructureElement.Loci.size(StructureElement.Bundle.toLoci(background.bundle, root)), 2);
    await h.adapter.applyPresentation({ colorSelections: [] });
    assert.equal(h.cells.has('overpaint-controls'), false);
});

test('canonical auth-only selection drives native manager and sequence markers, not metric colors', async t => {
    selectors(t);
    const root = await rootStructure();
    const second = await rootStructure();
    const h = await harness([root, second]);
    const derived = component(root);
    const sequence = new PolymerSequenceWrapper({ structure: derived, units: [...derived.units] });
    let marks = 0;
    const markingKinds: string[] = [];
    h.plugin.managers.interactivity.lociSelects.addProvider(({ loci }: any, action: any) => {
        markingKinds.push(loci.kind);
        sequence.markResidue(loci, action);
        marks++;
    });
    const manager = h.plugin.managers.structure.selection;
    const metrics = [{ document_id: 'doc-0', color: '#808080' }];
    await h.adapter.applyPresentation({ colorSelections: metrics });
    assert.equal(manager.elementCount(), 0);
    assert.deepEqual([...sequence.markerArray], [0, 0, 0]);
    const selection = [{ document_id: 'doc-0', auth_asym_id: 'Q', auth_residue_number: 42, auth_ins_code_id: 'A' }];
    await h.adapter.applyPresentation({ colorSelections: metrics, selectionSelections: selection });
    assert.equal(manager.elementCount(), 1);
    assert.deepEqual([...sequence.markerArray], [0, 2, 0]);
    assert.equal(StructureElement.Loci.size(manager.getLoci(root)), 1);
    assert.equal(StructureElement.Loci.size(manager.getLoci(second)), 0);
    const priorMarks = marks;
    await h.adapter.applyPresentation({ colorSelections: metrics, selectionSelections: selection });
    assert.equal(marks, priorMarks, 'unchanged native selection must not re-mark');
    // A native gesture can change markers without changing canonical queries.
    // Compare actual manager loci rather than trusting a request-only cache.
    manager.fromLoci('add', StructureElement.Loci.all(root), false);
    assert.equal(manager.elementCount(), 4);
    await h.adapter.applyPresentation({ colorSelections: metrics, selectionSelections: selection });
    assert.equal(manager.elementCount(), 1);
    assert.deepEqual([...sequence.markerArray], [0, 2, 0]);
    await h.adapter.applyPresentation({ colorSelections: metrics });
    assert.equal(manager.elementCount(), 1, 'omitted canonical selection leaves native tools independent');
    // Changing to a blank insertion must not retain the A insertion residue.
    await h.adapter.applyPresentation({ selectionSelections: [{ ...selection[0], auth_ins_code_id: '' }] });
    assert.deepEqual([...sequence.markerArray], [2, 0, 0]);
    await h.adapter.applyPresentation({ selectionSelections: [{ document_id: 'doc-1', auth_asym_id: 'Q', auth_seq_id: -7, auth_ins_code_id: '' }] });
    assert.equal(manager.elementCount(), 1);
    assert.deepEqual([...sequence.markerArray], [0, 0, 0]);
    const selected = StructureElement.Loci.getFirstLocation(manager.getLoci(second))!;
    assert.equal(StructureProperties.residue.auth_seq_id(selected), -7);
    await h.adapter.applyPresentation({ selectionSelections: [] });
    assert.equal(manager.elementCount(), 0);
    assert.deepEqual([...sequence.markerArray], [0, 0, 0]);
    assert.ok(markingKinds.every(kind => kind !== 'every-loci'), 'canonical changes never issue a global whole-scene clear');
});
