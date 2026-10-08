import { lazy, Suspense } from 'react';

import type { EpitopeMolstarViewerProps } from './EpitopeMolstarViewerImpl';

const LazyEpitopeMolstarViewer = lazy(() => import('./EpitopeMolstarViewerImpl'));

export type { EpitopeMolstarViewerProps } from './EpitopeMolstarViewerImpl';

const heightCss = (height: number | string | undefined): string | number => (
    typeof height === 'number' ? `${height}px` : (height ?? 400)
);

export default function EpitopeMolstarViewer(props: EpitopeMolstarViewerProps) {
    return (
        <Suspense
            fallback={(
                <div
                    className="w-full flex items-center justify-center text-slate-300 bg-slate-900"
                    style={{ height: heightCss(props.height) }}
                    data-bms-epitope-molstar-status="chunk-loading"
                >
                    Loading epitope viewer…
                </div>
            )}
        >
            <LazyEpitopeMolstarViewer {...props} />
        </Suspense>
    );
}
