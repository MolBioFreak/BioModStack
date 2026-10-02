import { api } from './api';
import type { FrozenSelection, SavedDesign, SaveDesignRequest, WorkflowResult, WorkflowRequest } from './goldenGateWorkflowTypes';
const route = '/api/molbio/assembly/golden-gate/design';
export const designGoldenGate = (request: WorkflowRequest, signal?: AbortSignal) => api.post<WorkflowResult>(route, request, { signal });
export const saveGoldenGateDesign = (request: SaveDesignRequest) => api.post<SavedDesign>(`${route}/save`, request);
export const readGoldenGateDesign = (operationId: string, signal?: AbortSignal) => api.get<SavedDesign>(`${route}/${encodeURIComponent(operationId)}`, { signal });
export const exportGoldenGateDesign = (result: WorkflowResult, operationId?: string) => operationId
  ? api.get<Blob>(`${route}/${encodeURIComponent(operationId)}/export`, { responseType: 'blob' })
  : api.post<Blob>(`${route}/export`, result, { responseType: 'blob' });
export const importGoldenGateDesign = (document: unknown) => api.post<WorkflowResult>(`${route}/import`, document);
/** Same fixed-selection projection as native freeze_selection; never submit the optimizer again to Save. */
export function freezeGoldenGateSelection(result: WorkflowResult, id: string): FrozenSelection {
  const selected = result.solutions.find(s => s.id === id);
  if (!selected) throw new Error('Select a physical candidate.');
  const r = result.requested;
  return structuredClone({ solution_id: id, edit_evidence: result.edits, authored_request: r,
    request: selected.fixed_request, fidelity: r.fidelity, reaction: 'reaction' in r ? r.reaction : null,
    original_sources: r.task === 'assemble_parts' && result.edits.some(e => e.accepted) ? r.sources : [],
    accepted_edits: r.task === 'assemble_parts' ? r.domestication.filter(e => e.accepted_sequence !== null) : [] });
}
/** Explicit portable clone: historical IDs remain in the retained result, never falsely reused cross-installation. */
export function cloneGoldenGateRequest(result: WorkflowResult): WorkflowRequest {
  const request = structuredClone(result.requested);
  if (request.task === 'assemble_parts') {
    const design = result.solutions.find(s => s.id === result.selected_solution_id)?.design;
    request.sources = request.sources.map(source => {
      if (source.source.kind === 'inline') return source;
      const original = result.edits.find(e => e.source_id === source.id)?.original;
      const material = original ?? design?.materials.find(m => m.id === `source:${source.id}`);
      return material ? { ...source, source: { kind: 'inline', sequence: material.sequence, topology: material.topology, features: material.features } } : source;
    });
  }
  if (request.task === 'split_target' && request.target.source.kind === 'molecular_revision') {
    const material=result.solutions.find(s=>s.id===result.selected_solution_id)?.design.materials.find(m=>m.id===`source:${request.target.id}`);
    if(material) request.target={...request.target,source:{kind:'inline',sequence:material.sequence,topology:material.topology,features:material.features}};
  }
  return request;
}
