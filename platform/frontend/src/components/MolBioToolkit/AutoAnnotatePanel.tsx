/**
 * AutoAnnotatePanel - Settings panel for pLannotate auto-annotation
 *
 * Exposes all pLannotate configuration options to the user.
 */

import { useEffect, useRef, useState } from 'react';
import type { AnnotationSourceStatus } from './utils/annotationSources';
import {
    focusTrapTarget,
    restoreFocusIfConnected,
} from './utils/focusManagement';

export interface AutoAnnotateSettings {
    minIdentity: number;      // Minimum percent identity threshold (0-100)
    detailed: boolean;        // Use detailed search mode (more hits, more false positives)
    filterFragments: boolean; // Filter out partial/fragment matches
}

const DEFAULT_SETTINGS: AutoAnnotateSettings = {
    minIdentity: 35,       // Lower threshold catches more features
    detailed: true,        // Detailed search by default for better detection
    filterFragments: false
};

interface AutoAnnotatePanelProps {
    isOpen: boolean;
    onClose: () => void;
    onAnnotate: (settings: AutoAnnotateSettings) => void;
    onClearAnnotations: () => void;
    onImportAnnotations: (file: File) => Promise<string>;
    onRetrieveNcbi: (accession: string) => Promise<string>;
    onRetrieveAddgene: (plasmidId: string) => Promise<string>;
    annotationSourceStatus: AnnotationSourceStatus | null;
    isAnnotating: boolean;
    hasSequence: boolean;
    featureCount: number;
    sequenceLength: number;
    isCircular: boolean;
}

export function AutoAnnotatePanel({
    isOpen,
    onClose,
    onAnnotate,
    onClearAnnotations,
    onImportAnnotations,
    onRetrieveNcbi,
    onRetrieveAddgene,
    annotationSourceStatus,
    isAnnotating,
    hasSequence,
    featureCount,
    sequenceLength,
    isCircular
}: AutoAnnotatePanelProps) {
    const [settings, setSettings] = useState<AutoAnnotateSettings>(DEFAULT_SETTINGS);
    const [confirmingClear, setConfirmingClear] = useState(false);
    const [isImporting, setIsImporting] = useState(false);
    const [retrievingSource, setRetrievingSource] = useState<'ncbi' | 'addgene' | null>(null);
    const [ncbiAccession, setNcbiAccession] = useState('');
    const [addgenePlasmidId, setAddgenePlasmidId] = useState('');
    const [importMessage, setImportMessage] = useState<string | null>(null);
    const [importError, setImportError] = useState<string | null>(null);
    const panelRef = useRef<HTMLDivElement | null>(null);
    const returnFocusRef = useRef<HTMLElement | null>(null);

    useEffect(() => {
        if (!isOpen) return;
        setConfirmingClear(false);
        setImportMessage(null);
        setImportError(null);
    }, [isOpen]);

    const handleAnnotationFile = async (file: File | undefined) => {
        if (!file) return;
        setIsImporting(true);
        setImportMessage(null);
        setImportError(null);
        try {
            setImportMessage(await onImportAnnotations(file));
        } catch (error) {
            setImportError(error instanceof Error ? error.message : 'Annotation import failed.');
        } finally {
            setIsImporting(false);
        }
    };

    const handlePublishedSource = async (
        provider: 'ncbi' | 'addgene',
        value: string,
        retrieve: (identifier: string) => Promise<string>,
    ) => {
        setRetrievingSource(provider);
        setImportMessage(null);
        setImportError(null);
        try {
            setImportMessage(await retrieve(value));
        } catch (error) {
            setImportError(error instanceof Error ? error.message : 'Published annotation retrieval failed.');
        } finally {
            setRetrievingSource(null);
        }
    };

    useEffect(() => {
        if (!isOpen) return;
        returnFocusRef.current = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        const focusableSelector = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
        const frame = window.requestAnimationFrame(() => {
            panelRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
        });
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                onClose();
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(
                panelRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [],
            );
            const target = focusTrapTarget(
                focusable,
                document.activeElement as HTMLElement | null,
                event.shiftKey,
            );
            if (target) {
                event.preventDefault();
                target.focus();
            } else if (focusable.length === 0) {
                event.preventDefault();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => {
            window.cancelAnimationFrame(frame);
            window.removeEventListener('keydown', handleKeyDown);
            restoreFocusIfConnected(returnFocusRef.current, (target) => document.contains(target));
        };
    }, [isOpen, onClose]);

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
            <div
                ref={panelRef}
                role="dialog"
                aria-modal="true"
                aria-label="Auto-Annotate Settings"
                className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-slate-600 bg-slate-800 p-6 shadow-xl"
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
                        <svg className="w-5 h-5 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
                        </svg>
                        Auto-Annotate Settings
                    </h3>
                    <button
                        aria-label="Close auto-annotate settings"
                        onClick={onClose}
                        className="p-1 hover:bg-slate-700 rounded transition-colors"
                    >
                        <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Sequence Info */}
                <div className="mb-4 p-3 bg-slate-900 rounded text-sm text-slate-400">
                    <div className="flex justify-between">
                        <span>Sequence Length:</span>
                        <span className="text-slate-200">{sequenceLength.toLocaleString()} bp</span>
                    </div>
                    <div className="flex justify-between mt-1">
                        <span>Topology:</span>
                        <span className={isCircular ? "text-emerald-400" : "text-slate-200"}>
                            {isCircular ? "Circular" : "Linear"}
                        </span>
                    </div>
                </div>

                {/* Settings */}
                <div className="space-y-4">
                    {/* Minimum Identity Slider */}
                    <div>
                        <label className="flex items-center justify-between text-sm text-slate-300 mb-2">
                            <span>Minimum Identity</span>
                            <span className="text-accent font-mono">{settings.minIdentity}%</span>
                        </label>
                        <input
                            type="range"
                            min="20"
                            max="99"
                            value={settings.minIdentity}
                            onChange={e => setSettings({ ...settings, minIdentity: parseInt(e.target.value) })}
                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-accent"
                        />
                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                            <span>More hits</span>
                            <span>Fewer false positives</span>
                        </div>
                    </div>

                    {/* Detailed Mode Toggle */}
                    <div className="flex items-center justify-between p-3 bg-slate-900 rounded">
                        <div>
                            <div className="text-sm text-slate-300">Detailed Search</div>
                            <div className="text-xs text-slate-500">More thorough but slower, may have more false positives</div>
                        </div>
                        <button
                            onClick={() => setSettings({ ...settings, detailed: !settings.detailed })}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${settings.detailed ? 'bg-accent' : 'bg-slate-600'
                                }`}
                        >
                            <span
                                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${settings.detailed ? 'translate-x-6' : 'translate-x-1'
                                    }`}
                            />
                        </button>
                    </div>

                    {/* Filter Fragments Toggle */}
                    <div className="flex items-center justify-between p-3 bg-slate-900 rounded">
                        <div>
                            <div className="text-sm text-slate-300">Filter Fragments</div>
                            <div className="text-xs text-slate-500">Exclude partial feature matches</div>
                        </div>
                        <button
                            onClick={() => setSettings({ ...settings, filterFragments: !settings.filterFragments })}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${settings.filterFragments ? 'bg-accent' : 'bg-slate-600'
                                }`}
                        >
                            <span
                                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${settings.filterFragments ? 'translate-x-6' : 'translate-x-1'
                                    }`}
                            />
                        </button>
                    </div>
                </div>

                {/* Info */}
                <div className="mt-4 p-3 bg-blue-900/30 border border-blue-800 rounded text-xs text-blue-300">
                    <strong>Databases:</strong> SnapGene, SwissProt, UniProt
                    <br />
                    <strong>Detects:</strong> Origins (ori), Resistance genes (KanR, AmpR), Promoters, Tags, CDS
                </div>

                <div className="mt-4 space-y-3 rounded-lg border border-slate-600 bg-slate-900/70 p-3">
                    <div>
                        <div className="text-sm font-medium text-slate-200">Published annotations</div>
                        <div className="mt-1 text-xs text-slate-400">
                            Transfer features from an authoritative annotated file only when its topology and sequence match this construct exactly. Unique circular rotations and reverse complements are aligned automatically.
                        </div>
                    </div>
                    <label className={`flex w-full items-center justify-center rounded border border-cyan-600 px-3 py-2 text-sm font-medium text-cyan-200 transition-colors ${isImporting || !hasSequence ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:bg-cyan-950/60'}`}>
                        <input
                            type="file"
                            accept=".dna,.gb,.gbk,.genbank"
                            className="sr-only"
                            disabled={isImporting || !hasSequence}
                            onChange={(event) => {
                                void handleAnnotationFile(event.target.files?.[0]);
                                event.target.value = '';
                            }}
                        />
                        {isImporting ? 'Checking annotated file…' : 'Import SnapGene / GenBank annotations'}
                    </label>
                    <div className="space-y-2 rounded border border-slate-700 bg-slate-950/50 p-3">
                        <label className="block text-xs font-medium text-slate-300" htmlFor="annotation-source-ncbi">
                            NCBI nucleotide accession
                        </label>
                        <div className="flex gap-2">
                            <input
                                id="annotation-source-ncbi"
                                type="text"
                                value={ncbiAccession}
                                onChange={(event) => setNcbiAccession(event.target.value)}
                                placeholder="e.g. J01749.1"
                                disabled={retrievingSource !== null || !hasSequence}
                                className="min-w-0 flex-1 rounded border border-slate-600 bg-slate-900 px-2 py-2 text-sm text-slate-100"
                            />
                            <button
                                type="button"
                                disabled={retrievingSource !== null || !hasSequence || !ncbiAccession.trim()}
                                onClick={() => void handlePublishedSource('ncbi', ncbiAccession, onRetrieveNcbi)}
                                className="rounded border border-cyan-700 px-3 py-2 text-xs text-cyan-200 disabled:cursor-not-allowed disabled:opacity-40"
                            >Retrieve NCBI annotations</button>
                        </div>
                    </div>
                    <div className="space-y-2 rounded border border-slate-700 bg-slate-950/50 p-3">
                        <label className="block text-xs font-medium text-slate-300" htmlFor="annotation-source-addgene">
                            Addgene plasmid ID
                        </label>
                        <div className="flex gap-2">
                            <input
                                id="annotation-source-addgene"
                                type="text"
                                inputMode="numeric"
                                value={addgenePlasmidId}
                                onChange={(event) => setAddgenePlasmidId(event.target.value)}
                                placeholder="e.g. 10878"
                                disabled={retrievingSource !== null || !hasSequence || annotationSourceStatus?.addgene.available !== true}
                                className="min-w-0 flex-1 rounded border border-slate-600 bg-slate-900 px-2 py-2 text-sm text-slate-100"
                            />
                            <button
                                type="button"
                                disabled={retrievingSource !== null || !hasSequence || !addgenePlasmidId.trim() || annotationSourceStatus?.addgene.available !== true}
                                onClick={() => void handlePublishedSource('addgene', addgenePlasmidId, onRetrieveAddgene)}
                                className="rounded border border-cyan-700 px-3 py-2 text-xs text-cyan-200 disabled:cursor-not-allowed disabled:opacity-40"
                            >Retrieve Addgene annotations</button>
                        </div>
                        {annotationSourceStatus?.addgene.available === false && (
                            <div className="text-xs text-amber-300">Addgene API token is not configured on the server.</div>
                        )}
                        {annotationSourceStatus === null && (
                            <div className="text-xs text-slate-500">Checking Addgene API availability…</div>
                        )}
                    </div>
                    {retrievingSource && (
                        <div role="status" className="text-xs text-cyan-300">
                            Retrieving {retrievingSource === 'ncbi' ? 'NCBI' : 'Addgene'} GenBank annotations…
                        </div>
                    )}
                    {importMessage && (
                        <div role="status" className="rounded border border-emerald-700 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-300">
                            {importMessage}
                        </div>
                    )}
                    {importError && (
                        <div role="alert" className="rounded border border-red-700 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                            {importError}
                        </div>
                    )}
                </div>

                <div className="mt-4 rounded-lg border border-red-900/70 bg-red-950/20 p-3">
                    {!confirmingClear ? (
                        <button
                            type="button"
                            onClick={() => setConfirmingClear(true)}
                            disabled={featureCount === 0 || isAnnotating || isImporting}
                            className="w-full rounded border border-red-700 px-3 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-950/60 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            Clear all feature annotations ({featureCount})
                        </button>
                    ) : (
                        <div>
                            <div className="text-sm font-medium text-red-200">Clear {featureCount} feature annotations?</div>
                            <div className="mt-1 text-xs text-red-300/80">Primers and sequence bases will be preserved. This action can be undone.</div>
                            <div className="mt-3 flex justify-end gap-2">
                                <button
                                    type="button"
                                    onClick={() => setConfirmingClear(false)}
                                    className="rounded px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
                                >
                                    Keep annotations
                                </button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        onClearAnnotations();
                                        setConfirmingClear(false);
                                    }}
                                    className="rounded bg-red-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600"
                                >
                                    Confirm clear
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {/* Actions */}
                <div className="flex justify-end gap-2 mt-6">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-sm text-slate-300 hover:bg-slate-700 rounded transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => onAnnotate(settings)}
                        disabled={isAnnotating || !hasSequence}
                        className="flex items-center gap-2 px-4 py-2 text-sm bg-accent hover:bg-accent disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-white transition-colors"
                    >
                        {isAnnotating ? (
                            <>
                                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                </svg>
                                Detecting...
                            </>
                        ) : (
                            <>
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
                                </svg>
                                Detect Features
                            </>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
