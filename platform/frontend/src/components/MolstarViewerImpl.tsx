import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import 'molstar/build/viewer/molstar.css';

import {
    MolstarDirectAdapter,
    MolstarDirectAdapterCancelledError,
} from '../structureViewer/adapters/MolstarDirectAdapter';
import type {
    MolstarDirectDocument,
    MolstarDirectResidueClick,
} from '../structureViewer/adapters/MolstarDirectAdapter';
import type { ResidueColor } from '../structureViewer/adapters/residueColorSelections';
import type { MolstarResidueMetricLayer } from '../lib/molstar-metrics';
import { createStructureSceneState } from '../structureViewer/contracts/sceneState';
import type { MDSceneState } from '../structureViewer/contracts/mdTrajectory';
import type { ViewerMeasurement } from '../structureViewer/contracts/measurements';
import type { StructureScenePresentation } from '../structureViewer/contracts/scenePresentation';
import type { ViewerEvent } from '../structureViewer/contracts/viewerEvents';
import { MolstarDirectSceneEngineAdapter } from '../structureViewer/runtime/MolstarDirectSceneEngineAdapter';
import type { EngineResidueClick } from '../structureViewer/runtime/MolstarEngineAdapter';
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
    /** Every controller event with viewer/scene/generation/document provenance. */
    onViewerEvent?: (event: ViewerEvent) => void;
    /** Exact, provenance-bound atom measurements reconciled declaratively. */
    measurements?: readonly ViewerMeasurement[];
    /** Complete declarative presentation state owned by the shared scene controller. */
    scenePresentation?: StructureScenePresentation;
    /** Monotonic token; changing it resets the camera to the complete scene bounds. */
    cameraResetToken?: number;
    /** Governed, hash-bound MD metadata; playback remains capability-gated by the direct adapter. */
    molecularDynamics?: MDSceneState;
    /** Shared-controller bridge for governed M6 workbench controls; never owns lifecycle. */
    onControllerReady?: (controller: StructureSceneController | null) => void;
    /** Runtime-only job scope for authorized viewer artifacts; never enters scene state. */
    artifactJobId?: string;
    /** Governed primary structure identity used for cross-artifact registration. */
    structureDocumentId?: string;
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

    overlayStructures,
    onResidueClick,
    onViewerEvent,
    measurements,
    scenePresentation,
    cameraResetToken,
    molecularDynamics,
    onControllerReady,
    artifactJobId,
    structureDocumentId = 'primary',
}: MolstarViewerProps) {
    const mountRef = useRef<HTMLDivElement>(null);
    const adapterRef = useRef<MolstarDirectAdapter | null>(null);
    const controllerRef = useRef<StructureSceneController | null>(null);
    const sceneRequestGenerationRef = useRef(0);
    const latestMeasurementsRef = useRef(measurements);
    const latestScenePresentationRef = useRef(scenePresentation);
    const appliedCameraResetTokenRef = useRef(cameraResetToken);
    const viewerIdRef = useRef(`molstar-viewer-${crypto.randomUUID()}`);
    const [status, setStatus] = useState<ViewerStatus>('idle');
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    useEffect(() => {
        latestMeasurementsRef.current = measurements;
        latestScenePresentationRef.current = scenePresentation;
    }, [measurements, scenePresentation]);


    const absoluteUrl = useMemo(() => toAbsoluteStructureUrl(structureUrl), [structureUrl]);
    const normalizedBackgroundColor = useMemo(
        () => normalizeBackgroundColor(backgroundColor),
        [backgroundColor],
    );
    const effectiveAlphafoldView = alphafoldView && scenePresentation === undefined;

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
            id: structureDocumentId,
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
    }, [absoluteUrl, format, overlayStructures, structureDocumentId]);

    const buildRequestedScene = useCallback(() => {
        const primaryDocument = documents[0];
        if (!primaryDocument) return undefined;
        const presentation = latestScenePresentationRef.current;
        return createStructureSceneState({
            ref: {
                viewerId: viewerIdRef.current,
                sceneId: `${viewerIdRef.current}-scene`,
                generation: ++sceneRequestGenerationRef.current,
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
            presentation: {
                ...presentation,
                measurements: latestMeasurementsRef.current ?? presentation?.measurements,
            },
            molecularDynamics,
        });
    }, [documents, molecularDynamics]);

    const hasStructure = Boolean(absoluteUrl);
    const adapterSignature = useMemo(() => JSON.stringify({
        hideControls,
        effectiveAlphafoldView,
        normalizedBackgroundColor,
        artifactJobId,
        hasGovernedMDPlayback: molecularDynamics?.playbackCapability.supported === true,
    }), [artifactJobId, effectiveAlphafoldView, hideControls, molecularDynamics?.playbackCapability.supported, normalizedBackgroundColor]);
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
            artifactJobId?: string;
            hasGovernedMDPlayback: boolean;
        };
        let cancelled = false;
        const adapter = new MolstarDirectAdapter({
            hideControls: options.hideControls,
            alphafoldView: options.effectiveAlphafoldView,
            backgroundColor: options.normalizedBackgroundColor,
            resolveViewerArtifactUrl: options.artifactJobId
                ? (artifactId) => `/api/jobs/${encodeURIComponent(options.artifactJobId!)}/${options.hasGovernedMDPlayback ? 'md' : 'viewer'}/artifacts/${encodeURIComponent(artifactId)}/content`
                : undefined,
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
        const controller = adapterEpoch > 0 ? controllerRef.current : null;
        onControllerReady?.(controller);
        return () => onControllerReady?.(null);
    }, [adapterEpoch, onControllerReady]);

    useEffect(() => {
        const controller = controllerRef.current;
        if (!controller || (!onViewerEvent && !onResidueClick)) return undefined;
        return controller.subscribe((event) => {
            onViewerEvent?.(event);
            if (event.type !== 'selection-changed' || event.origin !== 'canvas' || !onResidueClick) return;
            const click = event.payload as EngineResidueClick;
            const residue = click.residue;
            if (!residue.documentId || !residue.labelAsymId || !residue.authAsymId || residue.labelSeqId === undefined || residue.authSeqId === undefined) return;
            onResidueClick({
                documentId: residue.documentId,
                labelAsymId: residue.labelAsymId,
                authAsymId: residue.authAsymId,
                labelSeqId: residue.labelSeqId,
                authSeqId: residue.authSeqId,
                insertionCode: residue.insertionCode ?? '',
                sceneGeneration: click.engineGeneration,
            });
        });
    }, [adapterEpoch, onResidueClick, onViewerEvent]);

    useEffect(() => {
        const controller = controllerRef.current;
        const initialScene = buildRequestedScene();
        if (!controller || adapterEpoch === 0 || !initialScene) return undefined;
        if (initialScene.status !== 'ok') {
            const error = initialScene.status === 'error'
                ? initialScene.error
                : new Error(initialScene.reason);
            setErrorMessage(formatError(error));
            setStatus('error');
            return undefined;
        }

        let cancelled = false;
        setStatus('loading');
        setErrorMessage(null);
        void (async () => {
            let result = await controller.loadScene(initialScene.value);
            if (cancelled || controllerRef.current !== controller || result.status === 'cancelled') return;
            if (result.status === 'ok') {
                const latestScene = buildRequestedScene();
                if (latestScene?.status === 'ok') {
                    result = await controller.reconcileScene(latestScene.value);
                } else if (latestScene) {
                    result = latestScene;
                }
            }
            if (cancelled || controllerRef.current !== controller || result.status === 'cancelled') return;
            if (result.status === 'ok') {
                setStatus('ready');
                return;
            }
            const error = result.status === 'error' ? result.error : new Error(result.reason);
            console.error('Failed to load direct Mol* scene:', error);
            setErrorMessage(formatError(error));
            setStatus('error');
        })();
        return () => {
            cancelled = true;
        };
    }, [adapterEpoch, buildRequestedScene]);

    useEffect(() => {
        const controller = controllerRef.current;
        const currentScene = controller?.currentScene;
        const requestedScene = buildRequestedScene();
        if (!controller || !currentScene || !requestedScene || requestedScene.status !== 'ok') return;
        const currentDocuments = currentScene.documents.map((document) => `${document.documentId}:${document.sourceUrl}`).join('|');
        const requestedDocuments = requestedScene.value.documents.map((document) => `${document.documentId}:${document.sourceUrl}`).join('|');
        if (currentDocuments !== requestedDocuments) return;

        let cancelled = false;
        void controller.reconcileScene(requestedScene.value).then((result) => {
            if (cancelled || controllerRef.current !== controller || result.status === 'cancelled') return;
            if (result.status === 'ok') {
                setStatus('ready');
                return;
            }
            const error = result.status === 'error' ? result.error : new Error(result.reason);
            console.error('Failed to reconcile direct Mol* presentation:', error);
            setErrorMessage(formatError(error));
            setStatus('error');
        });
        return () => {
            cancelled = true;
        };
    }, [adapterEpoch, buildRequestedScene, measurements, scenePresentation]);

    useEffect(() => {
        if (status !== 'ready' || cameraResetToken === undefined || cameraResetToken === appliedCameraResetTokenRef.current) return;
        const result = adapterRef.current?.resetCamera();
        if (result?.status === 'ok') appliedCameraResetTokenRef.current = cameraResetToken;
    }, [cameraResetToken, status]);


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
