import { useState } from 'react';
import type { StructureSourceSelection } from '../lib/api';
import type { NativeGenerationPage } from '../lib/nativeBinderResults';
import { continuationHref } from '../lib/deNovoContinuation';

/** State membership is not inferred from filenames or neighboring records. */
export function Rfd4RetainedStateContinuation({ page, source, candidate }: { page: NativeGenerationPage; source: StructureSourceSelection; candidate?: string }) {
    const [selected, setSelected] = useState('');
    const [seed, setSeed] = useState('');
    const states = page.artifacts?.filter(item => item.role === 'sampling_state') ?? [];
    if (!states.length) return null;
    const state = states.find(item => item.artifact_id === selected);
    const href = state?.source_path ? new URL(continuationHref(source, 'rfd4_state'), window.location.origin) : undefined;
    if (href && state) href.searchParams.set('rfd4_state', JSON.stringify({ path: state.source_path, artifact_id: state.artifact_id, sha256: state.sha256, ...(seed.trim() && Number.isSafeInteger(Number(seed)) ? { seed: Number(seed) } : {}) }));
    if (href) {
        const back = new URL(window.location.href);
        if (candidate) back.searchParams.set('native_candidate', candidate);
        if (source.document?.artifact_id) back.searchParams.set('artifact_id', source.document.artifact_id);
        if (source.model_number != null) back.searchParams.set('model_number', String(source.model_number));
        href.searchParams.set('return_to', `${back.pathname}${back.search}${back.hash}`);
    }
    return <section aria-label="Retained RFD4 sampling state" className="my-3 space-y-2">
        <h5>Retained sampling state continuation</h5>
        <p>Choose the native state deliberately. These are publication-level files; association with this molecule is not reported. This opens a new editor, not Jobs resume or interrupted RNG replay.</p>
        <label>Sampling state<select aria-label="RFD4 retained state" value={selected} onChange={event => setSelected(event.target.value)}><option value="">Choose retained state</option>{states.map(state => <option key={state.artifact_id} value={state.artifact_id}>{state.path}</option>)}</select></label>
        <label>Continuation seed<input aria-label="RFD4 continuation seed" type="number" value={seed} onChange={event => setSeed(event.target.value)} /></label>
        {href && <a href={`${href.pathname}${href.search}`}>Continue retained RFD4 state</a>}
    </section>;
}
