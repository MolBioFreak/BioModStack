import { describe, expect, it } from 'vitest';
import * as adapter from '../../src/lib/scientificViewerIdentity';

import {document, fixture} from '../fixtures/scientificViewerFixture';

describe('API native identity parser', () => {
    it('preserves native T/H/L direction, full insertion codes, and absent label IDs', () => {
        expect(typeof adapter.parseScientificPae).toBe('function');
        const parsed = adapter.parseScientificPae(fixture(), document);
        expect(parsed.status).toBe('ok');
        if (parsed.status !== 'ok') return;
        expect(parsed.rows.map(r => r.authAsymId)).toEqual(['T','H','H','H','L']);
        expect(parsed.rows.map(r => r.insertionCode)).toEqual(['','','A','B','']);
        expect(parsed.rows[1].labelSeqId).toBeUndefined();
        expect(parsed.matrix[4][0]).toBe(20);
    });
    it.each(['unknown', 'bool', 'null', 'nonfinite', 'sorted', 'hash', 'downsample'])('rejects %s contradictions', mode => {
        const raw: any = fixture();
        if (mode === 'unknown') raw.extra = true;
        if (mode === 'bool') raw.pae_matrix[0][0] = true;
        if (mode === 'null') raw.pae_matrix[0][0] = null;
        if (mode === 'nonfinite') raw.pae_matrix[0][0] = Infinity;
        if (mode === 'sorted') raw.row_axis.residues = raw.row_axis.residues.sort((a: any,b: any) => a.chain_id.localeCompare(b.chain_id)).map((r: any,index: number) => ({...r,index}));
        if (mode === 'hash') raw.document = {...document, contentSha256:'c'.repeat(64)};
        if (mode === 'downsample') raw.sampled_row_indices = null;
        expect(adapter.parseScientificPae(raw, document).status).toBe('unavailable');
    });
});
