/** Presentation only: never use display labels as native request keys. */
const labels: Record<string, string> = {
    maturation_anchors_enabled: 'Analyze interface anchors',
    maturation_flow_enabled: 'Run PPIFlow maturation',
    maturation_redesign_enabled: 'Run FA-MPNN sequence redesign',
    maturation_repack_enabled: 'Repack side chains',
    maturation_anchor_threshold: 'Anchor energy threshold',
    maturation_anchor_distance_cutoff: 'Anchor distance cutoff',
    ppiflow_rotamer_shell_distance: 'PPIFlow repacking shell distance',
    ppiflow_start_t: 'PPIFlow starting time',
    ppiflow_samples_per_target: 'PPIFlow samples per target',
    ppiflow_checkpoint: 'PPIFlow checkpoint',
    fampnn_temperature: 'FA-MPNN sampling temperature',
    fampnn_num_steps: 'FA-MPNN sampling steps',
    fampnn_checkpoint: 'FA-MPNN checkpoint',
};
export function nativeParameterLabel(parameter: { name: string; label?: string }): string {
    if (parameter.label && parameter.label !== parameter.name && !parameter.label.includes('_')) return parameter.label;
    return labels[parameter.name] ?? parameter.name.replace(/^boltzgen_/, '').replaceAll('_', ' ')
        .replace(/\bppiflow\b/gi, 'PPIFlow').replace(/\bfampnn\b/gi, 'FA-MPNN')
        .replace(/\bpdb\b/gi, 'PDB').replace(/^./, letter => letter.toUpperCase());
}
