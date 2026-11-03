import { useMemo, useState } from 'react';
import type { PrimerTmOptionsResponse, PrimerTmSettings } from '../../lib/api';

interface PrimerTmSettingsPanelProps {
    sequenceType: 'dna' | 'rna';
    options: PrimerTmOptionsResponse | null;
    settings: PrimerTmSettings;
    onChange: (settings: PrimerTmSettings) => void;
    title?: string;
}

function NumericInput({
    label,
    value,
    step = 0.1,
    disabled = false,
    onChange,
}: {
    label: string;
    value: number;
    step?: number;
    disabled?: boolean;
    onChange: (value: number) => void;
}) {
    return (
        <label className="space-y-1">
            <span className="text-[11px] uppercase tracking-wide text-slate-500">{label}</span>
            <input
                type="number"
                min={0}
                disabled={disabled}
                value={Number.isFinite(value) ? value : 0}
                step={step}
                onChange={(event) => onChange(Number(event.target.value))}
                className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-1 text-sm text-slate-200 focus:border-blue-500 focus:outline-none"
            />
        </label>
    );
}

export function PrimerTmSettingsPanel({
    sequenceType,
    options,
    settings,
    onChange,
    title = 'Tm Model',
}: PrimerTmSettingsPanelProps) {
    const [showChemistry, setShowChemistry] = useState(false);

    const algorithms = useMemo(
        () => (options?.algorithms || []).filter((option) => option.sequence_types.includes(sequenceType)),
        [options, sequenceType],
    );

    const algorithm = algorithms.find(option => option.id === settings.algorithm);
    const compatibleSalts = options?.salt_corrections.filter(option => !algorithm?.salt_corrections?.length || algorithm.salt_corrections.includes(option.id)) || [];
    const saltIsCompatible = compatibleSalts.some(option => option.id === settings.salt_correction);
    const referenceRna = algorithm?.polymer_pairing?.startsWith('rna') || false;
    const approximate = ['wallace', 'gc_empirical'].includes(settings.algorithm);
    const switchAlgorithm = (id: string) => {
        const next = algorithms.find(option => option.id === id);
        const allowed = next?.salt_corrections || [];
        onChange({
            ...settings,
            algorithm: id,
            salt_correction: allowed.length && !allowed.includes(settings.salt_correction) ? allowed[0] : settings.salt_correction,
            ...(next?.polymer_pairing?.startsWith('rna') ? {
                salt_correction: 'none', na_mM: 1000, k_mM: 0, tris_mM: 0, mg_mM: 0, dntps_mM: 0,
                dmso_percent: 0, formamide_percent: 0,
            } : {}),
        });
    };

    const updateSetting = <K extends keyof PrimerTmSettings>(key: K, value: PrimerTmSettings[K]) => {
        onChange({
            ...settings,
            [key]: value,
        });
    };

    return (
        <div className="rounded border border-slate-700 bg-slate-800/70 p-3 space-y-3">
            <div className="flex items-center justify-between">
                <div>
                    <div className="text-sm font-medium text-slate-200">{title}</div>
                    <div className="text-xs text-slate-500">
                        Shared calculation model for {sequenceType.toUpperCase()} oligos
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => setShowChemistry((current) => !current)}
                    className="rounded bg-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-600"
                >
                    {showChemistry ? 'Hide Chemistry' : 'Show Chemistry'}
                </button>
            </div>

            <div className="grid grid-cols-1 gap-2">
                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-wide text-slate-500">Algorithm</span>
                    <select
                        value={settings.algorithm}
                        onChange={(event) => switchAlgorithm(event.target.value)}
                        className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-1 text-sm text-slate-200 focus:border-blue-500 focus:outline-none"
                    >
                        {algorithms.map((option) => (
                            <option key={option.id} value={option.id}>
                                {option.label}
                            </option>
                        ))}
                    </select>
                    <div className="text-xs text-slate-500">
                        {algorithms.find((option) => option.id === settings.algorithm)?.description || 'Loading available algorithms...'}
                    </div>
                </label>

                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-wide text-slate-500">Salt Correction</span>
                    <select
                        value={settings.salt_correction}
                        onChange={(event) => updateSetting('salt_correction', event.target.value)}
                        className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-1 text-sm text-slate-200 focus:border-blue-500 focus:outline-none"
                    >
                        {!saltIsCompatible && <option value={settings.salt_correction} disabled>{settings.salt_correction} — incompatible</option>}
                        {compatibleSalts.map((option) => (
                            <option key={option.id} value={option.id}>
                                {option.label}
                            </option>
                        ))}
                    </select>
                    <div className="text-xs text-slate-500">
                        {options?.salt_corrections.find((option) => option.id === settings.salt_correction)?.description || 'Loading salt models...'}
                    </div>
                </label>
            </div>

            {algorithm?.applicability && <p className="text-xs text-slate-400">{algorithm.applicability}</p>}
            {!saltIsCompatible && options && <p role="alert" className="text-xs text-amber-300">Choose a supported salt correction before calculating Tm.</p>}
            <div className="grid grid-cols-2 gap-2">
                <NumericInput
                    label="Primer nM"
                    disabled={approximate}
                    value={settings.primer_concentration_nM}
                    onChange={(value) => updateSetting('primer_concentration_nM', value)}
                />
                <NumericInput
                    label="Template nM"
                    disabled={approximate}
                    value={settings.template_concentration_nM}
                    onChange={(value) => updateSetting('template_concentration_nM', value)}
                />
                <NumericInput
                    label="Na mM"
                    disabled={referenceRna || settings.algorithm === 'wallace'}
                    value={settings.na_mM}
                    onChange={(value) => updateSetting('na_mM', value)}
                />
                <NumericInput
                    label="Mg mM"
                    disabled={referenceRna || settings.algorithm === 'wallace'}
                    value={settings.mg_mM}
                    onChange={(value) => updateSetting('mg_mM', value)}
                />
                <NumericInput
                    label="dNTP mM"
                    disabled={referenceRna || settings.algorithm === 'wallace'}
                    value={settings.dntps_mM}
                    onChange={(value) => updateSetting('dntps_mM', value)}
                />
                <label className="flex items-center gap-2 rounded border border-slate-700 bg-slate-700/40 px-2 py-2 text-sm text-slate-300">
                    <input
                        type="checkbox"
                        checked={settings.self_complementary}
                        disabled={approximate}
                        onChange={(event) => updateSetting('self_complementary', event.target.checked)}
                        className="h-3 w-3"
                    />
                    Self-complementary
                </label>
            </div>

            {referenceRna && <button type="button" className="text-xs text-blue-300" onClick={() => switchAlgorithm(settings.algorithm)}>Use the 1 M Na reference settings</button>}
            {showChemistry && (
                <div className="grid grid-cols-2 gap-2 border-t border-slate-700 pt-3">
                    <NumericInput
                        label="K mM"
                        disabled={referenceRna || settings.algorithm === 'wallace'}
                        value={settings.k_mM}
                        onChange={(value) => updateSetting('k_mM', value)}
                    />
                    <NumericInput
                        label="Tris mM"
                        disabled={referenceRna || settings.algorithm === 'wallace'}
                        value={settings.tris_mM}
                        onChange={(value) => updateSetting('tris_mM', value)}
                    />
                    <NumericInput
                        label="DMSO %"
                        disabled={referenceRna}
                        value={settings.dmso_percent}
                        onChange={(value) => updateSetting('dmso_percent', value)}
                    />
                    <NumericInput
                        label="Formamide %"
                        disabled={referenceRna}
                        value={settings.formamide_percent}
                        onChange={(value) => updateSetting('formamide_percent', value)}
                    />
                </div>
            )}
        </div>
    );
}
