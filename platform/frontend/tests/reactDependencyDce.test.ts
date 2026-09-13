import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const frontend = fileURLToPath(new URL('..', import.meta.url));

// Run the installed Vite dependency optimizer and load its actual ESM output.
// A fresh child/cache per variant prevents React's singleton or NODE_ENV leaking.
const optimizerProbe = `
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';
import { optimizeDeps, resolveConfig } from 'vite';
const cache=mkdtempSync(path.join(tmpdir(),'bms-react-dce-'));
let dom;
try {
    const config=await resolveConfig({root:process.cwd(),configFile:path.resolve('vite.config.ts'),mode:'development',cacheDir:cache,logLevel:'silent'},'serve');
    const configured=config.optimizeDeps.esbuildOptions.minifySyntax;
    assert.equal(config.mode,'development');
    assert.equal(config.base,'/');
    assert.equal(configured,process.env.NODE_ENV==='production');
    if(process.env.BMS_DCE_VARIANT==='debug-config') {
        console.log(JSON.stringify({configured,mode:config.mode,base:config.base}));
    } else {
        // Only bound the dependency roster; retain the real resolved optimizer options.
        config.optimizeDeps.noDiscovery=true;
        config.optimizeDeps.include=['react','react-dom','react-dom/client'];
        if(process.env.BMS_DCE_VARIANT==='unrepaired-control')config.optimizeDeps.esbuildOptions.minifySyntax=false;
        config.environments.client.optimizeDeps=config.optimizeDeps;
        const metadata=await optimizeDeps(config,true);
        dom=new JSDOM('<div id="root"></div>',{pretendToBeVisual:true});
        globalThis.window=dom.window;globalThis.document=dom.window.document;
        const checks=[],renderers=[];
        // Same source-string criterion as React DevTools' checkDCE in
        // packages/react-devtools-shared/src/hook.js. Do not suppress the hook.
        globalThis.__REACT_DEVTOOLS_GLOBAL_HOOK__={supportsFiber:true,
            checkDCE(fn){checks.push(Function.prototype.toString.call(fn).includes('^_^'));},
            inject(renderer){renderers.push({version:renderer.version,bundleType:renderer.bundleType});return renderers.length;},
            onCommitFiberRoot(){},onCommitFiberUnmount(){},onPostCommitFiberRoot(){}};
        const load=async id=>(await import(pathToFileURL(metadata.optimized[id].file))).default;
        const React=await load('react');
        const {createRoot}=await load('react-dom/client');
        const {flushSync}=await load('react-dom');
        const root=createRoot(document.getElementById('root'));
        function Chart({samples}){return React.createElement('span',null,samples.at(-1).value);}
        for(let i=0;i<20;i++) {
            flushSync(()=>root.render(React.createElement(Chart,{samples:Array.from({length:180},(_,j)=>({value:i+j}))})));
            await new Promise(resolve=>setTimeout(resolve,0));
        }
        const result={configured,checks,renderers,text:document.body.textContent,measures:performance.getEntriesByType('measure').length};
        root.unmount();console.log(JSON.stringify(result));
    }
} finally {dom?.window.close();rmSync(cache,{recursive:true,force:true});}
`;

interface ProbeResult {
    configured: boolean;
    checks: boolean[];
    renderers: Array<{ version: string; bundleType: number }>;
    text: string;
    measures: number;
}
function probe(variant: string, nodeEnv = 'production') {
    return JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', optimizerProbe], {
        cwd: frontend, env: { ...process.env, NODE_ENV: nodeEnv, BMS_DCE_VARIANT: variant },
        encoding: 'utf8', timeout: 60_000,
    }));
}

test('actual optimized React entrypoints pass DevTools DCE without enabling profiling; control reproduces both failures', () => {
    const control: ProbeResult = probe('unrepaired-control');
    const repaired: ProbeResult = probe('configured');
    assert.deepEqual(control.checks, [true, true], 'unrepaired bundling retains both React DOM sentinels');
    assert.deepEqual(repaired.checks, [false, false], 'both live checks execute and pass, rather than being disabled');
    assert.ok(repaired.renderers.length > 0, 'DevTools still receives the renderer');
    assert.ok(repaired.renderers.every(renderer => renderer.bundleType === 0), 'React remains production, not debug/profiling');
    assert.equal(repaired.measures, 0);
    assert.equal(repaired.text, control.text, 'identical repeated chart renders');
});

test('syntax optimization is scoped to production React, not Vite environment identity', () => {
    assert.deepEqual(probe('debug-config', 'development'), {configured:false,mode:'development',base:'/'});
});
