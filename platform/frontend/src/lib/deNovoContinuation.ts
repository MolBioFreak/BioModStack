import { materializeExactStructure, type StructureSourceSelection, type StructureMaterialization } from './api';


export type DeNovoDestination = 'redesign' | 'sequence' | 'prediction';
export const sequenceDestinations = [
    ['proteinmpnn', 'design', 'ProteinMPNN design'],
    ['fampnn', 'design', 'FA-MPNN design'],
    ['fampnn', 'fixed_backbone', 'FA-MPNN fixed backbone'],
    ['caliby_experimental', 'ensemble_design', 'Caliby ensemble design'],
] as const;

export function continuationHref(source: StructureSourceSelection, destination: DeNovoDestination): string {
    const query = new URLSearchParams({ source_structure: JSON.stringify(source), continuation: destination });
    query.set('return_to', `${window.location.pathname}${window.location.search}${window.location.hash}`);
    const context = new URLSearchParams(window.location.search);
    for (const key of ['launch_context_id', 'project_id', 'setup_context_id']) {
        const value = context.get(key); if (value) query.set(key, value);
    }
    if (destination === 'redesign') {
        query.set('template', 'protein_modification_experimental');
        query.set('modification_mode', 'rfd3_local_redesign');
    } else if (destination === 'prediction') query.set('template', 'structure_prediction');
    else { query.set('model', 'proteinmpnn'); query.set('mode', 'design'); }
    return `/submit?${query}`;
}

export function nativeSequenceInput(model: string, prepared: StructureMaterialization, native: StructureMaterialization = prepared): Record<string, unknown> {
    return model === 'caliby_experimental'
        ? { ensembles: [{ ensemble_id: 'selected-candidate', states: [{ state_id: 'selected-structure', path: native.path }] }] }
        : prepared.format === 'pdb' ? { input_pdb: prepared.path } : {};
}

// Materialization retains immutable input only; never submits a scientific Job.
export async function prepareDeNovoContinuation(source: StructureSourceSelection, destination: DeNovoDestination) {
    const native = await materializeExactStructure({ ...source, output_format: 'native' });
    const prepared = destination !== 'redesign' || native.format === 'pdb' ? native
        : await materializeExactStructure({ path: native.path, expected_sha256: native.sha256, output_format: 'pdb', ...(native.model_number == null ? {} : { model_number: native.model_number }) });
    const retainedSource = native.source_structure ?? source;
    const common = { source_structure: retainedSource, _source_prepared: prepared, _source_native_prepared: native };
    if (destination === 'sequence') return { ...common, ...nativeSequenceInput('proteinmpnn', prepared) };
    if (destination === 'redesign') return { ...common, input_structure: prepared.path,
        model_number: prepared.model_number ?? source.model_number,
        _redesign_source: { type: 'run', name: prepared.path.split('/').pop(), path: prepared.path,
            url: `/api/files/download/${encodeURIComponent(prepared.path)}`, sourceStructure: retainedSource, prepared },
    };
    // Use the materializer's native author residue inventory, not the display
    // CIF-to-PDB projection (which cannot preserve multi-character author chains).
    const aminoAcids: Record<string, string> = { ALA: 'A', ARG: 'R', ASN: 'N', ASP: 'D', CYS: 'C', GLN: 'Q', GLU: 'E', GLY: 'G', HIS: 'H', ILE: 'I', LEU: 'L', LYS: 'K', MET: 'M', PHE: 'F', PRO: 'P', SER: 'S', THR: 'T', TRP: 'W', TYR: 'Y', VAL: 'V', MSE: 'M', HYP: 'P', PYL: 'O', SEC: 'U', UNK: 'X' };
    const chains = new Map<string, { model_number: number; id: string; sequence: string }>();
    for (const residue of native.author_residues) {
        const letter = aminoAcids[residue.residue_name];
        if (!letter) continue; // Never translate DNA/RNA/ligands into protein.
        const key = JSON.stringify([residue.model_number, residue.auth_asym_id]);
        const chain = chains.get(key) ?? { model_number: residue.model_number, id: residue.auth_asym_id, sequence: '' };
        chain.sequence += letter; chains.set(key, chain);
    }
    return { ...common, _continuation_chains: [...chains.values()], sequence: '' };
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
