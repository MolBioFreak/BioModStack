import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
// The node-test runner uses the root solution config (classic JSX), unlike Vite.
Object.assign(globalThis, { React });
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { JSDOM } from 'jsdom';
import { ProjectNarrative } from '../src/components/project-manager/ProjectNarrative';
import { VirtualFolderPanel } from '../src/components/project-manager/VirtualFolderPanel';
import type { ProjectManagerReadModel } from '../src/lib/projectManager';

function narrative(text: string) {
    return new JSDOM(renderToStaticMarkup(<MemoryRouter><ProjectNarrative text={text} /></MemoryRouter>)).window.document;
}

test('named project and result links preserve identity, query and readable multiline copy', () => {
    const doc = narrative('BC2 source: [Site 2 minibinders](/designs/source-id)\nNext: [Three-binder validation](/projects/project-id?focus=group-id&selected=domain_experiment%3Acandidate-id)');
    const links = [...doc.querySelectorAll('a')];
    assert.deepEqual(links.map(a => [a.textContent, a.getAttribute('href')]), [
        ['Site 2 minibinders', '/designs/source-id'],
        ['Three-binder validation', '/projects/project-id?focus=group-id&selected=domain_experiment%3Acandidate-id'],
    ]);
    assert.equal(doc.body.textContent, 'BC2 source: Site 2 minibinders\nNext: Three-binder validation');
});

test('unsupported links and HTML remain literal, not executable or external navigation', () => {
    const text = '<img src=x onerror=alert(1)> [remote](https://example.org) [protocol](//example.org) [script](javascript:alert(1)) [slash](/projects/\\evil) [control](/jobs/%0aevil) [encoded slash](/designs/%5cevil)';
    const doc = narrative(text);
    assert.equal(doc.querySelectorAll('a,img,script').length, 0);
    assert.equal(doc.body.textContent, text);
});

test('plain existing notes and incomplete link syntax remain unchanged', () => {
    const text = 'Original note\n42 candidates; [not a complete link](/designs/';
    const doc = narrative(text);
    assert.equal(doc.body.textContent, text);
    assert.equal(doc.querySelectorAll('a').length, 0);
});

test('notes folder links are independent of record-selection buttons', () => {
    const summary = { pagination: { notes: { items: [{ id: 'note-1', record_kind: 'note', body: '[BC2 Site 1](/designs/source-1)' }], next_cursor: null } } } as unknown as ProjectManagerReadModel;
    const html = renderToStaticMarkup(<MemoryRouter><VirtualFolderPanel folder="notes" summary={summary} onLoadMore={() => {}} onSelectRecord={() => {}} /></MemoryRouter>);
    const doc = new JSDOM(html).window.document;
    assert.equal(doc.querySelector('a')?.textContent, 'BC2 Site 1');
    assert.equal(doc.querySelector('a')?.closest('button'), null);
    assert.ok(doc.querySelector('button')?.textContent?.includes('Note'));
});
