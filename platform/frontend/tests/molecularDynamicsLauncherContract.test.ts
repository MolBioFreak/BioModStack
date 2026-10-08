import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

const frontendRoot = process.cwd();
const source = (relativePath: string) => fs.readFileSync(path.join(frontendRoot, 'src', relativePath), 'utf8');

describe('first-class molecular dynamics launcher', () => {
    it('is routed as a dedicated workflow surface', () => {
        const state = source('components/jobSubmissionTemplateState.ts');
        const submission = source('components/JobSubmission.tsx');
        assert.equal(state.includes("'molecular_dynamics'"), true);
        assert.equal(submission.includes("import { MolecularDynamicsTemplate } from './MolecularDynamicsTemplate'"), true);
        assert.equal(submission.includes("selectedTemplateId === 'molecular_dynamics'"), true);
        assert.equal(submission.includes('<MolecularDynamicsTemplate'), true);
    });

    it('exposes useful scientific, replica, cadence, and checkpoint controls', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        for (const label of [
            'Starting system',
            'Engine & replicas',
            'Preparation & ensemble',
            'Production & output cadence',
            'Checkpoint interval',
            'Aggregate simulation',
        ]) {
            assert.equal(launcher.includes(label), true, label);
        }
        assert.equal(launcher.includes('md_job_spec'), true);
        assert.equal(launcher.includes("model_id: 'molecular_dynamics'"), true);
        assert.equal(launcher.includes("mode: 'simulate'"), true);
    });

    it('ships smoke-safe defaults and no hidden broad maxima for the selected smoke profile', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        assert.match(launcher, /replicas:\s*1/u);
        assert.match(launcher, /productionNs:\s*0\.001/u);
        assert.match(launcher, /temperatureK:\s*300/u);
        assert.match(launcher, /pressureBar:\s*1/u);
        assert.match(launcher, /saltMolar:\s*0\.15/u);
        assert.match(launcher, /paddingNm:\s*1/u);
        assert.doesNotMatch(launcher, /max=\{16\}|max=\{10000\}|max=\{500\}|max=\{100\}|max=\{5\}/u);
    });

    it('clearly labels automatic preparation as exact 1AKI smoke-only and not production science', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        assert.match(launcher, /exact 1AKI.*smoke-only/is);
        assert.match(launcher, /not (?:validated for |intended for )?production science/is);
    });
});
