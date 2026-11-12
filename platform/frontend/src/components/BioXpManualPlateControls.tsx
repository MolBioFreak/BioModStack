import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { methodsGet, type MethodCatalog } from '../lib/bioxpMethods';
import { custodyMetadata } from './BioXpMethodCustodyEditor';

export type BioXpManualPlateIntent = { action_id: 'oem.deck.catch_plate'; inputs: { plate: number } }
    | { action_id: 'oem.deck.release_plate'; inputs: { destination: number; press_plate: false } };

/** Pickers only: the cockpit retains canonical admission, identity and receipt ownership. */
export function BioXpManualPlateControls({ visible, disabledReason, onSubmit }: {
    visible: boolean;
    disabledReason: (action: BioXpManualPlateIntent['action_id']) => string | null;
    onSubmit: (intent: BioXpManualPlateIntent) => void;
}) {
    const [object, setObject] = useState('');
    const [destination, setDestination] = useState('');
    // Share the existing transfer catalog cache; documentary choices are not admission gates.
    const catalog = useQuery({ queryKey: ['bioxp', 'methods', 'catalog'], queryFn: () => methodsGet<MethodCatalog>('catalog'),
        enabled: visible, retry: false, staleTime: Infinity });
    const metadata = catalog.data ? custodyMetadata(catalog.data) : { objects: [], destinations: [] };
    const objects = metadata.objects.filter(row => typeof row.token === 'string' && typeof row.label === 'string' && Number.isSafeInteger(row.ordinal));
    const destinations = metadata.destinations.filter(row => typeof row.token === 'string' && typeof row.label === 'string' && Number.isSafeInteger(row.plate_destination));
    const selectedObject = objects.find(row => row.token === object);
    const selectedDestination = destinations.find(row => row.token === destination);
    const pickupReason = disabledReason('oem.deck.catch_plate');
    const placeReason = disabledReason('oem.deck.release_plate');
    return <section aria-label="Manual plate handling" className="space-y-2">
        <h3>Pick up &amp; hold / Place held plate</h3>
        <p className="text-sm">Physical plate handling, not head-only travel. Pick up &amp; hold stops with the selected object held; Place releases the held plate without pressing. The robot owns clearance and door prerequisites.</p>
        <div className="grid gap-3 sm:grid-cols-2">
            <label>Object to pick up<select aria-label="Object to pick up" className="block w-full bg-slate-950 p-2" value={object} onChange={event => setObject(event.target.value)}>
                <option value="">Select object</option>{objects.map(row => <option key={String(row.token)} value={String(row.token)}>{String(row.label)}</option>)}
            </select></label>
            <label>Place destination<select aria-label="Place destination" className="block w-full bg-slate-950 p-2" value={destination} onChange={event => setDestination(event.target.value)}>
                <option value="">Select destination</option>{destinations.map(row => <option key={String(row.token)} value={String(row.token)}>{String(row.label)}</option>)}
            </select></label>
        </div>
        <button type="button" className="rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={pickupReason !== null || !selectedObject} title={pickupReason ?? undefined}
            onClick={() => { if (selectedObject && pickupReason === null) onSubmit({ action_id: 'oem.deck.catch_plate', inputs: { plate: selectedObject.ordinal as number } }); }}>Pick up &amp; hold</button>
        <button type="button" className="ml-3 rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={placeReason !== null || !selectedDestination} title={placeReason ?? undefined}
            onClick={() => { if (selectedDestination && placeReason === null) onSubmit({ action_id: 'oem.deck.release_plate', inputs: { destination: selectedDestination.plate_destination as number, press_plate: false } }); }}>Place held plate</button>
        {pickupReason && <p role="status">Pick up: {pickupReason}</p>}{placeReason && <p role="status">Place: {placeReason}</p>}
        {catalog.isError && <p role="status">Published object and placement choices unavailable.</p>}
        <p className="text-xs">Deck records are informational, not physical confirmation of what is held. Use Command details below to observe the same request.</p>
    </section>;
}
