import { Component, type ErrorInfo, type ReactNode } from 'react';

interface StructureViewerErrorBoundaryProps {
    readonly children: ReactNode;
    readonly resetKey: string;
    readonly height?: number | string;
}

interface StructureViewerErrorBoundaryState {
    readonly error: Error | null;
}

const heightCss = (height: number | string | undefined): number | string => (
    typeof height === 'number' ? `${height}px` : (height ?? 480)
);

export class StructureViewerErrorBoundary extends Component<
    StructureViewerErrorBoundaryProps,
    StructureViewerErrorBoundaryState
> {
    state: StructureViewerErrorBoundaryState = { error: null };

    static getDerivedStateFromError(error: unknown): StructureViewerErrorBoundaryState {
        return { error: error instanceof Error ? error : new Error(String(error)) };
    }

    componentDidCatch(error: Error, info: ErrorInfo): void {
        console.error('Structure viewer boundary caught an error:', error, info.componentStack);
    }

    componentDidUpdate(previous: StructureViewerErrorBoundaryProps): void {
        if (previous.resetKey !== this.props.resetKey && this.state.error) {
            this.setState({ error: null });
        }
    }

    render(): ReactNode {
        if (!this.state.error) return this.props.children;
        return (
            <div
                role="alert"
                data-bms-molstar-status="error-boundary"
                className="flex w-full items-center justify-center rounded-lg border border-red-900 bg-slate-950 p-4 text-center"
                style={{ height: heightCss(this.props.height) }}
            >
                <div className="max-w-lg">
                    <div className="font-medium text-red-300">Unable to render the 3D structure viewer</div>
                    <div className="mt-2 break-words text-xs text-slate-400">{this.state.error.message}</div>
                    <div className="mt-2 text-xs text-slate-500">Select another structure or reopen this view to retry.</div>
                </div>
            </div>
        );
    }
}
