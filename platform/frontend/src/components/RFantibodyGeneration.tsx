import type { ReactNode } from 'react';
import type { QualitySettings } from './QualitySettingsPanel';
import { NativeSetting } from './NativeBinderGeneration';
import type { NativeBinderParameter } from '../lib/nativeBinderAuthoring';

// Existing RF native intervals, not execution defaults. Catalog metadata can
// replace these presentation descriptors; the parent remains the value owner.
const sampling: NativeBinderParameter[] = [
    { name: 'rfantibody_diffusion_steps', label: 'Diffusion steps', type: 'integer', minimum: 20, maximum: 200, step: 10, ui_control: 'slider', description: 'Denoising steps. More steps take longer; native backbone sampling remains independent of follow-on stages.' },
    { name: 'rfantibody_guide_scale', label: 'Hotspot guidance', type: 'number', minimum: 1, maximum: 50, step: 1, ui_control: 'slider', description: 'Guidance strength toward the selected epitope; higher values strengthen hotspot focus.' },
    { name: 'rfantibody_noise_scale_ca', label: 'C-alpha noise', type: 'number', minimum: 0.5, maximum: 2, step: 0.1, ui_control: 'slider', description: 'Backbone positional noise. Lower values favor more consistent designs. Exact saved values are retained.' },
    { name: 'rfantibody_noise_scale_frame', label: 'Frame noise', type: 'number', minimum: 0.5, maximum: 2, step: 0.1, ui_control: 'slider', description: 'Rotational noise affecting backbone diversity. Exact saved values are retained.' },
];

/** Mount in RF Generation, not inside optional downstream/refinement controls. */
export function RFantibodyGeneration({ settings, onSettingsChange, parameters, generationSlot }: {
    settings: QualitySettings;
    onSettingsChange: (settings: QualitySettings) => void;
    parameters?: NativeBinderParameter[];
    generationSlot?: ReactNode;
}) {
    const patch = (values: Record<string, UntypedApiValue>) => onSettingsChange({ ...settings, ...values });
    return <section aria-label="RFantibody backbone sampling" className="space-y-4">
        <h3 className="text-lg font-semibold">Backbone sampling</h3>
        <div className="grid gap-5 sm:grid-cols-2">{sampling.map(field => <NativeSetting key={field.name}
            parameter={{ ...field, ...parameters?.find(parameter => parameter.name === field.name) }}
            values={settings} onPatch={patch} chains={[]} />)}</div>
        <details><summary className="cursor-pointer">Checkpoint and development options</summary><div className="mt-4 grid gap-5 sm:grid-cols-2">
            <NativeSetting parameter={{ name: 'rfantibody_ckpt_override', label: 'Checkpoint override', type: 'string', description: 'Optional RFantibody checkpoint path; leave empty to retain the packaged checkpoint.' }} values={settings} onPatch={patch} chains={[]} />
            <NativeSetting parameter={{ name: 'rfantibody_debug_repo_overlay', label: 'Debug repository overlay', type: 'boolean', description: 'Development option: use the local RFantibody repository overlay instead of the packaged code path.' }} values={settings} onPatch={patch} chains={[]} />
        </div></details>
        {generationSlot}
    </section>;
}
