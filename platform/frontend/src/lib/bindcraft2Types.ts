/** Read-only discovery metadata; none of these projections enter requests. */
export type BC2Request = Record<string, unknown>;
export type BC2Section = 'sources' | 'binder' | 'campaign' | 'objectives' | 'expert';
export type BC2Display = {
  selectors: Record<string, unknown>;
  values: Record<string, unknown>;
  origins?: Record<string, string>;
};
export type BC2Field = {
  native_key: string;
  observed_types: string[];
  has_native_default: boolean;
  native_default: unknown;
  choices?: string[];
  items?: Record<string, unknown>;
  runtime_fallback?: unknown;
  applicable_when?: Record<string, unknown>;
  fallback_authority?: string;
  status: 'typed' | 'unresolved';
};
export type BC2MetricParameter = {
  default_literal: unknown;
  source_default: string | null;
  required?: boolean;
  request_types?: string[];
  resolved_default?: unknown;
  native_default_encoding?: string;
  unresolved_reason?: string;
  items?: Record<string, unknown>;
  control?: string;
  description?: string;
};
export type BC2Inventory = {
  upstream_commit: string;
  fields: Record<string, BC2Field>;
  presets: Record<string, Record<string, unknown>>;
  paratope_conformations: string[];
  registered_metrics: Record<string, Record<string, { params: Record<string, BC2MetricParameter> }>>;
  display?: BC2Display;
};
export type BC2NativeSettingsResponse = {
  model_id: 'bindcraft2';
  launch_available: boolean;
  settings: BC2Inventory;
};
