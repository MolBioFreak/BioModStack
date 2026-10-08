import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const panel = readFileSync(new URL('../src/components/MolBioToolkit/panels/AssemblyPanel.tsx', import.meta.url), 'utf8');
const workspacePath = new URL('../src/components/MolBioToolkit/panels/GibsonDesignWorkspace.tsx', import.meta.url);
const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');

test('Gibson defaults to target-first DNA Weaver planning while retaining validation and optional PCR', () => {
    assert.match(panel, /Plan vendor fragments \(DNA Weaver\)/);
    assert.match(panel, /Validate purchased fragments/);
    assert.match(panel, /PCR template route \(optional\)/);
    assert.match(panel, /Exact purchase sequence and source-core interval/);
});

test('DNA Weaver plan exposes constraints, QC, exports, and server-authoritative save', () => {
    assert.match(panel, /Minimum core bp/);
    assert.match(panel, /quality_checks/);
    assert.match(panel, /Export order FASTA/);
    assert.match(panel, /Export order CSV/);
    assert.match(panel, /sequence_sha256=/);
    assert.match(panel, /setDnaWeaverPlan\(null\);/);
    assert.match(panel, /saveDnaWeaverGibsonAssembly/);
    assert.match(panel, /selected_plan_checksum/);
    assert.match(panel, /Regenerate \+ Verify \+ Save/);
    assert.match(api, /\/api\/molbio\/assembly\/gibson\/dnaweaver\/save/);
    assert.match(api, /plan_checksum/);
});

test('assembly payload topology follows the current construct instead of forcing circular', () => {
    const occurrences = panel.match(/circular: sequenceData\.circular/g) || [];
    assert.ok(occurrences.length >= 4, `expected topology propagation in planner and three assembly modes, found ${occurrences.length}`);
    assert.doesNotMatch(panel, /circular: true/);
});

test('Gibson design workspace exposes design, primer review, preview, and explicit save', () => {
    const workspace = readFileSync(workspacePath, 'utf8');
    assert.match(workspace, /Design & Simulate/);
    assert.match(workspace, /Generated primers/);
    assert.match(workspace, /Load preview/);
    assert.match(workspace, /Save as new construct/);
    assert.match(workspace, /selected_candidate_checksum/);
    assert.match(workspace, /initialCircular/);
    assert.match(workspace, /requestScopeRef\.current === scope/);
    assert.match(workspace, /primers: result\.primers\.map/);
    assert.match(workspace, /Array\.isArray\(detail\)/);
});

test('late DNA Weaver responses cannot republish a plan after the target or constraints change', () => {
    assert.match(panel, /plannerScopeRef\.current !== scope/);
    assert.match(panel, /\[plannerScope\]/);
    assert.match(panel, /setPlanning\(false\)/);
});

test('saved Gibson constructs expose persisted design and vendor-order evidence in Assembly', () => {
    assert.match(panel, /Saved Gibson workup/);
    assert.match(panel, /Server-selected candidate checksum/);
    assert.match(panel, /DNA Weaver plan checksum/);
    assert.match(panel, /operationParams\.ordered_fragments/);
    assert.match(panel, /Validated junctions/);
    assert.match(panel, /Generated primers/);
});
