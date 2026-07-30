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

test('Clone validation restores bounded vendor and plasmid-dimer tuning selections', () => {
    const restored = normalizeNanoporeCloneState({
        id: 'clone-source', name: 'Clone source', model_id: 'nanopore', mode: 'clone_validation', status: 'completed',
        params: {
            fastq_path: '/data/clone.fastq', run_assembly: true,
            wf_clone_flye_quality: 'nano-raw',
            wf_clone_non_uniform_coverage: true, wf_clone_canu_fast: false,
            wf_clone_cutsite_mismatch: 3, wf_clone_primer_mismatch: 4,
            wf_clone_expected_coverage: 92.5, wf_clone_expected_identity: 98.25,
            enable_rotating_reference_frames: false, rotation_scan_step_bp: 5,
            single_ref_split_min_mapq: 30, single_ref_split_min_segment_bp: 300, single_ref_split_max_query_gap_bp: 700,
        },
    } as never);
    assert.equal(restored?.selectedWorkflow, 'clone');
    assert.equal(restored?.wfCloneFlyeQuality, 'nano-raw');
    assert.equal(restored?.wfCloneNonUniformCoverage, true);
    assert.equal(restored?.wfCloneCanuFast, false);
    assert.equal(restored?.wfCloneCutsiteMismatch, 3);
    assert.equal(restored?.wfClonePrimerMismatch, 4);
    assert.equal(restored?.wfCloneExpectedCoverage, 92.5);
    assert.equal(restored?.wfCloneExpectedIdentity, 98.25);
    assert.equal(restored?.enableRotatingReferenceFrames, false);
    assert.equal(restored?.rotationScanStepBp, 5);
    assert.equal(restored?.singleRefSplitMinMapq, 30);
});
