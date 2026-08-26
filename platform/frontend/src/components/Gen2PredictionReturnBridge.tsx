import type { MolecularDynamicsStartingStructureRef } from './molecularDynamicsUiState';

export interface Gen2PredictionCandidate {
    source_ref: MolecularDynamicsStartingStructureRef;
    name: string;
    format: 'pdb' | 'cif';
    eligible: boolean;
    blocker_code: string | null;
    metrics: { plddt: number | null; ptm: number | null; iptm: number | null; confidence: number | null };
    created_at?: string | null;
}

export interface Gen2PredictionPage {
    schema_version: 'bms.md.prediction-source-candidates.v1';
    job: { id: string; name: string; status: string; failure: { code: string; message: string } | null };
    candidates: Gen2PredictionCandidate[];
    next_cursor: string | null;
}

export function Gen2PredictionReturnBridge({ page, selectedId, onSelect, onRunAnother }: {
    page: Gen2PredictionPage;
    selectedId: string | null;
    onSelect: (candidate: Gen2PredictionCandidate) => void;
    onRunAnother: () => void;
}) {
    return (
        <section className="space-y-3" aria-label="Returned Structure Prediction Job">
            <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                <div><div className="text-sm font-semibold text-slate-100">{page.job.name}</div><div className="text-xs text-slate-400">Job {page.job.id} · {page.job.status}</div></div>
                <button type="button" onClick={onRunAnother} className="min-h-11 rounded-lg border border-slate-600 px-3 py-2 text-xs text-slate-200">Run another prediction</button>
            </div>
            {page.job.failure && <div role="alert" className="rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">{page.job.failure.message}</div>}
            <div className="grid gap-2 sm:grid-cols-2">
                {page.candidates.map((candidate) => (
                    <button key={candidate.source_ref.id} type="button" aria-pressed={candidate.source_ref.id === selectedId} disabled={!candidate.eligible} onClick={() => onSelect(candidate)} className={`min-h-11 rounded-lg border p-3 text-left text-xs ${candidate.source_ref.id === selectedId ? 'border-cyan-500 bg-cyan-500/10 text-cyan-100' : 'border-slate-700 text-slate-300'} disabled:opacity-50`}>
                        <span className="block font-semibold">{candidate.name}</span>
                        <span className="mt-1 block text-slate-500">{candidate.format.toUpperCase()} · pLDDT {candidate.metrics.plddt ?? 'unavailable'}</span>
                    </button>
                ))}
            </div>
        </section>
    );
}
