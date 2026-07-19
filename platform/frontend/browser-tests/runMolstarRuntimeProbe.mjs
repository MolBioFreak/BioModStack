#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(frontendRoot, '../..');
const outputPath = path.resolve(
    process.argv[2] ?? path.join(repoRoot, 'docs/reviews/structure_visualization/evidence/m1_direct_molstar_runtime_probe_chrome150.json'),
);
const requestedCycles = Number.parseInt(process.env.BMS_M1_CYCLES ?? process.env.BMS_M0_CYCLES ?? '55', 10);
const chromeBinary = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function freePort() {
    return new Promise((resolve, reject) => {
        const server = createServer();
        server.once('error', reject);
        server.listen(0, '127.0.0.1', () => {
            const address = server.address();
            const port = typeof address === 'object' && address ? address.port : 0;
            server.close((error) => error ? reject(error) : resolve(port));
        });
    });
}

async function waitForHttp(url, timeoutMs = 30_000) {
    const deadline = Date.now() + timeoutMs;
    let lastError;
    while (Date.now() < deadline) {
        try {
            const response = await fetch(url);
            if (response.ok) return;
            lastError = new Error(`HTTP ${response.status}`);
        } catch (error) {
            lastError = error;
        }
        await sleep(100);
    }
    throw new Error(`Vite did not become ready: ${String(lastError)}`);
}

class CdpPipe {
    constructor(process) {
        this.process = process;
        this.nextId = 1;
        this.pending = new Map();
        this.events = [];
        this.buffer = '';
        process.stdio[4].setEncoding('utf8');
        process.stdio[4].on('data', (chunk) => this.onData(chunk));
        process.stdio[4].on('error', (error) => this.rejectAll(error));
        process.once('exit', (code, signal) => this.rejectAll(new Error(`Chrome exited code=${code} signal=${signal}`)));
    }

    onData(chunk) {
        this.buffer += chunk;
        while (true) {
            const boundary = this.buffer.indexOf('\0');
            if (boundary < 0) break;
            const raw = this.buffer.slice(0, boundary);
            this.buffer = this.buffer.slice(boundary + 1);
            if (!raw) continue;
            const message = JSON.parse(raw);
            if (message.id !== undefined) {
                const pending = this.pending.get(message.id);
                if (!pending) continue;
                this.pending.delete(message.id);
                clearTimeout(pending.timer);
                if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
                else pending.resolve(message.result ?? {});
            } else {
                this.events.push(message);
            }
        }
    }

    rejectAll(error) {
        for (const pending of this.pending.values()) {
            clearTimeout(pending.timer);
            pending.reject(error);
        }
        this.pending.clear();
    }

    send(method, params = {}, sessionId, timeoutMs = 240_000) {
        const id = this.nextId++;
        const message = { id, method, params, ...(sessionId ? { sessionId } : {}) };
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pending.delete(id);
                reject(new Error(`${method} timed out after ${timeoutMs}ms`));
            }, timeoutMs);
            this.pending.set(id, { resolve, reject, timer, method });
            this.process.stdio[3].write(`${JSON.stringify(message)}\0`);
        });
    }
}

function valueFromEvaluation(result) {
    if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text ?? 'Runtime evaluation failed');
    }
    return result.result?.value;
}

async function getDocumentListenerSummary(cdp, sessionId) {
    const evaluation = await cdp.send('Runtime.evaluate', {
        expression: 'document',
        returnByValue: false,
    }, sessionId);
    const objectId = evaluation.result?.objectId;
    if (!objectId) throw new Error('CDP did not return a document objectId');
    try {
        const { listeners } = await cdp.send('DOMDebugger.getEventListeners', {
            objectId,
            depth: 1,
            pierce: true,
        }, sessionId);
        const byType = {};
        for (const listener of listeners ?? []) {
            byType[listener.type] = (byType[listener.type] ?? 0) + 1;
        }
        return {
            total: listeners?.length ?? 0,
            byType: Object.fromEntries(Object.entries(byType).sort(([left], [right]) => left.localeCompare(right))),
        };
    } finally {
        await cdp.send('Runtime.releaseObject', { objectId }, sessionId).catch(() => {});
    }
}

async function main() {
    if (!Number.isInteger(requestedCycles) || requestedCycles < 50) {
        throw new Error('BMS_M1_CYCLES must be an integer >= 50');
    }

    const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'bms-m1-direct-molstar-'));
    const viteLog = [];
    const chromeLog = [];
    let vite;
    let chrome;
    let cdp;
    let lastProbeStatus = 'not started';
    try {
        const port = await freePort();
        const url = `http://127.0.0.1:${port}/browser-tests/molstar-runtime-probe.html`;
        vite = spawn('pnpm', ['exec', 'vite', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
            cwd: frontendRoot,
            env: {
                ...process.env,
                BMS_VITE_CACHE_DIR: path.join(tempRoot, 'vite-cache'),
            },
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        vite.stdout.on('data', (chunk) => viteLog.push(chunk.toString()));
        vite.stderr.on('data', (chunk) => viteLog.push(chunk.toString()));
        await waitForHttp(url);

        chrome = spawn(chromeBinary, [
            '--headless=new',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-background-networking',
            '--disable-default-apps',
            '--disable-extensions',
            '--disable-sync',
            '--enable-precise-memory-info',
            '--enable-unsafe-swiftshader',
            '--js-flags=--expose-gc',
            '--remote-debugging-pipe',
            `--user-data-dir=${path.join(tempRoot, 'chrome-profile')}`,
            'about:blank',
        ], {
            cwd: frontendRoot,
            stdio: ['ignore', 'pipe', 'pipe', 'pipe', 'pipe'],
        });
        chrome.stdout.on('data', (chunk) => chromeLog.push(chunk.toString()));
        chrome.stderr.on('data', (chunk) => chromeLog.push(chunk.toString()));

        cdp = new CdpPipe(chrome);
        const { targetId } = await cdp.send('Target.createTarget', { url });
        const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
        await Promise.all([
            cdp.send('Log.enable', {}, sessionId),
            cdp.send('Page.enable', {}, sessionId),
        ]);

        const waitForProbeFunction = async (timeoutMs) => {
            const readyDeadline = Date.now() + timeoutMs;
            while (Date.now() < readyDeadline) {
                const evaluation = await cdp.send('Runtime.evaluate', {
                    expression: 'typeof window.runMolstarRuntimeProbe === "function"',
                    returnByValue: true,
                }, sessionId);
                if (valueFromEvaluation(evaluation) === true) return true;
                await sleep(100);
            }
            return false;
        };
        let probeFunctionReady = await waitForProbeFunction(60_000);
        if (!probeFunctionReady) {
            // A cold Vite optimizer can invalidate dependency URLs while the
            // lazy Mol* implementation is first discovered. Reload once after
            // that optimization pass settles instead of invoking an undefined
            // probe function and waiting ten minutes for an impossible result.
            await cdp.send('Page.reload', { ignoreCache: true }, sessionId);
            probeFunctionReady = await waitForProbeFunction(120_000);
        }
        if (!probeFunctionReady) throw new Error('M1 probe module did not register after one optimizer reload');

        await cdp.send('HeapProfiler.collectGarbage', {}, sessionId);
        const domBefore = await cdp.send('Memory.getDOMCounters', {}, sessionId);
        const heapBefore = await cdp.send('Runtime.getHeapUsage', {}, sessionId);
        const documentListenersBefore = await getDocumentListenerSummary(cdp, sessionId);

        await cdp.send('Runtime.evaluate', {
            expression: `window.__bmsM1Probe = { done: false, result: null, error: null }; window.runMolstarRuntimeProbe(${requestedCycles}).then(result => { window.__bmsM1Probe = { done: true, result, error: null }; }).catch(error => { window.__bmsM1Probe = { done: true, result: null, error: String(error?.stack || error) }; }); true`,
            returnByValue: true,
        }, sessionId);

        const probeDeadline = Date.now() + 600_000;
        let probeState;
        let domWarmup;
        let heapWarmup;
        let documentListenersWarmup;
        while (Date.now() < probeDeadline) {
            const stateEvaluation = await cdp.send('Runtime.evaluate', {
                expression: `({ probe: window.__bmsM1Probe, status: document.querySelector('#status')?.textContent || '', warmupReady: window.__bmsM1WarmupReady === true })`,
                returnByValue: true,
            }, sessionId, 30_000);
            probeState = valueFromEvaluation(stateEvaluation);
            if (probeState?.status && probeState.status !== lastProbeStatus) {
                lastProbeStatus = probeState.status;
                console.error(`[probe] ${lastProbeStatus}`);
            }
            if (probeState?.warmupReady && !domWarmup) {
                await cdp.send('HeapProfiler.collectGarbage', {}, sessionId);
                domWarmup = await cdp.send('Memory.getDOMCounters', {}, sessionId);
                heapWarmup = await cdp.send('Runtime.getHeapUsage', {}, sessionId);
                documentListenersWarmup = await getDocumentListenerSummary(cdp, sessionId);
                await cdp.send('Runtime.evaluate', {
                    expression: 'window.__bmsM1WarmupAcknowledged = true',
                    returnByValue: true,
                }, sessionId);
            }
            if (probeState?.probe?.done) break;
            await sleep(2_000);
        }
        if (!probeState?.probe?.done) throw new Error(`M1 browser probe timed out; last status: ${lastProbeStatus}`);
        if (probeState.probe.error) throw new Error(`M1 page probe failed: ${probeState.probe.error}`);
        if (!domWarmup || !heapWarmup || !documentListenersWarmup) throw new Error('M1 warm retention baseline was not captured');
        const pageReport = probeState.probe.result;

        await cdp.send('HeapProfiler.collectGarbage', {}, sessionId);
        const domWithLiveViewer = await cdp.send('Memory.getDOMCounters', {}, sessionId);
        const heapWithLiveViewer = await cdp.send('Runtime.getHeapUsage', {}, sessionId);
        const documentListenersWithLiveViewer = await getDocumentListenerSummary(cdp, sessionId);

        const cleanupEvaluation = await cdp.send('Runtime.evaluate', {
            expression: 'window.cleanupMolstarRuntimeProbe()',
            awaitPromise: true,
            returnByValue: true,
        }, sessionId, 120_000);
        const cleanupSample = valueFromEvaluation(cleanupEvaluation);
        await cdp.send('HeapProfiler.collectGarbage', {}, sessionId);
        const domAfterCleanup = await cdp.send('Memory.getDOMCounters', {}, sessionId);
        const heapAfterCleanup = await cdp.send('Runtime.getHeapUsage', {}, sessionId);
        const documentListenersAfterCleanup = await getDocumentListenerSummary(cdp, sessionId);

        const browserEvents = cdp.events
            .filter((event) => event.method === 'Runtime.exceptionThrown' || event.method === 'Log.entryAdded')
            .map((event) => ({ method: event.method, params: event.params }));
        const retentionFailures = [];
        const listenerGrowth = domAfterCleanup.jsEventListeners - domWarmup.jsEventListeners;
        const documentListenerGrowth = documentListenersAfterCleanup.total - documentListenersWarmup.total;
        const heapGrowth = heapAfterCleanup.usedSize - heapWarmup.usedSize;
        if (listenerGrowth > 100) retentionFailures.push(`CDP live listener growth after cleanup: ${listenerGrowth}`);
        if (documentListenerGrowth > 12) retentionFailures.push(`document listener growth after cleanup: ${documentListenerGrowth}`);
        if (heapGrowth > 50 * 1024 * 1024) retentionFailures.push(`renderer heap growth after cleanup: ${heapGrowth} bytes`);
        const severeBrowserEvents = browserEvents.filter((event) =>
            event.method === 'Runtime.exceptionThrown'
            || (event.method === 'Log.entryAdded'
                && event.params?.entry?.level === 'error'
                && !event.params?.entry?.url?.endsWith('/favicon.ico'))
        );

        const evidence = {
            schemaVersion: 2,
            generatedAt: new Date().toISOString(),
            command: `BMS_M1_CYCLES=${requestedCycles} node browser-tests/runMolstarRuntimeProbe.mjs ${path.relative(repoRoot, outputPath)}`,
            url,
            chromeBinary,
            pageReport,
            cleanupSample,
            acceptance: {
                retentionFailures,
                severeBrowserEventCount: severeBrowserEvents.length,
                listenerGrowth,
                documentListenerGrowth,
                heapGrowth,
            },
            cdp: {
                domBefore,
                heapBefore,
                documentListenersBefore,
                domWarmup,
                heapWarmup,
                documentListenersWarmup,
                domWithLiveViewer,
                heapWithLiveViewer,
                documentListenersWithLiveViewer,
                domAfterCleanup,
                heapAfterCleanup,
                documentListenersAfterCleanup,
                browserEvents,
            },
            logs: {
                vite: viteLog.join('').split('\n').filter(Boolean).slice(-100),
                chrome: chromeLog.join('').split('\n').filter(Boolean).slice(-100),
            },
        };

        await mkdir(path.dirname(outputPath), { recursive: true });
        await writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`);
        console.log(JSON.stringify({
            outputPath,
            cycles: pageReport.cycles.length,
            cycleFailures: pageReport.cycles.filter((cycle) => !cycle.ready || !cycle.usable || !cycle.disposedAfterUnmount || !cycle.pluginDisposedAfterUnmount).length,
            finalLiveViewer: pageReport.finalLiveViewer,
            directOwner: pageReport.directOwner,
            zeroViewer: pageReport.zeroViewer,
            replacement: pageReport.replacement,
            pageFailures: pageReport.failures,
            retentionFailures,
            severeBrowserEventCount: severeBrowserEvents.length,
            domBefore,
            documentListenersBefore,
            domWarmup,
            documentListenersWarmup,
            domWithLiveViewer,
            documentListenersWithLiveViewer,
            domAfterCleanup,
            documentListenersAfterCleanup,
            heapBefore,
            heapWithLiveViewer,
            heapAfterCleanup,
            browserEventCount: browserEvents.length,
        }, null, 2));

        if (pageReport.failures.length > 0 || retentionFailures.length > 0 || severeBrowserEvents.length > 0) {
            process.exitCode = 2;
        }
        await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
    } catch (error) {
        const failurePath = outputPath.replace(/\.json$/i, '.failure.json');
        await mkdir(path.dirname(failurePath), { recursive: true });
        await writeFile(failurePath, `${JSON.stringify({
            schemaVersion: 1,
            generatedAt: new Date().toISOString(),
            error: error?.stack ?? String(error),
            lastProbeStatus,
            cdpEvents: cdp?.events?.slice(-200) ?? [],
            logs: {
                vite: viteLog.join('').split('\n').filter(Boolean).slice(-200),
                chrome: chromeLog.join('').split('\n').filter(Boolean).slice(-200),
            },
        }, null, 2)}\n`);
        console.error(`M1 failure evidence: ${failurePath}`);
        throw error;
    } finally {
        if (chrome && !chrome.killed) chrome.kill('SIGTERM');
        if (vite && !vite.killed) vite.kill('SIGTERM');
        await sleep(200);
        await rm(tempRoot, { recursive: true, force: true });
    }
}

main().catch((error) => {
    console.error(error?.stack ?? String(error));
    process.exitCode = 1;
});
