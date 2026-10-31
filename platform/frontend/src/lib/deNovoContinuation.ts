import { fetchJobById, materializeExactStructure, type StructureSourceSelection, type StructureMaterialization } from './api';
import { mountedApplicationBase } from './sourceResultReturn';


export type ContinuationRecipe = NonNullable<StructureMaterialization['native_recipe']>;

export type Rfd4Destination = 'rfd4_partial' | 'rfd4_inverse' | 'rfd4_evaluate' | 'rfd4_state';
export type DeNovoDestination = 'redesign' | 'sequence' | 'prediction' | Rfd4Destination;
export const rfd4Destinations = ['rfd4_partial', 'rfd4_inverse', 'rfd4_evaluate'] as const;
export const isDeNovoDestination = (value: string | null): value is DeNovoDestination => ['redesign', 'sequence', 'prediction', ...rfd4Destinations, 'rfd4_state'].includes(value ?? '');
export const continuationLabel = (destination: DeNovoDestination) => ({ redesign: 'Redesign exact structure', sequence: 'Design sequence from exact structure', prediction: 'Predict exact structure', rfd4_partial: 'Refine with partial diffusion', rfd4_inverse: 'Native inverse folding', rfd4_evaluate: 'Evaluate with RFD4', rfd4_state: 'Continue retained RFD4 state' }[destination]);
export const sequenceDestinations = [
    ['proteinmpnn', 'design', 'ProteinMPNN design'],
    ['fampnn', 'design', 'FA-MPNN design'],
    ['fampnn', 'fixed_backbone', 'FA-MPNN fixed backbone'],
    ['caliby_experimental', 'ensemble_design', 'Caliby ensemble design'],
    ['caliby_experimental', 'sidechain_pack', 'Caliby sequence-preserving side-chain packing'],
    ['ligandmpnn', 'ligand_aware', 'LigandMPNN ligand-aware design'],
    ['ligandmpnn', 'ntp_aware', 'LigandMPNN NTP-aware design'],
    ['ligandmpnn', 'metal_aware', 'LigandMPNN metal-aware design'],
    ['ligandmpnn', 'dna_aware', 'LigandMPNN DNA-aware design'],
    ['protonpottsmpnn', 'redesign', 'ProtonPottsMPNN pH-specific redesign'],
] as const;

export function continuationHref(source: StructureSourceSelection, destination: DeNovoDestination, sequenceModel?: readonly [string, string]): string {
    const query = new URLSearchParams({ source_structure: JSON.stringify(source), continuation: destination });
    query.set('return_to', `${window.location.pathname}${window.location.search}${window.location.hash}`);
    const context = new URLSearchParams(window.location.search);
    for (const key of ['launch_context_id', 'project_id', 'setup_context_id']) {
        const value = context.get(key); if (value) query.set(key, value);
    }
    if (destination === 'redesign') {
        query.set('template', 'protein_modification_experimental');
        query.set('modification_mode', 'rfd3_local_redesign');
    } else if (destination.startsWith('rfd4_')) {
        query.set('template', 'protein_modification_experimental');
        query.set('modification_mode', 'de_novo_design');
        query.set('generator', 'rfd4_proteina');
        query.set('native_mode', destination === 'rfd4_evaluate' ? 'evaluate' : 'generate');
    } else if (destination === 'prediction') query.set('template', 'structure_prediction');
    else { query.set('model', sequenceModel?.[0] ?? 'proteinmpnn'); query.set('mode', sequenceModel?.[1] ?? 'design'); }
    return `${mountedApplicationBase()}submit?${query}`;
}

// Read only the selected materialization's producer document, never Job/input roles.
export function continuationRoleDefaults(native: StructureMaterialization, destination: DeNovoDestination, draft: Record<string, unknown> = {}): Record<string, unknown> {
    const document = native.source_identity?.producer_document as Record<string, unknown> | undefined;
    const read = (key: string): string[] | undefined => {
        const value = document?.[key];
        return typeof value === 'string' ? value.split(',').map(v => v.trim()).filter(Boolean)
            : Array.isArray(value) && value.every(v => typeof v === 'string') ? [...value] : undefined;
    };
    const binder = read('binder_chains'), target = read('target_chains');
    const defaults: Record<string, unknown> = destination === 'redesign'
        // Missing evidence leaves the existing editor defaults alone; it is not
        // an authored clear or a new requirement to choose a chain again.
        ? binder?.length === 1 ? {
            design_chains: binder[0],
            ...(target === undefined ? {} : { context_chains: target }),
        } : {}
        : destination === 'sequence' && binder?.length ? {
            binder_chains: binder.join(','), design_chain: binder.join(','),
            ...(target === undefined ? {} : { target_chains: target.join(','), target_chain: target.join(',') }),
        } : {};
    return { ...defaults, ...draft }; // Own-property clears, nulls and edits win.
}

export function nativeSequenceInput(model: string, prepared: StructureMaterialization, native: StructureMaterialization = prepared): Record<string, unknown> {
    return model === 'caliby_experimental'
        ? { ensembles: [{ ensemble_id: 'selected-candidate', states: [{ state_id: 'selected-structure', path: native.path }] }] }
        : model === 'ligandmpnn' || model === 'protonpottsmpnn'
            ? (prepared.format === 'pdb' ? { input_pdb: prepared.path, target_pdb: prepared.path } : {})
            : prepared.format === 'pdb' ? { input_pdb: prepared.path } : {};
}

// Materialization retains immutable input only; never submits a scientific Job.
export async function prepareDeNovoContinuation(source: StructureSourceSelection, destination: DeNovoDestination, state?: { path: string; sha256?: string; seed?: number }) {
    const native = await materializeExactStructure({ ...source, output_format: 'native' });
    // The receiving editor already owns MODEL selection. Keep an unselected
    // ensemble native until then; a PDB-only derivative cannot represent it.
    const needsModelSelection = native.model_number == null && (native.model_numbers?.length ?? 0) > 1;
    const prepared = destination !== 'redesign' || native.format === 'pdb' || needsModelSelection ? native
        : await materializeExactStructure({ path: native.path, expected_sha256: native.sha256, output_format: 'pdb', ...(native.model_number == null ? {} : { model_number: native.model_number }) });
    const retainedSource = native.source_structure ?? source;
    const common = { ...continuationRoleDefaults(native, destination), source_structure: retainedSource, _source_prepared: prepared, _source_native_prepared: native };
    if (destination.startsWith('rfd4_')) return { ...common,
        generator: 'rfd4_proteina', generator_model_id: 'rfd4_proteina',
        native_mode: destination === 'rfd4_evaluate' ? 'evaluate' : 'generate',
        native_sources: { selected: retainedSource, ...(destination === 'rfd4_state' && state ? { resume: { kind: 'file', path: state.path, ...(state.sha256 ? { expected_sha256: state.sha256 } : {}) } } : {}) },
        rfd4_request: { input: { kind: 'annotated', slots: ['selected'] },
            ...(destination === 'rfd4_inverse' ? { groups: { generation: 'inverse_folding' } } : {}),
            ...(destination === 'rfd4_state' && state ? { resume_slot: 'resume', ...(state.seed !== undefined ? { settings: { seed: state.seed } } : {}) } : {}) },
        _rfd4_continuation: destination,
    };
    if (destination === 'sequence') return { ...common, ...nativeSequenceInput('proteinmpnn', prepared) };
    if (destination === 'redesign') return { ...common, input_structure: prepared.path,
        model_number: prepared.model_number ?? source.model_number,
        _redesign_source: { type: 'run', name: prepared.path.split('/').pop(), path: prepared.path,
            url: `/api/files/download/${encodeURIComponent(prepared.path)}`, sourceStructure: retainedSource, prepared },
    };
    // The model-owned recipe, not a browser amino-acid alphabet, owns chemistry.
    const recipe = native.native_recipe;
    const chains = recipe?.complex_components.flatMap(component => {
        const entry = recipe.entries.find(item => item.id === component.id);
        return entry && ['protein', 'peptide'].includes(component.type)
            ? [{ model_number: entry.model_number, id: component.id, auth_asym_id: entry.auth_asym_id, sequence: component.sequence ?? '', component }] : [];
    }) ?? [];
    return { ...common, _continuation_recipe: recipe, _continuation_chains: chains, sequence: '' };
}

/** Reuse JobSubmission's ordinary clone receiver. No resubmit/retry or queue mutation. */
export async function repeatDeNovoSettings(jobId: string): Promise<string> {
    const { data: job } = await fetchJobById(jobId);
    const clone = {
        name: `${job.name}_repeat`, model_id: job.model_id, mode: job.mode,
        source_job_id: job.id, source_structure: job.source_structure,
        execution_target_id: job.execution_target_id ?? null,
        execution_policy: job.execution_policy ?? { remote_result_policy: 'manual' },
        params: structuredClone(job.params ?? {}), binder_round: job.binder_round,
        sequence_design: job.sequence_design, pinned_gpu: job.pinned_gpu ?? null,
    };
    localStorage.setItem('clonedJobData', JSON.stringify(clone));
    const query = new URLSearchParams({ return_to: `${window.location.pathname}${window.location.search}${window.location.hash}` });
    return `${mountedApplicationBase()}submit?${query}`;
}

export function inspectedResultState() {
    const query = new URLSearchParams(window.location.search);
    return { candidate: query.get('native_candidate'), page: Math.max(1, Number(query.get('native_page')) || 1) };
}
export function rememberInspectedResult(candidate: string | null, page: number) {
    const url = new URL(window.location.href);
    if (candidate) url.searchParams.set('native_candidate', candidate);
    url.searchParams.set('native_page', String(page));
    window.history.replaceState(window.history.state, '', url);
}
