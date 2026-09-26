export type PipetteResult = {
    action?: string; requested_pipette?: number; tip_location?: number; alignment_published?: boolean; already_matching_tip_type?: boolean;
    selected_channels?: number[]; lost_tip_channels?: number[]; cached_tip_channels?: number[]; ejected_channels?: number[];
    channels?: Record<string, unknown>[]; tests?: Record<string, unknown>[]; attempts?: unknown[];
    controller_outcome_ok?: boolean; source_return_completed?: boolean; physical_effect_verified?: boolean;
    kind?: string; action_id?: string; detail?: unknown; run_id?: string; body_completed?: boolean; completed?: boolean;
    source_return?: unknown; samples?: Record<string, unknown>[];
    scans?: Record<string, unknown>[]; measurements?: Record<string, unknown>[];
    saved_revision_id?: string | null; active_revision_id?: string | null;
    comparison_choice?: string | boolean | null; comparison_source?: string; source?: string; pending_restart?: boolean;
    error?: unknown; finalization_error?: unknown;
    calibration_persisted?: boolean; position_steps?: number; lost_steps?: number; lost_steps_warning?: boolean;
};
export const resultRecord = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;

// Traverse only known receipt envelopes, never arbitrary diagnostic logs. A compact
// result replaces the old nested measurement copies but not outer terminal errors.
export function pipetteResults(value: unknown): PipetteResult[] {
    const results: PipetteResult[] = [];
    const visit = (input: unknown) => {
        if (Array.isArray(input)) { input.forEach(visit); return; }
        const record = resultRecord(input);
        if (!record) return;
        const compact = resultRecord(record.pipette_result);
        if (compact) {
            results.push({ action_id: record.action_id, position_steps: record.position_steps, lost_steps: record.lost_steps,
                lost_steps_warning: record.lost_steps_warning, calibration_persisted: record.calibration_persisted, ...compact } as PipetteResult);
            if (record.detail != null) results.push({ detail: record.detail });
            if (record.error != null && record.error !== compact.error) results.push({ error: record.error });
            if (record.finalization_error != null && record.finalization_error !== compact.finalization_error)
                results.push({ finalization_error: record.finalization_error });
            return;
        }
        if (['action', 'requested_pipette', 'tip_location', 'alignment_published', 'samples', 'scans', 'measurements', 'run_id', 'calibration_persisted', 'saved_revision_id', 'active_revision_id',
            'body_completed', 'source_return', 'comparison_choice', 'position_steps', 'lost_steps', 'error', 'detail', 'finalization_error']
            .some(key => record[key] !== undefined && record[key] !== null)) {
            results.push(record as PipetteResult);
        }
        for (const key of ['result', 'response', 'completed_children', 'source_children', 'provider_results']) {
            const child = record[key];
            // Older failure envelopes may index provider results by child ID.
            const providers = key === 'provider_results' ? resultRecord(child) : null;
            if (providers && !['pipette_result', 'result', 'response', 'samples', 'scans', 'measurements', 'error'].some(field => field in providers))
                Object.values(providers).forEach(visit);
            else visit(child);
        }
    };
    visit(value);
    return results;
}
