import { useEffect, useRef, useState } from 'react';
import {
    bioXpErrorText, useBioXpDeckState, useBioXpTipTrays, useRecordBioXpDeck,
    type BioXpDeckRecordRequestV1, type BioXpDeckRecordResultV1, type BioXpDeckStateV1,
} from '../lib/bioxpClient';

// ClassMachineStatus movable objects (robot OEM_MOVABLE_OBJECT_DEFAULT_LOCATIONS).
export const DECK_RECORD_OBJECTS = [
    ['POOL_PLATE', 'Pool plate'], ['OUTPUT_PLATE', 'Output plate'], ['REAGENT_PLATE', 'Reagent plate'],
    ['OUTPUT_COVER', 'Output cover'], ['REAGENT_COVER', 'Reagent cover'], ['BIO_SECURITY_COVER', 'Biosecurity cover'],
    ['STRIP_ONE', 'Strip 1'], ['STRIP_TWO', 'Strip 2'], ['STRIP_THREE', 'Strip 3'], ['STRIP_FOUR', 'Strip 4'],
    ['TROUGH', 'Trough'],
] as const;

// Native deck locations an object can be recorded at. The robot validates each.
export const DECK_RECORD_LOCATIONS = [
    ['LOC_P_TC', 'Thermal cycler (plate)'], ['LOC_P_OC', 'Output chiller (plate)'], ['LOC_P_MS', 'Magnetic station (plate)'],
    ['LOC_RC', 'Reagent chiller'], ['LOC_OC_COVER', 'Output chiller (cover on)'], ['LOC_OC_COVER_STORAGE', 'Output cover storage'],
    ['LOC_RC_COVER', 'Reagent chiller (cover on)'], ['LOC_RC_COVER_STORAGE', 'Reagent cover storage'],
    ['LOC_BSC', 'Biosecurity cover (on thermal cycler)'], ['LOC_BSCS', 'Biosecurity cover storage'],
    ['LOC_STRIP1', 'Strip slot 1'], ['LOC_STRIP2', 'Strip slot 2'], ['LOC_STRIP3', 'Strip slot 3'], ['LOC_STRIP4', 'Strip slot 4'],
    ['LOC_TROUGH', 'Trough slot'], ['LOC_MS', 'Magnetic station'], ['LOC_OC', 'Output chiller'], ['LOC_TC', 'Thermal cycler'],
    ['LOC_GANTRY', 'Held by gripper'],
] as const;

// OEM ClassMachineStatus plate ordinals reported as plate_on_gantry.
const PLATE_ORDINAL_NAMES: Record<number, string> = {
    0: 'POOL_PLATE', 1: 'OUTPUT_PLATE', 2: 'REAGENT_PLATE', 3: 'BIO_SECURITY_COVER', 4: 'OUTPUT_COVER', 5: 'REAGENT_COVER',
    6: 'TIP_TRAY', 7: 'STRIP_ONE', 8: 'STRIP_TWO', 9: 'STRIP_THREE', 10: 'STRIP_FOUR', 11: 'TROUGH',
};
const TIP_TRAYS = [[1, 'Tip rack 1'], [2, 'Tip rack 2'], [3, 'Tip rack 3'], [4, 'Tip rack 4'], [5, 'Tip hotel']] as const;

const objectLabel = (name: string) => DECK_RECORD_OBJECTS.find(([id]) => id === name)?.[1] ?? name;
const locationLabel = (location: string) => DECK_RECORD_LOCATIONS.find(([id]) => id === location)?.[1] ?? location;

export function gantryHolding(state: BioXpDeckStateV1 | undefined): string | null | undefined {
    const value = state?.semantic?.plate_on_gantry;
    if (value === undefined) return undefined;
    if (value === null) return null;
    return PLATE_ORDINAL_NAMES[value] ?? `Object ${value}`;
}

/** Build the record-only body from edits; unchanged rows are omitted. */
export function deckRecordRequest(generation: number, edits: {
    objects: Record<string, string>; gantry: string; trays: Record<number, '' | 'full' | 'empty'>; reason: string;
}, current?: BioXpDeckStateV1): BioXpDeckRecordRequestV1 {
    const movable = Object.fromEntries(Object.entries(edits.objects)
        .filter(([name, location]) => location && location !== current?.movable_objects?.[name]?.location));
    const trays = Object.entries(edits.trays).filter(([, tips]) => tips)
        .map(([tray, tips]) => ({ tray: Number(tray), tips: tips as 'full' | 'empty' }));
    const request: BioXpDeckRecordRequestV1 = { expected_connection_generation: generation };
    if (Object.keys(movable).length) request.movable_objects = movable;
    if (trays.length) request.tip_trays = trays;
    if (edits.gantry) request.gantry = edits.gantry;
    if (edits.reason.trim()) request.reason = edits.reason.trim().slice(0, 500);
    return request;
}

/** Live deck card: where the robot records each plate and cover. Records, not observations. */
export function BioXpDeckObjectsCard({ generation, connected, visible }: { generation: number; connected: boolean; visible: boolean }) {
    const state = useBioXpDeckState(generation, connected && visible);
    const data = state.data;
    const holding = gantryHolding(data);
    return <section className="bld-pose-card" aria-label="Plates and covers" data-testid="deck-objects-card">
        <h3>Plates and covers</h3>
        {!connected ? <p>Connect to read the deck record.</p>
            : state.isLoading ? <p role="status">Loading deck record…</p>
                : state.isError && !data ? <p role="alert">Deck record unavailable: {bioXpErrorText(state.error)}</p>
                    : <>
                        <dl data-testid="deck-objects">
                            {DECK_RECORD_OBJECTS.map(([name, label]) => {
                                const row = data?.movable_objects?.[name];
                                return <div key={name} data-object={name} data-location={row?.location ?? ''}>
                                    <dt>{label}</dt>
                                    <dd>{row ? locationLabel(row.location) : 'Not recorded'}{row?.source === 'provider_machine_status' ? ' · default' : ''}</dd>
                                </div>;
                            })}
                            <div data-object="gantry"><dt>Gripper holds</dt><dd>{holding === undefined ? 'Unavailable' : holding === null ? 'Nothing' : objectLabel(holding)}</dd></div>
                            <div><dt>Tip loaded</dt><dd>{data?.semantic?.tip_loaded == null ? 'Unknown' : data.semantic.tip_loaded ? 'Yes' : 'No'}</dd></div>
                        </dl>
                        {state.isError && <p role="status">Refresh failed; showing the last deck record.</p>}
                        <p className="bld-note">The robot's record, not a camera observation. Correct it under Deck record.</p>
                    </>}
    </section>;
}

/** Record-only correction of the robot's deck record. Moves nothing. */
export function BioXpDeckRecordPanel({ generation, connected, visible }: { generation: number; connected: boolean; visible: boolean }) {
    const state = useBioXpDeckState(generation, connected && visible);
    const tips = useBioXpTipTrays(generation, connected && visible);
    const record = useRecordBioXpDeck();
    const [objects, setObjects] = useState<Record<string, string>>({});
    const [gantry, setGantry] = useState('');
    const [trays, setTrays] = useState<Record<number, '' | 'full' | 'empty'>>({});
    const [reason, setReason] = useState('');
    const [result, setResult] = useState<BioXpDeckRecordResultV1 | null>(null);
    const [error, setError] = useState<string | null>(null);
    const sending = useRef(false);
    useEffect(() => { setObjects({}); setGantry(''); setTrays({}); setResult(null); setError(null); }, [generation]);
    const request = deckRecordRequest(generation, { objects, gantry, trays, reason }, state.data);
    const changes = Object.keys(request.movable_objects ?? {}).length + (request.tip_trays?.length ?? 0) + (request.gantry ? 1 : 0);
    const disabled = !connected || generation <= 0 || record.isPending || changes === 0;

    async function submit() {
        if (disabled || sending.current) return;
        sending.current = true; setError(null); setResult(null);
        try {
            const outcome = await record.mutateAsync(request);
            setResult(outcome);
            setObjects({}); setGantry(''); setTrays({});
        } catch (cause) {
            setError(bioXpErrorText(cause));
        } finally { sending.current = false; }
    }

    const holding = gantryHolding(state.data);
    return <section aria-label="Deck record" className="space-y-3" data-testid="deck-record-panel">
        <p className="text-sm">Tell the robot where plates, covers and tips actually are. This only updates the record; nothing moves.</p>
        {state.isError && <p role="alert">Deck record unavailable: {bioXpErrorText(state.error)}</p>}
        <fieldset className="grid gap-2 sm:grid-cols-2"><legend className="font-semibold">Plates and covers</legend>
            {DECK_RECORD_OBJECTS.map(([name, label]) => {
                const current = state.data?.movable_objects?.[name]?.location;
                return <label key={name} className="text-sm">{label}
                    <select className="block w-full bg-slate-950 p-2" aria-label={`${label} location`} value={objects[name] ?? ''}
                        disabled={record.isPending} onChange={event => setObjects(previous => ({ ...previous, [name]: event.target.value }))}>
                        <option value="">{current ? `Unchanged · ${locationLabel(current)}` : 'Unchanged'}</option>
                        {DECK_RECORD_LOCATIONS.map(([id, text]) => <option key={id} value={id}>{text}</option>)}
                    </select></label>;
            })}
        </fieldset>
        <label className="block text-sm">Gripper holds
            <select className="block w-full bg-slate-950 p-2" aria-label="Gripper holds" value={gantry} disabled={record.isPending} onChange={event => setGantry(event.target.value)}>
                <option value="">{holding === undefined ? 'Unchanged' : `Unchanged · ${holding === null ? 'nothing' : objectLabel(holding)}`}</option>
                <option value="empty">Nothing</option>
                {DECK_RECORD_OBJECTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
        <fieldset className="grid gap-2 sm:grid-cols-2"><legend className="font-semibold">Tip racks</legend>
            {TIP_TRAYS.map(([tray, label]) => {
                const row = tips.data?.trays?.find(item => item.tray === tray);
                const present = row?.unavailable ? 'unavailable' : row?.tips_present != null ? `${row.tips_present}/96 tips` : 'unknown';
                return <label key={tray} className="text-sm">{label} <span className="text-slate-400">({present})</span>
                    <select className="block w-full bg-slate-950 p-2" aria-label={`${label} tips`} value={trays[tray] ?? ''} disabled={record.isPending}
                        onChange={event => setTrays(previous => ({ ...previous, [tray]: event.target.value as '' | 'full' | 'empty' }))}>
                        <option value="">Unchanged</option><option value="full">Full</option><option value="empty">Empty</option>
                    </select></label>;
            })}
        </fieldset>
        <label className="block text-sm">Note (optional)
            <input className="block w-full bg-slate-950 p-2" aria-label="Deck record note" maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button type="button" className="rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={disabled} onClick={() => void submit()}>
            {record.isPending ? 'Recording…' : changes ? `Record ${changes} change${changes === 1 ? '' : 's'}` : 'Record changes'}</button>
        {result && <p role="status">Recorded ({result.record_id}). No motion was commanded.</p>}
        {result?.warnings?.map(warning => <p key={warning.code} role="alert">{warning.message ?? warning.code}{warning.objects?.length ? ` · ${warning.objects.map(objectLabel).join(', ')}` : ''}</p>)}
        {error && <p role="alert">{error}</p>}
    </section>;
}
