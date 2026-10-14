import type { ReactNode } from 'react';
import { createRoot } from 'react-dom/client';

import { MolViewSpec } from 'molstar/lib/extensions/mvs/behavior';
import { PluginConfig } from 'molstar/lib/mol-plugin/config';
import { createPluginUI } from 'molstar/lib/mol-plugin-ui';
import type { PluginUIContext } from 'molstar/lib/mol-plugin-ui/context';
import { DefaultPluginUISpec } from 'molstar/lib/mol-plugin-ui/spec';
import type { PluginUISpec } from 'molstar/lib/mol-plugin-ui/spec';
import { PluginSpec } from 'molstar/lib/mol-plugin/spec';

import { MolstarEngineOwner } from './MolstarEngineOwner';
import type { MolstarPluginFactory } from './MolstarEngineOwner';
import { BmsPLDDTQualityAssessment } from './BmsPLDDTQualityAssessment';

export const MOLSTAR_DIRECT_ENGINE_VERSION = '4.5.0' as const;

export interface BmsMolstarUiOptions {
    readonly hideControls?: boolean;
    readonly alphafoldView?: boolean;
}

export function createBmsMolstarUiSpec({
    hideControls = false,
    alphafoldView = false,
}: BmsMolstarUiOptions = {}): PluginUISpec {
    const defaults = DefaultPluginUISpec();
    const alphafoldConfig: NonNullable<PluginUISpec['config']> = alphafoldView ? [[
        PluginConfig.Structure.DefaultRepresentationPresetParams,
        { theme: { globalName: 'plddt-confidence' } },
    ]] : [];
    return {
        ...defaults,
        behaviors: [
            ...(defaults.behaviors ?? []),
            PluginSpec.Behavior(MolViewSpec),
            ...(alphafoldView ? [PluginSpec.Behavior(BmsPLDDTQualityAssessment, {
                autoAttach: true,
                showTooltip: true,
            })] : []),
        ],
        layout: {
            ...defaults.layout,
            initial: {
                ...defaults.layout?.initial,
                isExpanded: false,
                showControls: !hideControls,
                // Keep stock panels inside the embedded viewer and responsive.
                // Compact callers collapse chrome initially; they never remove tools.
                controlsDisplay: 'reactive',
                regionState: { left: 'collapsed', top: 'full', right: 'full', bottom: 'full' },
            },
        },
        components: {
            ...defaults.components,
            remoteState: 'none',

        },
        config: [
            ...(defaults.config ?? []),
            [PluginConfig.Viewport.ShowExpand, true],
            [PluginConfig.Viewport.ShowControls, true],
            [PluginConfig.Viewport.ShowSettings, true],
            [PluginConfig.Viewport.ShowSelectionMode, true],
            [PluginConfig.Viewport.ShowAnimation, true],
            ...alphafoldConfig,
        ],
    };
}

export function createDirectMolstarEngineOwner(
    options: BmsMolstarUiOptions = {},
): MolstarEngineOwner<PluginUIContext> {
    const createPlugin: MolstarPluginFactory<PluginUIContext> = async ({
        target,
        publishPlugin,
        render,
    }) => createPluginUI({
        target,
        spec: createBmsMolstarUiSpec(options),
        onBeforeUIRender: (plugin) => {
            // Publish before React rendering so an unmount racing initialization can
            // dispose the plugin even if createPluginUI has not resolved yet.
            publishPlugin(plugin);
        },
        render,
    });

    return new MolstarEngineOwner<PluginUIContext>({
        createPlugin,
        // React 19 rejects a synchronous nested-root unmount while the parent
        // StrictMode root is committing its own cleanup. Defer the complete
        // ordered teardown—not just root.unmount—until that commit is done.
        scheduleTeardown: (teardown) => queueMicrotask(teardown),
        createUiRoot: (container) => {
            const root = createRoot(container);
            return {
                render: (component) => root.render(component as ReactNode),
                unmount: () => root.unmount(),
            };
        },
    });
}
