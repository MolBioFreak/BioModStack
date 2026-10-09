import { describe, expect, it } from 'vitest';
import * as adapter from '../../src/lib/scientificViewerIdentity';

import { esmfold2Fixture } from '../fixtures/scientificViewerEsmfold2Fixture';
import {document, fixture} from '../fixtures/scientificViewerFixture';

describe('ESMFold2 native axes', () => {
    it('retains collapsed CIF identity and fractions without token-mean substitution', () => {
        const f = esmfold2Fixture();
        const p = adapter.parseScientificNativeMetric(f.confidence, f.document, 'residue_plddt');
        expect(p.status).toBe('ok');
        if (p.status !== 'ok') return;
        expect(p.values).toEqual([.005,.8,.4]);
        expect(p.residues.map(r => [r.authAsymId,r.labelAsymId,r.authSeqId,r.labelSeqId,r.insertionCode])).toEqual([
            ['A','X',42,7,'A'], ['B','Y',42,1,''], ['L','Z',1,undefined,'']]);
        expect(p.confidenceSource).toEqual({producerVersion:f.confidence.axis.producer_version,scope:'collapsed_residue',storedUnits:'percent'});
    });
    it.each(['scope','units','producer','extra','partial','hash','candidate','position','identity','fraction','atom'])('rejects collapsed confidence %s contradictions', mode => {
        const f = esmfold2Fixture(), p: any = f.confidence;
        if (mode === 'scope') p.axis.confidence_scope = 'model_token_mean';
        if (mode === 'units') p.axis.stored_units = 'fraction';
        if (mode === 'producer') p.axis.producer_version = '';
        if (mode === 'extra') p.axis.extra = true;
        if (mode === 'partial') delete p.axis.stored_units;
        if (mode === 'hash') p.axis.source_sha256 = 'd'.repeat(64);
        if (mode === 'candidate') p.design_id = 'other';
        if (mode === 'position') p.native_positions = [2,1,0];
        if (mode === 'identity') p.axis.residues[0].chain_id = 'B';
        if (mode === 'fraction') p.values[1] = 80;
        if (mode === 'atom') p.metric = 'atom_plddt';
        expect(adapter.parseScientificNativeMetric(p,f.document,'residue_plddt').status).toBe('unavailable');
    });
    it('preserves sampled asymmetric token PAE and full ledgers without spatial identities', () => {
        const f = esmfold2Fixture();
        f.pae.sampled_row_indices = [0,4]; f.pae.sampled_column_indices = [0,4];
        f.pae.pae_matrix = [[0,2],[10,12]]; f.pae.size = 2;
        const p = adapter.parseScientificPae(f.pae,f.document);
        expect(p.status).toBe('ok');
        if (p.status !== 'ok' || p.axisKind !== 'model_token') throw Error('missing native tokens');
        expect(p.matrix).toEqual([[0,2],[10,12]]);
        expect(p.rowAxis).toEqual(f.pae.row_axis); expect(p.columnAxis).toEqual(f.pae.column_axis);
        expect(p.nativeShape).toEqual([5,5]); expect(p.nativeRowPositions).toEqual([0,1,2,3,4]);
        expect(p.sampledRowIndices).toEqual([0,4]); expect(p.sampledColumnIndices).toEqual([0,4]);
        expect(p.rowTokens.map(t=>t.index)).toEqual([0,4]); expect(p.columnTokens.map(t=>t.index)).toEqual([0,4]);
        expect(p.rows).toEqual([]); expect(p.columns).toEqual([]);
    });
    it('accepts null advisory metadata without guessing token identity', () => {
        const f = esmfold2Fixture(), p: any = f.pae;
        for (const axis of [p.row_axis,p.column_axis]) for (const token of axis.tokens) { token.residue_index = null; token.entity_id = null; }
        expect(adapter.parseScientificPae(p,f.document).status).toBe('ok');
    });
    it.each(['mixed','extra','tokenExtra','missing','orientation','mapping','hash','binding','candidate','document','ledger','duplicate','metadata','boolean','sampling','dimension','matrix'])('rejects token %s contradictions', mode => {
        const f = esmfold2Fixture(), p: any = f.pae;
        if (mode === 'mixed') p.column_axis = fixture().column_axis;
        if (mode === 'extra') p.row_axis.residues = [];
        if (mode === 'tokenExtra') p.row_axis.tokens[0].chain_id = 'A';
        if (mode === 'missing') delete p.row_axis.producer_version;
        if (mode === 'orientation') p.row_axis.orientation = 'transposed';
        if (mode === 'mapping') p.row_axis.mapping_reason = 'mapped';
        if (mode === 'hash') p.row_axis.source_sha256 = 'd'.repeat(64);
        if (mode === 'binding') p.column_axis.candidate_id = 'other';
        if (mode === 'candidate') p.design_id = 'other';
        if (mode === 'document') p.document = {...f.document,documentId:'other'};
        if (mode === 'ledger') p.native_row_positions = [4,3,2,1,0];
        if (mode === 'duplicate') p.row_axis.tokens[1].index = 0;
        if (mode === 'metadata') p.column_axis.tokens[0].entity_id = 9;
        if (mode === 'boolean') p.row_axis.tokens[0].residue_index = false;
        if (mode === 'sampling') p.sampled_row_indices = [0,0,2,3,4];
        if (mode === 'dimension') p.native_shape = [3,3];
        if (mode === 'matrix') p.pae_matrix[0][1] = NaN;
        expect(adapter.parseScientificPae(p,f.document).status).toBe('unavailable');
    });
});

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
