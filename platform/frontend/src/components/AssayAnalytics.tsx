import { useState } from 'react';

import {
    AssayModeTabs,
    AssayPageHeader,
    AssayPageShell,
    AssayPanel,
    AssayStatusStrip,
    type AssayNavItem,
} from './assay/AssayWorkbenchPrimitives';
import { HplcPage } from './hplc';
import { QpcrPage } from './qpcr';
import { StatisticsPage } from './statistics';
import { StatsToolsControlPanel } from './StatsToolsControlPanel';

type AssayTab = 'qpcr' | 'chromatography' | 'statistics' | 'debug';

const tabs: Array<AssayNavItem<AssayTab>> = [
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
            'Manual analytical QC, DOE generation, RSM/regression, SPC, process capability, hypothesis testing, and Plotly visualization.',
    },
    {
        id: 'debug',
        label: 'Debug',
        eyebrow: 'Stats-tools container',
        description:
            'Control panel for the optional stats-tools container lifecycle, health, logs, and CLI-equivalent operator commands.',
    },
];

const assayStatus = [
    {
        title: 'Scope',
        value: 'BMS-native Stats Toolkit: qPCR, chromatography, DOE/statistics, and stats-tools runtime controls',
        tone: 'accent' as const,
    },
    {
        title: 'Inputs',
        value: 'Real QuantStudio, StepOnePlus, Waters Empower, pasted assay, and DOE/statistics data',
    },
    {
        title: 'Visualization',
        value: 'Plotly workbench panels, manual QC summaries, cross-run rollups, calibration tables, and explicit empty states',
    },
];

export function AssayAnalytics() {
    const [activeTab, setActiveTab] = useState<AssayTab>('qpcr');

    return (
        <AssayPageShell contentClassName="max-w-[1840px]">
            <AssayPageHeader
                eyebrow="Stats Toolkit"
                title="qPCR, Waters/Empower chromatography, DOE/statistics, and stats-tools runtime"
                description="BMS-native analysis for QuantStudio, StepOnePlus, Waters Empower3, plasmid isoform workflows, JMP-like statistical tooling, Plotly visual analytics, and optional stats-tools container operations."
            />

            <AssayStatusStrip items={assayStatus} />

            <AssayModeTabs items={tabs} activeId={activeTab} onChange={setActiveTab} />

            <AssayPanel className="overflow-hidden">
                <div hidden={activeTab !== 'qpcr'}>
                    <QpcrPage />
                </div>
                <div hidden={activeTab !== 'chromatography'}>
                    <HplcPage />
                </div>
                <div hidden={activeTab !== 'statistics'}>
                    <StatisticsPage />
                </div>
                <div hidden={activeTab !== 'debug'}>
                    <StatsToolsControlPanel
                        embeddedContext="stats-toolkit-debug"
                        title="Stats Toolkit debug / stats-tools container"
                        subtitle="Container lifecycle control panel for optional assay/statistics runtime packages. Start/stop here without making stats-tools mandatory at boot."
                    />
                </div>
            </AssayPanel>
        </AssayPageShell>
    );
}
