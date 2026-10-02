import { api } from './api';
import type { DraftObject, WorkflowDraft, WorkflowDraftRow } from './bioxpWorkflowDraft';

// Planned associations never assert measured material presence or robot readiness.
export type WorkflowLabware = { id: string; station: string; name: string; profile_id: string };
export type WorkflowMaterial = { id: string; name: string; kind: 'sample' | 'reagent'; description: string };
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
export type SavedWorkflowSnapshot = { id: string; name: string; draft: WorkflowPlan | WorkflowDraft };
export const emptyDeckPlan = (): WorkflowDeckPlan => ({ labware: [], materials: [], assignments: [] });
export const emptyTransferIntent = (): WorkflowTransferIntent => ({
    operation: 'transfer', source: { station: '', location_id: '', wells: [] }, destination: { station: '', location_id: '', wells: [] },
    channels: [], volume_ul: '', aspirate_speed: '', dispense_speed: '', source_position_flag: '', destination_position_flag: '',
    source_lift_height_steps: '', destination_lift_height_steps: '',
});
export async function previewBioXpWorkflow(draft: WorkflowPlan | WorkflowDraft, protocol_id: string): Promise<WorkflowPreview> {
    return (await api.post<WorkflowPreview>('/api/bioxp/workflows/preview', { draft, protocol_id })).data;
}
