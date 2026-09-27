// Route identity is scientific intent: an unknown saved selector must not become RFantibody.
export type ExistingDeNovoGenerator = 'rfantibody' | 'boltzgen' | 'ppiflow' | 'bindcraft2';

export function resolveExistingDeNovoGenerator(values: Record<string, unknown> = {}): ExistingDeNovoGenerator | null {
    const explicit = values.denovo_generator ?? values.generator;
    if (explicit !== undefined && explicit !== null && String(explicit).trim()) {
        const selected = String(explicit).trim().toLowerCase();
        if (selected !== 'rfantibody' && selected !== 'boltzgen' && selected !== 'ppiflow' && selected !== 'bindcraft2') return null;
        // A saved selector cannot override a different model-owned execution mode.
        const mode = values.mode ?? (values.stage_family === 'ppiflow' ? values.stage_mode : undefined);
        if (mode !== undefined && mode !== null && String(mode).trim()) {
            const expected = mode === 'generator_backbone_refine' ? 'ppiflow'
                : mode === 'nanobody_binder' ? 'boltzgen'
                    : mode === 'campaign' ? 'bindcraft2'
                        : ['antibody_denovo', 'antibody_denovo_pipeline', 'antibody_refinement_pipeline'].includes(String(mode)) ? 'rfantibody' : null;
            if (expected !== selected) return null;
        }
        return selected;
    }
    const stage = values.stage_family === 'ppiflow' ? values.stage_mode : values.mode;
    if (stage === 'generator_backbone_refine') return 'ppiflow';
    if (stage === 'campaign' && (values.model_id === 'bindcraft2' || values.stage_family === 'bindcraft2')) return 'bindcraft2';
    if (values.boltzgen_mode === 'nanobody_binder' || values.mode === 'nanobody_binder') return 'boltzgen';
    if (typeof stage === 'string' && stage.trim() && ![
        'antibody_denovo', 'antibody_denovo_pipeline', 'antibody_refinement_pipeline',
    ].includes(stage)) return null;
    // Only legacy antibody jobs without a generator selector inherit RFantibody.
    return 'rfantibody';
}

export function hydrateInitialStageSelection(values: Record<string, unknown> = {}) {
    return {
        sequence_design: values.initial_orchestration_sequence_design !== undefined
            ? values.initial_orchestration_sequence_design === true
            : values.seq_designer !== undefined ? values.seq_designer !== 'none'
                : [values.seq_design_fampnn, values.seq_design_caliby, values.seq_design_antifold, values.seq_design_proteinmpnn].some(value => value === true),
        ppiflow: values.initial_orchestration_ppiflow !== undefined ? values.initial_orchestration_ppiflow === true
            : values.run_ppiflow_backbone_refine === true || values.run_ppiflow_maturation === true,
        validation: values.initial_orchestration_validation !== undefined ? values.initial_orchestration_validation === true : values.run_structure_validation === true,
        qc: values.initial_orchestration_qc !== undefined ? values.initial_orchestration_qc === true
            : [values.run_frustrampnn, values.run_anarcii_post, values.run_immunogenicity_scoring, values.run_stability_scoring, values.run_thermompnn, values.openmm_enabled].some(value => value === true),
    };
}
