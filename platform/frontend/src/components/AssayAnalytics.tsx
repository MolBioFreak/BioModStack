import { useState } from 'react';

import { HplcPage } from './hplc';
import { QpcrPage } from './qpcr';
import { StatisticsPage } from './statistics';

type AssayTab = 'qpcr' | 'chromatography' | 'statistics';

const tabs: Array<{
    id: AssayTab;
    label: string;
    eyebrow: string;
    description: string;
}> = [
    {
        id: 'qpcr',
        label: 'qPCR',
        eyebrow: 'QuantStudio / StepOnePlus',
        description:
            'Standard curves, spike recovery, replicate QC, ΔCq, ΔΔCq, fold-change, and MIQE-style qPCR metrics.',
    },
    {
        id: 'chromatography',
        label: 'Chromatography',
        eyebrow: 'Waters / Empower3',
        description:
            'Waters Empower imports, peak detection, calibration, recovery, system suitability, and plasmid isoform analysis.',
    },
    {
        id: 'statistics',
        label: 'DOE + Statistics',
        eyebrow: 'JMP-like workbench',
        description:
            'DOE generation, RSM/regression, SPC, process capability, hypothesis testing, and Plotly visualization.',
    },
];

function TabButton({ tab, active, onClick }: { tab: (typeof tabs)[number]; active: boolean; onClick: () => void }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="border px-4 py-3 text-left transition-all"
            style={{
                backgroundColor: active ? 'color-mix(in srgb, var(--accent-primary) 18%, var(--card-bg))' : 'var(--card-bg)',
                borderColor: active ? 'var(--accent-primary)' : 'var(--border-primary)',
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
        >
            <div className="text-xs uppercase tracking-[0.24em]" style={{ color: active ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
                {tab.eyebrow}
            </div>
            <div className="mt-1 text-lg font-semibold">{tab.label}</div>
            <p className="mt-1 max-w-sm text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {tab.description}
            </p>
        </button>
    );
}

export function AssayAnalytics() {
    const [activeTab, setActiveTab] = useState<AssayTab>('qpcr');

    return (
        <div className="min-h-full px-4 py-5 sm:px-6 lg:px-8" style={{ color: 'var(--text-primary)' }}>
            <div className="mx-auto max-w-7xl space-y-5">
                <header
                    className="border p-5 shadow-sm"
                    style={{
                        background: 'linear-gradient(135deg, var(--card-bg), color-mix(in srgb, var(--accent-primary) 8%, var(--card-bg)))',
                        borderColor: 'var(--border-primary)',
                    }}
                >
                    <div className="text-xs font-semibold uppercase tracking-[0.3em]" style={{ color: 'var(--accent-primary)' }}>
                        Assay Analytics
                    </div>
                    <div className="mt-2 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                        <div>
                            <h1 className="text-3xl font-bold">qPCR, Waters/Empower chromatography, and DOE/statistics</h1>
                            <p className="mt-2 max-w-4xl text-sm leading-6" style={{ color: 'var(--text-secondary)' }}>
                                BMS-native analysis additions for QuantStudio, StepOnePlus, Waters Empower3, plasmid isoform
                                workflows, JMP-like statistical tooling, and Plotly visual analytics. This tab is intentionally
                                separate from the legacy protein-design analytics dashboard so assay data has its own product surface.
                            </p>
                        </div>
                        <div className="grid grid-cols-3 gap-2 text-center text-xs">
                            <div className="border px-3 py-2" style={{ borderColor: 'var(--border-primary)', backgroundColor: 'var(--bg-secondary)' }}>
                                <div className="font-semibold">qPCR</div>
                                <div style={{ color: 'var(--text-muted)' }}>Std curve / ΔΔCq</div>
                            </div>
                            <div className="border px-3 py-2" style={{ borderColor: 'var(--border-primary)', backgroundColor: 'var(--bg-secondary)' }}>
                                <div className="font-semibold">Empower</div>
                                <div style={{ color: 'var(--text-muted)' }}>Peaks / isoforms</div>
                            </div>
                            <div className="border px-3 py-2" style={{ borderColor: 'var(--border-primary)', backgroundColor: 'var(--bg-secondary)' }}>
                                <div className="font-semibold">DOE</div>
                                <div style={{ color: 'var(--text-muted)' }}>JMP-like / Plotly</div>
                            </div>
                        </div>
                    </div>
                </header>

                <nav className="grid gap-3 md:grid-cols-3">
                    {tabs.map((tab) => (
                        <TabButton key={tab.id} tab={tab} active={activeTab === tab.id} onClick={() => setActiveTab(tab.id)} />
                    ))}
                </nav>

                <section
                    className="border shadow-sm"
                    style={{
                        backgroundColor: 'var(--bg-secondary)',
                        borderColor: 'var(--border-primary)',
                    }}
                >
                    {activeTab === 'qpcr' && <QpcrPage />}
                    {activeTab === 'chromatography' && <HplcPage />}
                    {activeTab === 'statistics' && <StatisticsPage />}
                </section>
            </div>
        </div>
    );
}
