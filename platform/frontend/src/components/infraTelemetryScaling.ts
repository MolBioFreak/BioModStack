export interface CpuTelemetryScaleStatus {
    name?: string | null;
    cores_logical?: number | null;
    frequency_max_mhz?: number | null;
    power_watts?: number | null;
}

export interface CpuTelemetryScaleSample {
    cpuFreqMhz?: number | null;
    cpuPower?: number | null;
}

const HIGH_CORE_CPU_LOGICAL_CORE_FLOOR = 32;
const HIGH_CORE_CPU_POWER_FLOOR_WATTS = 150;
const DEFAULT_CPU_POWER_FLOOR_WATTS = 75;
const CPU_POWER_SCALE_HEADROOM = 1.25;
const CPU_POWER_FINE_STEP_WATTS = 25;
const CPU_POWER_COARSE_STEP_WATTS = 50;
const CPU_POWER_COARSE_STEP_THRESHOLD_WATTS = 250;
const CPU_FREQUENCY_STEP_MHZ = 250;

export function roundUpToStep(value: number, step: number): number {
    if (!Number.isFinite(value) || !Number.isFinite(step) || step <= 0) return 0;
    return Math.ceil(value / step) * step;
}

function finitePositive(value: unknown): number | null {
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
    return value;
}

function isHighCoreCpu(cpu: CpuTelemetryScaleStatus): boolean {
    const logicalCores = finitePositive(cpu.cores_logical) ?? 0;
    const cpuName = cpu.name ?? '';
    return (
        logicalCores >= HIGH_CORE_CPU_LOGICAL_CORE_FLOOR ||
        /\b(threadripper|epyc|xeon)\b/i.test(cpuName)
    );
}

function maxFinitePositive(values: Array<number | null | undefined>): number | null {
    const finiteValues = values
        .map(finitePositive)
        .filter((value): value is number => value != null);
    if (finiteValues.length === 0) return null;
    return Math.max(...finiteValues);
}

export function resolveCpuPowerScaleWatts(
    cpu: CpuTelemetryScaleStatus,
    samples: CpuTelemetryScaleSample[],
): number | null {
    const observedPowerMax = maxFinitePositive([
        cpu.power_watts,
        ...samples.map((sample) => sample.cpuPower),
    ]);
    if (observedPowerMax == null) return null;

    const floorWatts = isHighCoreCpu(cpu)
        ? HIGH_CORE_CPU_POWER_FLOOR_WATTS
        : DEFAULT_CPU_POWER_FLOOR_WATTS;
    const targetWatts = Math.max(floorWatts, observedPowerMax * CPU_POWER_SCALE_HEADROOM);
    const stepWatts = targetWatts <= CPU_POWER_COARSE_STEP_THRESHOLD_WATTS
        ? CPU_POWER_FINE_STEP_WATTS
        : CPU_POWER_COARSE_STEP_WATTS;

    return roundUpToStep(targetWatts, stepWatts);
}

export function resolveCpuFrequencyScaleMhz(
    cpu: CpuTelemetryScaleStatus,
    samples: CpuTelemetryScaleSample[],
): number {
    const observedFrequencyMax = maxFinitePositive(samples.map((sample) => sample.cpuFreqMhz));
    const reportedFrequencyMax = finitePositive(cpu.frequency_max_mhz);
    const rawScale = Math.max(reportedFrequencyMax ?? 0, observedFrequencyMax ?? 0);
    return Math.max(1, roundUpToStep(rawScale || 1, CPU_FREQUENCY_STEP_MHZ));
}
