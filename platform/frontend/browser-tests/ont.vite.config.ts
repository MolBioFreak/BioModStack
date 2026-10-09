import { defineConfig, mergeConfig } from 'vite';
import { resolve } from 'node:path';
import applicationConfig from '../vite.config';

// Build the existing NGSToolkit harness with the application's production
// plugins/chunking. Native API proxy remains loopback-only in the runner.
export default defineConfig(async environment => {
    const base = typeof applicationConfig === 'function' ? await applicationConfig(environment) : await applicationConfig;
    return mergeConfig(base, {
        base: '/',
        build: { rollupOptions: { input: resolve(__dirname, 'ont-suite.html') } },
        preview: { host: '127.0.0.1', port: 18762, strictPort: true,
            proxy: { '/api': { target: 'http://127.0.0.1:18761', changeOrigin: true } } },
    });
});
