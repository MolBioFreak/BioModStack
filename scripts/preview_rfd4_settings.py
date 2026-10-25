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
    # Unresolved filesystem/environment interpolations are native expressions,
    # not invented paths. Scientific literal/profile overlays are composed here.
    return {'request': request, 'request_identity': request_identity(request),
            'effective_config': OmegaConf.to_container(cfg, resolve=False),
            'interpolation_policy': 'Native interpolations retained; runtime bindings deferred',
            'authority': 'selected runtime native Hydra; shared execution composition owner'}


if __name__ == '__main__':
    print(json.dumps(preview(json.load(sys.stdin)), allow_nan=False))
