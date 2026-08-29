export type Gen2StructureSourceTab = 'rcsb' | 'runs' | 'upload' | 'samples';

const TABS: Array<{ id: Gen2StructureSourceTab; label: string }> = [
    { id: 'rcsb', label: 'RCSB' },
    { id: 'runs', label: 'Your Runs' },
    { id: 'upload', label: 'Upload' },
    { id: 'samples', label: 'Accepted samples' },
];

export function Gen2StructureSourceSelector({ active, onChange }: {
    active: Gen2StructureSourceTab;
    onChange: (tab: Gen2StructureSourceTab) => void;
}) {
    return (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Starting structure source">
            {TABS.map((tab) => (
                <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={active === tab.id}
                    aria-controls={`md-source-panel-${tab.id}`}
                    onClick={() => onChange(tab.id)}
                    className={`min-h-11 rounded-lg border px-4 py-2 text-xs font-semibold ${active === tab.id ? 'border-cyan-500 bg-cyan-500/10 text-cyan-100' : 'border-slate-700 text-slate-400 hover:bg-slate-800'}`}
                >
                    {tab.label}
                </button>
            ))}
        </div>
    );
}
