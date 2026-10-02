import type { WorkflowPlan } from './bioxpWorkflowPlan';
import type { BioXpManualStep } from './bioxpManualPipetting';

export type DraftObject = { [key: string]: DraftValue };
export type DraftValue = null | boolean | number | string | DraftValue[] | DraftObject;
export type WorkflowDraftRow = { step_id: string; intent: DraftObject };
export type WorkflowDraft = { schema: 'bms.bioxp-workflow-draft.v1'; steps: WorkflowDraftRow[]; editor_state: DraftObject };
export type NativeDraft<T> = T extends number ? number | string : T extends object ? { [K in keyof T]: NativeDraft<T[K]> } : T;
export const isDraftObject = (value: unknown): value is DraftObject => !!value && typeof value === 'object' && !Array.isArray(value);

// JSON drafts are not native execution requests. Convert only explicit numeric
// text at the existing Run boundary; missing/unknown fields stay drafts.
const fields: Record<string, string[]> = {
    move: ['location_id', 'well', 'position_flag'], lower: ['location_id'], lift: ['location_id', 'height_steps'],
    aspirate: ['channels', 'volume_ul', 'speed'], dispense: ['channels', 'volume_ul', 'speed'],
    mix: ['channels', 'volume_ul', 'aspirate_speed', 'dispense_speed', 'cycles'],
    load_tip: ['tray', 'well', 'overpress', 'lift_z'], measure_fluid_height: ['speed'],
    source_fluid_offset: ['plate', 'speed', 'transfer_fluid', 'skip_steps'], diagnostic_detect_fluid: [], source_calwith_fluid: [],
    source_load_tips: ['tip_type', 'pipette', 'force_new_tip'],
    source_mix: ['volume_ul', 'air_ul', 'aspirate_speed', 'dispense_speed', 'aspirate_delay_ms', 'dispense_delay_ms', 'cycles', 'mix_type', 'tip_dip'],
    source_aspirate_air: ['volume_ul'], source_dispense_air: ['volume_ul'], source_purge: ['speed', 'amp', 'ntd'], diagnostic_pipette: ['diagnostic'],
};
const numeric = new Set(['location_id', 'position_flag', 'height_steps', 'volume_ul', 'speed', 'aspirate_speed', 'dispense_speed', 'cycles', 'tray', 'skip_steps', 'tip_type', 'pipette', 'air_ul', 'aspirate_delay_ms', 'dispense_delay_ms', 'steps']);
const booleans = new Set(['overpress', 'lift_z', 'transfer_fluid', 'force_new_tip', 'tip_dip', 'amp', 'ntd']);
export function nativeIntent(intent: DraftObject): BioXpManualStep {
    const error = () => new Error('Incomplete or unknown draft step. Review its native fields before running.');
    function convert(obj: DraftObject, keys: string[], discriminator: string): DraftObject {
        if (keys.some(k => !(k in obj)) || Object.keys(obj).some(k => k !== discriminator && !keys.includes(k))) throw error();
        return Object.fromEntries(Object.entries(obj).map(([key, value]) => {
            if (numeric.has(key)) {
                if ((key.endsWith('delay_ms') && (value === null || value === '')) || (key === 'height_steps' && value === null)) return [key, null];
                if ((typeof value !== 'number' && typeof value !== 'string') || (typeof value === 'string' && !value.trim()) || !Number.isFinite(Number(value))) throw error();
                return [key, Number(value)];
            }
            if (booleans.has(key) && typeof value !== 'boolean') throw error();
            if (key === 'channels' && (!Array.isArray(value) || value.some(v => typeof v !== 'number'))) throw error();
            return [key, value];
        }));
    }
    const op = intent.operation;
    if (typeof op !== 'string' || !Object.hasOwn(fields, op)) throw error();
    const result = convert(intent, fields[op], 'operation');
    if (op === 'diagnostic_pipette') {
        const d = intent.diagnostic;
        const diagnostics: Record<string, string[]> = { aspirate: ['channels', 'volume_ul', 'speed'], dispense: ['channels', 'volume_ul', 'speed'], eject: ['channels'], plunger_up: ['steps'], plunger_down: ['steps'], dispense_all: [], diagnoses: [], initialize: [], get_data: [], last_error: [] };
        if (!isDraftObject(d) || typeof d.action !== 'string' || !Object.hasOwn(diagnostics, d.action)) throw error();
        result.diagnostic = convert(d, diagnostics[d.action], 'action');
    }
    return result as unknown as BioXpManualStep;
}

// Database/API JSON may reorder object keys. Equality must still preserve
// value types, omitted keys, array order and null without normalizing the draft.
export function sameDraftValue(left: unknown, right: unknown): boolean {
    if (left === right) return true;
    if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
        && left.length === right.length && left.every((value, index) => sameDraftValue(value, right[index]));
    if (!isDraftObject(left) || !isDraftObject(right)) return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length
        && keys.every(key => Object.hasOwn(right, key) && sameDraftValue(left[key], right[key]));
}

// Apply only fields actually changed in the form. An untouched omitted/null/
// unknown field in a hydrated intent must not be replaced by a visual blank.
export function mergeDraftEdits(original: DraftObject, before: DraftObject, after: DraftObject): DraftObject {
    if ('action' in after && after.action !== before.action) return after;
    const result = { ...original };
    for (const [key, value] of Object.entries(after)) {
        if (sameDraftValue(value, before[key])) continue;
        result[key] = isDraftObject(value) && isDraftObject(before[key]) && isDraftObject(original[key])
            ? mergeDraftEdits(original[key], before[key], value) : value;
    }
    return result;
}

export function readWorkflowDraft(value: unknown): WorkflowDraft | WorkflowPlan {
    if (!isDraftObject(value) || (value.schema !== 'bms.bioxp-workflow-draft.v1' && value.schema !== 'bms.bioxp-workflow-draft.v2') || !Array.isArray(value.steps) || !isDraftObject(value.editor_state)
        || Object.keys(value).some(k => !['schema', 'steps', 'editor_state', ...(value.schema === 'bms.bioxp-workflow-draft.v2' ? ['deck_plan'] : [])].includes(k))) throw new Error('Unsupported workflow draft envelope.');
    if (value.schema === 'bms.bioxp-workflow-draft.v2' && (!isDraftObject(value.deck_plan)
        || !['labware', 'materials', 'assignments'].every(key => Array.isArray((value.deck_plan as DraftObject)[key])))) throw new Error('Invalid workflow deck plan.');
    const ids = new Set<string>();
    for (const row of value.steps) {
        if (!isDraftObject(row) || typeof row.step_id !== 'string' || !row.step_id.trim() || ids.has(row.step_id) || !isDraftObject(row.intent)
            || Object.keys(row).some(k => !['step_id', 'intent'].includes(k))) throw new Error('Invalid workflow draft row.');
        ids.add(row.step_id);
    }
    return value as unknown as WorkflowDraft | WorkflowPlan;
}
