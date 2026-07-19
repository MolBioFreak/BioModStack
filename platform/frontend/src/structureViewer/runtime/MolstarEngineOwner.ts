import { ViewerResourceOwner } from './resourceOwnership.js';

export interface MolstarOwnedPlugin {
    dispose(options?: { doNotForceWebGLContextLoss?: boolean }): void;
    readonly managers?: {
        readonly task?: {
            requestAbortAll(reason: string): void;
        };
    };
}

export interface MolstarOwnedUiRoot {
    render(component: unknown): void;
    unmount(): void;
}

export interface MolstarPluginCreationContext<TPlugin extends MolstarOwnedPlugin = MolstarOwnedPlugin> {
    readonly target: HTMLElement;
    readonly render: (component: unknown, container: Element) => void;
    readonly publishPlugin: (plugin: TPlugin) => void;
}

export type MolstarPluginFactory<TPlugin extends MolstarOwnedPlugin = MolstarOwnedPlugin> = (
    context: MolstarPluginCreationContext<TPlugin>,
) => Promise<TPlugin>;

export type MolstarEngineOwnerResult<TPlugin extends MolstarOwnedPlugin = MolstarOwnedPlugin> =
    | {
        readonly status: 'ok';
        readonly generation: number;
        readonly plugin: TPlugin;
    }
    | {
        readonly status: 'cancelled';
        readonly generation: number;
    }
    | {
        readonly status: 'error';
        readonly generation: number;
        readonly error: unknown;
    };

interface MolstarOwnershipAttempt<TPlugin extends MolstarOwnedPlugin> {
    readonly generation: number;
    plugin?: TPlugin;
    root?: MolstarOwnedUiRoot;
    pluginDisposed: boolean;
    rootUnmounted: boolean;
    terminal: boolean;
    teardownScheduled: boolean;
    readonly resources: ViewerResourceOwner;
}

export class MolstarOwnerCancelledError extends Error {
    constructor() {
        super('Mol* ownership generation was cancelled');
        this.name = 'MolstarOwnerCancelledError';
    }
}

export interface MolstarEngineOwnerDependencies<TPlugin extends MolstarOwnedPlugin = MolstarOwnedPlugin> {
    readonly createPlugin: MolstarPluginFactory<TPlugin>;
    readonly createUiRoot: (container: Element) => MolstarOwnedUiRoot;
    readonly scheduleTeardown?: (teardown: () => void) => void;
    readonly onTeardownError?: (error: unknown) => void;
}

export class MolstarEngineOwner<TPlugin extends MolstarOwnedPlugin = MolstarOwnedPlugin> {
    private generationCounter = 0;
    private currentAttempt: MolstarOwnershipAttempt<TPlugin> | undefined;
    private readonly dependencies: MolstarEngineOwnerDependencies<TPlugin>;

    constructor(dependencies: MolstarEngineOwnerDependencies<TPlugin>) {
        this.dependencies = dependencies;
    }

    get generation(): number {
        return this.generationCounter;
    }

    private get currentPlugin(): TPlugin | undefined {
        const attempt = this.currentAttempt;
        return attempt && !attempt.terminal ? attempt.plugin : undefined;
    }

    diagnostics(): {
        readonly generation: number;
        readonly active: boolean;
        readonly resources: readonly { id: string; generation: number; disposed: boolean }[];
    } {
        return {
            generation: this.generationCounter,
            active: Boolean(this.currentPlugin),
            resources: this.currentAttempt?.resources.snapshot() ?? [],
        };
    }

    async initialize(target: HTMLElement): Promise<MolstarEngineOwnerResult<TPlugin>> {
        this.terminateAttempt(this.currentAttempt);

        const attempt: MolstarOwnershipAttempt<TPlugin> = {
            generation: ++this.generationCounter,
            pluginDisposed: false,
            rootUnmounted: false,
            terminal: false,
            teardownScheduled: false,
            resources: new ViewerResourceOwner(),
        };
        this.currentAttempt = attempt;

        const isCurrent = () => this.currentAttempt === attempt && !attempt.terminal;
        const publishPlugin = (plugin: TPlugin): void => {
            if (attempt.plugin && attempt.plugin !== plugin) {
                plugin.dispose();
                throw new Error('Mol* factory published more than one plugin for one generation');
            }
            if (!attempt.plugin) {
                attempt.plugin = plugin;
                attempt.resources.own(`plugin:${attempt.generation}`, () => this.disposePlugin(attempt), attempt.generation);
            }
            if (!isCurrent()) {
                this.abortPluginTasks(attempt);
                this.scheduleAttemptTeardown(attempt);
            }
        };
        const render = (component: unknown, container: Element): void => {
            if (!isCurrent()) throw new MolstarOwnerCancelledError();
            if (attempt.root) throw new Error('Mol* factory rendered more than one UI root for one generation');

            const root = this.dependencies.createUiRoot(container);
            attempt.root = root;
            attempt.resources.own(`root:${attempt.generation}`, () => this.unmountRoot(attempt), attempt.generation);
            root.render(component);

            if (!isCurrent()) this.scheduleAttemptTeardown(attempt);
        };

        try {
            const plugin = await this.dependencies.createPlugin({ target, publishPlugin, render });
            publishPlugin(plugin);

            if (!isCurrent()) {
                this.terminateAttempt(attempt);
                return { status: 'cancelled', generation: attempt.generation };
            }

            return { status: 'ok', generation: attempt.generation, plugin };
        } catch (error) {
            const cancelled = error instanceof MolstarOwnerCancelledError || !isCurrent();
            this.terminateAttempt(attempt);
            if (this.currentAttempt === attempt) this.currentAttempt = undefined;

            if (cancelled) {
                return { status: 'cancelled', generation: attempt.generation };
            }
            return { status: 'error', generation: attempt.generation, error };
        }
    }

    dispose(): void {
        const attempt = this.currentAttempt;
        this.currentAttempt = undefined;
        this.terminateAttempt(attempt);
    }

    private terminateAttempt(attempt: MolstarOwnershipAttempt<TPlugin> | undefined): void {
        if (!attempt) return;
        attempt.terminal = true;
        this.abortPluginTasks(attempt);
        this.scheduleAttemptTeardown(attempt);
    }

    private abortPluginTasks(attempt: MolstarOwnershipAttempt<TPlugin>): void {
        if (!attempt.plugin || attempt.pluginDisposed) return;
        try {
            attempt.plugin.managers?.task?.requestAbortAll('BMS Mol* viewer disposed');
        } catch (error) {
            this.dependencies.onTeardownError?.(error);
        }
    }

    private scheduleAttemptTeardown(attempt: MolstarOwnershipAttempt<TPlugin>): void {
        if (attempt.teardownScheduled) return;
        attempt.teardownScheduled = true;
        const schedule = this.dependencies.scheduleTeardown ?? ((teardown: () => void) => teardown());
        schedule(() => {
            attempt.teardownScheduled = false;
            this.performAttemptTeardown(attempt);
            if (attempt.terminal && (
                (attempt.root && !attempt.rootUnmounted)
                || (attempt.plugin && !attempt.pluginDisposed)
            )) {
                this.scheduleAttemptTeardown(attempt);
            }
        });
    }

    private performAttemptTeardown(attempt: MolstarOwnershipAttempt<TPlugin>): void {
        try {
            attempt.resources.disposeSync(`root:${attempt.generation}`);
        } catch (error) {
            this.dependencies.onTeardownError?.(error);
        } finally {
            try {
                attempt.resources.disposeSync(`plugin:${attempt.generation}`);
            } catch (error) {
                this.dependencies.onTeardownError?.(error);
            }
        }
    }

    private unmountRoot(attempt: MolstarOwnershipAttempt<TPlugin>): void {
        if (!attempt.root || attempt.rootUnmounted) return;
        attempt.rootUnmounted = true;
        attempt.root.unmount();
    }

    private disposePlugin(attempt: MolstarOwnershipAttempt<TPlugin>): void {
        if (!attempt.plugin || attempt.pluginDisposed) return;
        attempt.pluginDisposed = true;
        attempt.plugin.dispose();
    }
}
