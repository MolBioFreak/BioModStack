import { execFileSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';
import { buildRfd4NativeSelection as build, type Rfd4NativeSelectionIdentity, type Rfd4NativeSelectionSource } from '../../src/lib/rfd4NativeSelection';

// Ledger-shaped fixtures, not a claim of full native CIF parsing or inference.
const position = (extra: Record<string, unknown> = {}) => ({
    model_number: 1, label_asym_id: 'AA', auth_asym_id: 'X', label_seq_id: '7',
    auth_seq_id: '101', pdbx_PDB_ins_code: 'B', label_entity_id: '2', label_comp_id: 'ALA',
    atoms: [
        { label_atom_id: 'CA', auth_atom_id: 'CAX', model_atom_index: 0, label_alt_id: '.', type_symbol: 'C' },
        { label_atom_id: 'CB', auth_atom_id: 'CBX', model_atom_index: 1, label_alt_id: '.', type_symbol: 'C' },
    ], ...extra,
});
const source = (extra: Partial<Rfd4NativeSelectionSource> = {}): Rfd4NativeSelectionSource => ({
    documentId: 'original-cif', source_positions: [position()], model_number: 1, model_numbers: [1], ...extra,
});
const ref = (extra: Partial<Rfd4NativeSelectionIdentity> = {}): Rfd4NativeSelectionIdentity => ({
    documentId: 'original-cif', labelAsymId: 'AA', labelSeqId: 7, ...extra,
});
const residue = '(chain_id == "AA" and res_id == 7)';
const unresolved = (result: ReturnType<typeof build>) => {
    expect(result.expression).toBeUndefined();
    expect(result.issue).toEqual(expect.any(String));
};

describe('source-qualified RFD4 native visual selection', () => {
    it('uses native labels, not author chain/number, including insertion identity', () => {
        expect(build([ref({ authAsymId: 'X', authSeqId: 101, insertionCode: 'B', entityId: '2' })], source())).toEqual({ expression: residue });
        expect(build([ref({ labelAsymId: undefined, labelSeqId: undefined, authAsymId: 'X', authSeqId: 101, insertionCode: 'B' })], source())).toEqual({ expression: residue });
        unresolved(build([ref({ insertionCode: 'A' })], source()));
    });
    it('maps author atom and model-local index through retained atoms', () => {
        expect(build([ref({ authAtomId: 'CBX', atomIndex: 1 })], source())).toEqual({ expression: '(chain_id == "AA" and res_id == 7 and atom_name == "CB")' });
        unresolved(build([ref({ labelAtomId: 'CB', atomIndex: 0 })], source()));
    });
    it('unions exact identities, deduplicates and JSON-escapes strings', () => {
        const chain = 'B"\\2';
        const s = source({ source_positions: [position(), position({ label_asym_id: chain, label_seq_id: '8' })] });
        expect(build([ref(), ref(), ref({ labelAsymId: chain, labelSeqId: 8 })], s)).toEqual({ expression: `${residue} or (chain_id == ${JSON.stringify(chain)} and res_id == 8)` });
    });
    it('does not invent nonpolymer numbering or accept missing labels', () => {
        unresolved(build([ref({ labelSeqId: undefined, authAsymId: 'X', authSeqId: 101 })], source({ source_positions: [position({ label_seq_id: '.' })] })));
        unresolved(build([ref()], source({ source_positions: undefined })));
        unresolved(build([], source()));
    });
    it('does not guess author insertion mappings or broaden colliding labels', () => {
        const rows = [position(), position({ label_seq_id: '8', pdbx_PDB_ins_code: 'A' })];
        unresolved(build([ref({ labelAsymId: undefined, labelSeqId: undefined, authAsymId: 'X', authSeqId: 101 })], source({ source_positions: rows })));
        unresolved(build([ref({ insertionCode: 'B' })], source({ source_positions: [position(), position({ pdbx_PDB_ins_code: 'A' })] })));
    });
    it('requires a source-local model and never emits a fake model predicate', () => {
        const s = source({ source_positions: [position(), position({ model_number: 2 })], model_numbers: [1, 2], model_number: null });
        unresolved(build([ref({ modelId: '2' })], s));
        expect(build([ref({ modelId: '2' })], { ...s, model_number: 2 })).toEqual({ expression: residue });
        unresolved(build([ref({ modelId: '1' })], { ...s, model_number: 2 }));
        unresolved(build([ref({ modelId: 'opaque-model' })], source()));
    });
    it.each(['assemblyId', 'operatorInstanceId', 'sourceInstanceId', 'altLoc'] as const)('reports unsupported %s mapping without throwing or a partial expression', key => {
        unresolved(build([ref(), ref({ [key]: 'copy-2' })], source()));
    });
    it('does not mix documents, entities or malformed identities', () => {
        unresolved(build([ref({ documentId: 'derivative-pdb' })], source()));
        unresolved(build([ref({ entityId: '3' })], source()));
        unresolved(build([ref({ labelSeqId: NaN })], source()));
    });
    it('does not silently broaden distinct atoms or alternate locations', () => {
        unresolved(build([ref({ labelAtomId: 'CA' })], source({ source_positions: [position({ atoms: [
            { label_atom_id: 'CA', label_alt_id: 'A' }, { label_atom_id: 'CA', label_alt_id: 'B' },
        ] })] })));
        unresolved(build([ref({ labelAtomId: 'missing' })], source()));
    });
});

// Opt-in exact-image CPU qualification. No install, model load, whole-image extraction,
// monkeypatched evaluator or reimplementation. AST closure is the pinned native class.
// Annotation vectors exercise evaluation only; parser field mapping is source-inspected.
it.skipIf(!process.env.BMS_RFD4_SELECTION_SIF)('evaluates generated queries with the pinned native QueryExpression AST', () => {
    const expressions = [build([ref()], source()).expression,
        build([ref({ labelAtomId: 'CB' })], source()).expression,
        build([ref(), ref({ labelAsymId: 'B2', labelSeqId: 9 })], source({ source_positions: [position(), position({ label_asym_id: 'B2', label_seq_id: '9' })] })).expression,
        build([ref({ labelAsymId: 'A"Q', labelAtomId: 'C"A' })], source({ source_positions: [position({ label_asym_id: 'A"Q', atoms: [{ label_atom_id: 'C"A' }] })] })).expression];
    const script = String.raw`
import ast, hashlib, json, operator, subprocess, sys
from types import MappingProxyType
from functools import reduce
from typing import Any, Callable
import numpy as np
image = sys.argv[1]
def read(path):
    return subprocess.check_output(['unsquashfs', '-o', '45056', '-cat', image, path])
raw = read('usr/local/lib/python3.12/site-packages/atomworks/io/utils/query.py')
assert hashlib.sha256(raw).hexdigest() == '73e5f09d393931369bc4a5e1aade09cae982d09f04a129cf4b4b2b6f9852d7f6'
parser = read('usr/local/lib/python3.12/site-packages/atomworks/io/utils/io_utils.py').decode()
assert 'use_author_fields=False' in parser
class AtomArray:
    chain_id = np.array(['AA', 'AA', 'B2', 'A"Q'])
    res_id = np.array([7, 7, 9, 7])
    atom_name = np.array(['CA', 'CB', 'CA', 'C"A'])
    coord = np.zeros((4, 3))
    def array_length(self): return 4
    def get_annotation_categories(self): return ['chain_id', 'res_id', 'atom_name']
AtomArrayStack = AtomArray
node = next(n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name == 'QueryExpression')
exec(compile(ast.Module(body=[node], type_ignores=[]), '<pinned-native-query>', 'exec'))
print(json.dumps([QueryExpression(expr).idxs(AtomArray()).tolist() for expr in json.loads(sys.argv[2])]))
`;
    const output = execFileSync(process.env.BMS_SELECTION_PYTHON || 'python', ['-c', script, process.env.BMS_RFD4_SELECTION_SIF!, JSON.stringify(expressions)], { encoding: 'utf8' });
    expect(JSON.parse(output)).toEqual([[0, 1], [1], [0, 1, 2], [3]]);
});
