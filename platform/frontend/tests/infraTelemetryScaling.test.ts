import assert from 'node:assert/strict';
import test from 'node:test';

import {
    resolveCpuFrequencyScaleMhz,
    resolveCpuPowerScaleWatts,
    roundUpToStep,
} from '../src/components/infraTelemetryScaling.js';

test('rounds positive values up to the requested step size', () => {
    assert.equal(roundUpToStep(5489.764, 250), 5500);
    assert.equal(roundUpToStep(287.5, 50), 300);
    assert.equal(roundUpToStep(150, 25), 150);
});

test('keeps Threadripper package power from self-normalizing to current idle draw', () => {
    const cpu = {
        name: 'AMD Ryzen Threadripper 9960X 24-Cores',
        cores_logical: 48,
        power_watts: 62,
        frequency_max_mhz: 5489.764,
    };
    const samples = [
        { cpuPower: 62, cpuFreqMhz: 2150 },
        { cpuPower: 92, cpuFreqMhz: 5000 },
    ];

    const scale = resolveCpuPowerScaleWatts(cpu, samples);

    assert.equal(scale, 150);
    assert.equal(Number(((62 / scale!) * 100).toFixed(1)), 41.3);
    assert.equal(Number(((92 / scale!) * 100).toFixed(1)), 61.3);
});

test('uses a high-core CPU wattage floor even when only the current sample exists', () => {
    const scale = resolveCpuPowerScaleWatts({
        name: 'AMD Ryzen Threadripper 9960X 24-Cores',
        cores_logical: 48,
        power_watts: 92,
        frequency_max_mhz: 5489.764,
    }, []);

    assert.equal(scale, 150);
});

test('expands CPU wattage scale with visible high-load history and rounds to a nice value', () => {
    const scale = resolveCpuPowerScaleWatts({
        name: 'AMD Ryzen Threadripper 9960X 24-Cores',
        cores_logical: 48,
        power_watts: 130,
        frequency_max_mhz: 5489.764,
    }, [
        { cpuPower: 130, cpuFreqMhz: 4400 },
        { cpuPower: 230, cpuFreqMhz: 5000 },
    ]);

    assert.equal(scale, 300);
});

test('uses a lower wattage floor for ordinary desktop CPUs', () => {
    const scale = resolveCpuPowerScaleWatts({
        name: 'AMD Ryzen 7 7700X 8-Core Processor',
        cores_logical: 16,
        power_watts: 40,
        frequency_max_mhz: 5400,
    }, [
        { cpuPower: 40, cpuFreqMhz: 3200 },
    ]);

    assert.equal(scale, 75);
});

test('normalizes CPU frequency against a rounded reported max frequency', () => {
    const scale = resolveCpuFrequencyScaleMhz({
        name: 'AMD Ryzen Threadripper 9960X 24-Cores',
        cores_logical: 48,
        power_watts: 62,
        frequency_max_mhz: 5489.764,
    }, [
        { cpuPower: 62, cpuFreqMhz: 2150 },
    ]);

    assert.equal(scale, 5500);
});

test('falls back to rounded observed frequency when reported CPU max is missing', () => {
    const scale = resolveCpuFrequencyScaleMhz({
        name: 'Unknown CPU',
        cores_logical: 8,
        power_watts: 32,
        frequency_max_mhz: 0,
    }, [
        { cpuPower: 32, cpuFreqMhz: 4820 },
    ]);

    assert.equal(scale, 5000);
});
