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
        description: 'Curves, QC, ΔCq/ΔΔCq.',
    },
    {
        id: 'chromatography',
        label: 'Chromatography',
        eyebrow: 'Waters / Empower3',
        description: 'Empower, peaks, calibration, isoforms.',
    },
    {
        id: 'statistics',
        label: 'DOE + Statistics',
        eyebrow: 'JMP-like workbench',
        description: 'QC, DOE/RSM, SPC, capability.',
    },
    {
        id: 'debug',
        label: 'Runtime',
        eyebrow: 'stats-tools',
        description: 'Lifecycle, health, logs.',
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
                title="qPCR · chromatography · DOE/statistics"
                description="Instrument data, QC, plots, runtime."
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
                        title="Stats Tools"
                        subtitle="Actions + logs."
                    />
                </div>
            </AssayPanel>
        </AssayPageShell>
    );
}
