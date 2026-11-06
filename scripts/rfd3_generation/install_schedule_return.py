"""Build-time return-only backport of Foundry commit 122f39b7e89eade3b470ffa10a58e8c1fb5450b2.

No sampler defaults or Shape geometry changes. Never run on a shared installed image.
"""
from pathlib import Path
import hashlib

BASE_SHA256 = 'e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f'
PATCHED_SHA256 = 'fcc9d94bf92f0fcbb2f91a305e89985696016b2a2786db12ba5790240b53191a'
SHAPE_BASE_SHA256 = 'bea19f55bc545963dd8834b6d7b22d5f7b6fd3ad9425e4cd3900cd7aa040a5ab'
SHAPE_PATCHED_SHA256 = 'b27da5ee1341255cf351e2cd55f2de046a3e9a8b5a4df65fd831b3639240f86d'
LINEAGES = {BASE_SHA256: PATCHED_SHA256, SHAPE_BASE_SHA256: SHAPE_PATCHED_SHA256}
OLD = b"        return t_hat\n"
NEW = b"        return noise_schedule if partial_t is not None else t_hat\n"


def patch_source(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if digest in LINEAGES.values():
        return source
    if digest not in LINEAGES or source.count(OLD) != 1:
        raise RuntimeError("unexpected rc-foundry 0.1.9 sampler source at build time")
    patched = source.replace(OLD, NEW, 1)
    if hashlib.sha256(patched).hexdigest() != LINEAGES[digest]:
        raise RuntimeError("unexpected corrected sampler identity")
    return patched


if __name__ == "__main__":
    from importlib.util import find_spec
    spec = find_spec("rfd3")
    assert spec is not None and spec.submodule_search_locations
    target = Path(spec.submodule_search_locations[0]) / "model/inference_sampler.py"
    target.write_bytes(patch_source(target.read_bytes()))
    print(hashlib.sha256(target.read_bytes()).hexdigest())
