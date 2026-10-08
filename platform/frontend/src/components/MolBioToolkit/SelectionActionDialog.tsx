import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';

import { FEATURE_COLOR_PALETTE, FEATURE_TYPES, getFeatureColor } from './featureCatalog';
import { calculateGcPercent, reverseComplementSequence, sequenceUnitLabel } from './utils/nucleotides';
import type { SelectionSnapshot } from './utils/selectionActions';
import {
    chooseReturnFocusTarget,
    focusTrapTarget,
    restoreFocusWithFallback,
} from './utils/focusManagement';

export type SelectionActionKind = 'feature' | 'forward_primer' | 'reverse_primer';

export interface SelectionFeatureInput {
    name: string;
    type: string;
    strand: 1 | -1;
    color: string;
    description: string;
}

export interface SelectionPrimerInput {
    name: string;
    notes: string;
}

interface SelectionActionDialogProps {
    action: SelectionActionKind;
    snapshot: SelectionSnapshot;
    sequenceType: 'dna' | 'rna' | 'protein';
    busy: boolean;
    error?: string | null;
    returnFocusTarget?: HTMLElement | null;
    fallbackFocusTarget?: HTMLElement | null;
    onClose: () => void;
    onConfirmFeature: (input: SelectionFeatureInput) => void;
    onConfirmPrimer: (input: SelectionPrimerInput) => void;
}

const groupedFeatureTypes = FEATURE_TYPES.reduce<Record<string, typeof FEATURE_TYPES>>((groups, entry) => {
    if (!groups[entry.category]) {
        groups[entry.category] = [];
    }
    groups[entry.category].push(entry);
    return groups;
}, {});

export function SelectionActionDialog({
    action,
    snapshot,
    sequenceType,
    busy,
    error,
    returnFocusTarget,
    fallbackFocusTarget,
    onClose,
    onConfirmFeature,
    onConfirmPrimer,
}: SelectionActionDialogProps) {
    const [name, setName] = useState('');
    const [featureType, setFeatureType] = useState('misc_feature');
    const [featureStrand, setFeatureStrand] = useState<1 | -1>(1);
    const [featureColor, setFeatureColor] = useState(getFeatureColor('misc_feature'));
    const [description, setDescription] = useState('');
    const [primerNotes, setPrimerNotes] = useState('');
    const nameInputRef = useRef<HTMLInputElement | null>(null);
    const dialogPanelRef = useRef<HTMLDivElement | null>(null);
    const previouslyFocusedRef = useRef<HTMLElement | null>(null);
    const fallbackFocusedRef = useRef<HTMLElement | null>(null);

    const isFeature = action === 'feature';
    const primerStrand = action === 'reverse_primer' ? -1 : 1;
    const directionLabel = primerStrand === 1 ? 'Forward' : 'Reverse';
    const unitLabel = sequenceUnitLabel(sequenceType === 'rna' ? 'rna' : 'dna');
    const dialogTitle = isFeature ? 'Add Feature from Selection' : `Add ${directionLabel} Primer`;
    const dialogDescription = isFeature
        ? 'Name and classify the selected span before it is added to this construct.'
        : `Confirm the ${directionLabel.toLowerCase()} primer identity before Tm calculation and creation.`;
    const primerSequence = primerStrand === 1
        ? snapshot.sequence
        : reverseComplementSequence(snapshot.sequence, sequenceType === 'rna' ? 'rna' : 'dna');
    const primerGcPercent = useMemo(
        () => calculateGcPercent(primerSequence),
        [primerSequence],
    );

    useEffect(() => {
        setName('');
        setFeatureType('misc_feature');
        setFeatureStrand(1);
        setFeatureColor(getFeatureColor('misc_feature'));
        setDescription('');
        setPrimerNotes('');
        window.requestAnimationFrame(() => nameInputRef.current?.focus());
    }, [action, snapshot.coordinateKey]);

    useEffect(() => {
        const activeTarget = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        previouslyFocusedRef.current = chooseReturnFocusTarget(
            returnFocusTarget,
            activeTarget,
        );
        fallbackFocusedRef.current = fallbackFocusTarget
            ?? (activeTarget !== previouslyFocusedRef.current ? activeTarget : null);
        return () => {
            restoreFocusWithFallback(
                previouslyFocusedRef.current,
                fallbackFocusedRef.current,
                (target) => document.contains(target),
            );
        };
    }, [fallbackFocusTarget, returnFocusTarget]);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape' && !busy) {
                onClose();
                return;
            }
            if (event.key !== 'Tab') {
                return;
            }
            const focusable = Array.from(
                dialogPanelRef.current?.querySelectorAll<HTMLElement>(
                    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
                ) ?? [],
            );
            if (!focusable.length) {
                event.preventDefault();
                return;
            }
            const trapTarget = focusTrapTarget(
                focusable,
                document.activeElement as HTMLElement | null,
                event.shiftKey,
            );
            if (trapTarget) {
                event.preventDefault();
                trapTarget.focus();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [busy, onClose]);

    const submit = (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const trimmedName = name.trim();
        if (!trimmedName || busy) {
            return;
        }

        if (isFeature) {
            onConfirmFeature({
                name: trimmedName,
                type: featureType,
                strand: featureStrand,
                color: featureColor,
                description: description.trim(),
            });
            return;
        }

        onConfirmPrimer({
            name: trimmedName,
            notes: primerNotes.trim(),
        });
    };

    const changeFeatureType = (type: string) => {
        setFeatureType(type);
        setFeatureColor(getFeatureColor(type));
    };

    return (
        <div
            className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget && !busy) {
                    onClose();
                }
            }}
        >
            <div
                ref={dialogPanelRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="selection-action-dialog-title"
                aria-describedby="selection-action-dialog-description"
                className="w-full max-w-xl overflow-hidden rounded-2xl border border-slate-600 bg-slate-900 shadow-2xl"
            >
                <div className="flex items-start justify-between gap-4 border-b border-slate-700 px-5 py-4">
                    <div>
                        <h2 id="selection-action-dialog-title" className="text-lg font-semibold text-slate-100">
                            {dialogTitle}
                        </h2>
                        <p id="selection-action-dialog-description" className="mt-1 text-sm text-slate-400">
                            {dialogDescription}
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={busy}
                        aria-label="Close creation dialog"
                        className="rounded-lg border border-slate-700 px-2.5 py-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-white disabled:opacity-40"
                    >
                        ×
                    </button>
                </div>

                <form onSubmit={submit}>
                    <div className="max-h-[72vh] space-y-4 overflow-y-auto px-5 py-4">
                        <div className="grid gap-3 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                            <div>
                                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-300/70">
                                    Locked selected span
                                </div>
                                <div className="mt-1 font-mono text-sm text-cyan-100">
                                    {snapshot.coordinateLabel}
                                </div>
                            </div>
                            <div className="self-center rounded-full border border-cyan-500/20 bg-slate-950/60 px-3 py-1 text-xs text-cyan-100">
                                {snapshot.length.toLocaleString()} {unitLabel}
                            </div>
                        </div>

                        <label className="block space-y-1.5">
                            <span className="text-sm font-medium text-slate-200">
                                {isFeature ? 'Feature name' : 'Primer name'} <span className="text-rose-400">*</span>
                            </span>
                            <input
                                ref={nameInputRef}
                                value={name}
                                onChange={(event) => setName(event.target.value)}
                                required
                                autoComplete="off"
                                placeholder={isFeature ? 'e.g. T7 promoter' : `e.g. ${directionLabel}_T7_amplicon`}
                                className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none transition-colors focus:border-cyan-500"
                            />
                        </label>

                        {isFeature ? (
                            <>
                                <div className="grid gap-4 sm:grid-cols-2">
                                    <label className="block space-y-1.5">
                                        <span className="text-sm font-medium text-slate-200">Feature type</span>
                                        <select
                                            value={featureType}
                                            onChange={(event) => changeFeatureType(event.target.value)}
                                            className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500"
                                        >
                                            {Object.entries(groupedFeatureTypes).map(([category, entries]) => (
                                                <optgroup key={category} label={category}>
                                                    {entries.map((entry) => (
                                                        <option key={entry.value} value={entry.value}>{entry.label}</option>
                                                    ))}
                                                </optgroup>
                                            ))}
                                        </select>
                                    </label>

                                    <fieldset className="space-y-1.5">
                                        <legend className="text-sm font-medium text-slate-200">Direction</legend>
                                        <div className="flex h-[38px] items-center gap-4 rounded-lg border border-slate-700 bg-slate-800/60 px-3 text-sm">
                                            <label className="flex items-center gap-2 text-slate-300">
                                                <input
                                                    type="radio"
                                                    name="feature-strand"
                                                    checked={featureStrand === 1}
                                                    onChange={() => setFeatureStrand(1)}
                                                />
                                                Forward
                                            </label>
                                            <label className="flex items-center gap-2 text-slate-300">
                                                <input
                                                    type="radio"
                                                    name="feature-strand"
                                                    checked={featureStrand === -1}
                                                    onChange={() => setFeatureStrand(-1)}
                                                />
                                                Reverse
                                            </label>
                                        </div>
                                    </fieldset>
                                </div>

                                <div className="space-y-2">
                                    <div className="text-sm font-medium text-slate-200">Display color</div>
                                    <div className="flex flex-wrap gap-2">
                                        {FEATURE_COLOR_PALETTE.map((color) => (
                                            <button
                                                key={color}
                                                type="button"
                                                aria-label={`Use feature color ${color}`}
                                                aria-pressed={featureColor === color}
                                                onClick={() => setFeatureColor(color)}
                                                className={`h-7 w-7 rounded-full border-2 transition-transform hover:scale-110 ${featureColor === color ? 'border-white ring-2 ring-cyan-500/50' : 'border-slate-700'}`}
                                                style={{ backgroundColor: color }}
                                            />
                                        ))}
                                    </div>
                                </div>

                                <label className="block space-y-1.5">
                                    <span className="text-sm font-medium text-slate-200">Description / note</span>
                                    <textarea
                                        value={description}
                                        onChange={(event) => setDescription(event.target.value)}
                                        rows={3}
                                        placeholder="Optional biological context or annotation note"
                                        className="w-full resize-y rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500"
                                    />
                                </label>
                            </>
                        ) : (
                            <>
                                <div className="grid gap-3 sm:grid-cols-3">
                                    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-3">
                                        <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Primer type</div>
                                        <div className={`mt-1 font-semibold ${primerStrand === 1 ? 'text-emerald-300' : 'text-rose-300'}`}>
                                            {directionLabel}
                                        </div>
                                    </div>
                                    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-3">
                                        <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Length</div>
                                        <div className="mt-1 font-semibold text-slate-200">{snapshot.length} {unitLabel}</div>
                                    </div>
                                    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-3">
                                        <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">GC</div>
                                        <div className="mt-1 font-semibold text-slate-200">{primerGcPercent}%</div>
                                    </div>
                                </div>

                                <label className="block space-y-1.5">
                                    <span className="text-sm font-medium text-slate-200">Primer sequence (5′→3′)</span>
                                    <textarea
                                        readOnly
                                        value={primerSequence}
                                        rows={3}
                                        className="w-full resize-none rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 font-mono text-sm leading-6 text-cyan-100"
                                    />
                                    {primerStrand === -1 && (
                                        <span className="text-xs text-slate-500">
                                            Reverse-complement oligo shown above; coordinates remain locked to the selected template span.
                                        </span>
                                    )}
                                </label>

                                <label className="block space-y-1.5">
                                    <span className="text-sm font-medium text-slate-200">Notes</span>
                                    <textarea
                                        value={primerNotes}
                                        onChange={(event) => setPrimerNotes(event.target.value)}
                                        rows={2}
                                        placeholder="Optional purpose, target, or design note"
                                        className="w-full resize-y rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500"
                                    />
                                </label>
                            </>
                        )}

                        {error && (
                            <div role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                                {error}
                            </div>
                        )}
                    </div>

                    <div className="flex items-center justify-end gap-3 border-t border-slate-700 bg-slate-950/40 px-5 py-4">
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={busy}
                            className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-40"
                        >
                            Cancel
                        </button>
                        <button
                            type="submit"
                            disabled={!name.trim() || busy}
                            className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {busy ? 'Creating…' : isFeature ? 'Add Feature' : `Add ${directionLabel} Primer`}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
