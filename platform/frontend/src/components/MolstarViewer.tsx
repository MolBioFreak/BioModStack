import { lazy, Suspense } from 'react';

import type { MolstarViewerProps } from './MolstarViewerImpl';

const LazyMolstarViewer = lazy(() => import('./MolstarViewerImpl'));

export type { MolstarViewerProps } from './MolstarViewerImpl';

const heightCss = (height: number | string | undefined): string | number => (
    typeof height === 'number' ? `${height}px` : (height ?? 480)
);

export default function MolstarViewer(props: MolstarViewerProps) {
    if (!props.structureUrl) {
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
            <LazyMolstarViewer {...props} />
        </Suspense>
    );
}
