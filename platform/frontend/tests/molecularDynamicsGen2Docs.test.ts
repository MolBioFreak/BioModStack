import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it } from 'node:test';

const docs = (name: string) => readFileSync(resolve(process.cwd(), '../../docs', name), 'utf8');

describe('Molecular Dynamics operator documentation', () => {
    it('documents immutable starting-structure inspection, prediction handoff, preview, and typed launch identity', () => {
        const md = docs('Molecular_Dynamics_Suite.md');
        for (const phrase of [
            'Molecular Dynamics launcher',
            'bms.md.launch-intent.v1',
            'bms.md.launch-preview-request.v1',
            'bms.md.launch-request.v1',
            'effective request digest',
            'Prediction-result handoff',
            'prior_md_input',
            'browser never submits a host filesystem path',
        ]) assert.match(md, new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), phrase);
    });

    it('documents fixed, recommended, operator-owned, and scheduler-owned authority without inventing hidden controls', () => {
        const policy = docs('Model_Configuration_Operator_Control_and_Agent_Parity.md');
        for (const phrase of [
            'fixed by the selected profile',
            'recommended default',
            'operator-owned',
            'scheduler-owned',
            'requested settings',
            'effective request',
            'preview digest',
        ]) assert.match(policy, new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), phrase);
    });

    it('keeps the prepared-system compatibility boundary explicit', () => {
        const md = docs('Molecular_Dynamics_Suite.md');
        assert.match(md, /prepared-system compatibility lane/i);
        assert.match(md, /OpenMM 8\.5\.2/i);
        assert.match(md, /does not use the typed automatic-preparation route/i);
    });
});
