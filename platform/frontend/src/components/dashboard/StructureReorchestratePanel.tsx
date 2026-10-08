import type { StructurePredictor, StructureReorchestrateSettings } from './reorchestrateStructureSettings.js';

interface StructureReorchestratePanelProps {
    settings: StructureReorchestrateSettings;
    onChange: (next: StructureReorchestrateSettings) => void;
    disabled?: boolean;
}

const predictorLabel: Record<StructurePredictor, string> = {
    boltz: 'Boltz-2',
    rf3: 'RoseTTAFold 3',
    protenix: 'Protenix',
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

    const updateBoltz = (patch: Partial<StructureReorchestrateSettings['boltz']>) => {
        update({ boltz: { ...settings.boltz, ...patch } });
    };

    const updateRf3 = (patch: Partial<StructureReorchestrateSettings['rf3']>) => {
        update({ rf3: { ...settings.rf3, ...patch } });
    };

    const updateProtenix = (patch: Partial<StructureReorchestrateSettings['protenix']>) => {
        update({ protenix: { ...settings.protenix, ...patch } });
    };

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

            {settings.predictors.includes('rf3') && (
                <div className={sectionClass}>
                    <div>
                        <h3 className="text-base font-semibold text-slate-100">RoseTTAFold 3 settings</h3>
                        <p className="mt-1 text-sm text-slate-400">Expose only the RF3 knobs that matter for this retry.</p>
                    </div>
                    <div className="mt-4 grid gap-4 md:grid-cols-2">
                        <label className="text-sm text-slate-300">
                            Recycle iterations
                            <input
                                type="number"
                                min={1}
                                value={settings.rf3.numRecycles}
                                onChange={(event) => updateRf3({ numRecycles: toPositiveInteger(event.target.value, settings.rf3.numRecycles) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
                        </label>
                        <label className="text-sm text-slate-300">
                            Num samples
                            <input
                                type="number"
                                min={1}
                                value={settings.rf3.numSamples}
                                onChange={(event) => updateRf3({ numSamples: toPositiveInteger(event.target.value, settings.rf3.numSamples) })}
                                className={numberInputClass}
                                disabled={disabled}
                            />
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
