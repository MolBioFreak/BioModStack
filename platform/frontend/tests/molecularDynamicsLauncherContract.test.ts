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

    it('exposes inspected-source, typed scientific, replica, cadence, and checkpoint controls', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        for (const label of [
            'Choose a starting structure',
            'Typed scientific controls',
            'Chemistry profile',
            'Independent replicas',
            'Base random seed',
            'Production per replica',
            'Neutralize system',
            'Checkpoint interval',
            'Aggregate simulation',
            'Preview effective request',
            'Effective JSON',
        ]) {
            assert.equal(launcher.includes(label), true, label);
        }
        assert.equal(launcher.includes("'/api/molecular-dynamics/launch-preview'"), true);
        assert.equal(launcher.includes("'/api/molecular-dynamics/launch'"), true);
        assert.equal(launcher.includes("schema_version: 'bms.md.launch-request.v1'"), true);
    });

    it('ships smoke-safe defaults and no hidden broad maxima for the selected smoke profile', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        assert.match(launcher, /replicas:\s*1/u);
        assert.match(launcher, /productionNs:\s*0\.001/u);
        assert.match(launcher, /temperatureK:\s*300/u);
        assert.match(launcher, /pressureBar:\s*1/u);
        assert.match(launcher, /saltMolar:\s*0\.15/u);
        assert.match(launcher, /paddingNm:\s*1/u);
        assert.match(launcher, /trajectoryIntervalPs:\s*1(?:\.0)?\s*,/u);
        assert.match(launcher, /energyIntervalPs:\s*0\.2\s*,/u);
        assert.doesNotMatch(launcher, /max=\{16\}|max=\{10000\}|max=\{500\}|max=\{100\}|max=\{5\}/u);
    });

    it('labels the accepted 1AKI lane without broadening it into general production science', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        assert.match(launcher, /RCSB 1AKI exact product bytes/is);
        assert.match(launcher, /limited to the profile's declared validation scope/is);
        assert.match(launcher, /scientific campaigns require separate qualification/is);
        assert.doesNotMatch(launcher, /long-timescale (?:production )?science is validated/is);
    });

    it('does not offer prepared GROMACS and explains the OpenMM-only prepared lane', () => {
        const launcher = source('components/MolecularDynamicsTemplate.tsx');
        assert.match(launcher, /disabled=\{mode === 'prepared' && form\.engine !== 'openmm'\}/u);
        assert.match(launcher, /Prepared systems are supported only by OpenMM/i);
        assert.match(launcher, /<option value="gromacs" disabled=\{form\.inputMode === 'prepared'\}>/u);
    });
});
