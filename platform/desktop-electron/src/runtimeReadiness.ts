import type { ServiceRuntimeMode, ServiceStatusPayload } from './serviceControl.js';

export type RuntimeReadinessService = {
  startAll: (runtimeMode: ServiceRuntimeMode) => Promise<void>;
  getStatus: (runtimeMode: ServiceRuntimeMode) => Promise<ServiceStatusPayload>;
};

export type RuntimeReadinessOptions = {
  timeoutMs?: number;
  initialDelayMs?: number;
  maxDelayMs?: number;
  now?: () => number;
  sleep?: (delayMs: number) => Promise<void>;
};

export function isRuntimeReady(payload: ServiceStatusPayload, runtimeMode: ServiceRuntimeMode): boolean {
  const health = payload.health;
  const healthRecord = health && typeof health === 'object'
    ? health as Record<string, unknown>
    : null;
  const controlPlaneReady = Boolean(
    healthRecord
    && healthRecord.api_ready === true
    && healthRecord.frontend_ready === true,
  );
  const workflowBoundaryReady = runtimeMode === 'dev'
    || healthRecord?.adapter_ready === true;
  return payload.runtime_mode === runtimeMode
    && payload.runtime_active === true
    && controlPlaneReady
    && workflowBoundaryReady;
}

function defaultSleep(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

export async function ensureRuntimeReady(
  runtimeMode: ServiceRuntimeMode,
  service: RuntimeReadinessService,
  options: RuntimeReadinessOptions = {},
): Promise<void> {
  const timeoutMs = Math.max(1, options.timeoutMs ?? 30_000);
  const maxDelayMs = Math.max(1, options.maxDelayMs ?? 2_000);
  let delayMs = Math.max(1, Math.min(options.initialDelayMs ?? 250, maxDelayMs));
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? defaultSleep;
  const deadline = now() + timeoutMs;

  await service.startAll(runtimeMode);
  while (true) {
    const payload = await service.getStatus(runtimeMode);
    if (now() >= deadline) {
      throw new Error(`BioModStack ${runtimeMode} runtime did not become ready before the ${timeoutMs}ms deadline`);
    }
    if (isRuntimeReady(payload, runtimeMode)) {
      return;
    }
    if (now() >= deadline) {
      throw new Error(`BioModStack ${runtimeMode} runtime did not become ready before the ${timeoutMs}ms deadline`);
    }
    await sleep(delayMs);
    delayMs = Math.min(maxDelayMs, delayMs * 2);
  }
}
