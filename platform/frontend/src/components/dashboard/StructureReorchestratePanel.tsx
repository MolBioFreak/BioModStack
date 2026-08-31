import { useMemo } from 'react';
import { deriveBoltzCpGpuLaunchSettings } from '../structurePredictionUiState.js';
import { useLiveGpuCatalog } from '../useLiveGpuCatalog';
import type { StructurePredictor, StructureReorchestrateSettings } from './reorchestrateStructureSettings.js';

interface StructureReorchestratePanelProps {
    settings: StructureReorchestrateSettings;
    onChange: (next: StructureReorchestrateSettings) => void;
    disabled?: boolean;
}

const predictorLabel: Record<StructurePredictor, string> = {
    boltz: 'Boltz-2',
    fold_cp: 'NVIDIA Fold-CP',
    protenix: 'Protenix',
    esmfold2: 'ESMFold2',
};

const numberInputClass = 'mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-100';
const sectionClass = 'rounded-xl border border-slate-700 bg-slate-800/40 p-4';

const toPositiveInteger = (value: string, fallback: number, min = 1): number => {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? Math.max(min, parsed) : fallback;
};

export function StructureReorchestratePanel({
    settings,
    onChange,
    disabled = false,
}: StructureReorchestratePanelProps) {
    const update = (patch: Partial<StructureReorchestrateSettings>) => onChange({ ...settings, ...patch });
    const { gpuOptions } = useLiveGpuCatalog();
    const boltzCpFallbackGpuIds = useMemo(() => gpuOptions.map((gpu) => gpu.index).join(','), [gpuOptions]);

    const updateBoltz = (patch: Partial<StructureReorchestrateSettings['boltz']>) => {
        update({ boltz: { ...settings.boltz, ...patch } });
    };

    const updateBoltzCp = (patch: Partial<StructureReorchestrateSettings['boltzCp']>) => {
        update({ boltzCp: { ...settings.boltzCp, ...patch } });
    };


    const updateProtenix = (patch: Partial<StructureReorchestrateSettings['protenix']>) => {
        update({ protenix: { ...settings.protenix, ...patch } });
    };

    const boltzCpGpuSettings = settings.boltzCp.enabled
        ? deriveBoltzCpGpuLaunchSettings({
            pinnedGpus: settings.boltzCp.pinnedGpus,
            requestedSizeCp: settings.boltzCp.sizeCp,
            fallbackGpuIds: boltzCpFallbackGpuIds,
        })
        : null;

    return (
        <div className="space-y-4">
            <div className={sectionClass}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-base font-semibold text-slate-100">Structure retry controls</h3>
                        <p className="mt-1 text-sm text-slate-400">
                            Active predictors: {settings.predictors.map((predictor) => predictorLabel[predictor]).join(', ')}
                        </p>
                    </div>
                    <label className="inline-flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
                        <input
                            type="checkbox"
                            checked={settings.skipMsa}
                            onChange={(event) => update({ skipMsa: event.target.checked })}
                            className="rounded border-slate-600 bg-slate-950"
                            disabled={disabled}
                        />
                        Skip MSA on retry
                    </label>
                </div>

                <div className="mt-4 grid gap-4 md:grid-cols-[minmax(0,1fr)_14rem]">
                    <div>
                        <p className="text-sm font-medium text-slate-200">MSA source</p>
                        <div className="mt-2 inline-flex rounded-lg border border-slate-600 bg-slate-900/80 p-1">
                            <button
                                type="button"
                                onClick={() => update({ msaProvider: 'local' })}
                                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                                    settings.msaProvider === 'local'
                                        ? 'bg-emerald-500/20 text-emerald-200'
                                        : 'text-slate-300 hover:text-slate-100'
                                }`}
                                disabled={disabled}
                            >
                                Local MMseqs2
                            </button>
                            <button
                                type="button"
                                onClick={() => update({ msaProvider: 'colabfold_api' })}
                                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                                    settings.msaProvider === 'colabfold_api'
                                        ? 'bg-cyan-500/20 text-cyan-200'
                                        : 'text-slate-300 hover:text-slate-100'
                                }`}
                                disabled={disabled}
                            >
                                ColabFold API
                            </button>
                        </div>
                        <p className="mt-2 text-xs text-slate-500">
                            Toggle between the local stack and ColabFold before re-launching the exact predictors from this run.
                        </p>
                    </div>

                    <label className="text-sm text-slate-300">
                        MSA preset
                        <select
                            value={settings.msaPreset}
                            onChange={(event) => update({ msaPreset: event.target.value as StructureReorchestrateSettings['msaPreset'] })}
                            className="mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100"
                            disabled={disabled}
                        >
                            <option value="fast">Fast</option>
                            <option value="balanced">Balanced</option>
                            <option value="maximum">Maximum</option>
                        </select>
                    </label>
                </div>

                <label className="mt-4 inline-flex items-center gap-2 text-sm text-slate-300">
                    <input
                        type="checkbox"
                        checked={settings.msaAllowEmptyFallback}
                        onChange={(event) => update({ msaAllowEmptyFallback: event.target.checked })}
                        className="rounded border-slate-600 bg-slate-950"
                        disabled={disabled}
                    />
                    Allow empty fallback if the selected MSA source returns zero depth
                </label>
            </div>

            {settings.predictors.includes('boltz') && (
                <div className={sectionClass}>
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <h3 className="text-base font-semibold text-slate-100">Boltz-2 settings</h3>
                            <p className="mt-1 text-sm text-slate-400">Tune the Boltz runtime that will be reused on retry.</p>
                        </div>
                        <label className="inline-flex items-center gap-2 text-sm text-slate-300">
                            <input
                                type="checkbox"
                                checked={settings.boltz.usePotentials}
                                onChange={(event) => updateBoltz({ usePotentials: event.target.checked })}
                                className="rounded border-slate-600 bg-slate-950"
                                disabled={disabled}
                            />
                            Use potentials
                        </label>
                    </div>
                    <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        <label className="text-sm text-slate-300">
                            Recycling steps
                            <input
                                type="number"
                                min={1}
                                value={settings.boltz.recyclingSteps}
                                onChange={(event) => updateBoltz({ recyclingSteps: toPositiveInteger(event.target.value, settings.boltz.recyclingSteps) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Sampling steps
                            <input
                                type="number"
                                min={1}
                                value={settings.boltz.samplingSteps}
                                onChange={(event) => updateBoltz({ samplingSteps: toPositiveInteger(event.target.value, settings.boltz.samplingSteps) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Num samples
                            <input
                                type="number"
                                min={1}
                                value={settings.boltz.numSamples}
                                onChange={(event) => updateBoltz({ numSamples: toPositiveInteger(event.target.value, settings.boltz.numSamples) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Max parallel samples
                            <input
                                type="number"
                                min={1}
                                value={settings.boltz.maxParallelSamples}
                                onChange={(event) => updateBoltz({ maxParallelSamples: toPositiveInteger(event.target.value, settings.boltz.maxParallelSamples) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                    </div>
                </div>
            )}

            {settings.boltzCp.enabled && boltzCpGpuSettings && (
                <div className="rounded-xl border border-orange-500/20 bg-orange-500/5 p-4 space-y-4">
                    <div>
                        <h3 className="text-base font-semibold text-orange-100">Fold-CP settings</h3>
                        <p className="mt-1 text-sm text-orange-100/70">
                            Reuse the same CP runtime controls from the structure launcher when re-orchestrating this experimental job.
                        </p>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">
                            GPU Pinning {settings.boltzCp.pinnedGpus.length > 0 && <span className="text-blue-400">({settings.boltzCp.pinnedGpus.length} selected)</span>}
                        </label>
                        <div className="flex flex-wrap gap-2">
                            <button
                                type="button"
                                onClick={() => updateBoltzCp({ pinnedGpus: [], lockGpus: false })}
                                className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${settings.boltzCp.pinnedGpus.length === 0
                                    ? 'bg-slate-600 text-white ring-2 ring-slate-400'
                                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                                disabled={disabled}
                            >
                                Auto
                            </button>
                            {gpuOptions.map((gpu) => (
                                <button
                                    key={gpu.index}
                                    type="button"
                                    onClick={() => updateBoltzCp({
                                        pinnedGpus: settings.boltzCp.pinnedGpus.includes(gpu.index)
                                            ? settings.boltzCp.pinnedGpus.filter((value) => value !== gpu.index)
                                            : [...settings.boltzCp.pinnedGpus, gpu.index].sort((left, right) => left - right),
                                    })}
                                    className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${settings.boltzCp.pinnedGpus.includes(gpu.index)
                                        ? 'bg-blue-600 text-white ring-2 ring-blue-400'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                                    disabled={disabled}
                                >
                                    {gpu.label}
                                </button>
                            ))}
                        </div>
                        {settings.boltzCp.pinnedGpus.length > 0 && (
                            <label className="flex items-center gap-2 mt-3 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={settings.boltzCp.lockGpus}
                                    onChange={(event) => updateBoltzCp({ lockGpus: event.target.checked })}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                                    disabled={disabled}
                                />
                                <span className="text-sm text-slate-400">Lock selected GPU(s) exclusively during workflow</span>
                            </label>
                        )}
                    </div>

                    <div>
                        <label className="text-sm text-orange-100/80 block mb-1">Context Parallel Size Request</label>
                        <input
                            type="number"
                            min={1}
                            max={16}
                            value={settings.boltzCp.sizeCp}
                            onChange={(event) => updateBoltzCp({
                                sizeCp: Math.min(16, toPositiveInteger(event.target.value, settings.boltzCp.sizeCp)),
                            })}
                            className="w-full max-w-xs bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                            disabled={disabled}
                        />
                        <p className="mt-2 text-xs text-slate-400">
                            OEM Fold-CP uses a square context-parallel mesh. Current GPU resolution: {boltzCpGpuSettings.gpuIds || 'auto fallback'} → size_cp {boltzCpGpuSettings.sizeCp}.
                        </p>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label className="text-xs text-slate-400 block mb-1">Output Format</label>
                            <select
                                value={settings.boltzCp.outputFormat}
                                onChange={(event) => updateBoltzCp({
                                    outputFormat: (event.target.value === 'pdb' ? 'pdb' : 'mmcif') as StructureReorchestrateSettings['boltzCp']['outputFormat'],
                                })}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                disabled={disabled}
                            >
                                <option value="mmcif">mmCIF</option>
                                <option value="pdb">PDB</option>
                            </select>
                        </div>
                        <div>
                            <label className="text-xs text-slate-400 block mb-1">Seed</label>
                            <input
                                type="text"
                                value={settings.boltzCp.seed}
                                onChange={(event) => updateBoltzCp({ seed: event.target.value.replace(/[^0-9-]/g, '') })}
                                placeholder="optional"
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                disabled={disabled}
                            />
                        </div>
                        <label className="flex items-center gap-3 rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-sm text-slate-200">
                            <input
                                type="checkbox"
                                checked={settings.boltzCp.writeFullPae}
                                onChange={(event) => updateBoltzCp({ writeFullPae: event.target.checked })}
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-orange-500"
                                disabled={disabled}
                            />
                            <span>Write full PAE matrix</span>
                        </label>
                    </div>
                </div>
            )}


            {settings.predictors.includes('protenix') && (
                <div className={sectionClass}>
                    <div>
                        <h3 className="text-base font-semibold text-slate-100">Protenix settings</h3>
                        <p className="mt-1 text-sm text-slate-400">Update the same Protenix runtime controls before launching the retry.</p>
                    </div>
                    <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        <label className="text-sm text-slate-300 xl:col-span-2">
                            Model weights
                            <input
                                type="text"
                                value={settings.protenix.modelWeights}
                                onChange={(event) => updateProtenix({ modelWeights: event.target.value })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Seeds
                            <input
                                type="text"
                                value={settings.protenix.seeds}
                                onChange={(event) => updateProtenix({ seeds: event.target.value })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Samples / seed
                            <input
                                type="number"
                                min={1}
                                value={settings.protenix.nSample}
                                onChange={(event) => updateProtenix({ nSample: toPositiveInteger(event.target.value, settings.protenix.nSample) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Diffusion steps
                            <input
                                type="number"
                                min={1}
                                value={settings.protenix.nStep}
                                onChange={(event) => updateProtenix({ nStep: toPositiveInteger(event.target.value, settings.protenix.nStep) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Recycle iterations
                            <input
                                type="number"
                                min={1}
                                value={settings.protenix.nCycle}
                                onChange={(event) => updateProtenix({ nCycle: toPositiveInteger(event.target.value, settings.protenix.nCycle) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                    </div>
                </div>
            )}
        </div>
    );
}
