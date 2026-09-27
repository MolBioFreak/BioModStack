import { isAxiosError } from 'axios';
import { api, prepareJobSubmission, submitJob, type Job } from './api';
import type { BC2Document, BC2Source } from './bindcraft2StructureInputs';

/** Native preparation stays with Jobs; review the returned immutable request once. */
export async function submitNativeBinderRequest(request: Partial<Job>) {
    const payload = prepareJobSubmission(request, { launchContext: false });
    // Explicit destinations must reach the existing context owner, not the
    // standalone opt-out header. Never derive a destination from a source Job.
    const options = { launchContext: Boolean(payload.launch_context_id) };
    try {
        return payload.execution_target_id && !payload.execution_plan_approval
            ? await api.post('/api/jobs', payload, options.launchContext ? undefined : { headers: { 'X-BMS-Skip-Launch-Context': '1' } })
            : await submitJob(request, options);
    } catch (error) {
        if (!isAxiosError(error) || error.response?.status !== 409) throw error;
        const detail = error.response.data?.detail;
        if (detail?.code !== 'remote_prepared_job_review_required'
            || !detail.job_request?.execution_target_id || detail.job_request.execution_plan_approval) throw error;
        return submitJob(detail.job_request, { launchContext: Boolean(detail.job_request.launch_context_id) });
    }
}
import type { ResidueRef } from '../structureViewer/contracts/structureIdentity';

export type NativeBinderModel = 'ppiflow' | 'boltzgen';
export function nativeBinderDraft(values: Record<string, UntypedApiValue>, jobName: string) {
    const { binder_native_drafts: _drafts, binder_workflow_draft: _workflow, native_job_request: _request, ...settings } = values;
    return { ...settings, job_name: jobName };
}
export interface NativeBinderParameter {
    name: string; type: string; label?: string; description?: string; default?: unknown;
    minimum?: number; maximum?: number; step?: number; enum?: Array<string | number>;
    required?: boolean; nullable?: boolean; aliases?: string[]; group?: string; section?: string; ui_group?: string; ui_control?: string;
    native_cli?: string | Record<string, string>; native_path?: string; ui_placeholder?: string;
}
export const nativeBinderField = (name: string) => name.replace(/^boltzgen_/, '').replace(/^target_pdb_path$/, 'target_pdb');
export const nativeParameterKey = (parameter: NativeBinderParameter, values: Record<string, UntypedApiValue>) =>
    Object.hasOwn(values, parameter.name) ? parameter.name : parameter.aliases?.find(alias => Object.hasOwn(values, alias)) || parameter.name;
export interface NativeGenerationInventory {
    schema_version: string; mode: string; parameters: NativeBinderParameter[];
    profile?: unknown; assets?: unknown; native_behavior?: unknown;
}
export async function fetchNativeGenerationInventory(model: NativeBinderModel, mode: string): Promise<NativeGenerationInventory> {
    const response = await fetch(`/api/models/${model}/generation-settings?mode=${encodeURIComponent(mode)}`);
    if (!response.ok) throw new Error(`Native settings discovery failed (${response.status}).`);
    const inventory = await response.json() as NativeGenerationInventory;
    if (inventory.mode !== mode || !Array.isArray(inventory.parameters)) throw new Error('Native settings discovery returned a different mode or no parameter contract.');
    return inventory;
}
export const nativeBinderSections = (model: NativeBinderModel) => (model === 'ppiflow'
    ? ['Targets & templates', 'Design', 'Generation', 'Expert']
    : ['Sources', 'Binder & protocol', 'Generation', 'Native selection', 'Expert']).map(label => ({ id: label, label }));

/** Presentation grouping only. Applicability, bounds and defaults come from discovery. */
export function nativeBinderSection(model: NativeBinderModel, parameter: NativeBinderParameter): string {
    if (nativeBinderSections(model).some(section => section.id === parameter.section)) return parameter.section!;
    const key = nativeBinderField(parameter.name);
    if (model === 'ppiflow') {
        if (['target_pdb', 'framework_pdb', 'input_csv', 'target_chain', 'antigen_chain', 'heavy_chain', 'light_chain'].includes(key)) return 'Targets & templates';
        if (['specified_hotspots', 'binder_chain', 'samples_min_length', 'samples_max_length', 'sample_hotspot_rate_min', 'sample_hotspot_rate_max', 'cdr_length'].includes(key)) return 'Design';
        if (['samples_per_target', 'num_timesteps', 'dataset_seed'].includes(key)) return 'Generation';
        return 'Expert';
    }
    if (['target_pdb', 'target_pdb_path', 'target_chains', 'target_binding_positions', 'binding_site_residues', 'scaffold_path', 'scaffold_chain', 'scaffold_design_ranges', 'input_pdb', 'ligand_pdb', 'ligand_smiles', 'ntp_type'].includes(key)) return 'Sources';
    if (/^(protocol|binder_sequence|scaffold_length|nanobody_framework|cdr_|secondary_structure)/.test(key)) return 'Binder & protocol';
    if (/^(num_designs|batch_size|seed|diffusion|sampling|step_scale|noise_scale|recycling)/.test(key)) return 'Generation';
    if (/^(rank|filter|budget|alpha|max_rmsd|min_plddt|diversity)/.test(key)) return 'Native selection';
    if (parameter.group === 'selection') return 'Native selection';
    if (parameter.group === 'run') return 'Generation';
    if (parameter.group === 'source') return 'Sources';
    return 'Expert';
}

/** Provenance is a draft reference, not an alternative scientific parameter bag. */
export function portableNativeSource(source: BC2Source): Omit<BC2Source, 'file'> {
    const { file: _file, derivedFrom, ...reference } = source;
    return { ...reference, ...(derivedFrom ? { derivedFrom: portableNativeSource(derivedFrom) } : {}) };
}

/** PPIFlow consumes PDB author tokens. Never turn viewer ordinals into numbers.
 * BoltzGen chain-local indexing needs a producer-qualified native residue map,
 * which the current source parser does not export. Keep that selection as context. */
export function ppiflowHotspotsFromSelection(document: BC2Document, documentId: string, refs: readonly ResidueRef[], chain: string, mode: string): string {
    if (document.format !== 'pdb' || document.models.length !== 1) throw new Error('PPIFlow hotspot mapping requires the exact single-model PDB consumed by the sampler.');
    if (!chain || chain.length !== 1) throw new Error('Choose the exact native target/antigen chain first.');
    const residues = document.models[0].chains.find(item => item.id === chain)?.residues ?? [];
    return refs.map(ref => {
        if (ref.documentId !== documentId || ref.authAsymId !== chain || ref.authSeqId === undefined) throw new Error('The selected residue is not on the declared target/antigen chain and document.');
        const match = residues.find(residue => residue.resNum === ref.authSeqId && (residue.iCode || '') === (ref.insertionCode || ''));
        if (!match) throw new Error('The selected author residue is not present in the consumed PDB.');
        if (mode === 'protein_binder' && (match.iCode || residues.some(residue => residue.resNum === match.resNum && residue.iCode))) throw new Error('Protein PPIFlow integer hotspots cannot distinguish insertion variants. Enter the intended native integer mask explicitly.');
        return `${chain}${match.resNum}${match.iCode || ''}`;
    }).join(',');
}
