// Unsaved form transport, not a clone/retry authority normalizer. The native API
// still validates reference/context authority and the generic job gate stays shut.
const workflows: Record<string, string> = {
    basecall_dna: 'dna', basecall_rna: 'rna', plasmid_qc: 'plasmidQc', construct_screening: 'constructScreening',
    methylation_analysis: 'modified', fastq_qc: 'fastqQc', pooled_reference_assignment: 'pooledAssignment',
    clone_validation: 'clone', wf_clone: 'clone', wf_clone_validation: 'clone',
};
const fields: Record<string, string> = {
    pod5_dir: 'pod5Dir', bam_path: 'bamPath', fastq_path: 'fastqPath', reference_set_manifest: 'referenceSetManifest',
    pooled_assignment_min_mapq: 'pooledAssignmentMinMapq', pooled_assignment_min_alignment_score_margin: 'pooledAssignmentMinAlignmentScoreMargin',
    dorado_model: 'doradoModel', dorado_quality_mode: 'doradoModel', ont_molecule_type: 'doradoMolecule', dorado_basecall_mode: 'doradoMode', basecalling_mode: 'doradoMode',
    dorado_batch_size: 'batchSize', wf_clone_assembly_tool: 'assemblyTool', wf_clone_approx_size: 'assemblyApproxSize',
    wf_clone_assm_coverage: 'assemblyCoverage', wf_clone_trim_length: 'assemblyTrimLength', wf_clone_min_quality: 'assemblyMinQuality',
    global_domain_experiment_id: 'globalDomainExperimentId', molbio_ngs_state_revision_id: 'molbioNgsStateRevisionId',
    ngs_reference_revision_id: 'ngsReferenceRevisionId', molbio_revision_binding: 'molbioRevisionBinding',
};

export function buildNanoporeHandoff(request: { name: string; mode: string; params: Record<string, unknown>; pinned_gpu?: number | null; defaultOnlyParams?: string[] }, search: string): { to: string; values: Record<string, unknown> } {
    const params = structuredClone(request.params);
    // This is the current generic selection, not persisted clone authority.
    const mode = request.mode.replace(/^ont_/, '');
    const values: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(params)) {
        // Unknown fields remain in the transport snapshot too; they are not sent as authority.
        values[fields[key] ?? key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase())] = value;
    }
    // Only the form can identify injected defaults. Absent provenance, every
    // supplied alias is explicit (including values equal to a registry default).
    const defaultOnly = new Set(request.defaultOnlyParams);
    for (const [left, right, target] of [
        ['dorado_model', 'dorado_quality_mode', 'doradoModel'],
        ['basecalling_mode', 'dorado_basecall_mode', 'doradoMode'],
    ]) {
        const present = [left, right].filter((key) => key in params);
        const explicit = present.filter((key) => !defaultOnly.has(key));
        const candidates = explicit.length ? explicit : present;
        if (candidates.length === 2 && params[candidates[0]] !== params[candidates[1]]) {
            values.cloneRefusal = `${left} and ${right} must preserve one exact choice. Return to the generic form to correct the conflict.`;
        }
        if (candidates.length) values[target] = params[candidates[0]];
    }
    values.selectedWorkflow = workflows[mode];
    values.jobName = request.name;
    values.inputSource = params.fastq_path ? 'fastq' : params.bam_path ? 'bam' : 'pod5';
    const requiredMolecule = mode === 'basecall_rna' ? 'rna' : 'dna';
    if (!('ont_molecule_type' in params) || defaultOnly.has('ont_molecule_type')) {
        values.doradoMolecule = requiredMolecule;
    } else if (params.ont_molecule_type !== requiredMolecule) {
        values.cloneRefusal = `${request.mode} requires ont_molecule_type=${requiredMolecule}. Return to the generic form to correct the explicit molecule conflict.`;
    }
    const pin = request.pinned_gpu ?? params.pinned_gpu;
    values.pinnedGpus = params.pinned_gpus ?? (pin != null ? [pin] : []);
    values.genericHandoffParams = params;
    if (!values.selectedWorkflow) values.cloneRefusal = `Unknown NGS workflow ${request.mode}. Return to the generic form and select a supported NGS mode.`;
    const query = new URLSearchParams(search);
    query.delete('template');
    query.delete('clone');
    query.set('view', 'launch');
    return { to: `/ngs?${query.toString()}`, values };
}
