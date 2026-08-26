import MolstarViewer from './MolstarViewer';
import type { MolecularDynamicsStartingStructureInspection } from './molecularDynamicsUiState';

export function Gen2StructureReviewPanel({
    inspection,
    viewerState,
    viewerError,
    viewerCurrent,
    promoted,
    actionLabel = 'Use this structure',
    onLoadStateChange,
    onPromote,
}: {
    inspection: MolecularDynamicsStartingStructureInspection | null;
    viewerState: 'idle' | 'loading' | 'loaded' | 'failed';
    viewerError: string;
    viewerCurrent: boolean;
    promoted: boolean;
    actionLabel?: string;
    onLoadStateChange: (state: 'loading' | 'loaded' | 'failed', errorMessage?: string) => void;
    onPromote: () => void;
}) {
    return (
        <div className="min-h-64 overflow-hidden rounded-xl border border-slate-800 bg-slate-950/45" data-gen2-structure-review>
            {inspection ? <>
                <MolstarViewer structureUrl={inspection.viewer.url} format={inspection.viewer.format} height={320} onLoadStateChange={onLoadStateChange} />
                <div className="space-y-2 border-t border-slate-800 p-4 text-xs">
                    <div className="font-semibold text-slate-200">{inspection.identity.label}</div>
                    <div className="text-slate-400">{inspection.inspection.atom_count.toLocaleString()} atoms · {inspection.inspection.model_count} model{inspection.inspection.model_count === 1 ? '' : 's'} · chains {inspection.inspection.chains.join(', ')}</div>
                    <div className="break-all font-mono text-[10px] text-slate-500">SHA-256 {inspection.identity.sha256}</div>
                    <div className={inspection.admission.state === 'admitted' ? 'text-cyan-300' : 'text-amber-300'}>{inspection.admission.message}</div>
                    {viewerState === 'loading' && <div className="text-cyan-300">Mol* is loading the inspected structure…</div>}
                    {viewerState === 'failed' && <div role="alert" className="text-red-300">Mol* could not display this structure. {viewerError}</div>}
                    <button type="button" disabled={!viewerCurrent} onClick={onPromote} className="min-h-11 w-full rounded-lg border border-cyan-500/50 px-3 py-2 text-sm font-semibold text-cyan-200 disabled:border-slate-700 disabled:text-slate-500">{actionLabel}</button>
                    {promoted && <div className="text-cyan-300">Starting structure promoted to chemistry.</div>}
                </div>
            </> : <div className="flex min-h-80 items-center justify-center p-8 text-center text-sm text-slate-500">Inspect a source to bind exact bytes, composition, provenance, and profile admission.</div>}
        </div>
    );
}
