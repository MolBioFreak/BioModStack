import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeNanoporeCloneState } from '../src/lib/nanoporeCloneState';

test('P4 clone restores exact user selections rather than resolved model identities', () => {
    const restored = normalizeNanoporeCloneState({
        id: 'p4-source',
        name: 'P4 source',
        model_id: 'nanopore',
        mode: 'basecall_dna',
        status: 'completed',
        params: {
            pod5_dir: '/data/pod5',
            dorado_model: 'dna_r10.4.1_e8.2_400bps_hac@v5.2.0',
            dorado_quality_mode: 'hac',
            ont_molecule_type: 'dna',
            dorado_basecall_mode: 'duplex',
            duplex_pairs: '/data/pairs.txt',
            barcode_kit: '',
            sample_sheet: '',
            modified_bases: 'none',
            run_modkit: false,
            min_qscore: 0,
            dorado_batch_size: 32,
        },
    } as never);
    assert.equal(restored?.doradoModel, 'hac');
    assert.equal(restored?.doradoMolecule, 'dna');
    assert.equal(restored?.doradoMode, 'duplex');
    assert.equal(restored?.duplexPairs, '/data/pairs.txt');
    assert.equal(restored?.modifiedBases, 'none');
    assert.equal(restored?.runModkit, false);
    assert.equal(restored?.minQscore, 0);
    assert.equal(restored?.batchSize, 32);
});

test('P4 clone derives quality from exact retained model identities and disables invalid barcode assembly', () => {
    const restored = normalizeNanoporeCloneState({
        id: 'legacy-p4-source',
        name: 'Legacy P4 source',
        model_id: 'nanopore',
        mode: 'basecall_dna',
        status: 'completed',
        params: {
            pod5_dir: '/data/pod5',
            dorado_model: 'dna_r10.4.1_e8.2_400bps_fast@v5.2.0',
            barcode_kit: 'SQK-RBK114-96',
            run_assembly: true,
        },
    } as never);
    assert.equal(restored?.doradoModel, 'fast');
    assert.equal(restored?.runAssembly, false);
});
