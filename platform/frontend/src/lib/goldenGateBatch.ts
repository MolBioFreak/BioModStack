import type { AssembleTask, WorkflowResult } from './goldenGateWorkflowTypes';

/** Composition fields reference native children, never another scientific DTO. */
export type SlotAlternative = {
  id: string;
  source: AssembleTask['sources'][number];
  part: AssembleTask['parts'][number];
  automatic_primer: AssembleTask['automatic_primers'][number] | null;
  domestication: AssembleTask['domestication'][number] | null;
  reaction_part: NonNullable<AssembleTask['reaction']>['parts'][number] | null;
};
export type BatchSlot = { part_id: string; alternatives: SlotAlternative[] };
export type BatchScope = { mode: 'full' } | { mode: 'sampled'; count: number; seed: number };
export type BatchRequest = { schema_version: 'bms.golden-gate-batch.v1'; base: AssembleTask; slots: BatchSlot[]; scope: BatchScope };
export type BatchProgress = { total: string; selected: string; evaluated: string; completed: string; omitted: string; remaining: string };
export type BatchEvent =
  | { event: 'scope'; mode: BatchScope['mode']; sampling: 'python-random-floyd-v1'; seed: number | null; progress: BatchProgress }
  | { event: 'result'; index: string; alternatives: string[]; result: WorkflowResult; progress: BatchProgress }
  | { event: 'error'; index: string; message: string; progress: BatchProgress }
  | { event: 'finished'; coverage: 'full' | 'sampled' | 'incomplete'; progress: BatchProgress };
export type BatchResultEvent = Extract<BatchEvent, { event: 'result' }>;
export const batchCount = (slots: BatchSlot[]) => slots.reduce((n, s) => n * BigInt(s.alternatives.length), 1n);

/** One streaming POST. Completed lines are delivered immediately, not held until EOF. */
export async function designGoldenGateBatch(request: BatchRequest, receive: (event: BatchEvent) => void, signal?: AbortSignal) {
  const context = new URLSearchParams(window.location.search).get('launch_context_id');
  const response = await fetch('/api/molbio/assembly/golden-gate/design/batch', {
    method: 'POST', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json', ...(context ? { 'X-BMS-Launch-Context-ID': context } : {}) },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error(`Batch request ${response.status}: ${await response.text()}`);
  if (!response.body) throw new Error('Batch response has no stream; coverage is incomplete.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = ''; let finished = false;
  const deliver = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as BatchEvent;
    if (!['scope', 'result', 'error', 'finished'].includes(event.event)) throw new Error('Unknown batch event');
    if (signal?.aborted) return;
    receive(event);
    if (event.event === 'finished') finished = true;
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline: number;
      while ((newline = buffer.indexOf('\n')) >= 0) { deliver(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1); }
      if (done) break;
    }
    if (buffer.trim()) deliver(buffer);
    if (!finished && !signal?.aborted) throw new Error('Batch stream ended before completion; delivered results remain available.');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export function captureAlternative(base: AssembleTask, partId: string, id: string): SlotAlternative {
  const part = base.parts.find(p => p.id === partId);
  const source = base.sources.find(s => s.id === part?.source_id);
  if (!part || !source) throw new Error('Select a part with an explicit source.');
  return structuredClone({ id, source, part,
    automatic_primer: base.automatic_primers.find(p => p.part_id === partId) ?? null,
    domestication: base.domestication.find(e => e.source_id === source.id) ?? null,
    reaction_part: base.reaction?.parts.find(p => p.part_id === partId) ?? null,
  });
}

/** Restore an alternative into the existing typed controls; shared stage settings stay independent. */
export function editAlternative(base: AssembleTask, alternative: SlotAlternative): AssembleTask {
  const a = structuredClone(alternative);
  return { ...base, sources: [...base.sources.filter(s => s.id !== a.source.id), a.source],
    parts: base.parts.map(p => p.id === a.part.id ? a.part : p),
    automatic_primers: [...base.automatic_primers.filter(p => p.part_id !== a.part.id), ...(a.automatic_primer ? [a.automatic_primer] : [])],
    domestication: [...base.domestication.filter(e => e.source_id !== a.source.id), ...(a.domestication ? [a.domestication] : [])],
    reaction: base.reaction ? { ...base.reaction, parts: [...base.reaction.parts.filter(p => p.part_id !== a.part.id), ...(a.reaction_part ? [a.reaction_part] : [])] } : null,
  };
}
