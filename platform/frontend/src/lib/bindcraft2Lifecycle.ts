import type { Job } from './api';
import { submitNativeBinderRequest } from './nativeBinderAuthoring';

export interface BC2ActionField {
  type: 'string' | 'number' | 'integer' | 'boolean' | 'array' | 'object';
  default?: unknown;
  enum?: string[];
  control?: string;
  items?: BC2ActionField;
  properties?: Record<string, BC2ActionField>;
  required?: string[];
}
export interface BC2ActionDescriptor {
  source: string;
  properties: Record<string, BC2ActionField>;
  required?: string[];
}
export type BC2Actions = Record<string, BC2ActionDescriptor>;

export function nativeActionDefaults(descriptor: BC2ActionDescriptor): Record<string, unknown> {
  return Object.fromEntries(Object.entries(descriptor.properties)
    .filter(([, field]) => Object.prototype.hasOwnProperty.call(field, 'default'))
    .map(([key, field]) => [key, structuredClone(field.default)]));
}

/** Existing Jobs own snapshot materialization, placement review and dispatch. */
export async function submitBindCraft2Lifecycle(sourceJobId: string, operation: string,
  options: Record<string, unknown>, executionTargetId: string | null,
  launchContextId?: string | null): Promise<Job> {
  const request: Partial<Job> = {
    name: `BindCraft2 ${operation}`, model_id: 'bindcraft2', mode: operation,
    params: { bc2_source_job_id: sourceJobId, bc2_action_options: structuredClone(options) },
    execution_target_id: executionTargetId,
    ...(launchContextId ? { launch_context_id: launchContextId } : {}),
  };
  return (await submitNativeBinderRequest(request)).data;
}
