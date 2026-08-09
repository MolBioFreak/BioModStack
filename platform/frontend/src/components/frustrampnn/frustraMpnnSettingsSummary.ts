import type { FrustraMpnnEffectiveSettingsProjection } from '../../lib/frustraMpnnApi.js';

export type FrustraMpnnValueOrigin = 'bms_default' | 'operator_request';

export interface FrustraMpnnRequestedEffectiveSummary {
    selectionMode: string;
    model: { requested: number; effective: number; origin: FrustraMpnnValueOrigin };
    altloc: { requested: string; effective: string; origin: FrustraMpnnValueOrigin };
    thresholds: {
        mode: string;
        highMax: number;
        minimalMin: number;
        origins: { mode: FrustraMpnnValueOrigin; highMax: FrustraMpnnValueOrigin; minimalMin: FrustraMpnnValueOrigin };
    };
    counts: {
        selectedEntities: number;
        selectedResidues: number;
        resolvedEntities: number;
        resolvedChains: number;
        resolvedResidues: number;
    };
    selectedEntities: string[];
    selectedResidues: string[];
    resolvedEntities: string[];
    resolvedResidues: string[];
    valueOrigins: Record<string, FrustraMpnnValueOrigin>;
}

const entityLabel = (entity: {
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    auth_asym_id: string;
}): string => [
    `entity ${entity.entity_instance_id}`,
    `author chain ${entity.auth_asym_id}`,
    entity.source_entity_id ? `source entity ${entity.source_entity_id}` : null,
    entity.label_asym_id ? `label chain ${entity.label_asym_id}` : null,
].filter(Boolean).join(' · ');

const residueLabel = (residue: {
    entity_instance_id: string;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
}): string => (
    `entity ${residue.entity_instance_id} · ${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code} · sequence ${residue.sequence_index}`
);

export const buildFrustraMpnnRequestedEffectiveSummary = (
    effective: FrustraMpnnEffectiveSettingsProjection,
): FrustraMpnnRequestedEffectiveSummary => {
    const requested = effective.requested_settings;
    const selectedEntities = requested.protein_selection.mode === 'selected_entities'
        ? requested.protein_selection.entities.map(entityLabel)
        : [];
    const selectedResidues = requested.protein_selection.mode === 'selected_residues'
        ? requested.protein_selection.residues.map(residueLabel)
        : [];
    const resolvedEntities = effective.resolved_chains.map((chain) => (
        `${entityLabel(chain.entity)} · resolved PDB chain ${chain.pdb_chain_id}`
    ));
    const resolvedResidues = effective.resolved_chains.flatMap((chain) => chain.residues.map((residue) => (
        `${residueLabel(residue)} · ${residue.wt} · PDB chain ${residue.pdb_chain_id} · model position ${residue.model_position}`
    )));
    const valueOrigins = {
        'protein selection mode': effective.value_sources.protein_selection.mode,
        'selected entities': effective.value_sources.protein_selection.entities,
        'selected residues': effective.value_sources.protein_selection.residues,
        'selected model number': effective.value_sources.source_structure.selected_model_number,
        'preferred altloc': effective.value_sources.source_structure.preferred_altloc,
        'classification mode': effective.value_sources.classification_policy.mode,
        'high threshold': effective.value_sources.classification_policy.high_max,
        'minimal threshold': effective.value_sources.classification_policy.minimal_min,
    };
    return {
        selectionMode: requested.protein_selection.mode,
        model: {
            requested: requested.source_structure.selected_model_number,
            effective: requested.source_structure.selected_model_number,
            origin: effective.value_sources.source_structure.selected_model_number,
        },
        altloc: {
            requested: requested.source_structure.preferred_altloc,
            effective: requested.source_structure.preferred_altloc,
            origin: effective.value_sources.source_structure.preferred_altloc,
        },
        thresholds: {
            mode: requested.classification_policy.mode,
            highMax: requested.classification_policy.high_max,
            minimalMin: requested.classification_policy.minimal_min,
            origins: {
                mode: effective.value_sources.classification_policy.mode,
                highMax: effective.value_sources.classification_policy.high_max,
                minimalMin: effective.value_sources.classification_policy.minimal_min,
            },
        },
        counts: {
            selectedEntities: selectedEntities.length,
            selectedResidues: selectedResidues.length,
            resolvedEntities: resolvedEntities.length,
            resolvedChains: effective.resolved_chains.length,
            resolvedResidues: resolvedResidues.length,
        },
        selectedEntities,
        selectedResidues,
        resolvedEntities,
        resolvedResidues,
        valueOrigins,
    };
};
