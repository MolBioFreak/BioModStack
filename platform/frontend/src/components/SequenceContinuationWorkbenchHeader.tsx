import { sequenceDestinations } from '../lib/deNovoContinuation';

interface ContinuationChainSummary {
    id: string;
    auth_asym_id?: string;
    model_number?: number;
    entity_type?: string;
    sequence?: string;
}

interface SequenceContinuationWorkbenchHeaderProps {
    selectedModelId: string;
    selectedModeId: string;
    onSelectModelMode: (model: string, mode: string) => void;
    sourceStructure?: unknown;
    chains?: ContinuationChainSummary[];
    designChain?: unknown;
    targetChain?: unknown;
    onBack?: () => void;
}

const MODEL_CARDS = [
    { id: 'ligandmpnn', name: 'LigandMPNN', subtitle: 'DNA / Ligand / Metal', defaultMode: 'dna_aware' },
    { id: 'proteinmpnn', name: 'ProteinMPNN', subtitle: 'Backbone only', defaultMode: 'design' },
    { id: 'fampnn', name: 'FA-MPNN', subtitle: 'All-atom + pSCE', defaultMode: 'design' },
    { id: 'caliby_experimental', name: 'Caliby', subtitle: 'Ensemble / Pack', defaultMode: 'ensemble_design' },
    { id: 'protonpottsmpnn', name: 'ProtonPotts', subtitle: 'pH-specific', defaultMode: 'redesign' },
] as const;

const MODE_NOTES: Record<string, string> = {
    'ligandmpnn:dna_aware': 'Atomic context includes DNA bases & backbone',
    'ligandmpnn:ligand_aware': 'Atomic context includes bound small-molecule ligands',
    'ligandmpnn:ntp_aware': 'Atomic context includes nucleotide triphosphates',
    'ligandmpnn:metal_aware': 'Atomic context includes coordinated metal ions',
    'proteinmpnn:design': 'Backbone-conditioned sequence sampling',
    'fampnn:design': 'Full-atom sequence and side-chain diffusion',
    'fampnn:fixed_backbone': 'Redesign sequence with fixed residue positions',
    'caliby_experimental:ensemble_design': 'Ensemble-conditioned Potts sequence design',
    'caliby_experimental:sidechain_pack': 'Sequence-preserving side-chain packing',
    'protonpottsmpnn:redesign': 'pH-conditioned residue titration & redesign',
};

const SHORT_MODE_LABELS: Record<string, string> = {
    dna_aware: 'DNA-aware',
    ligand_aware: 'Ligand-aware',
    ntp_aware: 'NTP-aware',
    metal_aware: 'Metal-aware',
    design: 'Design',
    fixed_backbone: 'Fixed backbone',
    ensemble_design: 'Ensemble design',
    sidechain_pack: 'Side-chain pack',
    redesign: 'pH redesign',
};

function sourceLabel(source: unknown): string | null {
    if (!source || typeof source !== 'object') return null;
    const rec = source as Record<string, unknown>;
    const path = typeof rec.path === 'string' ? rec.path : typeof rec.artifact_id === 'string' ? rec.artifact_id : null;
    if (!path) return null;
    const base = path.split('/').pop() || path;
    return rec.model_number != null ? `${base} · Model ${rec.model_number}` : base;
}

function parseChainTokens(value: unknown): string[] {
    if (Array.isArray(value)) return value.map(String).map(s => s.trim()).filter(Boolean);
    if (typeof value === 'string') return value.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
    return [];
}

export function SequenceContinuationWorkbenchHeader({
    selectedModelId,
    selectedModeId,
    onSelectModelMode,
    sourceStructure,
    chains,
    designChain,
    targetChain,
    onBack,
}: SequenceContinuationWorkbenchHeaderProps) {
    const activeModes = sequenceDestinations.filter(([model]) => model === selectedModelId);
    const modeNote = MODE_NOTES[`${selectedModelId}:${selectedModeId}`] ?? 'Exact structure handed off from generated candidate';
    const srcText = sourceLabel(sourceStructure);
    const designTokens = parseChainTokens(designChain);
    const targetTokens = parseChainTokens(targetChain);
    const chainList = Array.isArray(chains) ? chains : [];

    return (
        <section
            aria-label="Sequence design workbench"
            className="mx-auto mb-6 max-w-[104rem] rounded-2xl border border-slate-800 bg-[#111726] p-5 text-slate-100 shadow-lg"
        >
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="mb-1 text-xs font-medium text-slate-400">
                        Results / De Novo / Sequence Design
                    </div>
                    <div className="flex flex-wrap items-center gap-2.5">
                        <h2 className="text-lg font-semibold tracking-tight text-slate-100">
                            Design Sequence from Exact Structure
                        </h2>
                        {srcText && (
                            <span className="rounded-full border border-slate-700/80 bg-[#151d2e] px-2.5 py-0.5 text-xs text-slate-300">
                                {srcText}
                            </span>
                        )}
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2.5">
                    <label className="inline-flex items-center gap-2 text-xs text-slate-400">
                        <span className="sr-only">Sequence design model</span>
                        <select
                            aria-label="Sequence design model"
                            value={`${selectedModelId}:${selectedModeId}`}
                            onChange={event => {
                                const [model, mode] = event.target.value.split(':');
                                onSelectModelMode(model, mode);
                            }}
                            className="rounded-lg border border-slate-700/80 bg-[#0d1321] px-2.5 py-1.5 text-xs text-slate-200 focus:border-blue-500 focus:outline-none"
                        >
                            {sequenceDestinations.map(([model, mode, label]) => (
                                <option key={`${model}:${mode}`} value={`${model}:${mode}`}>
                                    {label}
                                </option>
                            ))}
                        </select>
                    </label>
                    {onBack && (
                        <button
                            type="button"
                            onClick={onBack}
                            className="rounded-lg border border-slate-700 bg-[#151d2e] px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:border-slate-500 hover:bg-slate-800"
                        >
                            Back to selected result
                        </button>
                    )}
                </div>
            </div>

            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                1. Sequence Design Model
            </div>
            <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {MODEL_CARDS.map(card => {
                    const active = selectedModelId === card.id;
                    const firstMode = sequenceDestinations.find(([m]) => m === card.id)?.[1] ?? card.defaultMode;
                    return (
                        <button
                            key={card.id}
                            type="button"
                            onClick={() => onSelectModelMode(card.id, active ? selectedModeId : firstMode)}
                            aria-pressed={active}
                            className={`rounded-xl border px-3 py-2.5 text-left transition-colors ${
                                active
                                    ? 'border-blue-500 bg-blue-500/15 text-slate-100'
                                    : 'border-slate-800 bg-[#151d2e] text-slate-200 hover:border-slate-600'
                            }`}
                        >
                            <div className="text-xs font-semibold">{card.name}</div>
                            <div className={`mt-0.5 text-[11px] ${active ? 'text-blue-300' : 'text-slate-400'}`}>
                                {card.subtitle}
                            </div>
                        </button>
                    );
                })}
            </div>

            {activeModes.length > 0 && (
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-[#0d1321] px-3 py-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                        {activeModes.map(([model, mode, label]) => {
                            const active = selectedModeId === mode;
                            return (
                                <button
                                    key={`${model}:${mode}`}
                                    type="button"
                                    onClick={() => onSelectModelMode(model, mode)}
                                    aria-pressed={active}
                                    className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                                        active
                                            ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                                            : 'border-transparent text-slate-400 hover:border-slate-700 hover:text-slate-200'
                                    }`}
                                >
                                    {SHORT_MODE_LABELS[mode] ?? label}
                                </button>
                            );
                        })}
                    </div>
                    <span className="text-xs text-slate-400">{modeNote}</span>
                </div>
            )}

            {(chainList.length > 0 || designTokens.length > 0 || targetTokens.length > 0) && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-[#151d2e] px-3.5 py-2.5 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-slate-400">Redesign:</span>
                        {designTokens.length > 0 ? (
                            designTokens.map(chain => (
                                <span
                                    key={`design:${chain}`}
                                    className="rounded-full border border-blue-500/35 bg-blue-500/15 px-2.5 py-0.5 font-semibold text-blue-300"
                                >
                                    Chain {chain}
                                </span>
                            ))
                        ) : chainList.length > 0 ? (
                            chainList.slice(0, 1).map(chain => (
                                <span
                                    key={`chain:${chain.id}`}
                                    className="rounded-full border border-blue-500/35 bg-blue-500/15 px-2.5 py-0.5 font-semibold text-blue-300"
                                >
                                    Chain {chain.auth_asym_id ?? chain.id}{chain.sequence ? ` · ${chain.sequence.length} aa` : ''}
                                </span>
                            ))
                        ) : (
                            <span className="text-slate-400">Default from structure</span>
                        )}
                    </div>
                    {(targetTokens.length > 0 || chainList.length > 1) && (
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-slate-400">Fixed context:</span>
                            {targetTokens.length > 0
                                ? targetTokens.map(chain => (
                                    <span
                                        key={`target:${chain}`}
                                        className="rounded-full border border-emerald-500/35 bg-emerald-500/15 px-2.5 py-0.5 font-semibold text-emerald-300"
                                    >
                                        Chain {chain}
                                    </span>
                                ))
                                : chainList.slice(1).map(chain => (
                                    <span
                                        key={`ctx:${chain.id}`}
                                        className="rounded-full border border-emerald-500/35 bg-emerald-500/15 px-2.5 py-0.5 font-semibold text-emerald-300"
                                    >
                                        Chain {chain.auth_asym_id ?? chain.id}
                                    </span>
                                ))}
                        </div>
                    )}
                </div>
            )}
        </section>
    );
}
