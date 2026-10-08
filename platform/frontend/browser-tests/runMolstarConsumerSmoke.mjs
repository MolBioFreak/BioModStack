#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(frontendRoot, '../..');
const outputPath = path.resolve(process.argv[2] ?? path.join(
    repoRoot,
    'docs/reviews/structure_visualization/evidence/m1_molstar_consumer_smoke_chrome150.json',
));
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
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pending.delete(id);
                reject(new Error(`${method} timed out after ${timeoutMs}ms`));
            }, timeoutMs);
            this.pending.set(id, { resolve, reject, timer, method });
            this.process.stdio[3].write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`);
        });
    }
}

function valueFromEvaluation(result) {
    if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text ?? 'Runtime evaluation failed');
    }
    return result.result?.value;
}

async function documentListeners(cdp, sessionId) {
    const evaluation = await cdp.send('Runtime.evaluate', { expression: 'document', returnByValue: false }, sessionId);
    const objectId = evaluation.result?.objectId;
    if (!objectId) return { total: 0, byType: {} };
    try {
        const { listeners = [] } = await cdp.send('DOMDebugger.getEventListeners', { objectId, depth: 1, pierce: true }, sessionId);
        const byType = {};
        for (const listener of listeners) byType[listener.type] = (byType[listener.type] ?? 0) + 1;
        return { total: listeners.length, byType };
    } finally {
        await cdp.send('Runtime.releaseObject', { objectId }, sessionId).catch(() => {});
    }
}

async function main() {
    const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'bms-molstar-consumers-'));
    const viteLog = [];
    const chromeLog = [];
    let vite;
    let chrome;
    let cdp;
    let lastStatus = 'not started';
    try {
        const port = await freePort();
        const url = `http://127.0.0.1:${port}/browser-tests/molstar-consumer-smoke.html`;
        vite = spawn('pnpm', ['exec', 'vite', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
            cwd: frontendRoot,
            env: { ...process.env, BMS_VITE_CACHE_DIR: path.join(tempRoot, 'vite-cache') },
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        vite.stdout.on('data', (chunk) => viteLog.push(chunk.toString()));
        vite.stderr.on('data', (chunk) => viteLog.push(chunk.toString()));
        await waitForHttp(url);

        chrome = spawn(chromeBinary, [
            '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
            '--disable-background-networking', '--disable-default-apps', '--disable-extensions',
            '--disable-sync', '--enable-precise-memory-info', '--enable-unsafe-swiftshader',
            '--js-flags=--expose-gc', '--remote-debugging-pipe',
            `--user-data-dir=${path.join(tempRoot, 'chrome-profile')}`, 'about:blank',
        ], { cwd: frontendRoot, stdio: ['ignore', 'pipe', 'pipe', 'pipe', 'pipe'] });
        chrome.stdout.on('data', (chunk) => chromeLog.push(chunk.toString()));
        chrome.stderr.on('data', (chunk) => chromeLog.push(chunk.toString()));
        cdp = new CdpPipe(chrome);

        const { targetId } = await cdp.send('Target.createTarget', { url });
        const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
        await Promise.all([
            cdp.send('Runtime.enable', {}, sessionId),
            cdp.send('Log.enable', {}, sessionId),
            cdp.send('Page.enable', {}, sessionId),
        ]);

        const readyDeadline = Date.now() + 60_000;
        let ready = false;
        while (Date.now() < readyDeadline) {
            const evaluation = await cdp.send('Runtime.evaluate', {
                expression: 'typeof window.runMolstarConsumerSmoke === "function"',
                returnByValue: true,
            }, sessionId);
            if (valueFromEvaluation(evaluation) === true) { ready = true; break; }
            await sleep(100);
        }
        if (!ready) throw new Error('consumer smoke page did not become ready');

        await cdp.send('HeapProfiler.collectGarbage', {}, sessionId);
        const domBefore = await cdp.send('Memory.getDOMCounters', {}, sessionId);
        const listenersBefore = await documentListeners(cdp, sessionId);
        await cdp.send('Runtime.evaluate', {
            expression: `window.__bmsConsumerSmoke = { done: false, result: null, error: null }; window.runMolstarConsumerSmoke().then(result => { window.__bmsConsumerSmoke = { done: true, result, error: null }; }).catch(error => { window.__bmsConsumerSmoke = { done: true, result: null, error: String(error?.stack || error) }; }); true`,
            returnByValue: true,
        }, sessionId);

        const deadline = Date.now() + 900_000;
        let state;
        while (Date.now() < deadline) {
            const evaluation = await cdp.send('Runtime.evaluate', {
                expression: `({ smoke: window.__bmsConsumerSmoke, status: document.querySelector('#status')?.textContent || '' })`,
                returnByValue: true,
            }, sessionId, 30_000);
            state = valueFromEvaluation(evaluation);
            if (state?.status && state.status !== lastStatus) {
                lastStatus = state.status;
                console.error(`[consumer-smoke] ${lastStatus}`);
            }
            if (state?.smoke?.done) break;
            await sleep(1_000);
        }
        if (!state?.smoke?.done) throw new Error(`consumer smoke timed out; last status=${lastStatus}`);
        if (state.smoke.error) throw new Error(state.smoke.error);
        const pageReport = state.smoke.result;

        const cleanup = await cdp.send('Runtime.evaluate', {
            expression: 'window.cleanupMolstarConsumerSmoke()',
            awaitPromise: true,
            returnByValue: true,
        }, sessionId, 120_000);
        valueFromEvaluation(cleanup);
        await cdp.send('HeapProfiler.collectGarbage', {}, sessionId);
        const domAfter = await cdp.send('Memory.getDOMCounters', {}, sessionId);
        const listenersAfter = await documentListeners(cdp, sessionId);
        const browserEvents = cdp.events.filter((event) => event.method === 'Runtime.exceptionThrown' || event.method === 'Log.entryAdded');
        const severeEvents = browserEvents.filter((event) => event.method === 'Runtime.exceptionThrown'
            || (event.method === 'Log.entryAdded' && event.params?.entry?.level === 'error'
                && !event.params?.entry?.url?.endsWith('/favicon.ico')));
        const duplicateSymbolWarnings = pageReport.consoleWarnings.filter((warning) => warning.includes('already added. Call removeSymbol'));
        const acceptanceFailures = [...pageReport.failures];
        if (duplicateSymbolWarnings.length) acceptanceFailures.push(`${duplicateSymbolWarnings.length} duplicate-symbol warnings`);
        if (severeEvents.length) acceptanceFailures.push(`${severeEvents.length} severe browser events`);

        const evidence = {
            schemaVersion: 1,
            generatedAt: new Date().toISOString(),
            command: `node browser-tests/runMolstarConsumerSmoke.mjs ${path.relative(repoRoot, outputPath)}`,
            url,
            chromeBinary,
            pageReport,
            acceptance: {
                failures: acceptanceFailures,
                severeBrowserEventCount: severeEvents.length,
                duplicateSymbolWarningCount: duplicateSymbolWarnings.length,
                documentListenerGrowth: listenersAfter.total - listenersBefore.total,
                domNodeGrowth: domAfter.nodes - domBefore.nodes,
            },
            cdp: { domBefore, listenersBefore, domAfter, listenersAfter, browserEvents },
            logs: {
                vite: viteLog.join('').split('\n').filter(Boolean).slice(-100),
                chrome: chromeLog.join('').split('\n').filter(Boolean).slice(-100),
            },
        };
        await mkdir(path.dirname(outputPath), { recursive: true });
        await writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`);
        console.log(JSON.stringify({ outputPath, inventory: pageReport.currentInventory, generic: pageReport.generic, epitope: pageReport.epitope, acceptance: evidence.acceptance }, null, 2));
        if (acceptanceFailures.length) process.exitCode = 2;
        await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
    } catch (error) {
        const failurePath = outputPath.replace(/\.json$/i, '.failure.json');
        await mkdir(path.dirname(failurePath), { recursive: true });
        await writeFile(failurePath, `${JSON.stringify({
            schemaVersion: 1,
            generatedAt: new Date().toISOString(),
            error: error?.stack ?? String(error),
            lastStatus,
            cdpEvents: cdp?.events?.slice(-200) ?? [],
            logs: { vite: viteLog.join('').split('\n').filter(Boolean).slice(-200), chrome: chromeLog.join('').split('\n').filter(Boolean).slice(-200) },
        }, null, 2)}\n`);
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
