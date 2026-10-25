"""CPU-only native selected-profile readback; never loads models or sources."""
import importlib.util
import json
import sys
import rootutils
from omegaconf import OmegaConf
from bms_rfd4.run import compose_selected_config
from lib.rfd4_proteina_contract import normalize_request, request_identity


def preview(request):
    request = normalize_request(request)
    package = importlib.util.find_spec('rfproteina')
    config_dir = rootutils.find_root(package.origin, indicator='.project-root') / 'configs'
    cfg = compose_selected_config(request, config_dir)
    # Resolve with native OmegaConf, not a browser merge. Unavailable runtime
    # environment/source bindings are explicit observations, never fake paths.
    from omegaconf import DictConfig, ListConfig
    from omegaconf.errors import InterpolationResolutionError, MissingMandatoryValue
    unresolved = []
    def read(node, path=()):
        if not isinstance(node, (DictConfig, ListConfig)):
            return node
        result = {} if isinstance(node, DictConfig) else []
        for key in node.keys() if isinstance(node, DictConfig) else range(len(node)):
            try:
                value = read(node[key], (*path, str(key)))
            except (InterpolationResolutionError, MissingMandatoryValue):
                raw = OmegaConf.to_container(node, resolve=False)[key]
                value = {'native_expression': raw, 'resolution': 'runtime_binding_required'}
                unresolved.append(list((*path, str(key))))
            if isinstance(result, dict):
                result[key] = value
            else:
                result.append(value)
        return result
    effective = read(cfg)
    return {'request': request, 'request_identity': request_identity(request),
            'effective_config': effective, 'unresolved_runtime_bindings': unresolved,
            'interpolation_policy': 'Native OmegaConf resolution; unavailable runtime bindings explicitly labelled',
            'authority': 'selected runtime native Hydra; shared execution composition owner'}


if __name__ == '__main__':
    print(json.dumps(preview(json.load(sys.stdin)), allow_nan=False))
