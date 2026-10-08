import React, { StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { StructureElement } from 'molstar/lib/mol-model/structure';
import { ButtonsType, ModifiersKeys } from 'molstar/lib/mol-util/input/input-observer';

import EpitopeMolstarViewer from '../src/components/EpitopeMolstarViewer';
import MolstarViewer from '../src/components/MolstarViewer';
import type { MolstarViewerProps } from '../src/components/MolstarViewer';
import { getMolstarDirectAdapterForElement } from '../src/structureViewer/adapters/MolstarDirectAdapter';
import type { MolstarResidueMetricLayer } from '../src/lib/molstar-metrics';

const PDB_TEXT = `HEADER    BMS CONSUMER SMOKE
ATOM      1  N   ALA A   1      -0.525   1.363   0.000  1.00 95.00           N
ATOM      2  CA  ALA A   1       0.000   0.000   0.000  1.00 95.00           C
ATOM      3  C   ALA A   1       1.526   0.000   0.000  1.00 95.00           C
ATOM      4  O   ALA A   1       2.152  -1.055   0.000  1.00 95.00           O
ATOM      5  CB  ALA A   1      -0.507  -0.779  -1.216  1.00 95.00           C
ATOM      6  N   GLY A   2       2.121   1.194   0.000  1.00 65.00           N
ATOM      7  CA  GLY A   2       3.576   1.285   0.000  1.00 65.00           C
ATOM      8  C   GLY A   2       4.078   2.720   0.000  1.00 65.00           C
ATOM      9  O   GLY A   2       3.329   3.682   0.000  1.00 65.00           O
TER
END
`;

interface SiteResult {
    readonly id: string;
    readonly source: string;
    readonly ready: boolean;
    readonly usable: boolean;
    readonly structureCount: number;
    readonly disposed: boolean;
    readonly error?: string;
}

interface ConsumerSmokeReport {
    readonly currentInventory: {
        readonly generic: 12;
        readonly epitope: 3;
        readonly total: 15;
        readonly routeReachable: 12;
        readonly orphanFixtures: readonly ['G1', 'G2', 'G3'];
        readonly retiredHistoricalSites: readonly string[];
    };
    readonly generic: readonly SiteResult[];
    readonly epitope: readonly SiteResult[];
    readonly consoleErrors: readonly string[];
    readonly consoleWarnings: readonly string[];
    readonly failures: readonly string[];
}

const metricLayer: MolstarResidueMetricLayer = {
    scope: 'residue-scalar',
    descriptor: {
        id: 'consumer-smoke-frustration',
        label: 'Frustration',
        semanticType: 'frustration',
        units: null,
        direction: 'lower_is_better',
        source: 'consumer-smoke',
        range: [0, 1],
    },
    points: [
        { residue: { labelAsymId: 'A', labelSeqId: 1 }, value: 0.2, color: { r: 16, g: 185, b: 129 } },
        { residue: { labelAsymId: 'A', labelSeqId: 2 }, value: 0.8, color: { r: 239, g: 68, b: 68 } },
    ],
    nonSelectedColor: { r: 68, g: 68, b: 68 },
};

const selection = [{
    chain_id: 'A',
    start_residue_number: 1,
    end_residue_number: 2,
    color: { r: 59, g: 130, b: 246 },
}];

const status = document.querySelector<HTMLElement>('#status');
const hostCandidate = document.querySelector<HTMLElement>('#consumer-root');
if (!hostCandidate) throw new Error('consumer root missing');
const host: HTMLElement = hostCandidate;
const root: Root = createRoot(host);
const errors: string[] = [];
const warnings: string[] = [];
const originalError = console.error.bind(console);
const originalWarn = console.warn.bind(console);
console.error = (...args: unknown[]) => {
    errors.push(args.map(String).join(' '));
    originalError(...args);
};
console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(' '));
    originalWarn(...args);
};
window.addEventListener('error', (event) => errors.push(`window.error: ${event.message}`));
window.addEventListener('unhandledrejection', (event) => errors.push(`unhandledrejection: ${String(event.reason)}`));

const delay = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
const setStatus = (text: string) => { if (status) status.textContent = text; };

async function waitFor(predicate: () => boolean, timeoutMs = 45_000): Promise<boolean> {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
        if (predicate()) return true;
        await delay(50);
    }
    return false;
}

function directSite(id: string, props: MolstarViewerProps) {
    return (
        <div key={id} data-consumer-site={id} style={{ width: 520, minHeight: 360 }}>
            <MolstarViewer {...props} />
        </div>
    );
}

async function smokeDirectGroup(
    ids: readonly string[],
    sources: readonly string[],
    props: readonly MolstarViewerProps[],
): Promise<SiteResult[]> {
    setStatus(`generic ${ids.join('+')}`);
    root.render(<StrictMode><div style={{ display: 'flex', gap: 8 }}>{ids.map((id, index) => directSite(id, props[index]))}</div></StrictMode>);
    const ready = await waitFor(() => ids.every((id) => (
        host.querySelector(`[data-consumer-site="${id}"] [data-bms-molstar-status="ready"]`) !== null
    )));

    const results = ids.map((id, index): SiteResult => {
        const site = host.querySelector<HTMLElement>(`[data-consumer-site="${id}"]`);
        const mount = site?.querySelector<HTMLElement>('[data-bms-molstar-mount="true"]');
        const adapter = mount ? getMolstarDirectAdapterForElement(mount) : undefined;
        const canvas = site?.querySelector('canvas');
        const expectedStructures = props[index].overlayStructures?.length
            ? props[index].overlayStructures!.length + 1
            : 1;
        const usable = Boolean(canvas && adapter?.diagnostics.hasCanvas3d
            && adapter.diagnostics.structureCount === expectedStructures);
        return {
            id,
            source: sources[index],
            ready,
            usable,
            structureCount: adapter?.diagnostics.structureCount ?? 0,
            disposed: false,
            ...(!ready || !usable ? { error: `ready=${ready} canvas=${Boolean(canvas)} expectedStructures=${expectedStructures}` } : {}),
        };
    });

    const adapters = Array.from(host.querySelectorAll<HTMLElement>('[data-bms-molstar-mount="true"]'))
        .map((element) => getMolstarDirectAdapterForElement(element))
        .filter((adapter): adapter is NonNullable<typeof adapter> => Boolean(adapter));
    root.render(null);
    await waitFor(() => adapters.every((adapter) => adapter.diagnostics.disposed && adapter.diagnostics.pluginDisposed));
    return results.map((result, index) => ({
        ...result,
        disposed: adapters[index]?.diagnostics.disposed === true && adapters[index]?.diagnostics.pluginDisposed === true,
    }));
}

async function smokeEpitope(
    id: string,
    source: string,
    useRawData: boolean,
    clickEnabled: boolean,
): Promise<SiteResult> {
    setStatus(`epitope ${id}`);
    const structureUrl = URL.createObjectURL(new Blob([PDB_TEXT], { type: 'chemical/x-pdb' }));
    const clicked: string[] = [];
    const render = (selectedResidues: Set<string>) => root.render(
        <StrictMode>
            <div data-consumer-site={id} style={{ width: 520 }}>
                <EpitopeMolstarViewer
                    {...(useRawData ? { pdbData: PDB_TEXT } : { structureUrl })}
                    format="pdb"
                    height={id === 'E3' ? 420 : id === 'E2' ? 350 : 400}
                    selectedResidues={selectedResidues}
                    {...(clickEnabled ? { onResidueClick: (key) => clicked.push(key) } : {})}
                />
            </div>
        </StrictMode>,
    );

    render(new Set(['A1']));
    const ready = await waitFor(() => {
        const mount = host.querySelector<HTMLElement>('[data-bms-molstar-mount="true"]');
        const adapter = mount ? getMolstarDirectAdapterForElement(mount) : undefined;
        return Boolean(adapter?.activePlugin && adapter.diagnostics.hasCanvas3d && adapter.diagnostics.structureCount === 1);
    }, 60_000);
    const mount = host.querySelector<HTMLElement>('[data-bms-molstar-mount="true"]');
    const adapter = mount ? getMolstarDirectAdapterForElement(mount) : undefined;
    const usable = Boolean(ready && adapter?.activePlugin && mount?.querySelector('canvas'));
    if (ready) {
        render(new Set(['A1', 'A2']));
        await waitFor(() => host.textContent?.includes('2 selected') === true, 5_000);
        if (clickEnabled) {
            const plugin = adapter?.activePlugin;
            const structure = plugin?.managers.structure.hierarchy.current.structures[0]?.cell.obj?.data;
            if (plugin && structure) {
                const loci = StructureElement.Loci.firstResidue(StructureElement.Loci.all(structure));
                plugin.behaviors.interaction.click.next({
                    current: { loci },
                    buttons: ButtonsType.create(ButtonsType.Flag.Primary),
                    button: ButtonsType.Flag.Primary,
                    modifiers: ModifiersKeys.None,
                });
            }
            await waitFor(() => clicked.includes('A1'), 2_000);
        }
    }
    root.render(null);
    const disposed = await waitFor(() => adapter?.diagnostics.disposed === true
        && adapter.diagnostics.pluginDisposed === true
        && host.querySelector('canvas') === null, 10_000);
    URL.revokeObjectURL(structureUrl);
    return {
        id,
        source,
        ready,
        usable: usable && (!clickEnabled || clicked.includes('A1')),
        structureCount: usable ? 1 : 0,
        disposed,
        ...(!ready || !usable || (clickEnabled && !clicked.includes('A1'))
            ? { error: `ready=${ready} usable=${usable} clicked=${clicked.join(',')}` }
            : {}),
    };
}

async function runMolstarConsumerSmoke(): Promise<ConsumerSmokeReport> {
    const structureUrl = URL.createObjectURL(new Blob([PDB_TEXT], { type: 'chemical/x-pdb' }));
    const overlayUrl = URL.createObjectURL(new Blob([`${PDB_TEXT}REMARK overlay\n`], { type: 'chemical/x-pdb' }));
    const base: MolstarViewerProps = { structureUrl, format: 'pdb', hideControls: true, alphafoldView: false, height: 360 };
    const generic: SiteResult[] = [];
    const failures: string[] = [];
    try {
        generic.push(...await smokeDirectGroup(
            ['G1', 'G2'],
            ['DockingComparePane:DiffDock orphan fixture', 'DockingComparePane:Uni-Dock orphan fixture'],
            [{ ...base, height: 350, backgroundColor: '#1e1b4b' }, { ...base, height: 350, backgroundColor: '#022c22' }],
        ));
        generic.push(...await smokeDirectGroup(['G3'], ['FloatingViewer orphan fixture'], [{ ...base, height: '100%' }]));
        generic.push(...await smokeDirectGroup(['G4'], ['JobDetailPage docking result'], [{ ...base, height: 500 }]));
        generic.push(...await smokeDirectGroup(['G5'], ['MutagenesisTemplate FrustraMPNN'], [{ ...base, height: 280, alphafoldView: false, residueMetricLayer: metricLayer, label: 'Frustration Map' }]));
        generic.push(...await smokeDirectGroup(['G6'], ['OligoDesignerTemplate scaffold'], [{ ...base, height: 300, hideControls: false, backgroundColor: '#1e293b' }]));
        generic.push(...await smokeDirectGroup(['G7'], ['Dashboard QuickViewer'], [{ ...base, height: 480, alphafoldView: true }]));
        generic.push(...await smokeDirectGroup(['G8'], ['ResultsViewer antibody binder info'], [{ ...base, height: '100%', selections: selection, label: 'CDR regions' }]));
        generic.push(...await smokeDirectGroup(['G9'], ['StructurePredictionTemplate target preview'], [{ ...base, height: 240, selections: selection, label: 'A' }]));
        generic.push(...await smokeDirectGroup(['G10'], ['StructurePredictionTemplate modal preview'], [{ ...base, height: 260, selections: selection, label: 'A' }]));
        generic.push(...await smokeDirectGroup(
            ['G11', 'G12'],
            ['StructureViewerPane primary/overlay', 'StructureViewerPane reference'],
            [
                { ...base, height: '100%', residueMetricLayer: metricLayer, overlayStructures: [{ id: 'overlay', structureUrl: overlayUrl, format: 'pdb' }] },
                { ...base, height: '100%', label: 'Reference structure' },
            ],
        ));

        const epitope = [
            await smokeEpitope('E1', 'AntibodyDenovoTemplate target/framework', false, true),
            await smokeEpitope('E2', 'MutagenesisTemplate click-to-mutate', true, true),
            await smokeEpitope('E3', 'ProteinLocalRedesignTemplate region selection', true, true),
        ];

        for (const result of [...generic, ...epitope]) {
            if (!result.ready || !result.usable || !result.disposed) {
                failures.push(`${result.id}: ${result.error ?? JSON.stringify(result)}`);
            }
        }
        if (errors.length) failures.push(`${errors.length} console/runtime errors`);
        setStatus(failures.length ? `consumer smoke FAIL (${failures.length})` : 'consumer smoke PASS');
        return {
            currentInventory: {
                generic: 12,
                epitope: 3,
                total: 15,
                routeReachable: 12,
                orphanFixtures: ['G1', 'G2', 'G3'],
                retiredHistoricalSites: ['one retired template', 'one pre-HEAD epitope site absent from current history'],
            },
            generic,
            epitope,
            consoleErrors: errors.slice(0, 30),
            consoleWarnings: warnings.slice(0, 30),
            failures,
        };
    } finally {
        root.render(null);
        URL.revokeObjectURL(structureUrl);
        URL.revokeObjectURL(overlayUrl);
    }
}

async function cleanupMolstarConsumerSmoke(): Promise<void> {
    root.render(null);
    await delay(250);
    root.unmount();
}

declare global {
    interface Window {
        runMolstarConsumerSmoke: typeof runMolstarConsumerSmoke;
        cleanupMolstarConsumerSmoke: typeof cleanupMolstarConsumerSmoke;
    }
}

window.runMolstarConsumerSmoke = runMolstarConsumerSmoke;
window.cleanupMolstarConsumerSmoke = cleanupMolstarConsumerSmoke;
setStatus('consumer smoke ready');
