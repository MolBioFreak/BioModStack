import { useQuery } from '@tanstack/react-query';

type Scalar = string | number | boolean | null;
type Setting = {key: string; scope: string; requested: Scalar; effective: Scalar;
    origin: 'request' | 'workflow_default' | 'runner_default' | 'compute_tier'};
type Receipt = {model: 'esmfold2' | 'openmm'; artifact_sha256: string; settings: Setting[];
    sources: {scope: string; sha256: string; size_bytes: number | null}[]};
type Projection = {status: 'ok' | 'unavailable'; reason: string | null; receipts: Receipt[]};
const keys = new Set(['seed','model_variant','local_files_only','num_loops','num_sampling_steps',
    'num_diffusion_samples','msa_format','msa_max_sequences','msa_remove_insertions',
    'pdb_include_dna_rna','chain_id','cdr_only','force_field','max_iterations',
    'energy_tolerance','restraint_mode','antibody_chain','fix_structure']);
const origins = {request:'Request',workflow_default:'Workflow default',runner_default:'Runner default',compute_tier:'Compute tier'};
const scalar = (v: unknown): v is Scalar => v === null || typeof v === 'string' || typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v));
const digest = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
export function parseExecutionSettings(value: unknown): Projection {
    const v = value as Projection;
    if (!v || !['ok','unavailable'].includes(v.status) || !Array.isArray(v.receipts)) throw Error('Invalid execution receipt');
    if (v.status === 'unavailable' && v.receipts.length) throw Error('Invalid unavailable receipt');
    for (const r of v.receipts) {
        if (!['esmfold2','openmm'].includes(r.model) || !digest(r.artifact_sha256) || !Array.isArray(r.settings) || !Array.isArray(r.sources)) throw Error('Invalid execution receipt');
        for (const s of r.settings) {
            if (!keys.has(s.key) || typeof s.scope !== 'string' || !Object.hasOwn(origins,s.origin) || !scalar(s.requested) || !scalar(s.effective)) throw Error('Invalid execution setting');
        }
        for (const s of r.sources) if (typeof s.scope !== 'string' || !digest(s.sha256)) throw Error('Invalid source identity');
    }
    return v;
}
const display = (v: Scalar) => v === null ? 'Not supplied' : String(v);
export function ExecutionSettingsPanel({jobId}: {jobId: string}) {
    const query = useQuery({queryKey:['execution-settings',jobId], queryFn:async () => {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/execution-settings`);
        if (!response.ok) throw Error('Execution settings unavailable');
        return parseExecutionSettings(await response.json());
    }});
    if (query.isPending) return <p>Loading execution settings…</p>;
    if (query.isError || query.data.status === 'unavailable') return <p>Execution settings unavailable</p>;
    return <section className="my-3 text-xs text-slate-300">
        <h3>Effective execution settings</h3>
        {query.data.receipts.map((receipt, i) => <div key={`${receipt.artifact_sha256}:${i}`}>
            <p>{receipt.model} · Receipt SHA-256: <code>{receipt.artifact_sha256}</code></p>
            <table aria-label="Effective execution settings"><thead><tr><th>Setting</th><th>Scope</th><th>Requested</th><th>Effective</th><th>Origin</th></tr></thead>
                <tbody>{receipt.settings.map(s => <tr key={`${s.scope}:${s.key}`} data-setting-key={s.key}>
                    <td>{s.key}</td><td>{s.scope}</td><td>{display(s.requested)}</td><td>{display(s.effective)}</td><td>{origins[s.origin]}</td>
                </tr>)}</tbody></table>
            {receipt.sources.map((s,j) => <p key={`${s.scope}:${j}`}>Input identity ({s.scope}): <code>{s.sha256}</code>{s.size_bytes === null ? '' : ` · ${s.size_bytes} bytes`}</p>)}
        </div>)}
    </section>;
}
