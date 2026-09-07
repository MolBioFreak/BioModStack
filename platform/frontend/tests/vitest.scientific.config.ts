// Synthetic software acceptance only; never conditionally omit a suite.
import { defineConfig } from 'vitest/config';
import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const gates = JSON.parse(readFileSync(new URL('../../../tests/scientific_acceptance_gates.json', import.meta.url), 'utf8'));
for (const key of Object.keys(gates.wire_files)) {
    const path = process.env[key];
    if (!path || !statSync(path).isFile()) throw new Error(`Required fresh synthetic API wire: ${key}`);
    JSON.parse(readFileSync(path, 'utf8'));
}
const directory = process.env.BMS_BOLTZGEN_ANALYTICS_WIRES;
if (!directory) throw new Error('Required fresh synthetic API wires: BMS_BOLTZGEN_ANALYTICS_WIRES');
for (const name of gates.boltzgen_cases) JSON.parse(readFileSync(resolve(directory, `${name}.json`), 'utf8'));
for (const file of gates.frontend) {
    if (!statSync(resolve(root, file)).isFile()) throw new Error(`Missing scientific suite: ${file}`);
}
export default defineConfig({
    root,
    cacheDir: process.env.BMS_SCIENTIFIC_VITE_CACHE,
    test: {
        environment: 'jsdom',
        setupFiles: ['./tests/vitest/setup.ts'],
        include: gates.frontend,
        passWithNoTests: false,
    },
});
