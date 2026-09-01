import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const productionGpuControlFiles = [
    'src/components/NanoporeTemplate.tsx',
    'src/components/StructurePredictionTemplate.tsx',
    'src/components/dashboard/StructureReorchestratePanel.tsx',
    'src/components/dashboard/reorchestrateStructureSettings.ts',
    'src/components/dashboard/SystemResources.tsx',
];

const localRigMarkers = [
    '0,1,2,3',
    'GPU_SPECS',
    'GPU_NAMES',
    'RTX 5090',
    'RTX 5060 Ti',
    'RTX 3090',
    "name: '5090'",
    "name: '5060Ti'",
    "name: '3090#1'",
    "name: '3090#2'",
];

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

test('production GPU controls do not expose DALAB-specific GPU labels or VRAM spec constants', () => {
    for (const relativePath of productionGpuControlFiles) {
        const source = readSource(relativePath);
        for (const marker of localRigMarkers) {
            assert.equal(
                source.includes(marker),
                false,
                `${relativePath} should not contain local GPU marker ${marker}`,
            );
        }
    }
});

test('template GPU pinning surfaces are wired to the live GPU catalog helper', () => {
    const pinningFiles = [
            'src/components/NanoporeTemplate.tsx',
        'src/components/StructurePredictionTemplate.tsx',
        'src/components/dashboard/StructureReorchestratePanel.tsx',
    ];

    for (const relativePath of pinningFiles) {
        const source = readSource(relativePath);
        assert.match(source, /useLiveGpuCatalog/u, `${relativePath} should use live GPU metadata`);
        assert.match(source, /gpuOptions\.map/u, `${relativePath} should render live GPU options`);
    }
});

test('scheduler controls derive labels, VRAM bounds, and power totals from discovered metadata', () => {
    const source = readSource('src/components/dashboard/SystemResources.tsx');

    assert.match(source, /buildGpuCatalog/u);
    assert.match(source, /formatGpuLabel/u);
    assert.match(source, /getGpuMemoryTotalMb/u);
    assert.match(source, /totalGpuPowerCap/u);
    assert.match(source, /GPU cap/u);
    assert.match(source, /<WorkflowPinningSection gpuCatalog=\{gpuCatalog\}/u);
    assert.doesNotMatch(source, /\[0,\s*1,\s*2,\s*3\]/u);
    assert.doesNotMatch(source, /\+\s*350/u, 'scheduler power summary should not assume a 350W CPU/system budget');
});

test('template GPU lock toggles hydrate saved lock state and clear lock when returning to auto', () => {
    const lockableTemplateFiles = [
        'src/components/StructurePredictionTemplate.tsx',
    ];

    for (const relativePath of lockableTemplateFiles) {
        const source = readSource(relativePath);
        assert.match(source, /resolveInitialGpuPinningState/u, `${relativePath} should hydrate pin and lock state through the shared helper`);
        assert.match(source, /useState\(initialGpuPinningState\.pinnedGpus\)/u, `${relativePath} should initialize pinned GPUs from normalized saved params`);
        assert.match(source, /useState\(initialGpuPinningState\.lockGpus\)/u, `${relativePath} should initialize lock checkbox from saved lock_gpus when pins exist`);
        assert.match(source, /const clearGpuPinning = \(\) => \{\s*setPinnedGpus\(\[\]\);\s*setLockGpus\(false\);\s*\}/u, `${relativePath} should clear hidden lock state when Auto mode is selected`);
        assert.match(source, /onClick=\{clearGpuPinning\}/u, `${relativePath} should use the shared Auto-mode clearing handler`);
    }
});

test('scheduler global save preserves backend GPU policy fields while editing UI sliders', () => {
    const systemResources = readSource('src/components/dashboard/SystemResources.tsx');
    const apiTypes = readSource('src/lib/api.ts');

    for (const field of ['msa_preferred_gpu_ids', 'msa_avoid_heavy_gpus', 'force_run_excluded_gpu_ids']) {
        assert.match(systemResources, new RegExp(`${field}: config\\?\\.global\\?\\.${field}`, 'u'), `SystemResources should preserve ${field} from the current scheduler config`);
        assert.match(apiTypes, new RegExp(`${field}\\??:`, 'u'), `SchedulerConfig API type should expose ${field}`);
    }

    for (const field of ['auto_cpu_threads', 'auto_cpu_thread_job_threshold']) {
        assert.match(apiTypes, new RegExp(`${field}:`, 'u'), `SchedulerConfig API type should expose ${field}`);
    }
});

test('infra telemetry keeps unavailable CPU power as null instead of false zero watts', () => {
    const source = readSource('src/components/InfraLiveTelemetry.tsx');
    const apiTypes = readSource('src/lib/api.ts');

    assert.match(apiTypes, /power_telemetry\??:/u, 'CPUStatus should expose CPU power telemetry diagnostics');
    assert.doesNotMatch(source, /sample\.cpuPower\s*\?\?\s*0/u, 'CPU power trace should not convert null samples to 0W');
    assert.match(source, /sample\.cpuPower\s*==\s*null\s*\?\s*null\s*:/u, 'CPU power trace should render null samples as gaps');
});

test('dashboard telemetry exposes a hardware discovery refresh action', () => {
    const source = readSource('src/components/InfraLiveTelemetry.tsx');
    const picker = readSource('src/components/ExecutionTargetPicker.tsx');
    const apiTypes = readSource('src/lib/api.ts');

    assert.match(apiTypes, /discoverHardware/u, 'API client should expose dashboard-wide hardware discovery');
    assert.match(apiTypes, /\/api\/gpu\/hardware\/discover/u, 'hardware discovery should route through the GPU capability API');
    assert.match(source, /Discover hardware/u, 'Telemetry dashboard should render a hardware discovery control');
    assert.match(source, /SHARED_FAN_CONTROL_QUERY_KEY/u, 'Discovery action should refresh fan controls');
    assert.match(source, /SHARED_POWER_CONTROL_QUERY_KEY/u, 'Discovery action should refresh power controls');
    assert.match(source, /INFRA_LIVE_SHARED_QUERY_KEY/u, 'Discovery action should refresh system telemetry');
    assert.match(source, /Discover running Vast/u, 'Dashboard telemetry should render Vast discovery beside local hardware discovery');
    assert.match(source, /refreshVastExecutionTargets/u, 'Dashboard Vast discovery should call the provider refresh API');
    assert.match(source, /VAST_DISCOVERY_QUERY_KEY/u, 'Dashboard Vast discovery should preserve inventory for Job Launcher attachment');
    assert.doesNotMatch(picker, /Discover running Vast/u, 'Job Launcher should not duplicate the Dashboard discovery button');
    assert.match(picker, /VAST_DISCOVERY_QUERY_KEY/u, 'Job Launcher should consume the Dashboard-discovered Vast inventory');
});
