import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { AutoAnnotatePanel } from '../src/components/MolBioToolkit/AutoAnnotatePanel.js';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

const MODULE = '../src/components/MolBioToolkit/utils/annotationSources.js';

async function sources() {
    try {
        return await import(MODULE);
    } catch (error) {
        assert.fail(`annotation source retrieval client is missing: ${String(error)}`);
    }
}

const ARTIFACT = {
    content: 'LOCUS       TEST 4 bp DNA linear SYN 01-JAN-2026\nORIGIN\n        1 acgt\n//\n',
    file_name: 'ncbi-J01749.1.gb',
    media_type: 'text/plain',
    source: {
        provider: 'ncbi',
        source_id: 'J01749.1',
        source_url: 'https://www.ncbi.nlm.nih.gov/nuccore/J01749.1',
        artifact_sha256: 'abc123',
    },
};

test('annotation source identifiers are normalized before requests', async () => {
    const module = await sources();
    assert.equal(module.normalizeNcbiAccession(' j01749.1 '), 'J01749.1');
    assert.equal(module.normalizeAddgenePlasmidId(' 10878 '), 10878);
    for (const invalid of ['', '../x', 'J01749.1?x=1', 'https://example.test']) {
        assert.throws(() => module.normalizeNcbiAccession(invalid), /invalid format/);
    }
    for (const invalid of ['', '0', '-1', '12.5', '12x']) {
        assert.throws(() => module.normalizeAddgenePlasmidId(invalid), /positive integer/);
    }
});

test('retrieved NCBI artifact becomes a GenBank file with provenance', async () => {
    const module = await sources();
    const requests: string[] = [];
    const fetchImpl = async (input: string | URL | Request) => {
        requests.push(String(input));
        return new Response(JSON.stringify(ARTIFACT), {
            status: 200,
            headers: { 'content-type': 'application/json' },
        });
    };

    const result = await module.retrieveNcbiAnnotationSource('j01749.1', fetchImpl);
    assert.deepEqual(requests, ['/api/molbio/annotation-sources/ncbi/J01749.1']);
    assert.equal(result.file.name, 'ncbi-J01749.1.gb');
    assert.equal(result.file.type, 'text/plain');
    assert.equal(await result.file.text(), ARTIFACT.content);
    assert.deepEqual(result.source, ARTIFACT.source);
});

test('Addgene retrieval uses only a normalized numeric path segment', async () => {
    const module = await sources();
    let requested = '';
    const fetchImpl = async (input: string | URL | Request) => {
        requested = String(input);
        return new Response(JSON.stringify({
            ...ARTIFACT,
            file_name: 'addgene-10878.gb',
            source: { ...ARTIFACT.source, provider: 'addgene', source_id: '10878' },
        }), { status: 200, headers: { 'content-type': 'application/json' } });
    };
    const result = await module.retrieveAddgeneAnnotationSource('10878', fetchImpl);
    assert.equal(requested, '/api/molbio/annotation-sources/addgene/10878');
    assert.equal(result.source.provider, 'addgene');
});

test('status discovery exposes availability booleans only', async () => {
    const module = await sources();
    const fetchImpl = async () => new Response(JSON.stringify({
        ncbi: { available: true, endpoint: '/private/provider' },
        addgene: { available: false, token: 'synthetic-not-a-credential' },
        internal: { diagnostics: 'not part of the client status contract' },
    }), { status: 200, headers: { 'content-type': 'application/json' } });
    assert.deepEqual(await module.fetchAnnotationSourceStatus(fetchImpl), {
        ncbi: { available: true },
        addgene: { available: false },
    });
});

test('retrieved artifact checksum comparison fails closed', async () => {
    const module = await sources();
    const source = { ...ARTIFACT.source, artifact_sha256: 'aabbcc' };
    assert.doesNotThrow(() => module.assertAnnotationArtifactChecksum(source, 'AABBCC'));
    assert.throws(
        () => module.assertAnnotationArtifactChecksum(source, 'ddeeff'),
        /checksum does not match/,
    );
});

test('annotation menu renders NCBI and Addgene retrieval controls', () => {
    const html = renderToStaticMarkup(React.createElement(AutoAnnotatePanel, {
        isOpen: true,
        onClose: () => undefined,
        onAnnotate: () => undefined,
        onClearAnnotations: () => undefined,
        onImportAnnotations: async () => 'imported',
        onRetrieveNcbi: async () => 'imported',
        onRetrieveAddgene: async () => 'imported',
        annotationSourceStatus: { ncbi: { available: true }, addgene: { available: true } },
        isAnnotating: false,
        hasSequence: true,
        featureCount: 0,
        sequenceLength: 100,
        isCircular: true,
    }));
    assert.match(html, /NCBI nucleotide accession/);
    assert.match(html, /Retrieve NCBI annotations/);
    assert.match(html, /Addgene plasmid ID/);
    assert.match(html, /Retrieve Addgene annotations/);
    assert.doesNotMatch(html, /Addgene API token is not configured/);
});

test('annotation menu leaves Addgene visible but disabled when token is unavailable', async () => {
    const { JSDOM } = createRequire(import.meta.url)('jsdom');
    const dom = new JSDOM('<div id="root"></div>', { pretendToBeVisual: true });
    const globals = {
        window: dom.window,
        document: dom.window.document,
        navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement,
    };
    const previous = new Map(Object.keys(globals).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    for (const [key, value] of Object.entries(globals)) {
        Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    }
    const { createRoot } = await import('react-dom/client');
    const { flushSync } = await import('react-dom');
    const host = dom.window.document.getElementById('root') as HTMLDivElement;
    const root = createRoot(host);
    const render = (available: boolean) => flushSync(() => root.render(React.createElement(AutoAnnotatePanel, {
        isOpen: true,
        onClose: () => undefined,
        onAnnotate: () => undefined,
        onClearAnnotations: () => undefined,
        onImportAnnotations: async () => 'imported',
        onRetrieveNcbi: async () => 'imported',
        onRetrieveAddgene: async () => 'imported',
        annotationSourceStatus: { ncbi: { available: true }, addgene: { available } },
        isAnnotating: false,
        hasSequence: true,
        featureCount: 0,
        sequenceLength: 100,
        isCircular: true,
    })));
    try {
        render(true);
        const input = host.querySelector<HTMLInputElement>('#annotation-source-addgene')!;
        const button = Array.from(host.querySelectorAll('button')).find((node) => node.textContent === 'Retrieve Addgene annotations')!;
        assert.equal(input.disabled, false);
        // Enter a valid ID through the real input handler before changing availability.
        const setValue = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value')!.set!;
        flushSync(() => {
            setValue.call(input, '10878');
            input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        });
        assert.equal(button.disabled, false, 'available provider and valid ID enable retrieval');
        render(false);
        assert.equal(input.value, '10878');
        assert.equal(button.disabled, true, 'availability alone disables a valid request');
        assert.match(host.textContent!, /Addgene API token is not configured/);
        render(true);
        assert.equal(button.disabled, false);
        assert.doesNotMatch(host.textContent!, /Addgene API token is not configured/);
    } finally {
        flushSync(() => root.unmount());
        // Let React drain its queued scheduler callbacks before removing the DOM.
        await new Promise<void>((resolve) => setImmediate(resolve));
        dom.window.close();
        for (const [key, descriptor] of previous) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else Reflect.deleteProperty(globalThis, key);
        }
    }
});

test('controlled backend detail is surfaced and malformed success payloads fail closed', async () => {
    const module = await sources();
    const failedFetch = async () => new Response(JSON.stringify({ detail: 'Addgene API token is not configured on the server' }), {
        status: 503,
        headers: { 'content-type': 'application/json' },
    });
    await assert.rejects(
        module.retrieveAddgeneAnnotationSource('10878', failedFetch),
        /Addgene API token is not configured/,
    );

    const malformedFetch = async () => new Response(JSON.stringify({ content: 'LOCUS' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
    });
    await assert.rejects(
        module.retrieveNcbiAnnotationSource('J01749.1', malformedFetch),
        /invalid annotation artifact/,
    );
});
