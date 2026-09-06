import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { focusManager, onlineManager } from '@tanstack/react-query';
import { useTelemetryChartRefresh } from '../src/components/useTelemetryChartRefresh';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

test('refresh hook follows completion, retries, visibility, offline, range changes and unmount', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 100_000 });
    focusManager.setFocused(true);
    onlineManager.setOnline(true);
    let requests = 0;
    const query = {
        fetchStatus: 'idle' as 'idle' | 'fetching' | 'paused',
        dataUpdatedAt: 100_000, errorUpdatedAt: 0, isError: false, failureCount: 0,
        refetch: async () => { requests++; },
    };
    let enabled = true;
    let interval = 5_000;
    let identity = 10;
    let label = '';
    function Probe() {
        label = useTelemetryChartRefresh(query, enabled, interval, identity);
        return React.createElement('span', null, label);
    }
    let root: ReactTestRenderer;
    const render = async () => { await act(async () => root.update(React.createElement(Probe))); };
    const tick = async (ms: number) => { await act(async () => t.mock.timers.tick(ms)); };
    await act(async () => { root = create(React.createElement(Probe)); });
    try {
        assert.equal(label, 'Next chart refresh in 5s');
        await tick(1_000);
        assert.equal(label, 'Next chart refresh in 4s');
        await tick(4_000);
        assert.equal(requests, 1);
        query.fetchStatus = 'fetching'; await render();
        assert.equal(label, 'Updating chart…');
        await tick(30_000);
        assert.equal(requests, 1, 'slow requests must not overlap');
        query.failureCount = 1; await render();
        assert.equal(label, 'Retrying chart refresh…');
        query.fetchStatus = 'idle'; query.failureCount = 0;
        query.dataUpdatedAt = Date.now(); await render();
        assert.equal(label, 'Next chart refresh in 5s', 'new deadline starts after completion');
        await act(async () => focusManager.setFocused(false));
        assert.equal(label, 'Chart refresh paused');
        await tick(30_000); assert.equal(requests, 1);
        await act(async () => focusManager.setFocused(true));
        assert.equal(label, 'Next chart refresh in 5s');
        await act(async () => onlineManager.setOnline(false));
        await tick(30_000); assert.equal(requests, 1);
        assert.equal(label, 'Chart refresh paused');
        await act(async () => onlineManager.setOnline(true));
        query.fetchStatus = 'paused'; await render();
        assert.equal(label, 'Chart refresh paused');
        query.fetchStatus = 'idle'; query.isError = true; query.errorUpdatedAt = Date.now(); await render();
        assert.equal(label, 'Chart refresh failed · retry in 5s');
        interval = 30_000; identity = 60; query.isError = false; await render();
        assert.equal(label, 'Next chart refresh in 30s');
        await tick(5_000); assert.equal(requests, 1, 'old range timer was cancelled');
        enabled = false; await render();
        assert.equal(label, '');
        await tick(30_000); assert.equal(requests, 1, 'short windows have no second polling owner');
        enabled = true; await render();
    } finally {
        await act(async () => root.unmount());
        focusManager.setFocused(undefined);
        onlineManager.setOnline(true);
    }
    await tick(30_000); assert.equal(requests, 1, 'unmount cancels the timer');
});
