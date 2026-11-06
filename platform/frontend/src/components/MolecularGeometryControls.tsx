import { useEffect, useState } from 'react';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { constructionError, geometryApi, schemaEnums } from '../lib/molecularConstruction';
import type { MolecularGeometryDraft } from '../lib/molecularConstruction';

export function MolecularGeometryControls({ geometry, busy, onChange, onConstruct }: {
    geometry?: MolecularGeometryDraft; busy: boolean;
    onChange: (choices: MolecularGeometryDraft['choices']) => void; onConstruct: () => void;
}) {
    const [schema, setSchema] = useState<Schema>();
    const [error, setError] = useState('');
    useEffect(() => { let live = true; geometryApi.schemas().then(s => { if (live) setSchema(s.request); }).catch(e => { if (live) setError(constructionError(e)); }); return () => { live = false; }; }, []);
    const labels: Record<string, string> = { operation: 'Geometry operation', provider: 'Geometry provider', form: 'Helix form', topology: 'Geometry topology', hydrogen_policy: 'Hydrogen policy' };
    return <section aria-label="Optional idealized geometry">
        <h4>Create new idealized geometry · explicit opt-in</h4>
        <p>Canonical antiparallel dsDNA (abdna: B-DNA; adna: A-DNA) or dsRNA (arna: A-RNA) only. Independently author both strands and the complete canonical reverse pairing register above. Each strand needs hydroxyl ends and neutral-acid graph chemistry.</p>
        <p>No ssDNA/ssRNA folding, mismatches, overhangs, modifications, nicks, multiple copies, custom links or DNA–RNA hybrids. No automatic duplex/complement, T/U conversion, protonation, minimization or bound pose. Heavy-atom coordinates retain the separate implicit-hydrogen graph.</p>
        <p>Imported geometry is unchanged. Construction does not adopt a source or launch a model.</p>
        {error && <p role="status">Geometry schema unavailable: {error}. Graph/import/manual paths and model launch are unchanged.</p>}
        {schema && Object.entries(labels).map(([key, label]) => {
            const options = schemaEnums(schema, null, key);
            const value = geometry?.choices[key];
            return <label key={key}>{label}<select aria-label={label} value={String(value ?? '')} onChange={e => onChange({ ...geometry?.choices, [key]: e.target.value })}>
                <option value="">Choose explicitly…</option>
                {value !== undefined && !options.includes(value) && value !== '' && <option value={String(value)}>Retained: {String(value)}</option>}
                {options.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
            </select></label>;
        })}
        <button type="button" disabled={busy} onClick={onConstruct}>Create new idealized geometry</button>
        {geometry?.request && <details><summary>Last exact geometry request (may be historical)</summary><pre>{JSON.stringify(geometry.request, null, 2)}</pre></details>}
        {geometry?.result && <section aria-label="Retained native geometry">
            <p>Native idealized PDB · {geometry.result.coordinate_size_bytes} bytes · SHA256 {geometry.result.coordinate_sha256}. Not adopted as a source.</p>
            <p>{geometry.result.observations.join(' ')}</p>
            <details><summary>Exact PDB, atom map, connected graph and provider provenance</summary><pre>{JSON.stringify(geometry.result, null, 2)}</pre></details>
        </section>}
    </section>;
}
