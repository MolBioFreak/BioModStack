import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchCalibyNativeResults, fetchLigandMPNNDesignResults } from '../lib/api';
import type { Job } from '../lib/api';
import MolstarViewer from './MolstarViewer';

// These are separate native documents, not a common scientific score/Design model.
export type NativeValue = string | number | boolean | null | NativeValue[] | { [key: string]: NativeValue };
export interface NativeFile { path: string; kind?: string; sha256?: string; artifact_id?: string; download_url?: string | null; stream_url?: string | null }
export interface NativeArtifact extends Omit<NativeFile, 'path'> { relative_path: string; path?: string }
interface CalibyState { state_id: string; path: string; fixed_pos_seq?: string; fixed_pos_scn?: string; fixed_pos_override_seq?: string; pos_restrict_aatype?: string; symmetry_pos?: string }
interface CalibyCommon { schema_version: 1; batch_size: number; num_workers: number; scn_num_steps: number; scn_step_scale: number }
export type CalibySettings = CalibyCommon & ({
    task: 'ensemble_design'; ensembles: { ensemble_id: string; states: CalibyState[] }[];
    model_name: string; num_seqs_per_pdb: number; temperature: number; omit_aas: string[];
    verbose: boolean; use_primary_res_type: boolean; ensemble_ignore_res_idx_mismatch: boolean;
    gaussian_n_conformers: number; gaussian_noise_std: number; potts_regularization: string;
    potts_sweeps: number; potts_proposal: string; potts_rejection_step: boolean; potts_only_cond: boolean;
} | { task: 'sidechain_pack'; structures: CalibyState[]; packer_model_name: string });
export interface CalibyNativeResults {
    schema: 'bms.caliby-native-results.v1'; task: 'ensemble_design' | 'sidechain_pack';
    request: { requested: CalibySettings; effective: CalibySettings; sources: NativeValue[] };
    effective_sampling: Record<string, NativeValue>; runtime: Record<string, NativeValue>;
    records: { record_id: string; operation: 'ensemble_design' | 'sidechain_pack';
        native: { example_id: string; out_pdb: string; seq?: string | null; input_seq?: string | null; U?: number | null; [key: string]: NativeValue | undefined };
        source: { state_id: string; ensemble_id?: string; primary?: boolean; conditioning_states?: CalibyState[] } | null;
        structure_path: string; structure?: NativeFile;
    }[];
    artifacts?: NativeArtifact[];
}
export interface LigandMPNNOptions {
    seed?: number | null; batch_size?: number; number_of_batches?: number;
    temperature?: number | null; designed_chains?: string[] | null; fixed_chains?: string[] | null;
    designed_residues?: string[] | null; fixed_residues?: string[] | null;
    remove_ccds?: string[] | null; remove_waters?: boolean | null;
    [key: string]: NativeValue | undefined;
}
export interface LigandMPNNDesignResults {
    contract: 'ligandmpnn_design.v1'; model_type: 'ligand_mpnn'; mode: 'ligand_aware' | 'ntp_aware' | 'metal_aware' | 'dna_aware';
    request: { contract: 'ligandmpnn_design.v1'; model_type: 'ligand_mpnn'; mode: string; options: LigandMPNNOptions;
        annotations: Record<string, NativeValue>; write_fasta: boolean; write_structures: boolean };
    foundry_version: string; checkpoint_path: string; is_legacy_weights: boolean; source_sha256: string;
    records: { producer: { name: string; batch_idx: number; design_idx: number }; source_sha256: string;
        native_input: LigandMPNNOptions & { name: string; structure_path: string };
        native_output: { model_type: 'ligand_mpnn'; batch_idx: number; design_idx: number;
            designed_sequence?: string | null; sequence_recovery?: number | null; ligand_interface_sequence_recovery?: number | null;
            [key: string]: NativeValue | undefined };
        artifacts: NativeFile[];
    }[];
    artifacts?: NativeArtifact[];
}

export function nativeSequenceResultKind(job?: Pick<Job, 'model_id' | 'mode'> | null): 'caliby' | 'ligandmpnn' | null {
    if (job?.model_id === 'caliby_experimental' && ['ensemble_design', 'sidechain_pack'].includes(job.mode)) return 'caliby';
    if (job?.model_id === 'ligandmpnn' && ['ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'].includes(job.mode)) return 'ligandmpnn';
    return null;
}

// Read-only typed rendering retains null, false, zero, empty lists and nested native keys.
function Values({ value }: { value: unknown }) {
    if (value == null) return <span className="text-slate-400">Not reported</span>;
    if (Array.isArray(value)) return value.length ? <ol className="space-y-2 pl-4">{value.map((item, i) => <li key={i}><Values value={item} /></li>)}</ol> : <span>Empty list</span>;
    if (typeof value === 'object') return <dl className="grid gap-2">{Object.entries(value).map(([key, item]) => <div key={key} className="grid gap-1 sm:grid-cols-[minmax(12rem,1fr)_2fr]"><dt className="font-mono text-xs text-slate-400">{key}</dt><dd className="min-w-0 break-words"><Values value={item} /></dd></div>)}</dl>;
    return <span className="whitespace-pre-wrap break-all">{String(value) || '(empty)'}</span>;
}
function Receipt({ title, value }: { title: string; value: unknown }) {
    return <details className="rounded border border-slate-700 p-3"><summary className="cursor-pointer font-medium">{title}</summary><div className="mt-3 text-sm"><Values value={value} /></div></details>;
}
function Files({ files, onView }: { files: NativeFile[]; onView: (file: NativeFile) => void }) {
    return <ul className="space-y-2">{files.map(file => <li key={file.path} className="flex flex-wrap items-center gap-3 text-sm">
        <span className="break-all">{file.path}</span>
        {file.download_url && <a className="text-cyan-300 underline" href={file.download_url} download>Download {file.path}</a>}
        {file.download_url && /\.(cif|mmcif)$/i.test(file.path) && <button type="button" className="rounded border border-slate-600 px-3 py-1" onClick={() => onView(file)}>View CIF {file.path}</button>}
    </li>)}</ul>;
}

export default function NativeSequenceResults({ job }: { job: Pick<Job, 'id' | 'model_id' | 'mode' | 'status'> }) {
    const kind = nativeSequenceResultKind(job);
    const [viewed, setViewed] = useState<NativeFile | null>(null);
    const [page, setPage] = useState(0);
    const query = useQuery<CalibyNativeResults | LigandMPNNDesignResults>({
        queryKey: ['native-sequence-results', kind, job.id],
        queryFn: () => kind === 'caliby' ? fetchCalibyNativeResults(job.id) : fetchLigandMPNNDesignResults(job.id),
        enabled: kind !== null, retry: false,
        refetchInterval: job.status === 'running' || job.status === 'queued' ? 2000 : false,
    });
    if (!kind) return null;
    const data = query.data;
    const caliby = data && !('contract' in data) ? data : undefined;
    const ligand = data && 'contract' in data ? data : undefined;
    const count = data?.records.length ?? 0;
    const directory = kind === 'caliby' ? 'caliby_native' : 'ligandmpnn_design';
    const resolveFile = (file: NativeFile): NativeFile => {
        // Exact native-relative or explicitly Job-relative identity, never a basename join.
        const handle = data?.artifacts?.find(item => item.relative_path === file.path
            || item.relative_path === `${directory}/${file.path}`);
        return handle ? { ...file, ...handle, path: file.path } : file;
    };
    return <section aria-label="Native sequence results" className="space-y-4 rounded-xl border border-slate-700 bg-slate-900/50 p-5 text-slate-200">
        <h2 className="text-xl font-semibold">{kind === 'caliby' ? job.mode === 'sidechain_pack' ? 'Caliby fixed-sequence packing' : 'Caliby ensemble design' : 'LigandMPNN sequence design'}</h2>
        <p className="text-sm text-slate-400">{kind === 'caliby' ? job.mode === 'sidechain_pack' ? 'Native side-chain packing preserves amino-acid identity; sequence-design energies are not packing outputs.' : 'Native ensemble records retain their emitted primary/group/state identity. Conditioning states are not additional generated structures.' : 'Ordinary native sequence design with supplied structural context, separate from interface-context diagnostics.'}</p>
        {query.isPending && <p role="status">Loading native results…</p>}
        {query.isError && <div role="alert">Native results could not be loaded: {query.error instanceof Error ? query.error.message : String(query.error)} <button type="button" onClick={() => void query.refetch()}>Retry native results</button></div>}
        {data && <>
            <p>{count.toLocaleString()} native records</p>
            {count === 0 && <p>No native records were emitted. Settings and retained files remain available.</p>}
            {caliby && <div className="space-y-2">
                <Receipt title="Requested Caliby settings" value={caliby.request.requested} />
                <Receipt title="Effective Caliby settings" value={caliby.request.effective} />
                <Receipt title="Effective native sampling" value={caliby.effective_sampling} />
                <Receipt title="Source state ledger" value={caliby.request.sources} />
                <Receipt title="Caliby runtime" value={caliby.runtime} />
            </div>}
            {ligand && <div className="space-y-2">
                <Receipt title="Requested LigandMPNN settings" value={ligand.request} />
                <Receipt title="LigandMPNN runtime and source" value={{ foundry_version: ligand.foundry_version, checkpoint_path: ligand.checkpoint_path, is_legacy_weights: ligand.is_legacy_weights, source_sha256: ligand.source_sha256 }} />
                {!ligand.request.write_structures && <p>Structure writing was off. Native sequence records remain available.</p>}
            </div>}
            <Files files={(data.artifacts ?? []).map(file => ({ ...file, path: file.relative_path }))} onView={setViewed} />
            {caliby?.records.slice(page * 10, page * 10 + 10).map(row => <article key={row.record_id} className="space-y-3 rounded border border-slate-700 p-4">
                <h3 className="font-semibold">Record {row.record_id} · {row.native.example_id}</h3>
                <Values value={row.source} />
                <h4 className="font-medium">Native {row.operation === 'sidechain_pack' ? 'packing output' : 'sequence and measurements'}</h4>
                <Values value={row.native} />
                <Files files={[resolveFile(row.structure ?? { path: row.structure_path, kind: 'structure' })]} onView={setViewed} />
            </article>)}
            {ligand?.records.slice(page * 10, page * 10 + 10).map(row => <article key={JSON.stringify(row.producer)} className="space-y-3 rounded border border-slate-700 p-4">
                <h3 className="font-semibold">{row.producer.name} · batch {row.producer.batch_idx} · design {row.producer.design_idx}</h3>
                <h4 className="font-medium">Native sequence and measurements</h4><Values value={row.native_output} />
                <Receipt title="Effective native input settings" value={row.native_input} />
                <Receipt title="Source SHA256" value={row.source_sha256} />
                <Files files={row.artifacts.map(resolveFile)} onView={setViewed} />
            </article>)}
            {count > 10 && <nav aria-label="Native records pages" className="flex gap-4"><button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} of {Math.ceil(count / 10)}</span><button type="button" disabled={(page + 1) * 10 >= count} onClick={() => setPage(page + 1)}>Next</button></nav>}
        </>}
        {viewed?.download_url && <MolstarViewer structureUrl={viewed.stream_url ?? viewed.download_url} format="cif" label={viewed.path} artifactJobId={job.id} height={560} />}
    </section>;
}
