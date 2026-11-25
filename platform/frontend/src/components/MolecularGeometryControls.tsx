import { useEffect, useState } from 'react';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { constructionError, geometryApi, schemaEnums } from '../lib/molecularConstruction';
import type { MolecularGeometryDraft } from '../lib/molecularConstruction';
import MolstarViewer from './MolstarViewer';

const selectClass = 'w-full min-w-0 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] focus:border-[var(--accent-primary)] focus:outline-none';
const btnPrimary = 'inline-flex items-center justify-center rounded-lg bg-sky-600 px-3.5 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-45';
const btnBase = 'inline-flex items-center justify-center rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2.5 py-1 text-xs font-medium text-[var(--text-primary)] transition hover:bg-[var(--bg-primary)]';
const disclosureClass = 'rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 px-3 py-2 text-xs';
const isBrowserRuntime = () => typeof window !== 'undefined' && typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function' && !/jsdom/i.test(navigator.userAgent ?? '');

export function MolecularGeometryControls({ geometry, busy, onChange, onConstruct }: {
    geometry?: MolecularGeometryDraft; busy: boolean;
    onChange: (choices: MolecularGeometryDraft['choices']) => void; onConstruct: () => void;
}) {
    const [schema, setSchema] = useState<Schema>();
    const [error, setError] = useState('');
    const [showPreview, setShowPreview] = useState(() => isBrowserRuntime());
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);
    useEffect(() => { let live = true; geometryApi.schemas().then(s => { if (live) setSchema(s.request); }).catch(e => { if (live) setError(constructionError(e)); }); return () => { live = false; }; }, []);
    useEffect(() => {
        const text = geometry?.result?.coordinate_text;
        if (!text || !isBrowserRuntime()) { setPdbBlobUrl(null); return; }
        const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
        setPdbBlobUrl(url);
        return () => URL.revokeObjectURL(url);
    }, [geometry?.result?.coordinate_text]);
    const downloadPdb = () => {
        const text = geometry?.result?.coordinate_text;
        if (!text || !isBrowserRuntime()) return;
        const url = URL.createObjectURL(new Blob([text], { type: 'chemical/x-pdb' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = `idealized-${String(geometry?.result?.request?.form ?? 'nucleic')}.pdb`;
        link.click();
        URL.revokeObjectURL(url);
    };
    const labels: Record<string, string> = { operation: 'Geometry operation', provider: 'Geometry provider', form: 'Helix form', topology: 'Geometry topology', hydrogen_policy: 'Hydrogen policy' };
    return <section aria-label="Optional idealized geometry" className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/85 p-3.5">
        <div className="border-b border-[var(--border-primary)]/70 pb-2">
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">Create new idealized geometry · explicit opt-in</h4>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">Canonical antiparallel dsDNA (abdna: B-DNA; adna: A-DNA) or dsRNA (arna: A-RNA) only. Independently author both strands and the complete canonical reverse pairing register above. Each strand needs hydroxyl ends and neutral-acid graph chemistry.</p>
        </div>
        <div className="rounded-lg border border-[var(--border-primary)]/70 bg-[var(--bg-primary)]/50 px-3 py-2 text-[11px] text-[var(--text-secondary)] space-y-1">
            <p>No ssDNA/ssRNA folding, mismatches, overhangs, modifications, nicks, multiple copies, custom links or DNA–RNA hybrids. No automatic duplex/complement, T/U conversion, protonation, minimization or bound pose. Heavy-atom coordinates retain the separate implicit-hydrogen graph.</p>
            <p>Imported geometry is unchanged. Construction does not adopt a source or launch a model.</p>
        </div>
        {error && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">Geometry schema unavailable: {error}. Graph/import/manual paths and model launch are unchanged.</p>}
        {schema && <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(labels).map(([key, label]) => {
                const options = schemaEnums(schema, null, key);
                const value = geometry?.choices[key];
                return <label key={key} className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)]">{label}<select className={selectClass} aria-label={label} value={String(value ?? '')} onChange={e => onChange({ ...geometry?.choices, [key]: e.target.value })}>
                    <option value="">Choose explicitly…</option>
                    {value !== undefined && !options.includes(value) && value !== '' && <option value={String(value)}>Retained: {String(value)}</option>}
                    {options.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
                </select></label>;
            })}
        </div>}
        <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={btnPrimary} disabled={busy} onClick={onConstruct}>Create new idealized geometry</button>
        </div>
        {geometry?.request && <details className={disclosureClass}><summary className="cursor-pointer select-none font-medium text-[var(--text-primary)]">Last exact geometry request (may be historical)</summary><pre className="mt-2 max-h-60 overflow-auto rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 font-mono text-[11px] text-[var(--text-secondary)]">{JSON.stringify(geometry.request, null, 2)}</pre></details>}
        {geometry?.result && <section aria-label="Retained native geometry" className="space-y-3 rounded-xl border border-sky-500/35 bg-[var(--bg-primary)]/50 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-primary)]/60 pb-2">
                <div className="space-y-0.5">
                    <h5 className="text-xs font-semibold uppercase tracking-wide text-sky-300">Retained native geometry · {String(geometry.result.request?.form ?? geometry.result.format)}</h5>
                    <p className="text-xs text-[var(--text-secondary)]">Native idealized PDB · {geometry.result.coordinate_size_bytes} bytes · SHA256 {geometry.result.coordinate_sha256}. Not adopted as a source.</p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="rounded-full border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-primary)]">{geometry.result.atom_map.length} atoms</span>
                    {pdbBlobUrl && <button type="button" className={btnBase} onClick={() => setShowPreview(prev => !prev)}>{showPreview ? 'Hide 3D preview' : 'Show 3D preview'}</button>}
                    {pdbBlobUrl && <button type="button" className={btnBase} onClick={downloadPdb}>Download PDB</button>}
                </div>
            </div>
            {pdbBlobUrl && showPreview && <div className="overflow-hidden rounded-lg border border-[var(--border-primary)] bg-slate-950">
                <MolstarViewer structureUrl={pdbBlobUrl} format="pdb" height={340} />
            </div>}
            {geometry.result.observations.length > 0 && <p className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2.5 py-2 text-xs text-[var(--text-secondary)]">{geometry.result.observations.join(' ')}</p>}
            <details className={disclosureClass}><summary className="cursor-pointer select-none font-medium text-[var(--text-primary)]">Exact PDB, atom map, connected graph and provider provenance</summary><pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2.5 font-mono text-[11px] text-[var(--text-secondary)]">{JSON.stringify(geometry.result, null, 2)}</pre></details>
        </section>}
    </section>;
}
