interface MolstarPluginLike {
    dispose: () => void;
}

interface MolstarHostLike {
    viewerInstance?: {
        plugin?: MolstarPluginLike;
    };
}

const disposedMolstarPlugins = new WeakSet<object>();

export function disposeMolstarHost(host: unknown): void {
    const plugin = (host as MolstarHostLike | null)?.viewerInstance?.plugin;
    if (!plugin || typeof plugin.dispose !== 'function' || typeof plugin !== 'object') return;
    if (disposedMolstarPlugins.has(plugin)) return;
    disposedMolstarPlugins.add(plugin);
    try {
        plugin.dispose();
    } catch (error) {
        console.warn('Failed to dispose Mol* plugin cleanly:', error);
    }
}
