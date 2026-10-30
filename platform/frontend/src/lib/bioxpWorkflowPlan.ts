import { deckStations } from './bioxpWorkflowDeck';

// Planned associations never assert measured material presence or robot readiness.
export type WorkflowLabware = { id: string; station: string; name: string; profile_id: string; native_plate_id?: unknown };
export type WorkflowMaterial = { id: string; name: string; kind: 'sample' | 'reagent' | 'product' | 'waste'; description: string; concentration?: string | number | null; concentration_unit?: string };
export type WorkflowAssignment = { id: string; labware_id: string; well: string; material_id: string; volume_ul: string | number | null };
/** Tip trays 1-4 (TECANRACK1-4). The tip hotel (tray 5) is not fitted on this deck. */
export type WorkflowTipRack = { tray: 1 | 2 | 3 | 4; tip_type?: 'T10' | 'T50' | 'T200' | 'T201' | null; tips: 'full' | 'empty' | { available: string[] }; profile_id?: string };
/** Reconfigurable slots; the holder is a record, deck geometry never changes. */
export type WorkflowSlotId = 'LOC_TROUGH' | 'LOC_OC_COVER_STORAGE' | 'LOC_RC_COVER_STORAGE';
export type WorkflowSlotHolder = 'cover_storage' | 'trough' | 'tube_holder' | 'fluorometer' | 'tool_dock' | 'empty';
export type WorkflowSlot = { slot: WorkflowSlotId; holder: WorkflowSlotHolder; label?: string; positions?: string[] };
export type WorkflowDeckPlan = {
    labware: WorkflowLabware[]; materials: WorkflowMaterial[]; assignments: WorkflowAssignment[];
    tip_racks?: WorkflowTipRack[]; movable_objects?: Record<string, string>; slots?: WorkflowSlot[];
};
export const slotDefaults: Record<WorkflowSlotId, WorkflowSlotHolder> = { LOC_TROUGH: 'trough', LOC_OC_COVER_STORAGE: 'cover_storage', LOC_RC_COVER_STORAGE: 'cover_storage' };
export const slotHolderLabels: Record<WorkflowSlotHolder, string> = { cover_storage: 'Cover storage', trough: 'Trough', tube_holder: 'Tube holder', fluorometer: 'Fluorometer', tool_dock: 'Tool dock', empty: 'Empty' };
export type WorkflowPreviewAction = {
    index: number; step_id: string; pair_index: number | null; kind: string;
    params: Record<string, unknown>; label: string; station: string | null; well: string | null;
};
/** Display only actual emitted native destinations; never editable draft labels. */
export function previewActionDestination(action: Pick<WorkflowPreviewAction, 'kind' | 'params' | 'station' | 'well'>, custodyDestinations: Record<string, unknown>[] = []): { station: string | null; well: string | null } {
    const p = action.params;
    const explicit = p.target_location ?? p.location_id ?? p.destination_location_id ?? p.target ?? (['plate_move', 'move_cover', 'plate_release'].includes(action.kind) ? p.destination : undefined);
    const custody = ['plate_move', 'move_cover', 'plate_release'].includes(action.kind);
    const destination = custody ? custodyDestinations.find(d => d.token === explicit) : undefined;
    const mapped = explicit === undefined || explicit === null ? undefined : deckStations.find(s => s.id === (destination?.station ?? explicit) || !custody && s.locationId !== null && String(s.locationId) === String(explicit));
    const tip = p.operation === 'load_tip' ? deckStations.find(s => s.tipTray !== null && String(s.tipTray) === String(p.tray)) : undefined;
    const park = action.kind === 'park' || p.operation === 'park' ? 'LOC_PARK' : null;
    const station = mapped?.id ?? tip?.id ?? park ?? (explicit == null ? action.station : null);
    const well = p.well ?? p.reference_well ?? action.well;
    return { station, well: well === undefined || well === null ? null : String(well).trim().toUpperCase() };
}
