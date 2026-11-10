export interface ToolPlan {
  steps: {
    step: string;
    approx_size_gb?: number | null;
    already_present: boolean;
    license?: { id?: string; url?: string } | null;
    method?: string;
    instructions?: string | null;
    sources?: string[];
  }[];
  blockers: { reason_code: string; message: string }[];
}

/** Licences a build has to be told about: every licensed step not already on disk. */
export function licensesToAccept(plan: ToolPlan): { id: string; url?: string }[] {
  const seen = new Map<string, { id: string; url?: string }>();
  for (const step of plan.steps) {
    if (step.license?.id && !step.already_present && step.method !== 'manual') {
      seen.set(step.license.id, { id: step.license.id, url: step.license.url });
    }
  }
  return [...seen.values()];
}
