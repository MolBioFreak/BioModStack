"""Presence-based CAD transport; native contracts remain the settings owners."""
from copy import deepcopy
import json

from scripts.lib.laproteina_contract import CONTROLS, MOTIF_CONTROLS
from scripts.lib.disco_contract import NativeSettings, legacy_effective_settings


def is_cad(model_id, mode, params):
    return model_id == 'protein_cad_experimental' or (
        model_id == 'protein_modification_experimental' and mode == 'de_novo_design'
        and params.get('generator') in {'disco', 'laproteina'})


def settings_projection(params):
    """Project public typed overrides without truthiness filtering or defaults.

    Explicit native JSON wins over the older flat controls, matching preparer
    precedence. Return objects; the compiler serializes exactly once.
    """
    result = {}
    for model, fields in [('laproteina', CONTROLS | MOTIF_CONTROLS),
                          ('disco', NativeSettings.__annotations__)]:
        settings = {}
        # Named motifs inherit their composed dictionary. Old primary form
        # defaults are not authored native overrides; deliberate edits use
        # the sparse native map merged below. Custom-source transport keeps
        # the historical primary projection.
        named_motif = (params.get('laproteina_motif_task_name', params.get('pcad_laproteina_motif_task_name'))
                       and not params.get('laproteina_motif_pdb', params.get('pcad_laproteina_motif_pdb')))
        for field in fields:
            if model == 'laproteina' and field in MOTIF_CONTROLS and named_motif:
                continue
            key = model + '_' + field
            if key not in params and 'pcad_' + key in params:
                key = 'pcad_' + key
            if key in params:
                value = deepcopy(params[key])
                # Existing public comma-separated seed shorthand, not JSON text.
                if model == 'disco' and field == 'seeds' and isinstance(value, str):
                    value = [int(token.strip()) for token in value.split(',') if token.strip()]
                # Old motif length inputs use blank for omitted, integers otherwise.
                if model == 'laproteina' and field in {'motif_min_length', 'motif_max_length'} and isinstance(value, str):
                    if not value.strip():
                        continue
                    value = int(value)
                settings[field] = value
        key = f'pcad_{model}_settings_json'
        for native_key in (model + '_settings', key):
            if native_key in params:
                supplied = params[native_key]
                if isinstance(supplied, str):
                    supplied = json.loads(supplied)
                settings.update(deepcopy(supplied))
        if settings or key in params or model + '_settings' in params:
            result[key] = settings
    if 'disco_jobs' in params:
        result['pcad_disco_jobs_json'] = deepcopy(params['disco_jobs'])
    if 'pcad_disco_jobs_json' in params:
        result['pcad_disco_jobs_json'] = deepcopy(params['pcad_disco_jobs_json'])
    return result


def bind_new_cad_settings(model_id, mode, params):
    """Persist native settings at fresh admission, distinguishing future replays."""
    result = deepcopy(params)
    if is_cad(model_id, mode, params):
        result.update(settings_projection(params))
        if params.get('backend', params.get('generator', params.get('pcad_backend', 'disco'))) == 'disco':
            result.setdefault('pcad_disco_settings_json', {})
    return result


def replay_cad_settings(model_id, mode, source_params, params, *, overrides=None):
    """Called only by saved-Job replay owner; never infer age from task/effort.

    A persisted native settings object marks new intent, including an empty
    object. Pre-contract saved requests retain their old effective effort.
    Explicit resume controls override inherited snapshots; native JSON in that
    same override request retains precedence over its flat controls.
    """
    result = deepcopy(params)
    if not is_cad(model_id, mode, source_params):
        return result
    backend = source_params.get('backend', source_params.get('generator', source_params.get('pcad_backend', 'disco')))
    native_keys = ('pcad_disco_settings_json', 'disco_settings')
    if backend == 'disco' and not any(key in source_params for key in native_keys):
        explicit_effort = overrides is not None and any(key in overrides for key in (
            'disco_effort', 'pcad_disco_effort', *native_keys))
        if explicit_effort:
            result = bind_new_cad_settings(model_id, mode, result)
        elif (not any(key in result for key in native_keys)
              and result.get('disco_effort') == source_params.get('disco_effort')):
            settings = settings_projection(result).get('pcad_disco_settings_json', {})
            task = source_params.get('design_task', source_params.get('pcad_task', 'unconditional'))
            result['pcad_disco_settings_json'] = legacy_effective_settings(settings, task)
    # Admission snapshots are inherited state, not a competing override in this
    # resume request. Project the explicit delta separately so its native JSON
    # still wins over its own flat controls, then reconcile only supplied fields.
    # Do this after historical lowering: changing seeds alone must not reset an
    # old conditional request's effective effort from max back to fast.
    if overrides is not None:
        inherited = settings_projection(result)
        for key, delta in settings_projection(overrides).items():
            result[key] = ({**inherited.get(key, {}), **delta}
                           if key.endswith('_settings_json') else deepcopy(delta))
    return result
