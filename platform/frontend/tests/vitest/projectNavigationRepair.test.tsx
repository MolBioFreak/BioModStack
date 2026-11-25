import { expect, test } from 'vitest';
import { projectResultHref } from '../../src/lib/projectManager';

test('receipt route excludes scheduler descendants and retains all PM return filters', () => {
    const return_uri = '/projects/p?focus=g&selected=external_entity_receipt%3Ar&view=records&candidate_source=s&candidate_search=e8db269a';
    const route = projectResultHref(
        { template_id: 'bms.route.verified-external-entity.v1', path: '/designs/monomer', query: { include_children: 'true' } },
        { project_id: 'p', global_experiment_id: 'g', domain_experiment_id: 'd', global_experiment_revision_id: 'gr', domain_revision_id: 'dr', return_uri },
    );
    const parsed = new URL(route, window.location.origin);
    expect(parsed.pathname).toBe('/designs/monomer');
    expect(parsed.searchParams.get('include_children')).toBe('false');
    expect(parsed.searchParams.get('return_uri')).toBe(return_uri);
});

test('native produced attachment result is accepted by the PM read-model consumer', async () => {
    const { parseResultSurface } = await import('../../src/lib/projectManager');
    const surface = {
        schema: 'bms.result-surface.v1',
        receipt_id: 'r',
        entity_kind: 'typed_core_job_result',
        entity_id: 'monomer',
        contract_id: 'typed_core_job_result_v1',
        content_digest: 'a'.repeat(64),
        surface_kind: 'protein_design',
        route: { template_id: 'bms.route.verified-external-entity.v1', path: '/designs/monomer', query: {} },
        readiness: 'ready',
        native_summary: { schema_id: 'bms.result-surface.native-summary.v1', content_sha256: 'a'.repeat(64), canonical_size_bytes: 2, payload: {} },
        provenance: { schema_id: 'bms.result-surface.provenance.v1', content_sha256: 'a'.repeat(64), canonical_size_bytes: 2, payload: {} },
        scientific_acceptance: { state: 'review', reason: null },
        comparison: { state: 'unavailable', reason: null, authority: null },
        available_actions: ['open'],
        lineage_edge_key: 'attachment:bms.core-job.protenix.adapter.v1:090303b9-59dc-424d-bbd7-c74cc63ecf1d:produced',
    };
    expect(parseResultSurface(surface).lineage_edge_key).toBe(surface.lineage_edge_key);
    expect(() => parseResultSurface({ ...surface, lineage_edge_key: 'invalid' })).toThrow();
});
