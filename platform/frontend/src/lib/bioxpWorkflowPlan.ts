import { api } from './api';
import { deckStations } from './bioxpWorkflowDeck';
import type { DraftObject, WorkflowDraft, WorkflowDraftRow } from './bioxpWorkflowDraft';

// Planned associations never assert measured material presence or robot readiness.
export type WorkflowLabware = { id: string; station: string; name: string; profile_id: string; native_plate_id?: unknown };
export type WorkflowMaterial = { id: string; name: string; kind: 'sample' | 'reagent' | 'product' | 'waste'; description: string; concentration?: string | number | null; concentration_unit?: string };
export type WorkflowAssignment = { id: string; labware_id: string; well: string; material_id: string; volume_ul: string | number | null };
export type WorkflowDeckPlan = { labware: WorkflowLabware[]; materials: WorkflowMaterial[]; assignments: WorkflowAssignment[] };
export type WorkflowPlan = { schema: 'bms.bioxp-workflow-draft.v2'; steps: WorkflowDraftRow[]; editor_state: DraftObject; deck_plan: WorkflowDeckPlan };
export type TransferEndpoint = { station: string; location_id: number | string; wells: string[] };
export type WorkflowTransferIntent = {
    operation: 'transfer'; source: TransferEndpoint; destination: TransferEndpoint;
    channels: number[]; volume_ul: string | number; aspirate_speed: string | number; dispense_speed: string | number;
    source_position_flag: string | number; destination_position_flag: string | number;
    source_lift_height_steps: string | number | null; destination_lift_height_steps: string | number | null;
};
export type WorkflowPreviewAction = {
    index: number; step_id: string; pair_index: number | null; kind: string;
    params: Record<string, unknown>; label: string; station: string | null; well: string | null;
};
export type WorkflowPreviewIssue = { step_id: string | null; message: string };
export type WorkflowPreview = { document: Record<string, unknown> | null; actions: WorkflowPreviewAction[]; issues: WorkflowPreviewIssue[] };
export type WorkflowJobClone = { name: string | null; draft: WorkflowPlan | WorkflowDraft | null; issues: WorkflowPreviewIssue[] };
export async function cloneBioXpWorkflowJob(job_id: string, document: Record<string, unknown>): Promise<WorkflowJobClone> {
    return (await api.post<WorkflowJobClone>('/api/bioxp/workflows/clone', { job_id, document })).data;
}
export type SavedWorkflowSnapshot = { id: string; name: string; draft: WorkflowPlan | WorkflowDraft };
export const emptyDeckPlan = (): WorkflowDeckPlan => ({ labware: [], materials: [], assignments: [] });
export const emptyTransferIntent = (): WorkflowTransferIntent => ({
    operation: 'transfer', source: { station: '', location_id: '', wells: [] }, destination: { station: '', location_id: '', wells: [] },
    channels: [], volume_ul: '', aspirate_speed: '', dispense_speed: '', source_position_flag: '', destination_position_flag: '',
    source_lift_height_steps: '', destination_lift_height_steps: '',
});
/** Display only actual emitted native destinations; never editable draft labels. */
export function previewActionDestination(action: Pick<WorkflowPreviewAction, 'kind' | 'params' | 'station' | 'well'>): { station: string | null; well: string | null } {
    const p = action.params;
    const explicit = p.location_id ?? p.destination_location_id ?? p.target ?? (['plate_move', 'move_cover', 'plate_release'].includes(action.kind) ? p.destination : undefined);
    const mapped = explicit === undefined || explicit === null ? undefined : deckStations.find(s => s.id === explicit || s.locationId !== null && String(s.locationId) === String(explicit));
    const tip = p.operation === 'load_tip' ? deckStations.find(s => s.tipTray !== null && String(s.tipTray) === String(p.tray)) : undefined;
    const park = p.operation === 'park' ? 'LOC_PARK' : null;
    const station = mapped?.id ?? tip?.id ?? park ?? action.station;
    const well = p.well ?? p.reference_well ?? action.well;
    return { station, well: well === undefined || well === null ? null : String(well).trim().toUpperCase() };
}
export async function previewBioXpWorkflow(draft: WorkflowPlan | WorkflowDraft, protocol_id: string): Promise<WorkflowPreview> {
    const result = (await api.post<WorkflowPreview>('/api/bioxp/workflows/preview', { draft, protocol_id })).data;
    return { ...result, actions: result.actions.map(action => ({ ...action, ...previewActionDestination(action) })) };
}
