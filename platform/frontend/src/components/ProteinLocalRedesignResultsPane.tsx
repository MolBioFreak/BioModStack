import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
    fetchProteinLocalRedesignResults,
    type Job,
    type ProteinLocalRedesignResultArtifact,
    type ProteinLocalRedesignResultItem,
    type ProteinLocalRedesignResultSurface,
} from '../lib/api';
import MolstarViewer from './MolstarViewer';

interface ProteinLocalRedesignResultsPaneProps {
    job: Job;
}

export function isProteinLocalRedesignResultJob(job: Job | null | undefined): boolean {
    if (!job) return false;
    const modelId = String(job.model_id || '').toLowerCase();
    const mode = String(job.mode || '').toLowerCase();
    const stageFamily = String(job.stage_family || '').toLowerCase();
    const rfdMode = typeof job.params?.rfd_mode === 'string' ? job.params.rfd_mode.toLowerCase() : '';
    return modelId === 'protein_local_redesign'
        || (modelId === 'protein_modification_experimental'
            && (mode === 'region_redesign' || mode === 'local_redesign' || stageFamily.startsWith('protein_local_redesign') || rfdMode === 'protein_local_redesign'));
}

const formatValue = (value: unknown): string => {
    if (value == null || value === '') return '—';
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    if (typeof value === 'string') return value;
    try {
        return JSON.stringify(value);
    } catch {
        return String(value);
    }
};

const activeTabClasses: Record<string, string> = {
    rfd3: 'border-emerald-400/70 bg-emerald-500/15',
    fampnn: 'border-cyan-400/70 bg-cyan-500/15',
    esmfold2: 'border-violet-400/70 bg-violet-500/15',
    protenix_v2: 'border-amber-400/70 bg-amber-500/15',
};

function formatStructureFormat(artifact: ProteinLocalRedesignResultArtifact): 'pdb' | 'cif' {
    return /\.(?:cif|mmcif)(?:\.gz)?$/i.test(artifact.relative_path) ? 'cif' : 'pdb';
}

function MetricGrid({ metrics }: { metrics: Record<string, UntypedApiValue> }) {
    const entries = Object.entries(metrics);
    if (!entries.length) {
        return <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-sm text-slate-500">No model metrics were persisted for this item.</div>;
    }
    return (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {entries.map(([key, value]) => (
                <div key={key} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{key}</div>
                    <div className="mt-1 max-h-20 overflow-auto font-mono text-xs text-slate-200">{formatValue(value)}</div>
                </div>
            ))}
        </div>
    );
}

function ArtifactLinks({ surface, item }: { surface: ProteinLocalRedesignResultSurface; item: ProteinLocalRedesignResultItem }) {
    const ids = [
        ['structure', item.structure.artifact_id],
        ['metrics', item.metrics_artifact],
        ['confidence', item.confidence_artifact],
        ['MSA receipt', item.msa_artifact],
        ['native metadata', item.native_metadata_artifact],
    ] as const;
    const links: Array<{ label: string; artifact: ProteinLocalRedesignResultArtifact }> = [];
    ids.forEach(([label, id]) => {
        if (typeof id !== 'string') return;
        const artifact = surface.artifacts.find((candidate) => candidate.artifact_id === id);
        if (artifact) links.push({ label, artifact });
    });
    return (
        <div className="flex flex-wrap gap-2">
            {links.map(({ label, artifact }) => (
                <a
                    key={artifact.artifact_id}
                    href={artifact.content_url}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-1.5 text-xs text-slate-300 hover:border-cyan-400/60 hover:text-white"
                >
                    {label} · {artifact.bytes.toLocaleString()} bytes
                </a>
            ))}
        </div>
    );
}

function ItemTable({ items, selectedItemId, onSelect }: { items: ProteinLocalRedesignResultItem[]; selectedItemId: string | null; onSelect: (item: ProteinLocalRedesignResultItem) => void }) {
    return (
        <div className="overflow-x-auto rounded-xl border border-slate-800">
            <table className="min-w-full text-left text-xs">
                <thead className="bg-slate-950/90 text-[10px] uppercase tracking-[0.14em] text-slate-500">
                    <tr>
                        <th className="px-3 py-2">Candidate</th>
                        <th className="px-3 py-2">Sample</th>
                        <th className="px-3 py-2">Persisted item</th>
                        <th className="px-3 py-2">Structure</th>
                        <th className="px-3 py-2">Key values</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map((item) => {
                        const selected = item.item_id === selectedItemId;
                        const keyMetrics = Object.entries(item.metrics).slice(0, 3);
                        return (
                            <tr
                                key={item.item_id}
                                onClick={() => onSelect(item)}
                                className={`cursor-pointer border-t border-slate-800 transition-colors ${selected ? 'bg-cyan-500/10' : 'hover:bg-slate-900'}`}
                            >
                                <td className="px-3 py-2 font-medium text-white">{item.candidate_label}</td>
                                <td className="px-3 py-2 text-slate-300">{item.sample_index == null ? '—' : item.sample_index}</td>
                                <td className="max-w-[330px] truncate px-3 py-2 font-mono text-slate-400" title={item.name}>{item.name}</td>
                                <td className="px-3 py-2 text-emerald-300">{item.structure.relative_path.split('/').pop()}</td>
                                <td className="px-3 py-2 text-slate-300">
                                    {keyMetrics.length ? keyMetrics.map(([key, value]) => `${key}: ${formatValue(value)}`).join(' · ') : '—'}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

function SelectedItem({ surface, item, tabLabel }: { surface: ProteinLocalRedesignResultSurface; item: ProteinLocalRedesignResultItem; tabLabel: string }) {
    const metrics = item.metrics ?? {};
    const structureFormat = formatStructureFormat(item.structure);
    const msa = item.msa;
    return (
        <section className="space-y-4 rounded-2xl border border-slate-700 bg-slate-900/60 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">{tabLabel} native result</div>
                    <h3 className="mt-1 text-xl font-semibold text-white">{item.candidate_label}{item.sample_index == null ? '' : ` · sample ${item.sample_index}`}</h3>
                    <p className="mt-1 max-w-3xl break-all font-mono text-[11px] text-slate-500">{item.name}</p>
                </div>
                <div className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 text-right text-xs text-slate-400">
                    <div>Design row <span className="font-mono text-slate-200">{item.design_id || '—'}</span></div>
                    <div>Candidate <span className="font-mono text-slate-200">{item.candidate_id}</span></div>
                </div>
            </div>
            <div className="overflow-hidden rounded-xl border border-slate-800">
                <MolstarViewer
                    structureUrl={item.structure.content_url}
                    format={structureFormat}
                    height={560}
                    label={`${tabLabel} • ${item.candidate_label}${item.sample_index == null ? '' : ` • sample ${item.sample_index}`}`}
                    showSequenceTrack
                    showComplexWorkbench
                />
            </div>
            {item.sequence && (
                <div className="rounded-xl border border-cyan-500/25 bg-cyan-500/5 p-4">
                    <div className="text-[10px] uppercase tracking-[0.16em] text-cyan-300">Persisted sequence</div>
                    <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs leading-5 text-cyan-50">{item.sequence}</pre>
                </div>
            )}
            <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Native metrics</div>
                <MetricGrid metrics={metrics} />
            </div>
            {msa && (
                <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-4 text-sm text-emerald-100">
                    MSA receipt: <span className="font-mono">{formatValue(msa.backend)}</span> · state <span className="font-mono">{formatValue(msa.state)}</span>
                </div>
            )}
            <ArtifactLinks surface={surface} item={item} />
        </section>
    );
}

export default function ProteinLocalRedesignResultsPane({ job }: ProteinLocalRedesignResultsPaneProps) {
    const resultQuery = useQuery({
        queryKey: ['protein-local-redesign-results', job.id],
        queryFn: () => fetchProteinLocalRedesignResults(job.id).then((response) => response.data),
        enabled: Boolean(job.id),
        retry: false,
        staleTime: 30_000,
    });
    const surface = resultQuery.data;
    const [activeTabId, setActiveTabId] = useState<ProteinLocalRedesignResultSurface['tabs'][number]['id']>('rfd3');
    const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

    const activeTab = useMemo(
        () => surface?.tabs.find((tab) => tab.id === activeTabId) ?? surface?.tabs[0] ?? null,
        [activeTabId, surface],
    );
    const selectedItem = activeTab?.items.find((item) => item.item_id === selectedItemId) ?? activeTab?.items[0] ?? null;
    const statusText = resultQuery.isLoading ? 'Loading' : resultQuery.isError ? 'Unavailable' : surface?.job.status || 'Unknown';

    useEffect(() => {
        if (!surface?.tabs.length) return;
        const nextTab = surface.tabs.find((tab) => tab.id === activeTabId) ?? surface.tabs[0];
        if (nextTab.id !== activeTabId) setActiveTabId(nextTab.id);
        setSelectedItemId((current) => nextTab.items.some((item) => item.item_id === current) ? current : (nextTab.items[0]?.item_id ?? null));
    }, [activeTabId, surface]);

    if (resultQuery.isLoading) {
        return <div className="rounded-2xl border border-slate-700 bg-slate-900/60 p-6 text-sm text-slate-400">Loading the BMS-owned Protein Local Redesign result surface…</div>;
    }
    if (resultQuery.isError || !surface) {
        return <div role="alert" className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-6 text-sm text-amber-100">The typed Protein Local Redesign result surface is not available for this job.</div>;
    }

    return (
        <div className="space-y-5">
            <section className="rounded-2xl border border-cyan-500/25 bg-cyan-500/5 p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">BMS-owned workflow result</div>
                        <h2 className="mt-1 text-2xl font-semibold text-white">Protein Local Redesign</h2>
                        <p className="mt-1 text-sm text-slate-300">RFD3 generation, FA-MPNN sequence design, and independent structure validation.</p>
                    </div>
                    <div className="rounded-xl border border-slate-700 bg-slate-950/70 px-4 py-3 text-right text-xs text-slate-400">
                        <div>Status: <span className="font-semibold text-white">{statusText}</span></div>
                        <div>Composition <span className="font-mono text-cyan-200">{surface.composition.sha256.slice(0, 16)}…</span></div>
                        {surface.job.request_sha256 && <div>Request <span className="font-mono text-slate-300">{surface.job.request_sha256.slice(0, 16)}…</span></div>}
                    </div>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    {surface.tabs.map((tab) => (
                        <div key={tab.id} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{tab.label}</div>
                            <div className="mt-1 text-xl font-semibold text-white">{tab.count}</div>
                            <div className="text-[11px] text-slate-400">{tab.candidate_count} candidates · {tab.status}</div>
                        </div>
                    ))}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-4">
                <div className="flex flex-wrap gap-2" role="tablist" aria-label="Model-native Protein Local Redesign results">
                    {surface.tabs.map((tab) => (
                        <button
                            key={tab.id}
                            type="button"
                            role="tab"
                            aria-selected={activeTab?.id === tab.id}
                            data-testid={`plr-result-tab-${tab.id}`}
                            onClick={() => { setActiveTabId(tab.id); setSelectedItemId(tab.items[0]?.item_id ?? null); }}
                            className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${activeTab?.id === tab.id ? `${activeTabClasses[tab.id]} text-white` : 'border-slate-700 bg-slate-950/60 text-slate-400 hover:border-slate-500 hover:text-white'}`}
                        >
                            {tab.label} <span className="ml-1 text-xs text-slate-400">{tab.count}</span>
                        </button>
                    ))}
                </div>
            </section>

            {activeTab && (
                <section className="space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <h3 className="text-lg font-semibold text-white">{activeTab.label} results</h3>
                            <p className="mt-1 text-xs text-slate-400">{activeTab.role} · {activeTab.candidate_count} candidates · {activeTab.count} persisted artifacts</p>
                        </div>
                        <span className={`rounded-full border px-3 py-1 text-xs ${activeTab.status === 'complete' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-amber-500/30 bg-amber-500/10 text-amber-200'}`}>
                            {activeTab.status === 'complete' ? `Complete ${activeTab.candidate_count}/${activeTab.expected_candidate_count}` : 'Partial result'}
                        </span>
                    </div>
                    <ItemTable items={activeTab.items} selectedItemId={selectedItem?.item_id ?? null} onSelect={(item) => setSelectedItemId(item.item_id)} />
                    {selectedItem && <SelectedItem surface={surface} item={selectedItem} tabLabel={activeTab.label} />}
                </section>
            )}

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <h3 className="text-lg font-semibold text-white">Source and validator receipt</h3>
                        <p className="mt-1 text-xs text-slate-500">All links resolve through the BMS-owned result contract.</p>
                    </div>
                    <div className="text-xs text-slate-400">Persisted design rows: {surface.counts.persisted_design_rows}</div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                    {surface.source.artifacts.map((artifact) => (
                        <a key={artifact.artifact_id} href={artifact.content_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 text-xs text-slate-300 hover:border-cyan-400/60 hover:text-white">
                            {artifact.label} · {artifact.bytes.toLocaleString()} bytes
                        </a>
                    ))}
                </div>
                {surface.receipt && <pre className="mt-4 max-h-64 overflow-auto rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs text-slate-300">{JSON.stringify(surface.receipt, null, 2)}</pre>}
            </section>
        </div>
    );
}
