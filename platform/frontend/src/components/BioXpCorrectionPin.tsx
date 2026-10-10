import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { bioXpErrorText } from '../lib/bioxpClient';
import { methodsGet, methodsSave, methodRows, type MethodRecord, type MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';
import { sameDraftValue } from '../lib/bioxpWorkflowDraft';
import { PipetteValue } from './BioXpPipetteResults';

/** Explicit immutable library copy, never a device/settings write or fit calculation. */
export function BioXpCorrectionPin({ analysis }: { analysis: MethodValue }) {
    const [open, setOpen] = useState(false), [name, setName] = useState('');
    const [calibrationId, setCalibrationId] = useState(''), [revision, setRevision] = useState('');
    const [baseId, setBaseId] = useState(''), [offset, setOffset] = useState(0);
    const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState<MethodRecord | null>(null);
    const lock = useRef(false), accepted = useRef<MethodRecord | null>(null);
    const classes = useQuery({ queryKey: ['bioxp', 'methods', 'pin-classes', offset], queryFn: () => methodsGet<unknown>('liquid-classes', { offset, limit: 25 }), enabled: open, retry: false });
    async function pin() {
        if (lock.current) return;
        lock.current = true; setBusy(true); setError(null);
        try {
            // A failed exact GET retries the accepted ID, never creates a second copy.
            if (!accepted.current) {
                const base = baseId ? await methodsGet<MethodRecord>(`liquid-classes/${encodeURIComponent(baseId)}`) : null;
                const selected = { calibration_id: calibrationId, revision, context: structuredClone(object(object(analysis.requested).context)), function: structuredClone(analysis.correction) };
                const method: MethodValue = { ...structuredClone(base?.method ?? {}), schema: 'bms.bioxp-liquid-class.v1', name, id: calibrationId,
                    context: structuredClone(base?.method.context ?? object(analysis.requested).context ?? {}),
                    authored_settings: { ...object(base?.method.authored_settings), selected_correction: selected },
                    calibration_analysis: structuredClone(analysis),
                    ...(base ? { calibration_source_class: { id: base.id, revision: base.revision } } : {}),
                };
                // Explicit replacement affects this new copy only; retain all other settings.
                if (object(method.settings).selected_correction !== undefined) method.settings = { ...object(method.settings), selected_correction: selected };
                accepted.current = await methodsSave('liquid-classes', method, null);
            }
            const receipt = accepted.current;
            const exact = await methodsGet<MethodRecord>(`liquid-classes/${encodeURIComponent(receipt.id)}/revisions/${receipt.revision}`);
            if (!sameDraftValue(exact.method, receipt.method)) throw new Error('Pinned revision readback differs; accepted identity retained.');
            setSaved(exact);
        } catch (e) { setError(bioXpErrorText(e)); }
        finally { lock.current = false; setBusy(false); }
    }
    if (analysis.kind !== 'pipette' || !analysis.correction) return null;
    return <section aria-label="Explicit correction selection" className="min-w-0 space-y-2">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>Select correction for a liquid-class copy</button>
        <div hidden={!open}><p>Pin this returned function and exact measurement provenance into the existing liquid-class library. This does not alter the source class, apply robot calibration, or change any workflow. Select the pinned class explicitly in Workflows.</p>
            <fieldset disabled={busy || !!accepted.current}>
                <label>Copy settings from<select className="w-full" aria-label="Correction base class" value={baseId} onChange={e => setBaseId(e.target.value)}><option value="">New class (no inherited settings)</option>{methodRows<MethodRecord>(classes.data).map(row => <option key={row.id} value={row.id}>{row.name} · revision {row.revision}</option>)}</select></label>
                <button type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous classes</button><button type="button" disabled={methodRows(classes.data).length < 25} onClick={() => setOffset(offset + 25)}>Next classes</button>
                <label>New class name<input className="w-full" aria-label="Pinned class name" value={name} onChange={e => setName(e.target.value)} /></label>
                <label>Calibration ID<input className="w-full" aria-label="Correction calibration ID" value={calibrationId} onChange={e => setCalibrationId(e.target.value)} /></label>
                <label>Calibration revision<input className="w-full" aria-label="Correction calibration revision" value={revision} onChange={e => setRevision(e.target.value)} /></label>
                <p>Exact analysis context</p><PipetteValue value={object(analysis.requested).context} />
            </fieldset>
            <button type="button" disabled={busy || !!saved || !name || !calibrationId || !revision} onClick={() => void pin()}>{accepted.current ? 'Read accepted pinned revision' : 'Pin correction as new liquid class'}</button>
        </div>
        {(error || classes.error) && <p role="alert">{error ?? bioXpErrorText(classes.error)}</p>}
        {busy && <p role="status">Saving / reading exact correction revision…</p>}
        {saved && <p role="status">Pinned class {saved.name}: {saved.id} · revision {saved.revision}. Selected in this copy; not emitted, applied, read back from hardware or externally verified.</p>}
    </section>;
}
