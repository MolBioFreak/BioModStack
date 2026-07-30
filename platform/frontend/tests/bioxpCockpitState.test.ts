import assert from 'node:assert/strict';
import test from 'node:test';

import { deriveCockpitMutationState, type MutationSnapshot } from '../src/components/bioxpCockpitState';

interface Result {
    detail: string;
    remote_acknowledged: boolean;
}

const mutation = (overrides: Partial<MutationSnapshot<Result>> = {}): MutationSnapshot<Result> => ({
    data: undefined,
    error: null,
    isPending: false,
    submittedAt: 0,
    ...overrides,
});

test('newer command results replace retained stop receipts', () => {
    const state = deriveCockpitMutationState({
        execute: mutation({ data: { detail: 'move accepted', remote_acknowledged: true }, submittedAt: 20 }),
        stop: mutation({ data: { detail: 'old stop accepted', remote_acknowledged: true }, submittedAt: 10 }),
        emergency: mutation(),
    });
    assert.equal(state.latestResult?.detail, 'move accepted');
    assert.equal(state.latestError, null);
});

test('emergency stop success and failure become the latest visible outcome', () => {
    const failure = new Error('emergency delivery failed');
    const failed = deriveCockpitMutationState({
        execute: mutation({ data: { detail: 'old move', remote_acknowledged: true }, submittedAt: 10 }),
        stop: mutation(),
        emergency: mutation({ error: failure, submittedAt: 30 }),
    });
    assert.equal(failed.latestResult, undefined);
    assert.equal(failed.latestError, failure);

    const succeeded = deriveCockpitMutationState({
        execute: mutation(),
        stop: mutation({ data: { detail: 'old stop', remote_acknowledged: true }, submittedAt: 20 }),
        emergency: mutation({ data: { detail: 'emergency acknowledged', remote_acknowledged: true }, submittedAt: 40 }),
    });
    assert.equal(succeeded.latestResult?.detail, 'emergency acknowledged');
    assert.equal(succeeded.latestError, null);
});

test('stop in flight blocks normal commands while stop can still preempt a normal command', () => {
    const stopping = deriveCockpitMutationState({
        execute: mutation(),
        stop: mutation({ isPending: true, submittedAt: 10 }),
        emergency: mutation(),
    });
    assert.equal(stopping.normalCommandBlocked, true);
    assert.equal(stopping.stopBlocked, true);

    const moving = deriveCockpitMutationState({
        execute: mutation({ isPending: true, submittedAt: 20 }),
        stop: mutation(),
        emergency: mutation(),
    });
    assert.equal(moving.normalCommandBlocked, true);
    assert.equal(moving.stopBlocked, false);
});
