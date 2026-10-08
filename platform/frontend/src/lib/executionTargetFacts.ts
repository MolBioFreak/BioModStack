/**
 * Read-only projection of the execution-target facts the API publishes.
 *
 * Readiness is not capability, and a workspace row is not admission: the
 * picker and the worker card must state what the API itself requires before it
 * accepts work. Nothing here refuses anything locally; it only reports the
 * server's own clauses so the operator is not shown "Ready" for a worker the
 * API will reject.
 */

export interface TargetDeviceCapability {
    schema?: string | null;
    source?: string | null;
    observed_at?: string | null;
    devices?: Array<{ index?: number | null; uuid?: string | null; name?: string | null; memory_total_mb?: number | null }> | null;
    vram_envelope?: { target_vram_fill?: number | null; safety_margin_mb?: number | null } | null;
    per_device_memory_total_mb?: number | null;
    per_device_admissible_idle_mb?: number | null;
    heavy_model_per_device_mb?: {
        minimum_mb?: number | null; minimum_model?: string | null;
        maximum_mb?: number | null; maximum_model?: string | null;
    } | null;
    heavy_model_fits?: boolean | null;
}

export interface TargetSchedulingFacts {
    policy?: string | null;
    new_work_ready?: boolean | null;
    inventory_fresh?: boolean | null;
    leased_job_id?: string | null;
    preload_active?: boolean | null;
    provider_present?: boolean | null;
    provider_running?: boolean | null;
    not_ready_reason?: string | null;
    device_capability?: TargetDeviceCapability | null;
}

interface TargetLike {
    active?: boolean | null;
    state?: string | null;
    capabilities?: Record<string, unknown> | null;
}

function record(value: unknown): Record<string, unknown> | null {
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function schedulingFacts(target: TargetLike | null | undefined): TargetSchedulingFacts | null {
    const scheduling = record(record(target?.capabilities)?.scheduling);
    return scheduling ? scheduling as TargetSchedulingFacts : null;
}

export function deviceCapability(target: TargetLike | null | undefined): TargetDeviceCapability | null {
    return record(schedulingFacts(target)?.device_capability) as TargetDeviceCapability | null;
}

/**
 * The worker's runtime is attached and ready. This is persistent state, not
 * admission: it survives a provider inventory refresh, so saved placements and
 * worker-side GPU enumeration depend on it, while new work stays gated by
 * `newWorkReady`. Keep the two apart; conflating them is what made the UI claim
 * readiness the API would refuse.
 */
export function runtimeAttachedReady(target: TargetLike | null | undefined): boolean {
    return Boolean(target?.active && target?.state === 'ready');
}

/**
 * The API's admission predicate. When the server has published it, use it
 * verbatim; only payloads that predate it fall back to the row projection.
 */
export function newWorkReady(target: TargetLike | null | undefined): boolean {
    const published = schedulingFacts(target)?.new_work_ready;
    if (typeof published === 'boolean') return published;
    return runtimeAttachedReady(target);
}

/** Why the API will not accept new work, in the API's own words. */
export function notReadyReason(target: TargetLike | null | undefined): string | null {
    const facts = schedulingFacts(target);
    if (!facts) return null;
    const reason = facts.not_ready_reason;
    if (typeof reason === 'string' && reason.trim()) return reason;
    if (facts.new_work_ready === false) return 'Worker cannot accept new work';
    return null;
}

/** Observed per-device capacity of the worker, never inferred from readiness. */
export function deviceCapacitySummary(target: TargetLike | null | undefined): string | null {
    const capability = deviceCapability(target);
    if (!capability) return null;
    const parts: string[] = [];
    const total = capability.per_device_memory_total_mb;
    if (typeof total === 'number' && total > 0) parts.push(`${total} MB per device`);
    const admissible = capability.per_device_admissible_idle_mb;
    if (typeof admissible === 'number' && admissible > 0) parts.push(`${admissible} MB admissible`);
    const count = capability.devices?.length;
    if (!parts.length && count) parts.push(`${count} device${count === 1 ? '' : 's'} reported`);
    return parts.length ? parts.join(' · ') : null;
}

/**
 * Advisory capability warning: the worker is ready but cannot host every heavy
 * model, which the operator would otherwise only discover at submit time.
 */
export function deviceCapabilityWarning(target: TargetLike | null | undefined): string | null {
    const capability = deviceCapability(target);
    if (!capability || capability.heavy_model_fits !== false) return null;
    const range = capability.heavy_model_per_device_mb;
    const admissible = capability.per_device_admissible_idle_mb;
    const maximum = range?.maximum_mb;
    if (typeof admissible !== 'number' || typeof maximum !== 'number') return null;
    const examples = range?.maximum_model ? ` (${range.maximum_model} needs ${maximum} MB)` : '';
    const minimum = range?.minimum_mb;
    const span = typeof minimum === 'number' && minimum !== maximum
        ? `${minimum}–${maximum} MB` : `${maximum} MB`;
    return `Heavy models reserve ${span} per device${examples}; this worker admits ${admissible} MB`;
}
