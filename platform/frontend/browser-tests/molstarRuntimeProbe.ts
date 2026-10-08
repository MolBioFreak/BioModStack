import React, { StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import MolstarViewer from '../src/components/MolstarViewer';
import {
    getMolstarDirectAdapterForElement,
    MolstarDirectAdapter,
} from '../src/structureViewer/adapters/MolstarDirectAdapter';

const PDB_TEXT = `HEADER    BMS M1 DIRECT MOLSTAR PROBE
ATOM      1  N   ALA A   1      -0.525   1.363   0.000  1.00 20.00           N
ATOM      2  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C
ATOM      3  C   ALA A   1       1.526   0.000   0.000  1.00 20.00           C
ATOM      4  O   ALA A   1       2.152  -1.055   0.000  1.00 20.00           O
ATOM      5  CB  ALA A   1      -0.507  -0.779  -1.216  1.00 20.00           C
ATOM      6  N   GLY A   2       2.121   1.194   0.000  1.00 20.00           N
ATOM      7  CA  GLY A   2       3.576   1.285   0.000  1.00 20.00           C
ATOM      8  C   GLY A   2       4.078   2.720   0.000  1.00 20.00           C
ATOM      9  O   GLY A   2       3.329   3.682   0.000  1.00 20.00           O
TER
END
`;

// The probe reads browser/engine counters that are intentionally outside stable DOM typings.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Untyped = Record<string, any>;
type DirectAdapter = MolstarDirectAdapter;

interface ProbeSample {
    readonly label: string;
    readonly atMs: number;
    readonly hosts: number;
    readonly canvases: number;
    readonly taggedWebglCanvases: number;
    readonly webglContextRequests: number;
    readonly activeTimeouts: number;
    readonly activeIntervals: number;
    readonly activeAnimationFrames: number;
    readonly activeBlobUrls: number;
    readonly listenerAdds: number;
    readonly listenerRemoves: number;
    readonly listenerOperationDelta: number;
    readonly consoleErrors: number;
    readonly consoleWarnings: number;
    readonly usedJsHeapBytes: number | null;
}

interface CycleResult {
    readonly cycle: number;
    readonly ready: boolean;
    readonly usable: boolean;
    readonly disposedAfterUnmount: boolean;
    readonly pluginDisposedAfterUnmount: boolean;
    readonly error?: string;
}

interface MatrixResult {
    readonly ready: boolean;
    readonly usable: boolean;
    readonly disposed: boolean;
    readonly pluginDisposed: boolean;
    readonly structureCount: number;
    readonly canvasCountAfterDispose: number;
}

interface ReplacementResult {
    readonly firstReady: boolean;
    readonly secondReady: boolean;
    readonly secondUsable: boolean;
    readonly thirdReady: boolean;
    readonly thirdUsable: boolean;
    readonly adapterReused: boolean;
    readonly firstSceneGeneration: number;
    readonly secondSceneGeneration: number;
    readonly thirdSceneGeneration: number;
    readonly overlayAdded: boolean;
    readonly overlayRemoved: boolean;
    readonly finalDisposed: boolean;
    readonly finalPluginDisposed: boolean;
}

interface AlphafoldConcurrencyResult {
    readonly firstReady: boolean;
    readonly secondReady: boolean;
    readonly firstUsable: boolean;
    readonly secondUsable: boolean;
    readonly secondUsableAfterFirstDisposed: boolean;
    readonly firstDisposed: boolean;
    readonly secondDisposed: boolean;
    readonly firstPluginDisposed: boolean;
    readonly secondPluginDisposed: boolean;
    readonly canvasCountAfterDispose: number;
}

interface ProbeReport {
    readonly schemaVersion: 2;
    readonly runtime: {
        readonly userAgent: string;
        readonly engine: 'molstar@4.5.0-direct';
        readonly reactStrictMode: true;
        readonly requestedCycles: number;
    };
    readonly directOwner: MatrixResult;
    readonly alphafoldConcurrency: AlphafoldConcurrencyResult;
    readonly zeroViewer: { readonly hostCount: number; readonly canvasCount: number };
    readonly replacement: ReplacementResult;
    readonly cycles: readonly CycleResult[];
    readonly samples: readonly ProbeSample[];
    readonly finalLiveViewer: {
        readonly ready: boolean;
        readonly usable: boolean;
        readonly disposed: boolean;
        readonly pluginDisposed: boolean;
        readonly hostCount: number;
        readonly canvasCount: number;
        readonly structureCount: number;
    };
    readonly console: {
        readonly errors: readonly string[];
        readonly warnings: readonly string[];
    };
    readonly failures: readonly string[];
}

const statusElement = document.querySelector<HTMLElement>('#status');
const startedAt = performance.now();
const errors: string[] = [];
const warnings: string[] = [];

const originalConsoleError = console.error.bind(console);
const originalConsoleWarn = console.warn.bind(console);
console.error = (...args: unknown[]) => {
    errors.push(args.map(String).join(' '));
    originalConsoleError(...args);
};
console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(' '));
    originalConsoleWarn(...args);
};
window.addEventListener('error', (event) => errors.push(`window.error: ${event.message}`));
window.addEventListener('unhandledrejection', (event) => errors.push(`unhandledrejection: ${String(event.reason)}`));

const originalSetTimeout = window.setTimeout.bind(window);
const originalClearTimeout = window.clearTimeout.bind(window);
const originalSetInterval = window.setInterval.bind(window);
const originalClearInterval = window.clearInterval.bind(window);
const originalRequestAnimationFrame = window.requestAnimationFrame.bind(window);
const originalCancelAnimationFrame = window.cancelAnimationFrame.bind(window);
const activeTimeouts = new Set<number>();
const activeIntervals = new Set<number>();
const activeAnimationFrames = new Set<number>();

window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
    let id = 0;
    const wrapped = (...callbackArgs: unknown[]) => {
        activeTimeouts.delete(id);
        if (typeof handler === 'function') handler(...callbackArgs);
        else window.eval(handler);
    };
    id = originalSetTimeout(wrapped, timeout, ...args);
    activeTimeouts.add(id);
    return id;
}) as typeof window.setTimeout;
window.clearTimeout = ((id?: number) => {
    if (id !== undefined) activeTimeouts.delete(id);
    originalClearTimeout(id);
}) as typeof window.clearTimeout;
window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
    const id = originalSetInterval(handler, timeout, ...args);
    activeIntervals.add(id);
    return id;
}) as typeof window.setInterval;
window.clearInterval = ((id?: number) => {
    if (id !== undefined) activeIntervals.delete(id);
    originalClearInterval(id);
}) as typeof window.clearInterval;
window.requestAnimationFrame = ((callback: FrameRequestCallback) => {
    let id = 0;
    id = originalRequestAnimationFrame((time) => {
        activeAnimationFrames.delete(id);
        callback(time);
    });
    activeAnimationFrames.add(id);
    return id;
}) as typeof window.requestAnimationFrame;
window.cancelAnimationFrame = ((id: number) => {
    activeAnimationFrames.delete(id);
    originalCancelAnimationFrame(id);
}) as typeof window.cancelAnimationFrame;

const originalCreateObjectUrl = URL.createObjectURL.bind(URL);
const originalRevokeObjectUrl = URL.revokeObjectURL.bind(URL);
const activeBlobUrls = new Set<string>();
URL.createObjectURL = ((object: Blob | MediaSource) => {
    const url = originalCreateObjectUrl(object);
    activeBlobUrls.add(url);
    return url;
}) as typeof URL.createObjectURL;
URL.revokeObjectURL = ((url: string) => {
    activeBlobUrls.delete(url);
    originalRevokeObjectUrl(url);
}) as typeof URL.revokeObjectURL;

let listenerAdds = 0;
let listenerRemoves = 0;
const originalAddEventListener = EventTarget.prototype.addEventListener;
const originalRemoveEventListener = EventTarget.prototype.removeEventListener;
EventTarget.prototype.addEventListener = function (...args: Parameters<EventTarget['addEventListener']>) {
    listenerAdds += 1;
    return originalAddEventListener.apply(this, args);
};
EventTarget.prototype.removeEventListener = function (...args: Parameters<EventTarget['removeEventListener']>) {
    listenerRemoves += 1;
    return originalRemoveEventListener.apply(this, args);
};

let webglContextRequests = 0;
const originalGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (
    this: HTMLCanvasElement,
    contextId: string,
    ...args: unknown[]
) {
    const result = (originalGetContext as Untyped).call(this, contextId, ...args);
    if (contextId === 'webgl' || contextId === 'webgl2' || contextId === 'experimental-webgl') {
        webglContextRequests += 1;
        this.dataset.bmsM1Webgl = 'true';
    }
    return result;
} as typeof HTMLCanvasElement.prototype.getContext;

const delay = (milliseconds: number) => new Promise<void>((resolve) => originalSetTimeout(resolve, milliseconds));

function setStatus(message: string): void {
    if (statusElement) statusElement.textContent = message;
}

function getUsedHeapBytes(): number | null {
    const memory = (performance as Untyped).memory;
    return typeof memory?.usedJSHeapSize === 'number' ? memory.usedJSHeapSize : null;
}

function sample(label: string): ProbeSample {
    return {
        label,
        atMs: Math.round(performance.now() - startedAt),
        hosts: document.querySelectorAll('[data-bms-molstar-adapter="direct-4.5.0"]').length,
        canvases: document.querySelectorAll('canvas').length,
        taggedWebglCanvases: document.querySelectorAll('canvas[data-bms-m1-webgl="true"]').length,
        webglContextRequests,
        activeTimeouts: activeTimeouts.size,
        activeIntervals: activeIntervals.size,
        activeAnimationFrames: activeAnimationFrames.size,
        activeBlobUrls: activeBlobUrls.size,
        listenerAdds,
        listenerRemoves,
        listenerOperationDelta: listenerAdds - listenerRemoves,
        consoleErrors: errors.length,
        consoleWarnings: warnings.length,
        usedJsHeapBytes: getUsedHeapBytes(),
    };
}

function adapterForHost(host: HTMLElement): DirectAdapter | undefined {
    const mount = host.querySelector<HTMLElement>('[data-bms-molstar-mount="true"]');
    return mount ? getMolstarDirectAdapterForElement(mount) : undefined;
}

async function waitForReady(
    container: ParentNode,
    minimumStructures = 1,
    timeoutMs = 30_000,
): Promise<{ host: HTMLElement; adapter: DirectAdapter } | null> {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
        const host = container.querySelector<HTMLElement>('[data-bms-molstar-adapter="direct-4.5.0"]');
        const adapter = host ? adapterForHost(host) : undefined;
        if (host?.dataset.bmsMolstarStatus === 'error') return null;
        if (
            host?.isConnected
            && host.dataset.bmsMolstarStatus === 'ready'
            && adapter
            && !adapter.diagnostics.disposed
            && adapter.diagnostics.hasCanvas3d
            && adapter.diagnostics.structureCount >= minimumStructures
        ) {
            return { host, adapter };
        }
        await delay(25);
    }
    return null;
}

async function waitForDisposed(adapter: DirectAdapter, timeoutMs = 10_000): Promise<boolean> {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
        if (adapter.diagnostics.disposed && adapter.diagnostics.pluginDisposed) return true;
        await delay(10);
    }
    return false;
}

async function verifyUsable(adapter: DirectAdapter): Promise<boolean> {
    if (adapter.diagnostics.disposed || !adapter.diagnostics.hasCanvas3d) return false;
    try {
        await adapter.applyPresentation({
            colorSelections: [{ struct_asym_id: 'A', residue_number: 1, color: '#ff4d4d' }],
        });
        await adapter.applyPresentation({});
        return !adapter.diagnostics.disposed && adapter.diagnostics.hasCanvas3d;
    } catch (error) {
        errors.push(`usability: ${String(error)}`);
        return false;
    }
}

let reactRoot: Root | null = null;
let structureUrl: string | null = null;
let finalAdapter: DirectAdapter | null = null;

async function directOwnerProbe(rawRoot: HTMLElement): Promise<MatrixResult> {
    const target = document.createElement('div');
    target.style.cssText = 'width: 640px; height: 480px; position: relative';
    rawRoot.appendChild(target);
    const adapter = new MolstarDirectAdapter({ hideControls: true, alphafoldView: false });
    await adapter.mount(target);
    await adapter.loadScene([
        { id: 'primary', url: structureUrl!, format: 'pdb' },
        { id: 'overlay', url: structureUrl!, format: 'pdb' },
    ]);
    const ready = adapter.diagnostics.structureCount === 2 && adapter.diagnostics.hasCanvas3d;
    const usable = ready && await verifyUsable(adapter);
    const structureCount = adapter.diagnostics.structureCount;
    adapter.dispose();
    await waitForDisposed(adapter);
    const canvasCountAfterDispose = target.querySelectorAll('canvas').length;
    target.remove();
    return {
        ready,
        usable,
        disposed: adapter.diagnostics.disposed,
        pluginDisposed: adapter.diagnostics.pluginDisposed,
        structureCount,
        canvasCountAfterDispose,
    };
}

async function alphafoldConcurrencyProbe(rawRoot: HTMLElement): Promise<AlphafoldConcurrencyResult> {
    const firstTarget = document.createElement('div');
    const secondTarget = document.createElement('div');
    firstTarget.style.cssText = 'width: 480px; height: 360px; position: relative';
    secondTarget.style.cssText = firstTarget.style.cssText;
    rawRoot.append(firstTarget, secondTarget);

    const first = new MolstarDirectAdapter({ hideControls: true, alphafoldView: true });
    const second = new MolstarDirectAdapter({ hideControls: true, alphafoldView: true });
    await Promise.all([first.mount(firstTarget), second.mount(secondTarget)]);
    await Promise.all([
        first.loadScene([{ id: 'alpha-first', url: structureUrl!, format: 'pdb' }]),
        second.loadScene([{ id: 'alpha-second', url: structureUrl!, format: 'pdb' }]),
    ]);

    const firstReady = first.diagnostics.structureCount === 1 && first.diagnostics.hasCanvas3d;
    const secondReady = second.diagnostics.structureCount === 1 && second.diagnostics.hasCanvas3d;
    const firstUsable = firstReady && await verifyUsable(first);
    const secondUsable = secondReady && await verifyUsable(second);

    first.dispose();
    const firstDisposed = await waitForDisposed(first);
    const secondUsableAfterFirstDisposed = await verifyUsable(second);
    second.dispose();
    const secondDisposed = await waitForDisposed(second);
    const firstPluginDisposed = first.diagnostics.pluginDisposed;
    const secondPluginDisposed = second.diagnostics.pluginDisposed;
    const canvasCountAfterDispose = rawRoot.querySelectorAll('canvas').length;
    firstTarget.remove();
    secondTarget.remove();

    return {
        firstReady,
        secondReady,
        firstUsable,
        secondUsable,
        secondUsableAfterFirstDisposed,
        firstDisposed,
        secondDisposed,
        firstPluginDisposed,
        secondPluginDisposed,
        canvasCountAfterDispose,
    };
}

async function zeroViewerProbe(probeRoot: HTMLElement): Promise<ProbeReport['zeroViewer']> {
    reactRoot!.render(React.createElement(StrictMode, null, React.createElement(MolstarViewer, { height: 480 })));
    await delay(100);
    const result = {
        hostCount: probeRoot.querySelectorAll('[data-bms-molstar-adapter]').length,
        canvasCount: probeRoot.querySelectorAll('canvas').length,
    };
    reactRoot!.render(null);
    await delay(50);
    return result;
}

async function replacementProbe(probeRoot: HTMLElement): Promise<ReplacementResult> {
    reactRoot!.render(React.createElement(
        StrictMode,
        null,
        React.createElement(MolstarViewer, {
            key: 'replacement-probe',
            structureUrl: structureUrl!,
            format: 'pdb',
            alphafoldView: false,
            hideControls: true,
            height: 480,
        }),
    ));
    const first = await waitForReady(probeRoot);
    const firstSceneGeneration = first?.adapter.diagnostics.completedSceneGeneration ?? 0;
    const replacementUrl = URL.createObjectURL(new Blob([`${PDB_TEXT}REMARK replacement\n`], { type: 'chemical/x-pdb' }));
    reactRoot!.render(React.createElement(
        StrictMode,
        null,
        React.createElement(MolstarViewer, {
            key: 'replacement-probe',
            structureUrl: replacementUrl,
            format: 'pdb',
            alphafoldView: false,
            hideControls: true,
            height: 480,
            overlayStructures: [{ id: 'replacement-overlay', structureUrl: structureUrl!, format: 'pdb' }],
        }),
    ));

    const secondDeadline = performance.now() + 30_000;
    let second: Awaited<ReturnType<typeof waitForReady>> = null;
    while (performance.now() < secondDeadline) {
        const candidate = await waitForReady(probeRoot, 2, 250);
        if (candidate
            && candidate.adapter === first?.adapter
            && candidate.adapter.diagnostics.completedSceneGeneration > firstSceneGeneration) {
            second = candidate;
            break;
        }
        await delay(25);
    }
    const secondUsable = second ? await verifyUsable(second.adapter) : false;
    const secondSceneGeneration = second?.adapter.diagnostics.completedSceneGeneration ?? 0;
    const overlayAdded = second?.adapter.diagnostics.structureCount === 2;

    reactRoot!.render(React.createElement(
        StrictMode,
        null,
        React.createElement(MolstarViewer, {
            key: 'replacement-probe',
            structureUrl: replacementUrl,
            format: 'pdb',
            alphafoldView: false,
            hideControls: true,
            height: 480,
        }),
    ));
    const thirdDeadline = performance.now() + 30_000;
    let third: Awaited<ReturnType<typeof waitForReady>> = null;
    while (performance.now() < thirdDeadline) {
        const candidate = await waitForReady(probeRoot, 1, 250);
        if (candidate
            && candidate.adapter === second?.adapter
            && candidate.adapter.diagnostics.completedSceneGeneration > secondSceneGeneration
            && candidate.adapter.diagnostics.structureCount === 1) {
            third = candidate;
            break;
        }
        await delay(25);
    }
    const thirdUsable = third ? await verifyUsable(third.adapter) : false;
    const thirdSceneGeneration = third?.adapter.diagnostics.completedSceneGeneration ?? 0;
    const overlayRemoved = third?.adapter.diagnostics.structureCount === 1;

    reactRoot!.render(null);
    const finalDisposed = third ? await waitForDisposed(third.adapter) : false;
    const finalPluginDisposed = third?.adapter.diagnostics.pluginDisposed ?? false;
    URL.revokeObjectURL(replacementUrl);
    return {
        firstReady: Boolean(first),
        secondReady: Boolean(second),
        secondUsable,
        thirdReady: Boolean(third),
        thirdUsable,
        adapterReused: Boolean(first && second && third
            && first.adapter === second.adapter && second.adapter === third.adapter),
        firstSceneGeneration,
        secondSceneGeneration,
        thirdSceneGeneration,
        overlayAdded,
        overlayRemoved,
        finalDisposed,
        finalPluginDisposed,
    };
}

async function runMolstarRuntimeProbe(requestedCycles = 55): Promise<ProbeReport> {
    if (!Number.isInteger(requestedCycles) || requestedCycles < 50) {
        throw new Error('M1 requires at least 50 lifecycle cycles');
    }

    const probeRoot = document.querySelector<HTMLElement>('#probe-root');
    const rawRoot = document.querySelector<HTMLElement>('#raw-probe-root');
    if (!probeRoot || !rawRoot) throw new Error('Probe roots are missing');

    setStatus('loading direct Mol* runtime');
    structureUrl = URL.createObjectURL(new Blob([PDB_TEXT], { type: 'chemical/x-pdb' }));
    reactRoot = createRoot(probeRoot);

    const samples: ProbeSample[] = [sample('baseline-before-direct-owner')];
    const directOwner = await directOwnerProbe(rawRoot);
    samples.push(sample('after-direct-owner-cleanup'));
    const alphafoldConcurrency = await alphafoldConcurrencyProbe(rawRoot);
    samples.push(sample('after-alphafold-concurrency-cleanup'));
    const zeroViewer = await zeroViewerProbe(probeRoot);
    const replacement = await replacementProbe(probeRoot);
    samples.push(sample('after-replacement-cleanup'));
    (window as Untyped).__bmsM1WarmupReady = true;
    const warmupAckDeadline = performance.now() + 10_000;
    while (!(window as Untyped).__bmsM1WarmupAcknowledged && performance.now() < warmupAckDeadline) {
        await delay(25);
    }

    const cycles: CycleResult[] = [];
    for (let cycle = 1; cycle <= requestedCycles; cycle += 1) {
        setStatus(`direct StrictMode lifecycle ${cycle}/${requestedCycles}`);
        let adapter: DirectAdapter | undefined;
        try {
            reactRoot.render(React.createElement(
                StrictMode,
                null,
                React.createElement(MolstarViewer, {
                    key: `cycle-${cycle}`,
                    structureUrl: structureUrl!,
                    format: 'pdb',
                    alphafoldView: false,
                    hideControls: true,
                    height: 480,
                    ...(cycle % 2 === 0
                        ? { residueColors: new Map([['A:1', { r: 255, g: 77, b: 77 }]]) }
                        : { selections: [{ chain_id: 'A', start_residue_number: 1, end_residue_number: 1, color: { r: 255, g: 77, b: 77 } }] }),
                }),
            ));
            const readyResult = await waitForReady(probeRoot);
            adapter = readyResult?.adapter;
            const ready = Boolean(readyResult);
            const usable = adapter ? await verifyUsable(adapter) : false;
            reactRoot.render(null);
            const disposed = adapter ? await waitForDisposed(adapter) : false;
            cycles.push({
                cycle,
                ready,
                usable,
                disposedAfterUnmount: disposed && adapter?.diagnostics.disposed === true,
                pluginDisposedAfterUnmount: adapter?.diagnostics.pluginDisposed === true,
            });
        } catch (error) {
            reactRoot.render(null);
            cycles.push({
                cycle,
                ready: false,
                usable: false,
                disposedAfterUnmount: adapter?.diagnostics.disposed === true,
                pluginDisposedAfterUnmount: adapter?.diagnostics.pluginDisposed === true,
                error: String(error),
            });
        }

        if (cycle === 1 || cycle % 5 === 0 || cycle === requestedCycles) {
            (window as Untyped).gc?.();
            await delay(75);
            samples.push(sample(`after-cycle-${cycle}`));
        }
    }

    setStatus('mounting final direct viewer');
    reactRoot.render(React.createElement(
        StrictMode,
        null,
        React.createElement(MolstarViewer, {
            key: 'final-live-viewer',
            structureUrl: structureUrl!,
            format: 'pdb',
            alphafoldView: false,
            hideControls: true,
            height: 480,
        }),
    ));
    const final = await waitForReady(probeRoot);
    finalAdapter = final?.adapter ?? null;
    const finalReady = Boolean(final);
    const finalUsable = finalAdapter ? await verifyUsable(finalAdapter) : false;
    (window as Untyped).gc?.();
    await delay(100);
    samples.push(sample('final-live-viewer'));

    const finalLiveViewer = {
        ready: finalReady,
        usable: finalUsable,
        disposed: finalAdapter?.diagnostics.disposed ?? true,
        pluginDisposed: finalAdapter?.diagnostics.pluginDisposed ?? true,
        hostCount: document.querySelectorAll('[data-bms-molstar-adapter="direct-4.5.0"]').length,
        canvasCount: document.querySelectorAll('canvas').length,
        structureCount: finalAdapter?.diagnostics.structureCount ?? 0,
    };

    const failures: string[] = [];
    if (!directOwner.ready || !directOwner.usable || !directOwner.disposed || !directOwner.pluginDisposed) {
        failures.push(`direct owner: ${JSON.stringify(directOwner)}`);
    }
    if (directOwner.structureCount !== 2 || directOwner.canvasCountAfterDispose !== 0) {
        failures.push(`direct owner overlay/DOM cleanup: structures=${directOwner.structureCount} canvases=${directOwner.canvasCountAfterDispose}`);
    }
    if (!alphafoldConcurrency.firstReady
        || !alphafoldConcurrency.secondReady
        || !alphafoldConcurrency.firstUsable
        || !alphafoldConcurrency.secondUsable
        || !alphafoldConcurrency.secondUsableAfterFirstDisposed
        || !alphafoldConcurrency.firstDisposed
        || !alphafoldConcurrency.secondDisposed
        || !alphafoldConcurrency.firstPluginDisposed
        || !alphafoldConcurrency.secondPluginDisposed
        || alphafoldConcurrency.canvasCountAfterDispose !== 0) {
        failures.push(`AlphaFold concurrency: ${JSON.stringify(alphafoldConcurrency)}`);
    }
    if (zeroViewer.hostCount !== 0 || zeroViewer.canvasCount !== 0) {
        failures.push(`zero viewer allocated resources: ${JSON.stringify(zeroViewer)}`);
    }
    if (!replacement.firstReady || !replacement.secondReady || !replacement.secondUsable
        || !replacement.thirdReady || !replacement.thirdUsable
        || !replacement.adapterReused
        || replacement.secondSceneGeneration <= replacement.firstSceneGeneration
        || replacement.thirdSceneGeneration <= replacement.secondSceneGeneration
        || !replacement.overlayAdded || !replacement.overlayRemoved
        || !replacement.finalDisposed || !replacement.finalPluginDisposed) {
        failures.push(`replacement: ${JSON.stringify(replacement)}`);
    }
    for (const cycle of cycles) {
        if (!cycle.ready || !cycle.usable || !cycle.disposedAfterUnmount || !cycle.pluginDisposedAfterUnmount) {
            failures.push(`cycle ${cycle.cycle}: ready=${cycle.ready} usable=${cycle.usable} disposed=${cycle.disposedAfterUnmount} pluginDisposed=${cycle.pluginDisposedAfterUnmount}${cycle.error ? ` error=${cycle.error}` : ''}`);
        }
    }
    if (!finalReady || !finalUsable || finalLiveViewer.disposed || finalLiveViewer.pluginDisposed) {
        failures.push(`final live viewer: ${JSON.stringify(finalLiveViewer)}`);
    }
    if (errors.length > 0) failures.push(`${errors.length} browser console/runtime errors captured`);

    setStatus(failures.length === 0 ? 'probe complete: PASS' : `probe complete: ${failures.length} failures`);
    return {
        schemaVersion: 2,
        runtime: {
            userAgent: navigator.userAgent,
            engine: 'molstar@4.5.0-direct',
            reactStrictMode: true,
            requestedCycles,
        },
        directOwner,
        alphafoldConcurrency,
        zeroViewer,
        replacement,
        cycles,
        samples,
        finalLiveViewer,
        console: {
            errors: errors.slice(0, 20),
            warnings: warnings.slice(0, 20),
        },
        failures,
    };
}

async function cleanupMolstarRuntimeProbe(): Promise<ProbeSample> {
    reactRoot?.render(null);
    if (finalAdapter) await waitForDisposed(finalAdapter);
    reactRoot?.unmount();
    reactRoot = null;
    finalAdapter = null;
    if (structureUrl) URL.revokeObjectURL(structureUrl);
    structureUrl = null;
    (window as Untyped).gc?.();
    await delay(150);
    return sample('after-final-cleanup');
}

declare global {
    interface Window {
        runMolstarRuntimeProbe: typeof runMolstarRuntimeProbe;
        cleanupMolstarRuntimeProbe: typeof cleanupMolstarRuntimeProbe;
    }
}

window.runMolstarRuntimeProbe = runMolstarRuntimeProbe;
window.cleanupMolstarRuntimeProbe = cleanupMolstarRuntimeProbe;
setStatus('probe ready');
