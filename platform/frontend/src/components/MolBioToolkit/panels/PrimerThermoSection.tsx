/**
 * PrimerThermoSection - informational hairpin, self-dimer and forward/reverse
 * pair-dimer free energies. Flags are advice only: nothing here blocks or
 * changes a primer.
 */
import { useEffect, useMemo, useState } from 'react';
import {
    calculatePrimerThermo,
    type PrimerThermoRequest,
    type PrimerThermoResponse,
    type PrimerThermoSeverity,
    type PrimerThermoStructure,
    type PrimerTmSettings,
} from '../../../lib/api';

/** Largest number of forward x reverse combinations sent in one request. */
export const MAX_THERMO_PAIRS = 400;

export interface PrimerThermoInput {
    id?: string;
    name?: string;
    sequence: string;
    strand?: number;
}

const CHIP: Record<PrimerThermoSeverity, string> = {
    none: 'border-slate-700 bg-slate-800/60 text-slate-300',
    yellow: 'border-yellow-600/70 bg-yellow-500/15 text-yellow-200',
    orange: 'border-orange-600/80 bg-orange-500/20 text-orange-200',
};

function minus(value: number): string {
    return value < 0 ? `−${Math.abs(value)}` : String(value);
}

function thermoErrorText(error: unknown): string {
    const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
    if (typeof detail === 'string' && detail) return detail;
    return error instanceof Error ? error.message : 'Request failed.';
}

function buildRequest(primers: PrimerThermoInput[], settings: PrimerTmSettings) {
    const forward: number[] = [];
    const reverse: number[] = [];
    primers.forEach((primer, index) => {
        if (primer.strand === 1) forward.push(index);
        else if (primer.strand === -1) reverse.push(index);
    });
    const pairs = forward.flatMap((left) => reverse.map((right) => ({ left, right })));
    const request: PrimerThermoRequest = {
        primers: primers.map(({ id, name, sequence }) => ({ id, name, sequence })),
        pairs: pairs.slice(0, MAX_THERMO_PAIRS),
        settings: {
            na_mM: settings.na_mM,
            k_mM: settings.k_mM,
            tris_mM: settings.tris_mM,
            mg_mM: settings.mg_mM,
            dntps_mM: settings.dntps_mM,
        },
    };
    return { request, omittedPairs: Math.max(pairs.length - MAX_THERMO_PAIRS, 0) };
}

function DeltaG({ label, result, yellowAt, orangeAt }: {
    label: string;
    result: PrimerThermoStructure | null | undefined;
    yellowAt: number;
    orangeAt: number;
}) {
    if (!result) return <span className="text-slate-500">{label} n/a</span>;
    const none = result.dg_kcal_mol === 0;
    const limit = result.severity === 'orange' ? orangeAt : result.severity === 'yellow' ? yellowAt : null;
    return (
        <span
            data-severity={result.severity}
            title={result.structure}
            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${CHIP[result.severity]}`}
        >
            {label} {none ? 'none' : minus(result.dg_kcal_mol)}
            {limit !== null && <span className="opacity-80">(≤ {minus(limit)})</span>}
            {result.three_prime_paired && <span className="opacity-80">3′ end paired</span>}
        </span>
    );
}

interface PrimerThermoSectionProps {
    primers: PrimerThermoInput[];
    tmSettings: PrimerTmSettings;
}

export function PrimerThermoSection({ primers, tmSettings }: PrimerThermoSectionProps) {
    const built = useMemo(() => buildRequest(primers, tmSettings), [primers, tmSettings]);
    const key = useMemo(() => JSON.stringify(built.request), [built]);
    const [state, setState] = useState<{ key: string; value?: PrimerThermoResponse; error?: string } | null>(null);
    const hasPrimers = built.request.primers.length > 0;

    useEffect(() => {
        if (!hasPrimers) return;
        let cancelled = false;
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            calculatePrimerThermo(JSON.parse(key) as PrimerThermoRequest, controller.signal)
                .then((response) => { if (!cancelled) setState({ key, value: response.data }); })
                .catch((error) => { if (!cancelled) setState({ key, error: thermoErrorText(error) }); });
        }, 250);
        return () => { cancelled = true; controller.abort(); window.clearTimeout(timer); };
    }, [key, hasPrimers]);

    if (!hasPrimers) return null;
    const current = state?.key === key ? state : null;
    const value = current?.value;
    const yellowAt = value?.thresholds_kcal_mol.yellow_at_or_below ?? -5;
    const orangeAt = value?.thresholds_kcal_mol.orange_at_or_below ?? -10;
    const label = (index: number) => value?.primers[index]?.name || `Primer ${index + 1}`;

    return (
        <div className="space-y-2 rounded-lg border border-slate-700 bg-slate-900/50 p-3" data-testid="primer-thermo">
            <div className="font-medium text-slate-200">Hairpin and dimer ΔG</div>
            <p className="text-xs text-slate-400">
                Informational only; nothing is blocked. kcal/mol: yellow at or below {minus(yellowAt)}, orange at or
                below {minus(orangeAt)}; &ldquo;none&rdquo; means no structure forms. Pairs are each forward primer with each reverse
                primer on this construct, used as pairs.
            </p>
            {current?.error && (
                <div role="alert" className="text-sm text-red-300">Hairpin and dimer ΔG unavailable: {current.error}</div>
            )}
            {!current && (
                <div role="status" className="text-sm text-slate-400">Calculating hairpin and dimer ΔG…</div>
            )}
            {value && (
                <>
                    {built.omittedPairs > 0 && (
                        <p className="text-xs text-amber-300">
                            {built.omittedPairs} further forward/reverse combinations were not calculated (limit {MAX_THERMO_PAIRS} per request).
                        </p>
                    )}
                    <p className="text-xs text-slate-500">
                        {value.temperature_c} °C, monovalent-equivalent salt {value.monovalent_equivalent_mM} mM from the Tm
                        settings (Na⁺, K⁺, Tris, Mg²⁺, dNTP). {value.salt_note}
                    </p>
                    <div className="space-y-1.5">
                        {value.primers.map((entry, index) => (
                            <div key={entry.id || `${entry.name}-${index}`} className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2">
                                <div className="text-sm text-slate-200">{entry.name || `Primer ${index + 1}`}</div>
                                {entry.warnings.length > 0 ? (
                                    <div className="mt-1 text-xs text-amber-300">{entry.warnings.join(' • ')}</div>
                                ) : (
                                    <div className="mt-1 flex flex-wrap gap-2 text-xs">
                                        <DeltaG label="hairpin" result={entry.hairpin} yellowAt={yellowAt} orangeAt={orangeAt} />
                                        <DeltaG label="self-dimer" result={entry.self_dimer} yellowAt={yellowAt} orangeAt={orangeAt} />
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                    {value.pairs.length > 0 && (
                        <div className="space-y-1.5">
                            <div className="text-sm text-slate-300">Forward × reverse pair dimers</div>
                            {value.pairs.map((pair) => (
                                <div key={`${pair.left}:${pair.right}`} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs">
                                    <span className="text-sm text-slate-200">{label(pair.left)} × {label(pair.right)}</span>
                                    <DeltaG label="dimer" result={pair} yellowAt={yellowAt} orangeAt={orangeAt} />
                                </div>
                            ))}
                        </div>
                    )}
                    <details className="text-xs text-slate-400">
                        <summary>Method, credit and limits</summary>
                        <div className="mt-2 space-y-2">
                            <p>{value.engine} {value.engine_version}. {value.credit}</p>
                            <ul className="list-disc space-y-1 pl-4">
                                {value.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
                            </ul>
                            <ul className="list-disc space-y-1 pl-4">
                                {value.citations.map((citation) => <li key={citation}>{citation}</li>)}
                            </ul>
                        </div>
                    </details>
                </>
            )}
        </div>
    );
}
