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
    return {
        jobName: job.name,
        pinnedGpus,
        lockGpus: p.lock_gpus === true,
        inputSource,
        pod5Dir: p.pod5_dir || '',
        bamPath: p.bam_path || '',
        bamForceRealign: p.bam_force_realign === true,
        bamMinMapq: p.bam_min_mapq ?? 0,
        fastqPath: p.fastq_path || '',
        referencePath: p.reference_fasta || '',
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
        wfCloneBasecallerModel: p.wf_clone_basecaller_model || 'dna_r10.4.1_e8.2_400bps_hac@v5.0.0',
        wfCloneSample: p.wf_clone_sample || '',
        wfCloneLargeConstruct: p.wf_clone_large_construct === true,
        emitSummary: p.emit_summary !== false,
        batchSize: p.dorado_batch_size ?? null,
        minQscore: p.min_qscore ?? 10,
        modkitFilterThreshold: p.modkit_filter_threshold ?? null,
        qualityFilter: (p.min_qscore === 15 ? 'strict' : p.min_qscore === 7 ? 'permissive' : 'standard'),
    };
}
