import { lazy, Suspense } from 'react';

import type { StructureViewerHostProps } from '../structureViewer/StructureViewerHost';
import { StructureViewerErrorBoundary } from '../structureViewer/StructureViewerErrorBoundary';

const LazyStructureViewerHost = lazy(() => import('../structureViewer/StructureViewerHost'));

export type MolstarViewerProps = StructureViewerHostProps;

const heightCss = (height: number | string | undefined): string | number => (
    typeof height === 'number' ? `${height}px` : (height ?? 480)
);

export default function MolstarViewer(props: StructureViewerHostProps) {
    const hasGovernedMDPlayback = props.molecularDynamics?.playbackCapability.supported === true;
    if (!props.structureUrl && !hasGovernedMDPlayback) {
        return (
            <div
                className="w-full flex items-center justify-center text-slate-500 bg-slate-900"
                style={{ height: heightCss(props.height) }}
            >
                Select a design to view structure
            </div>
        );
    }

    return (
        <StructureViewerErrorBoundary
            resetKey={`${props.structureUrl}:${props.format ?? 'pdb'}`}
            height={props.height}
        >
        <Suspense
            fallback={(
                <div
                    className="w-full flex items-center justify-center text-slate-300 bg-slate-900"
                    style={{ height: heightCss(props.height) }}
                    data-bms-molstar-status="chunk-loading"
                >
                    Loading 3D viewer…
                </div>
            )}
        >
            <LazyStructureViewerHost {...props} />
        </Suspense>
        </StructureViewerErrorBoundary>
    );
}
