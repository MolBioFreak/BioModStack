import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { BioXpQuickDashboard } from '../src/components/BioXpQuickDashboard.js';

// tsx's node-test path uses classic JSX; the production Vite build uses automatic JSX.
Object.assign(globalThis, { React });

const render = (props: Partial<React.ComponentProps<typeof BioXpQuickDashboard>> = {}) => renderToStaticMarkup(
    <BioXpQuickDashboard connected data={undefined} isLoading={false} error={null} motionControlsAvailable={undefined} {...props} />,
);

test('null telemetry has a settled unknown explanation rather than a blank or updating dashboard', () => {
    assert.match(render(), /Robot did not report telemetry; motion availability is unknown/);
    assert.doesNotMatch(render(), /Updating|Loading live state/);
    assert.match(render({ unavailableReason: 'Observation expired' }), /Observation expired/);
});

test('dashboard loading, disconnected and error states remain distinct', () => {
    assert.match(render({ isLoading: true }), /Loading live state/);
    assert.doesNotMatch(render({ isLoading: true }), /Robot did not report telemetry/);
    assert.match(render({ connected: false }), /Connect to view robot state/);
    const failed = render({ error: { response: { data: { detail: 'invalid operator-control contract' } } } });
    assert.match(failed, /Dashboard unavailable: invalid operator-control contract/);
    assert.doesNotMatch(failed, /Robot did not report telemetry|\[object Object\]/);
});
