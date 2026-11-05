import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveApiResourceUrl } from '../src/runtime/apiResourceUrl';

test('raw API resources follow the configured server and retain encoded paths, query and fragment', () => {
  const base = 'https://node.example.ts.net';
  const page = 'https://localhost/__bms_ui__/active/index.html';
  for (const value of ['/api/files/a%20b.cif?download=1#atom', 'api/files/a%20b.cif?download=1#atom', 'https://localhost/api/files/a%20b.cif?download=1#atom']) {
    assert.equal(resolveApiResourceUrl(value, base + '/', page), base + '/api/files/a%20b.cif?download=1#atom');
  }
});
test('React routes, non-API resources and external URLs remain unchanged', () => {
  for (const value of ['/projects/a', 'assets/viewer.js', 'blob:https://localhost/123', 'https://external.example/api/file.cif', '/apiculture']) {
    assert.equal(resolveApiResourceUrl(value, 'https://node.example.ts.net', 'https://localhost/'), value);
  }
});
test('same-origin web links stay relative without a configured server, including SSR', () => {
  assert.equal(resolveApiResourceUrl('/api/files/a.cif', '', 'http://localhost:18082/'), '/api/files/a.cif');
  assert.equal(resolveApiResourceUrl('/api/files/a.cif'), '/api/files/a.cif');
});
