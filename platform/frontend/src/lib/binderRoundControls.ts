import type { BinderRoundCatalog } from './binderRound';
import { nativeParameterDisplay, type NativeBinderParameter } from './nativeBinderAuthoring';

/** Public receiving modes; this does not alter the round envelope or submission. */
export const binderRoundModes: Record<string, string> = {
    proteinmpnn: 'design', fampnn: 'binder_design', caliby_binder: 'design',
    protenix: 'complex', boltz2: 'complex', esmfold2: 'predict',
};
export function roundCatalogFields(catalog: BinderRoundCatalog): NativeBinderParameter[] {
    const mode = catalog.modes?.find(mode => mode.id === binderRoundModes[catalog.id]);
    return mode?.params?.length ? catalog.params.map(parameter => mode.params!.includes(parameter.name) ? parameter : { ...parameter, display_applicable: false }) : catalog.params;
}
const sampling = new Set(['seqs_per_design', 'caliby_num_seqs_per_pdb', 'mpnn_temperature', 'fampnn_temperature', 'caliby_temperature',
    'fampnn_seed', 'seed', 'protenix_seeds', 'protenix_n_sample', 'boltz_diffusion_samples', 'num_diffusion_samples', 'num_parallel_jobs']);
const msaMain = new Set(['esmf_use_msa', 'boltz_use_msa', 'protenix_use_msa', 'protenix_msa_backend', 'msa_provider']);
const titles: Record<string, string> = {
    seqs_per_design: 'Sequences per backbone', caliby_num_seqs_per_pdb: 'Sequences per backbone',
    mpnn_temperature: 'Sequence temperature', fampnn_temperature: 'Sequence temperature', caliby_temperature: 'Sequence temperature',
    fampnn_seed: 'Sequence seed', protenix_seeds: 'Prediction seeds', seed: 'Prediction seed',
    protenix_n_sample: 'Samples per seed', boltz_diffusion_samples: 'Diffusion samples', num_diffusion_samples: 'Diffusion samples',
    num_parallel_jobs: 'Independent prediction jobs', protenix_use_msa: 'Use MSA', boltz_use_msa: 'Use MSA', esmf_use_msa: 'Use MSA',
    protenix_msa_backend: 'MSA backend', msa_provider: 'MSA provider',
};
export function roundControlLabel(parameter: NativeBinderParameter): NativeBinderParameter {
    return { ...parameter, label: parameter.label || titles[parameter.name] || parameter.name.replace(/^(protenix_|boltz_|esmf_|fampnn_|mpnn_|caliby_)/, '').replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase()) };
}
export function roundMsaDisplay(model: string, values: Record<string, unknown>) {
    const flag = ({ protenix: 'protenix_use_msa', boltz2: 'boltz_use_msa', esmfold2: 'esmf_use_msa' } as Record<string, string>)[model];
    const enabled = flag ? values[flag] === true : false;
    const backend = values.protenix_msa_backend;
    const search = enabled && backend !== 'none' && backend !== 'esm';
    const rawProvider = values.msa_provider ?? backend;
    const provider = rawProvider === 'auto' ? 'colabfold_api' : rawProvider;
    const summary = !enabled ? 'MSA off; provider settings retained.' : !search ? `MSA backend: ${String(backend)}; external provider settings inactive.`
        : `MSA on · provider: ${provider === 'colabfold_api' ? 'ColabFold API' : provider === 'neurosnap_api' ? 'Neurosnap API' : provider === undefined ? 'not specified' : String(provider)}${backend !== undefined ? ` · backend: ${String(backend)}` : ''}.`;
    return { enabled, search, provider, summary };
}
/** Small task grouping map, not native normalization or validation. */
export function roundControlGroups(model: string, fields: NativeBinderParameter[], values: Record<string, unknown>) {
    const msa = roundMsaDisplay(model, values);
    const primary: NativeBinderParameter[] = [];
    const groups = new Map<string, NativeBinderParameter[]>();
    for (const parameter of fields) {
        const key = parameter.name;
        const applicable = nativeParameterDisplay(parameter, values).applicable;
        const provider = key.startsWith('msa_neurosnap_') ? 'neurosnap_api' : key.startsWith('colabfold_') ? 'colabfold_api' : undefined;
        const inactiveProvider = provider && (!msa.search || msa.provider !== provider);
        if (applicable && !inactiveProvider && (sampling.has(key) || msaMain.has(key))) { primary.push(parameter); continue; }
        let group = parameter.ui_group || parameter.group;
        if (provider) group = `${provider === 'neurosnap_api' ? 'Neurosnap' : 'ColabFold'} MSA options`;
        else if (/msa/.test(key)) group ||= 'MSA inputs and processing';
        else if (/step|cycle|sampling|timestep|scn_/.test(key)) group ||= 'Sampling schedule';
        else if (/fixed|constraint|bias|omit|anchor|template/.test(key)) group ||= 'Conditioning';
        else group ||= 'Expert and runtime';
        if (!applicable || inactiveProvider) group = `Inactive ${group} (retained)`;
        groups.set(group, [...(groups.get(group) || []), parameter]);
    }
    return { primary, groups, msa };
}
