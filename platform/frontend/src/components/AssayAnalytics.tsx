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
        description: 'Curves, recovery, replicate QC, ΔCq/ΔΔCq.',
    },
    {
        id: 'chromatography',
        label: 'Chromatography',
        eyebrow: 'Waters / Empower3',
        description: 'Empower imports, peaks, calibration, recovery, isoforms.',
    },
    {
        id: 'statistics',
        label: 'DOE + Statistics',
        eyebrow: 'JMP-like workbench',
        description: 'QC, DOE/RSM, SPC, capability, tests, Plotly.',
    },
    {
        id: 'debug',
        label: 'Debug',
        eyebrow: 'Stats-tools container',
        description: 'stats-tools lifecycle, health, logs, CLI.',
    },
];

const assayStatus = [
    {
        title: 'Scope',
        value: 'qPCR · chromatography · DOE/statistics · runtime controls',
        tone: 'accent' as const,
    },
    {
        title: 'Inputs',
        value: 'QuantStudio, StepOnePlus, Waters Empower, pasted assay/DOE data',
    },
    {
        title: 'Visualization',
        value: 'Plotly panels, QC summaries, rollups, calibration tables',
    },
];

export function AssayAnalytics() {
    const [activeTab, setActiveTab] = useState<AssayTab>('qpcr');

    return (
        <AssayPageShell contentClassName="max-w-[1840px]">
            <AssayPageHeader
                eyebrow="Stats Toolkit"
                title="qPCR, chromatography, DOE/statistics, runtime"
                description="QuantStudio, StepOnePlus, Empower3, plasmid isoforms, DOE/statistics, Plotly."
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
                        subtitle="Stats runtime actions + logs."
                    />
                </div>
            </AssayPanel>
        </AssayPageShell>
    );
}
