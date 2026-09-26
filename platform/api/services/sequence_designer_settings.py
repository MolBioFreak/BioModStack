"""One-time migration of saved sequence settings, before model defaults.

Validation and unsupported native overrides stay with their existing owners.
This only moves historical spellings onto the model's typed parameter fields.
"""
from __future__ import annotations

from copy import deepcopy
import json
import shlex

import yaml


_FAMPNN_NATIVE_FIELDS = {
    'batch_size': 'fampnn_batch_size',
    'exclude_cys': 'fampnn_exclude_cys',
    'num_seqs_per_pdb': 'seqs_per_design',
    'psce_threshold': 'fampnn_psce_threshold',
    'temperature': 'fampnn_temperature',
    'seq_only': 'fampnn_seq_only',
    'repack_last': 'fampnn_repack_last',
    'timestep_schedule.num_steps': 'fampnn_num_steps',
    'seed': 'fampnn_seed',
    'presort_by_length': 'fampnn_presort_by_length',
    'timestep_schedule.mode': 'fampnn_timestep_mode',
    'timestep_schedule.t_start': 'fampnn_timestep_start',
    'timestep_schedule.t_end': 'fampnn_timestep_end',
    'scn_diffusion.num_steps': 'fampnn_scn_num_steps',
    'scn_diffusion.timestep_schedule.mode': 'fampnn_scn_timestep_mode',
    'scn_diffusion.timestep_schedule.t_start': 'fampnn_scn_timestep_start',
    'scn_diffusion.timestep_schedule.t_end': 'fampnn_scn_timestep_end',
    'scn_diffusion.step_scale': 'fampnn_scn_step_scale',
    'scn_diffusion.churn_cfg.s_churn': 'fampnn_scn_s_churn',
    'scn_diffusion.churn_cfg.s_noise': 'fampnn_scn_s_noise',
    'scn_diffusion.churn_cfg.s_t_min': 'fampnn_scn_s_t_min',
    'scn_diffusion.churn_cfg.s_t_max': 'fampnn_scn_s_t_max',
}

# Matches the selected/parent native map in calibyBinderSamplingOverrides.
_CALIBY_NATIVE_FIELDS = {
    'caliby_verbose': ('verbose',),
    'caliby_gaussian_n_conformers': ('gaussian_conformers_cfg', 'n_conformers'),
    'caliby_gaussian_noise_std': ('gaussian_conformers_cfg', 'noise_std'),
    'caliby_potts_regularization': ('potts_sampling_cfg', 'regularization'),
    'caliby_potts_sweeps': ('potts_sampling_cfg', 'potts_sweeps'),
    'caliby_potts_proposal': ('potts_sampling_cfg', 'potts_proposal'),
    'caliby_potts_rejection_step': ('potts_sampling_cfg', 'rejection_step'),
    'caliby_potts_only_cond': ('potts_sampling_cfg', 'potts_only_cond'),
    'caliby_scn_num_steps': ('scn_packing_cfg', 'num_steps'),
    'caliby_scn_step_scale': ('scn_packing_cfg', 'step_scale'),
}


def normalize_historical_sequence_settings(model_id: str, params: dict) -> dict:
    """Return migrated settings; explicit typed fields win, including falsey ones."""
    result = deepcopy(params)
    if model_id in {'fampnn', 'antibody_denovo', 'template_antibody_denovo'}:
        raw = result.get('fampnn_extra_config')
        if isinstance(raw, str) and raw:
            try:
                tokens = shlex.split(raw)
            except ValueError:
                tokens = []  # Existing native parser owns malformed raw input.
            remaining = []
            migrated = {}
            for token in tokens:
                key, sep, value = token.partition('=')
                field = _FAMPNN_NATIVE_FIELDS.get(key.lstrip('+'))
                if field is None or not sep:
                    remaining.append(token)
                    continue
                try:
                    parsed = yaml.safe_load(value) if value else ''
                except yaml.YAMLError:
                    remaining.append(token)
                    continue
                if isinstance(parsed, (dict, list)):
                    remaining.append(token)
                    continue
                # Repeated native assignments retain their last value; fields
                # supplied explicitly in the saved typed request still win.
                migrated[field] = parsed
            if migrated:
                for field, value in migrated.items():
                    result.setdefault(field, value)
                result['fampnn_extra_config'] = shlex.join(remaining)
    if model_id in {'caliby_binder', 'antibody_denovo', 'template_antibody_denovo'}:
        raw = result.get('caliby_sampling_overrides_json')
        try:
            values = json.loads(raw) if isinstance(raw, str) and raw else deepcopy(raw)
        except (ValueError, TypeError):
            values = None  # Existing Caliby owner reports invalid overrides.
        if isinstance(values, dict):
            changed = False
            for field, path in _CALIBY_NATIVE_FIELDS.items():
                parent = values if len(path) == 1 else values.get(path[0])
                if isinstance(parent, dict) and path[-1] in parent:
                    result.setdefault(field, parent.pop(path[-1]))
                    changed = True
                    if len(path) > 1 and not parent:
                        values.pop(path[0])
            if changed:
                result['caliby_sampling_overrides_json'] = json.dumps(values) if isinstance(raw, str) else values
    return result
