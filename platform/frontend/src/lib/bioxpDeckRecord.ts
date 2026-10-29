import { deckStations } from './bioxpWorkflowDeck';
import type { BioXpDeckRecordRequestV1, BioXpDeckStateV1, BioXpTipTrayRowV1, BioXpTipTraysV1 } from './bioxpClient';

// Pure helpers for the deck record panel. Hooks live in bioxpClient.ts.
export type BioXpDeckRecordTips = 'full' | 'empty' | string[];

// Tip racks on this deck: trays 1-4. Tray 5 (tip hotel) is not fitted.
export const DECK_TIP_TRAYS = [1, 2, 3, 4] as const;
export const ROWS = 'ABCDEFGH';
export const wellName = (index: number) => `${ROWS[Math.floor(index / 12)]}${index % 12 + 1}`;

// OEM plate ordinals (ClassMachineStatus plateName) for plate_on_gantry.
const PLATE_ORDINALS = ['POOL_PLATE', 'OUTPUT_PLATE', 'REAGENT_PLATE', 'BIO_SECURITY_COVER', 'OUTPUT_COVER',
    'REAGENT_COVER', 'TIP_TRAY', 'STRIP_ONE', 'STRIP_TWO', 'STRIP_THREE', 'STRIP_FOUR', 'TROUGH'];
export const OBJECT_LABELS: Record<string, string> = {
    OUTPUT_COVER: 'Output cover', REAGENT_COVER: 'Reagent cover', BIO_SECURITY_COVER: 'Biosecurity cover',
    POOL_PLATE: 'Pool plate', OUTPUT_PLATE: 'Output plate', REAGENT_PLATE: 'Reagent plate',
    STRIP_ONE: 'Strip 1', STRIP_TWO: 'Strip 2', STRIP_THREE: 'Strip 3', STRIP_FOUR: 'Strip 4', TROUGH: 'Trough',
};
const LOCATION_LABELS: Record<string, string> = {
    LOC_OC_COVER: 'Output chiller', LOC_OC_COVER_STORAGE: 'Output cover storage',
    LOC_RC_COVER: 'Reagent chiller', LOC_RC_COVER_STORAGE: 'Reagent cover storage',
    LOC_GANTRY: 'Gripper', LOC_BSC: 'Thermal cycler', LOC_BSCS: 'Biosecurity cover storage',
    LOC_P_TC: 'Thermal cycler', LOC_P_OC: 'Output chiller', LOC_P_MS: 'Magnetic station',
};
export const locationLabel = (location: string | null | undefined) => !location ? 'Unknown'
    : LOCATION_LABELS[location] ?? deckStations.find(station => station.id === location)?.label ?? location;
export const objectLabel = (name: string) => OBJECT_LABELS[name] ?? name;
export const COVER_LOCATIONS: Record<'OUTPUT_COVER' | 'REAGENT_COVER', string[]> = {
    OUTPUT_COVER: ['LOC_OC_COVER', 'LOC_OC_COVER_STORAGE', 'LOC_GANTRY'],
    REAGENT_COVER: ['LOC_RC_COVER', 'LOC_RC_COVER_STORAGE', 'LOC_GANTRY'],
};

export function gripperObject(state: BioXpDeckStateV1 | undefined): string | null | undefined {
    const value: unknown = state?.semantic?.plate_on_gantry;
    if (value === undefined) return undefined;
    if (value === null) return null;
    return typeof value === 'number' ? PLATE_ORDINALS[value] ?? `Plate ${value}` : String(value);
}
export function coverLocation(state: BioXpDeckStateV1 | undefined, name: string): string | undefined {
    return state?.semantic?.movable_plate_locations?.[name] ?? state?.movable_objects?.[name]?.location;
}
export function tipCounts(row: BioXpTipTrayRowV1 | undefined) {
    const tips = Array.isArray(row?.occupancy) ? row!.occupancy!.filter(Boolean).length : row?.tips_present ?? null;
    const pickups = row?.four_channel_groups_available ?? (typeof row?.available_count === 'number' ? row.available_count : null);
    return { tips, pickups };
}
export function occupancyByTray(data: BioXpTipTraysV1 | undefined): Record<number, boolean[]> | undefined {
    if (!data) return undefined;
    const result: Record<number, boolean[]> = {};
    for (const row of data.trays ?? []) if (row.tray >= 1 && row.tray <= 4 && Array.isArray(row.occupancy) && row.occupancy.length === 96) result[row.tray] = row.occupancy;
    return result;
}

export type DeckRecordDraft = {
    covers: Partial<Record<'OUTPUT_COVER' | 'REAGENT_COVER', string>>;
    gripper?: string; // 'empty' or object name
    tips: Partial<Record<number, 'full' | 'empty' | string[]>>;
    note: string;
};
/** Only fields the operator set that differ from what the robot believes. */
export function deckRecordBody(generation: number, draft: DeckRecordDraft, state: BioXpDeckStateV1 | undefined,
    trays: BioXpTipTraysV1 | undefined): BioXpDeckRecordRequestV1 | null {
    const body: BioXpDeckRecordRequestV1 = { expected_connection_generation: generation };
    const objects: Record<string, string> = {};
    for (const [name, location] of Object.entries(draft.covers)) if (location && location !== coverLocation(state, name)) objects[name] = location;
    if (Object.keys(objects).length) body.movable_objects = objects;
    const held = gripperObject(state);
    if (draft.gripper && draft.gripper !== (held === null ? 'empty' : held)) body.gantry = draft.gripper;
    const tipTrays: { tray: number; tips: BioXpDeckRecordTips }[] = [];
    for (const tray of DECK_TIP_TRAYS) {
        const want = draft.tips[tray];
        if (!want) continue;
        const occupancy = trays?.trays.find(row => row.tray === tray)?.occupancy;
        const present = Array.isArray(occupancy) ? occupancy.flatMap((tip, index) => tip ? [wellName(index)] : []) : null;
        const wells = want === 'full' ? Array.from({ length: 96 }, (_, i) => wellName(i)) : want === 'empty' ? [] : want;
        if (present && present.length === wells.length && present.every(well => wells.includes(well))) continue;
        tipTrays.push({ tray, tips: want === 'full' || want === 'empty' ? want : [...want] });
    }
    if (tipTrays.length) body.tip_trays = tipTrays;
    if (!body.movable_objects && !body.gantry && !body.tip_trays) return null;
    if (draft.note.trim()) body.reason = draft.note.trim().slice(0, 500);
    return body;
}
