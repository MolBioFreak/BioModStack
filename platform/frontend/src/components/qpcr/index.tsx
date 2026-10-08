/**
 * BMS qPCR Workbench Page
 */

import { useState } from 'react';
import {
    AssayPanel,
    AssayStatusStrip,
    AssaySubnavGrid,
    AssayWorkbenchIntro,
    type AssayNavItem,
} from '../assay/AssayWorkbenchPrimitives';
import { RawDataImport } from './RawDataImport';
import { DeltaCqPanel, DeltaDeltaCqPanel } from './DeltaCqPanels';
import { StandardCurvePanel, QuantificationPanel, AnovaDunnettPanel } from './QuantificationPanels';

type AnalysisType = 'import' | 'deltacq' | 'deltadeltacq' | 'stdcurve' | 'quantify' | 'anova';

const analysisOptions: Array<AssayNavItem<AnalysisType>> = [
    {
        id: 'import',
        label: 'Instrument Import',
        status: 'QuantStudio / StepOnePlus',
        description: 'Parse real EDS, Excel, or CSV exports into plate heatmaps, well tables, standard-curve QC, replicate QC, NTCs, and spike recovery.',
    },
    {
        id: 'deltacq',
        label: 'ΔCq Analysis',
        status: 'Reference-normalized',
        description: 'Paste real Cq rows with sample, gene, Cq, and group metadata; BMS requires explicit reference and target genes.',
    },
    {
        id: 'deltadeltacq',
        label: 'ΔΔCq & Fold Change',
        status: 'Control-group required',
        description: 'Compute relative expression and fold-change from explicit control/treatment groups without inventing group labels.',
    },
    {
        id: 'stdcurve',
        label: 'Standard Curve',
        status: 'MIQE metrics',
        description: 'Fit log-quantity versus Cq, report slope, efficiency, R², residuals, QC flags, and Plotly fit visualization.',
    },
    {
        id: 'quantify',
        label: 'Absolute Quantification',
        status: 'Sample IDs required',
        description: 'Quantify unknowns against a real standard curve; each sample value must carry a real identifier.',
    },
    {
        id: 'anova',
        label: 'ANOVA + Dunnett',
        status: 'Named groups only',
        description: 'Run group comparison statistics with an explicit control group and named treatment groups.',
    },
];

const statusItems = [
    {
        title: 'Source of truth',
        value: '/api/assay-analytics qPCR routes',
        tone: 'accent' as const,
    },
    {
        title: 'Supported inputs',
        value: 'EDS, XLS/XLSX, or CSV instrument exports',
    },
    {
        title: 'Visualization',
        value: 'Plotly plate maps, curves, standard curves, and QC tables',
    },
    {
        title: 'Data policy',
        value: 'BMS does not preload built-in assay rows',
        tone: 'warning' as const,
    },
];

export function QpcrPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('import');

    return (
        <div className="space-y-6 p-6">
            <AssayWorkbenchIntro
                eyebrow="BMS qPCR Workbench"
                title="QuantStudio / StepOnePlus analysis on real assay data"
                description="Import actual instrument exports or paste explicit Cq tables for standard curves, spike recovery, replicate QC, ΔCq, ΔΔCq, fold-change, ANOVA, and MIQE-style metrics. The panels start empty by design; BMS does not preload built-in assay rows."
            >
                <AssayStatusStrip items={statusItems} columnsClass="sm:grid-cols-2 xl:grid-cols-4" />
            </AssayWorkbenchIntro>

            <AssaySubnavGrid
                items={analysisOptions}
                activeId={activeAnalysis}
                onChange={setActiveAnalysis}
                columnsClass="sm:grid-cols-2 xl:grid-cols-3"
            />

            <AssayPanel className="p-4">
                <div hidden={activeAnalysis !== 'import'}>
                    <RawDataImport />
                </div>
                <div hidden={activeAnalysis !== 'deltacq'}>
                    <DeltaCqPanel />
                </div>
                <div hidden={activeAnalysis !== 'deltadeltacq'}>
                    <DeltaDeltaCqPanel />
                </div>
                <div hidden={activeAnalysis !== 'stdcurve'}>
                    <StandardCurvePanel />
                </div>
                <div hidden={activeAnalysis !== 'quantify'}>
                    <QuantificationPanel />
                </div>
                <div hidden={activeAnalysis !== 'anova'}>
                    <AnovaDunnettPanel />
                </div>
            </AssayPanel>
        </div>
    );
}
