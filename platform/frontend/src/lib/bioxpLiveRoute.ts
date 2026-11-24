import type { MethodValue } from './bioxpMethods';
import type { BioXpTipTrayRowV1 } from './bioxpClient';
import { deckResources } from './bioxpWorkflowDeck';
import { nativeWellAddress } from './bioxpMethodManipulations';

/** One stop of a Live deck pipetting route, in click order. */
export type LiveRouteStop = { station: string; locationId: number; well: string; action: 'aspirate' | 'dispense'; volume: string };
export type LiveRouteSettings = {
    tray: number | null;          // tip rack to pick up from; null = use the tip already loaded
    channel: number;              // 0-3
    detect: boolean;              // find the liquid surface by pressure before each aspirate
    after: 'eject' | 'keep';
    aspirateSpeed: string;        // uL/s
    dispenseSpeed: string;        // uL/s
};
export type DeckContents = Record<string, Record<string, number>>;
export type PlateGeometry = { table: number[][] | null; gap: number | null };

/** Approved starting speeds (uL/s) per tip size; T200 is the middle of the Tecan 399156 p3 ranges. Editable in the card. */
export const LIVE_SPEED_DEFAULTS: Record<number, { aspirate: number; dispense: number }> = {
    10: { aspirate: 10, dispense: 20 }, 50: { aspirate: 20, dispense: 50 }, 200: { aspirate: 110, dispense: 425 } };

export const routeLetter = (index: number) => String.fromCharCode(65 + index % 26);

/**
 * The four heads sit two rows apart and move as one. To put `channel` over a
 * well, the head reference (channel 1) goes `2 * channel` rows above it.
 */
export function referenceWell(station: string, well: string, channel: number): string | null {
    const address = nativeWellAddress(well), resource = deckResources.find(r => r.id === station);
    if (!address || !resource) return resource?.wells.includes(well) && channel === 0 ? well : null;
    const row = address.row - 2 * channel;
    const label = row >= 0 && row < 8 ? `${String.fromCharCode(65 + row)}${address.column + 1}` : null;
    return label && resource.wells.includes(label) ? label : null;
}

/** First rack position (A1..B12) whose group of four tips is all present. */
export function firstTipGroup(row: BioXpTipTrayRowV1 | undefined): string | null {
    const occupancy = row?.occupancy;
    if (!Array.isArray(occupancy) || occupancy.length !== 96) return null;
    for (let column = 0; column < 12; column++) for (const start of [0, 1])
        if ([0, 2, 4, 6].every(offset => occupancy[(start + offset) * 12 + column])) return `${'AB'[start]}${column + 1}`;
    return null;
}

const strokeTimeout = (volume: number, speed: number) => Math.max(2000, Math.round(2000 * volume / speed) + 1000);

/** Workflows steps for the route; compiled and run by the same path as the Pipettes tab. Throws a plain reason when incomplete. */
export function liveRouteSteps(route: LiveRouteStop[], settings: LiveRouteSettings, options: {
    tipWell: string | null; plateAt: Record<string, string>; contents: DeckContents; geometry: Record<string, PlateGeometry>;
}): MethodValue[] {
    if (!route.length) throw new Error('Click wells on the deck to build a route.');
    const channels = [settings.channel];
    const aspirateSpeed = Number(settings.aspirateSpeed), dispenseSpeed = Number(settings.dispenseSpeed);
    if (!(aspirateSpeed > 0) || !(dispenseSpeed > 0)) throw new Error('Enter aspirate and dispense speeds (µL/s).');
    const application = (operations: MethodValue[]) => ({ application: { implementation: 'original-adp.application.v1', operations } });
    const steps: MethodValue[] = [];
    const add = (id: string, action: string, inputs: MethodValue) => steps.push({ step_id: id, type: 'action', action, inputs });
    if (settings.tray !== null) {
        if (!options.tipWell) throw new Error(`Tip rack ${settings.tray} has no complete group of four tips recorded.`);
        add('tip', 'tip_pickup', { tray: settings.tray, well: options.tipWell, overpress: false, lift_z: false, keep_channels: channels, release_lift_mm: 10 });
    }
    let held = 0;
    route.forEach((stop, index) => {
        const letter = routeLetter(index), volume = Number(stop.volume);
        if (!stop.volume.trim() || !(volume > 0)) throw new Error(`Stop ${letter}: enter a volume in µL.`);
        const well = referenceWell(stop.station, stop.well, settings.channel);
        if (!well) throw new Error(`Stop ${letter}: channel ${settings.channel + 1} cannot reach ${stop.well}; the head would leave the plate.`);
        held += stop.action === 'aspirate' ? volume : -volume;
        if (held < -1e-9) throw new Error(`Stop ${letter}: dispenses more than the tip holds.`);
        add(`move-${index}`, 'native_intent', { operation: 'move', location_id: stop.locationId, well, position_flag: 0 });
        if (stop.action === 'aspirate' && settings.detect) {
            const plate = options.plateAt[stop.station] ?? null, geometry = plate ? options.geometry[plate] : undefined;
            const expected = options.contents[stop.station]?.[stop.well];
            // The entered amount is only compared with the detected level; a mismatch is reported, not a stop.
            const check = expected !== undefined && geometry?.table && geometry.gap !== null
                ? { volume_check: { expected_ul: { [String(settings.channel)]: expected }, table: geometry.table, gap_mm: geometry.gap, response: 'proceed' } } : {};
            add(`aspirate-${index}`, 'cavro_application', application([
                { operation: 'liquid_search', channels, timeout_ms: 15000, location_id: stop.locationId, sensing: 'pressure', ...(plate ? { plate_type: plate } : {}), ...check },
                { operation: 'surface_aspirate', channels, timeout_ms: strokeTimeout(volume, aspirateSpeed), volume_ul: volume, speed_ul_s: aspirateSpeed }]));
        } else {
            add(`lower-${index}`, 'native_intent', { operation: 'lower', location_id: stop.locationId });
            const speed = stop.action === 'aspirate' ? aspirateSpeed : dispenseSpeed;
            add(`${stop.action}-${index}`, 'cavro_application', application([
                { operation: stop.action, channels, timeout_ms: strokeTimeout(volume, speed), volume_ul: volume, speed_ul_s: speed }]));
        }
        add(`lift-${index}`, 'native_intent', { operation: 'lift', location_id: stop.locationId, height_steps: null });
    });
    // The compiler moves over the waste bin before an eject.
    if (settings.after === 'eject') add('eject', 'tip_eject', { channels });
    return steps;
}

/** Recorded amounts after a finished route: only wells that already have an entry are adjusted. */
export function contentsAfterRoute(route: LiveRouteStop[], contents: DeckContents): Record<string, Record<string, number>> {
    const changed: Record<string, Record<string, number>> = {};
    for (const stop of route) {
        const current = changed[stop.station]?.[stop.well] ?? contents[stop.station]?.[stop.well];
        const volume = Number(stop.volume);
        if (current === undefined || !(volume > 0)) continue;
        (changed[stop.station] ??= {})[stop.well] = Math.max(0, Number((current + (stop.action === 'aspirate' ? -volume : volume)).toFixed(3)));
    }
    return changed;
}
