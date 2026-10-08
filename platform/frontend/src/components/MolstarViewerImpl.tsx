import { useEffect, useMemo, useRef, useState } from 'react';

import 'molstar/build/viewer/molstar.css';

import {
    MolstarDirectAdapter,
    MolstarDirectAdapterCancelledError,
} from '../structureViewer/adapters/MolstarDirectAdapter';
import type {
    MolstarDirectDocument,
    MolstarDirectPresentation,
    MolstarDirectQuery,
    MolstarDirectResidueClick,
} from '../structureViewer/adapters/MolstarDirectAdapter';
import { adaptLegacyResidueColors } from '../structureViewer/adapters/residueColorSelections';
import type { ResidueColor } from '../structureViewer/adapters/residueColorSelections';
import { adaptResidueMetricLayer } from '../lib/molstar-metrics';
import type { MolstarResidueMetricLayer } from '../lib/molstar-metrics';
import { createStructureSceneState } from '../structureViewer/contracts/sceneState';
import type { ViewerMeasurement } from '../structureViewer/contracts/measurements';
import { MolstarDirectSceneEngineAdapter } from '../structureViewer/runtime/MolstarDirectSceneEngineAdapter';
import { StructureSceneController } from '../structureViewer/runtime/StructureSceneController';
import {
    MOLSTAR_TOUCH_INTERACTION_SELECTOR,
    resolveMolstarTouchAction,
} from './molstarTouchInteraction';

interface Selection {
    chain_id?: string;
    start_residue_number?: number;
    end_residue_number?: number;
    color?: { r: number; g: number; b: number };
    focus?: boolean;
}

interface OverlayStructure {
    id: string;
    structureUrl: string;
    format?: 'cif' | 'pdb';
    label?: string;
}

export interface MolstarViewerProps {
    structureUrl?: string;
    format?: 'cif' | 'pdb';
    alphafoldView?: boolean;
    hideControls?: boolean;
    height?: number | string;
    backgroundColor?: string;
    label?: string;
    selections?: Selection[];
    /** Legacy BMS residue color map keyed by `chain:residue` or `A42`. */
    residueColors?: ReadonlyMap<string, ResidueColor>;
    /** Canonical chain- and numbering-aware scalar residue metric layer. */
    residueMetricLayer?: MolstarResidueMetricLayer;
    /** Additional structures loaded into the same Mol* scene after the primary structure. */
    overlayStructures?: OverlayStructure[];
    /** Viewer-scoped residue clicks with exact label and author identity. */
    onResidueClick?: (residue: MolstarDirectResidueClick) => void;
    /** Exact, provenance-bound atom measurements reconciled declaratively. */
    measurements?: readonly ViewerMeasurement[];
}

type ViewerStatus = 'idle' | 'loading' | 'ready' | 'error';

const toAbsoluteStructureUrl = (structureUrl?: string): string | null => {
    if (!structureUrl) return null;
    if (structureUrl.startsWith('/')) return `${window.location.origin}${structureUrl}`;
    return structureUrl;
};

const toMolstarLoadFormat = (format: 'cif' | 'pdb' | undefined): 'mmcif' | 'pdb' => (
    format === 'cif' || !format ? 'mmcif' : 'pdb'
);

const normalizeBackgroundColor = (backgroundColor: string): string => (
    /^#[0-9a-f]{6}$/i.test(backgroundColor) ? backgroundColor : '#0f172a'
);

const formatError = (error: unknown): string => {
    if (error instanceof Error && error.message) return error.message;
    return 'Unknown Mol* viewer error';
};

export default function MolstarViewer({
    structureUrl,
    format = 'pdb',
    alphafoldView = true,
    hideControls = false,
    height = 500,
    backgroundColor = '#0f172a',
    label,
    selections,
    residueColors,
    residueMetricLayer,
    overlayStructures,
    onResidueClick,
    measurements,
}: MolstarViewerProps) {
    const mountRef = useRef<HTMLDivElement>(null);
    const adapterRef = useRef<MolstarDirectAdapter | null>(null);
    const controllerRef = useRef<StructureSceneController | null>(null);
    const sceneRequestGenerationRef = useRef(0);
    const viewerIdRef = useRef(`molstar-viewer-${crypto.randomUUID()}`);
    const [status, setStatus] = useState<ViewerStatus>('idle');
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [measurementError, setMeasurementError] = useState<string | null>(null);

    const absoluteUrl = useMemo(() => toAbsoluteStructureUrl(structureUrl), [structureUrl]);
    const normalizedBackgroundColor = useMemo(
        () => normalizeBackgroundColor(backgroundColor),
        [backgroundColor],
    );
    const effectiveAlphafoldView = alphafoldView
        && !residueColors?.size
        && !residueMetricLayer?.points.length;

    const interactionTouchAction = useMemo(() => {
        const coarsePointer = typeof window.matchMedia === 'function'
            && (window.matchMedia('(unknown-pointer: coarse)').matches
                || window.matchMedia('(pointer: coarse)').matches);
        return resolveMolstarTouchAction({
            maxTouchPoints: navigator.maxTouchPoints ?? 0,
            coarsePointer,
        });
    }, []);

    const documents = useMemo<readonly MolstarDirectDocument[]>(() => {
        if (!absoluteUrl) return [];
        const primary: MolstarDirectDocument = {
            id: 'primary',
            url: absoluteUrl,
            format: toMolstarLoadFormat(format),
        };
        const overlays = (overlayStructures ?? []).flatMap((overlay) => {
            const url = toAbsoluteStructureUrl(overlay.structureUrl);
            return url ? [{
                id: `overlay:${overlay.id}`,
                url,
                format: toMolstarLoadFormat(overlay.format),
            } satisfies MolstarDirectDocument] : [];
        });
        return [primary, ...overlays];
    }, [absoluteUrl, format, overlayStructures]);

    const hasStructure = Boolean(absoluteUrl);
    const adapterSignature = useMemo(() => JSON.stringify({
        hideControls,
        effectiveAlphafoldView,
        normalizedBackgroundColor,
    }), [effectiveAlphafoldView, hideControls, normalizedBackgroundColor]);
    const [adapterEpoch, setAdapterEpoch] = useState(0);

    useEffect(() => {
        const target = mountRef.current;
        if (!target || !hasStructure) {
            if (controllerRef.current) void controllerRef.current.dispose();
            else adapterRef.current?.dispose();
            controllerRef.current = null;
            adapterRef.current = null;
            setStatus('idle');
            setErrorMessage(null);
            return undefined;
        }

        const options = JSON.parse(adapterSignature) as {
            hideControls: boolean;
            effectiveAlphafoldView: boolean;
            normalizedBackgroundColor: string;
        };
        let cancelled = false;
        const adapter = new MolstarDirectAdapter({
            hideControls: options.hideControls,
            alphafoldView: options.effectiveAlphafoldView,
            backgroundColor: options.normalizedBackgroundColor,
        });
        const controller = new StructureSceneController(new MolstarDirectSceneEngineAdapter(adapter));
        adapterRef.current = adapter;
        controllerRef.current = controller;
        setStatus('loading');
        setErrorMessage(null);

        void adapter.mount(target).then(() => {
            if (!cancelled && adapterRef.current === adapter) {
                setAdapterEpoch((value) => value + 1);
            }
        }).catch((error) => {
            if (cancelled || error instanceof MolstarDirectAdapterCancelledError) return;
            console.error('Failed to initialize direct Mol* viewer:', error);
            if (adapterRef.current === adapter) {
                setErrorMessage(formatError(error));
                setStatus('error');
            }
        });

        return () => {
            cancelled = true;
            if (adapterRef.current === adapter) adapterRef.current = null;
            if (controllerRef.current === controller) controllerRef.current = null;
            void controller.dispose();
        };
    }, [adapterSignature, hasStructure]);

    useEffect(() => {
        const adapter = adapterRef.current;
        adapter?.setResidueClickHandler(onResidueClick);
        return () => {
            if (adapterRef.current === adapter) adapter?.setResidueClickHandler(undefined);
        };
    }, [adapterEpoch, onResidueClick]);

    useEffect(() => {
        const adapter = adapterRef.current;
        const controller = controllerRef.current;
        const primaryDocument = documents[0];
        if (!adapter || !controller || adapterEpoch === 0 || !primaryDocument) return undefined;

        const sceneGeneration = ++sceneRequestGenerationRef.current;
        const sceneResult = createStructureSceneState({
            ref: {
                viewerId: viewerIdRef.current,
                sceneId: `${viewerIdRef.current}-scene`,
                generation: sceneGeneration,
            },
            documents: documents.map((document) => ({
                documentId: document.id,
                sourceKind: document.format === 'pdb'
                    ? 'pdb'
                    : document.format === 'sdf' ? 'sdf' : 'mmcif',
                sourceUrl: document.url,
            })),
            ...(documents.length > 1 ? {
                collection: {
                    kind: 'independent_hypotheses' as const,
                    orderedDocumentIds: documents.map((document) => document.id),
                },
            } : {}),
            activeDocumentId: primaryDocument.id,
            provenance: {
                createdBy: 'MolstarViewer compatibility facade',
                createdAt: new Date().toISOString(),
            },
        });
        if (sceneResult.status !== 'ok') {
            const error = sceneResult.status === 'error'
                ? sceneResult.error
                : new Error(sceneResult.reason);
            setErrorMessage(formatError(error));
            setStatus('error');
            return undefined;
        }

        let cancelled = false;
        setStatus('loading');
        setErrorMessage(null);
        void controller.loadScene(sceneResult.value).then((result) => {
            if (cancelled || controllerRef.current !== controller || result.status === 'cancelled') return;
            if (result.status === 'ok') {
                setStatus('ready');
                return;
            }
            const error = result.status === 'error' ? result.error : new Error(result.reason);
            console.error('Failed to load direct Mol* scene:', error);
            setErrorMessage(formatError(error));
            setStatus('error');
        });
        return () => {
            cancelled = true;
        };
    }, [adapterEpoch, documents]);

    useEffect(() => {
        const adapter = adapterRef.current;
        if (!adapter || adapterEpoch === 0 || documents.length === 0) return undefined;
        let cancelled = false;
        void adapter.setMeasurements(measurements ?? []).then((result) => {
            if (cancelled || adapterRef.current !== adapter || result.status === 'cancelled') return;
            if (result.status === 'ok') {
                setMeasurementError(null);
                return;
            }
            setMeasurementError(result.status === 'error' ? result.error.message : result.reason);
        });
        return () => {
            cancelled = true;
        };
    }, [adapterEpoch, documents, measurements]);

    const adaptedResidueColors = useMemo(
        () => residueColors?.size ? adaptLegacyResidueColors(residueColors) : null,
        [residueColors],
    );

    const presentation = useMemo<MolstarDirectPresentation>(() => {
        // Explicit workflow selections take precedence over a metric layer, matching
        // BMS color-mode behavior while avoiding competing asynchronous overpaint.
        if (selections && selections.length > 0) {
            const colorSelections: MolstarDirectQuery[] = selections.map((selection) => ({
                // BMS Selection.chain_id is explicitly the label/struct namespace.
                // Do not also populate auth_asym_id: mixed namespaces fail closed.
                struct_asym_id: selection.chain_id,
                start_residue_number: selection.start_residue_number,
                end_residue_number: selection.end_residue_number,
                color: selection.color,
                focus: selection.focus,
            }));
            return {
                colorSelections,
                nonSelectedColor: '#888888',
            };
        }

        if (adaptedResidueColors?.selections.length) {
            return {
                colorSelections: adaptedResidueColors.selections,
                nonSelectedColor: { r: 68, g: 68, b: 68 },
            };
        }

        if (residueMetricLayer?.points.length) {
            const adapted = adaptResidueMetricLayer(residueMetricLayer);
            return {
                colorSelections: adapted.colorSelections,
                tooltipSelections: adapted.tooltipSelections,
                nonSelectedColor: residueMetricLayer.nonSelectedColor ?? { r: 68, g: 68, b: 68 },
            };
        }
        return {};
    }, [adaptedResidueColors, residueMetricLayer, selections]);

    useEffect(() => {
        const adapter = adapterRef.current;
        if (status !== 'ready' || !adapter) return undefined;

        let cancelled = false;
        if (adaptedResidueColors?.rejected.length) {
            console.warn('Mol* residue color adapter rejected entries:', adaptedResidueColors.rejected);
        }
        if (!selections?.length && residueMetricLayer?.points.length) {
            const adapted = adaptResidueMetricLayer(residueMetricLayer);
            if (adapted.rejected.length > 0) {
                console.warn('Mol* metric adapter rejected residue points:', adapted.rejected);
            }
            if (adapted.colorSelections.length === 0) {
                console.error('Mol* metric layer has no selectable, explicitly numbered residues.');
            }
        }

        void adapter.applyPresentation(presentation).catch((error) => {
            if (cancelled || error instanceof MolstarDirectAdapterCancelledError) return;
            console.error('Failed to apply direct Mol* presentation:', error);
        });
        return () => {
            cancelled = true;
        };
    }, [adaptedResidueColors, presentation, residueMetricLayer, selections, status]);

    useEffect(() => {
        const target = mountRef.current;
        if (!target || status !== 'ready' || interactionTouchAction !== 'none') return undefined;

        const applyTouchAction = () => {
            target.style.touchAction = interactionTouchAction;
            target.querySelectorAll<HTMLElement>(MOLSTAR_TOUCH_INTERACTION_SELECTOR).forEach((element) => {
                element.style.touchAction = interactionTouchAction;
            });
        };
        applyTouchAction();
        const observer = new MutationObserver(applyTouchAction);
        observer.observe(target, { childList: true, subtree: true });
        return () => observer.disconnect();
    }, [interactionTouchAction, status]);

    const heightStyle = typeof height === 'number' ? `${height}px` : height;
    if (!absoluteUrl) {
        return (
            <div
                className="w-full flex items-center justify-center text-slate-500 bg-slate-900"
                style={{ height: heightStyle }}
            >
                Select a design to view structure
            </div>
        );
    }

    return (
        <div
            className="w-full rounded-lg overflow-hidden relative bg-slate-900"
            style={{ height: heightStyle }}
            data-bms-molstar-adapter="direct-4.5.0"
            data-bms-molstar-status={status}
        >
            <div
                ref={mountRef}
                className="absolute inset-0"
                style={{ touchAction: interactionTouchAction }}
                data-bms-molstar-mount="true"
            />
            {label && (
                <div className="absolute top-2 left-2 z-20 px-2 py-1 bg-slate-800/80 text-slate-200 text-xs rounded font-medium pointer-events-none">
                    {label}
                </div>
            )}
            {measurementError && (
                <div
                    className="absolute bottom-2 left-2 right-2 z-20 px-2 py-1 bg-amber-950/90 text-amber-200 text-xs rounded"
                    role="status"
                    data-bms-measurement-error="true"
                >
                    Measurements unavailable: {measurementError}
                </div>
            )}
            {status === 'loading' && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-900/70 pointer-events-none">
                    <div className="text-slate-300 flex items-center gap-2">
                        <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                        Loading Mol* viewer...
                    </div>
                </div>
            )}
            {status === 'error' && (
                <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/90 p-4">
                    <div className="max-w-lg text-center">
                        <div className="text-red-300 font-medium">Unable to load structure viewer</div>
                        <div className="mt-2 text-xs text-slate-400 break-words">{errorMessage}</div>
                    </div>
                </div>
            )}
        </div>
    );
}
