import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { normalizeProjectManagerReadModel } from '../../src/lib/projectManager';

it('accepts the real API-created mock project and preserves edited work in the frontend contract', () => {
    const trace = JSON.parse(readFileSync('/home/dalab/audits/project-management-debloat/mock-work-journey/http-trace.json', 'utf8'));
    const returned = trace.find((step: { step: string }) => step.step === 'Reopen populated Project summary').response;
    const summary = normalizeProjectManagerReadModel(returned);
    expect(summary.project.name).toBe('Hermes smoke test — mock work only');
    expect(summary.selection.title).toBe('Reviewed mock folding work');
    expect(summary.tasks).toHaveLength(1);
    expect(summary.runs.items).toEqual([]);
    expect(summary.tree.nodes.find(node => node.node_key === summary.selection.node_key)?.label).toBe('Reviewed mock folding work');
    expect(summary.map.nodes.find(node => node.node_key === summary.selection.node_key)?.label).toBe('Reviewed mock folding work');
});
