import { describe, expect, it } from 'vitest';
import { ngsArtifactUrl, type NgsPackageArtifact } from '../../src/lib/ngsArtifacts';
const artifact = (filename: string, id = 'a'): NgsPackageArtifact => ({ filename, artifact_id: id, kind: 'report', source: 'native', state: 'present', size_bytes: 10, url: `/api/jobs/job/ngs-artifacts/${id}` });
describe('native public artifact URL resolution', () => {
    it('uses only the catalog-owned URL for absolute and relative native paths', () => {
        const a = artifact('methylation/modkit_summary.tsv');
        for (const path of ['/scratch/bms_results/job/methylation/modkit_summary.tsv', 'bms_results/job/methylation/modkit_summary.tsv', a.filename!, a.url!]) expect(ngsArtifactUrl(path, 'job', [a])).toBe(a.url);
    });
    it('supports a basename-only public catalog', () => {
        const a = artifact('modkit_summary.tsv');
        expect(ngsArtifactUrl('bms_results/job/methylation/modkit_summary.tsv', 'job', [a])).toBe(a.url);
    });
    it('does not advertise unknown, unavailable, generic, external or other-job URLs', () => {
        const a = artifact('modkit_summary.tsv');
        expect(ngsArtifactUrl(a.filename!, 'job', [])).toBeNull();
        for (const url of ['/api/files/download/file', '/api/jobs/other/ngs-artifacts/a', 'https://example.org/file']) expect(ngsArtifactUrl(a.filename!, 'job', [{ ...a, url }])).toBeNull();
        expect(ngsArtifactUrl(a.filename!, 'job', [{ ...a, state: 'unavailable' }])).toBeNull();
    });
    it('does not confuse duplicated basenames but retains exact native path matches', () => {
        const a = artifact('first/summary.tsv'); const b = artifact('second/summary.tsv', 'b');
        expect(ngsArtifactUrl('summary.tsv', 'job', [a, b])).toBeNull();
        expect(ngsArtifactUrl('bms_results/job/first/summary.tsv', 'job', [a, b])).toBe(a.url);
    });
});
