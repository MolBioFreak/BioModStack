import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { bioXpReceiptFailureText } from '../src/lib/bioxpEvidencePresentation.js';

const receipt = (name = 'compact') => JSON.parse(readFileSync(new URL(`./fixtures/bioxp_xy_y5_${name}.json`, import.meta.url), 'utf8'));
const explanation = 'Move timeout reported. Recorded stopped position: X85000, Y5 (requested X85000, Y0). Past receipt only; not current position or readiness. Source result remains failed.';
for (const name of ['compact', 'detail', 'legacy']) {
    test(`actual saved Y5 ${name}: reports source timeout without rewriting old receipt`, () => {
        const raw = receipt(name);
        const before = structuredClone(raw);
        if (name === 'legacy') assert.equal(raw.error, 'Robot route reported an HTTP conflict.');
        else assert.equal(raw.error.code, 'route_http_conflict');
        assert.equal(bioXpReceiptFailureText(raw), explanation);
        assert.deepEqual(raw, before);
    });
}
for (const state of ['ambiguous', 'completed', 'interrupted', 'stopped']) {
    test(`does not reinterpret ${state} as a failed stopped timeout`, () => {
        const raw = receipt(); raw.status = state;
        assert.equal(bioXpReceiptFailureText(raw), raw.error.message);
    });
}
for (const fault of ['no24v', 'other-failure', 'missing-wait']) {
    test(`preserves ordinary source failure for ${fault}`, () => {
        const raw = receipt();
        if (fault === 'no24v') raw.xy_failure.controller_failure.wait.no24v = true;
        if (fault === 'other-failure') raw.xy_failure.controller_failure.wait.failure = 'controller_fault';
        if (fault === 'missing-wait') delete raw.xy_failure.controller_failure.wait;
        assert.equal(bioXpReceiptFailureText(raw), raw.error.message);
    });
}
for (const fault of ['unknown', 'missing', 'invalid', 'nonfinite']) {
    test(`does not claim stopped positions with ${fault} retained evidence`, () => {
        const raw = receipt();
        const terminal = raw.xy_failure.terminal_classification;
        if (fault === 'unknown') terminal.classification = 'unknown';
        if (fault === 'missing') delete terminal.readbacks;
        if (fault === 'invalid') terminal.readbacks.y.position.position_reply_valid = false;
        if (fault === 'nonfinite') terminal.readbacks.y.position.position = Infinity;
        assert.equal(bioXpReceiptFailureText(raw), 'Move timeout reported. Stopped position is not established in this receipt. Past receipt only; not current position or readiness. Source result remains failed.');
    });
}
test('does not apply an endpoint tolerance or require a new backend error message', () => {
    const raw = receipt();
    raw.xy_failure.terminal_classification.readbacks.y.position.position = 12345;
    raw.error.message = 'new backend source timeout message';
    assert.match(bioXpReceiptFailureText(raw)!, /Y12345 \(requested X85000, Y0\)/);
});
