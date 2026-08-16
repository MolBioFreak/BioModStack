import type { Job } from './api';

export function normalizeNanoporeCloneState(job: Job | null): Record<string, unknown> | undefined {
    if (!job) return undefined;
    const p = job.params || {};
    const pinnedGpus = (Array.isArray(p.pinned_gpus) ? p.pinned_gpus : (job.pinned_gpu != null ? [job.pinned_gpu] : []))
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value >= 0);
    const inputSource = p.fastq_path ? 'fastq' : (p.bam_path ? 'bam' : 'pod5');
    const legacyModel = String(p.dorado_quality_mode || p.dorado_model || 'sup').toLowerCase();
    const doradoModel = legacyModel === 'fast' || legacyModel.includes('_fast@')
        ? 'fast'
        : legacyModel === 'hac' || legacyModel.includes('_hac@')
            ? 'hac'
            : 'sup';
    const workflowId = String(p.ont_workflow_id || '');
    const selectedWorkflow = workflowId === 'ont_construct_screening'
        ? 'constructScreening'
        : workflowId === 'ont_fastq_qc'
            ? 'fastqQc'
            : workflowId === 'wf_clone_validation' || p.run_assembly === true
                ? 'clone'
                : workflowId === 'ont_basecall_rna'
                    ? 'rna'
                    : workflowId === 'ont_methylation_analysis' || (p.modified_bases && p.modified_bases !== 'none')
                        ? 'modified'
                        : workflowId === 'ont_basecall_dna' && p.barcode_kit
                            ? 'barcode'
                            : workflowId === 'ont_basecall_dna' && p.dorado_basecall_mode === 'duplex'
                                ? 'duplex'
                                : (p.bam_path ? 'bamQc' : (p.fastq_path ? 'plasmidQc' : 'dna'));
    return {
        selectedWorkflow,
        ontWorkflowId: workflowId,
        jobName: job.name,
        pinnedGpus,
        lockGpus: p.lock_gpus === true,
        inputSource,
        pod5Dir: p.pod5_dir || '',
        bamPath: p.bam_path || '',
        bamForceRealign: p.bam_force_realign === true,
        bamMinMapq: p.bam_min_mapq ?? 0,
        fastqPath: p.fastq_path || '',
        globalDomainExperimentId: typeof p.global_domain_experiment_id === 'string' ? p.global_domain_experiment_id : undefined,
        molbioNgsStateRevisionId: typeof p.molbio_ngs_state_revision_id === 'string' ? p.molbio_ngs_state_revision_id : undefined,
        ngsReferenceRevisionId: typeof p.ngs_reference_revision_id === 'string' ? p.ngs_reference_revision_id : undefined,
        doradoModel,
        doradoMolecule: p.ont_molecule_type || 'dna',
        doradoMode: p.dorado_basecall_mode || 'simplex',
        duplexPairs: p.duplex_pairs || '',
        barcodeKit: p.barcode_kit || '',
        sampleSheet: p.sample_sheet || '',
        modifiedBases: p.modified_bases || 'none',
        trimAdapters: p.trim_adapters !== false,
        runModkit: p.run_modkit === true,
        runFastqQc: (typeof p.run_fastq_qc === 'boolean') ? p.run_fastq_qc : p.run_multimer_qc === true,
        runMultimerQc: (typeof p.run_fastq_qc === 'boolean') ? p.run_fastq_qc : p.run_multimer_qc === true,
        expectedPlasmidSize: p.expected_plasmid_size ?? 7000,
        enableRotatingReferenceFrames: p.enable_rotating_reference_frames !== false,
        rotationScanStepBp: p.rotation_scan_step_bp ?? 1,
        minFastqReadLength: p.min_fastq_read_length ?? 0,
        fastqMinimap2Preset: p.fastq_minimap2_preset ?? 'map-ont',
        fastqMinimap2AllowSecondary: p.fastq_minimap2_allow_secondary ?? true,
        igvTrackWindowBp: p.igv_track_window_bp ?? 100,
        igvReportMaxSites: p.igv_report_max_sites ?? 40,
        igvReportFlankingBp: p.igv_report_flanking_bp ?? 200,
        runAssembly: p.run_assembly === true && !p.barcode_kit,
        assemblyTool: p.wf_clone_assembly_tool || 'flye',
        assemblyApproxSize: p.wf_clone_approx_size ?? 7000,
        assemblyCoverage: p.wf_clone_assm_coverage ?? 60,
        assemblyTrimLength: p.wf_clone_trim_length ?? 0,
        assemblyMinQuality: p.wf_clone_min_quality ?? 9,
        wfCloneSample: p.wf_clone_sample || '',
        wfClonePrimers: p.wf_clone_primers || '',
        wfCloneInsertReference: p.wf_clone_insert_reference || '',
        wfCloneHostReference: p.wf_clone_host_reference || '',
        wfCloneRegionsBedfile: p.wf_clone_regions_bedfile || '',
        wfCloneLargeConstruct: p.wf_clone_large_construct === true,
        wfCloneFlyeQuality: p.wf_clone_flye_quality || 'nano-hq',
        wfCloneNonUniformCoverage: p.wf_clone_non_uniform_coverage === true,
        wfCloneCanuFast: p.wf_clone_canu_fast === true,
        wfCloneCutsiteMismatch: p.wf_clone_cutsite_mismatch ?? 1,
        wfClonePrimerMismatch: p.wf_clone_primer_mismatch ?? 2,
        wfCloneExpectedCoverage: p.wf_clone_expected_coverage ?? 95,
        wfCloneExpectedIdentity: p.wf_clone_expected_identity ?? 99,
        singleRefSplitMinMapq: p.single_ref_split_min_mapq ?? 20,
        singleRefSplitMinSegmentBp: p.single_ref_split_min_segment_bp ?? 250,
        singleRefSplitMaxQueryGapBp: p.single_ref_split_max_query_gap_bp ?? 500,
        emitSummary: p.emit_summary !== false,
        batchSize: p.dorado_batch_size ?? null,
        minQscore: p.min_qscore ?? 10,
        modkitFilterThreshold: p.modkit_filter_threshold ?? null,
        qualityFilter: (p.min_qscore === 15 ? 'strict' : p.min_qscore === 7 ? 'permissive' : 'standard'),
    };
}
