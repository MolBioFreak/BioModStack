"""Build-time return-only backport of Foundry commit 122f39b7e89eade3b470ffa10a58e8c1fb5450b2.

No sampler defaults or Shape geometry changes. Never run on a shared installed image.
"""
from pathlib import Path
import hashlib

BASE_SHA256 = 'e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f'
PATCHED_SHA256 = 'fcc9d94bf92f0fcbb2f91a305e89985696016b2a2786db12ba5790240b53191a'
OLD = b"        return t_hat\n"
NEW = b"        return noise_schedule if partial_t is not None else t_hat\n"


def patch_source(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if digest == PATCHED_SHA256:
        return source
    if digest != BASE_SHA256 or source.count(OLD) != 1:
        raise RuntimeError("unexpected rc-foundry 0.1.9 sampler source at build time")
    return source.replace(OLD, NEW, 1)


if __name__ == "__main__":
    from importlib.util import find_spec
    spec = find_spec("rfd3")
    assert spec is not None and spec.submodule_search_locations
    target = Path(spec.submodule_search_locations[0]) / "model/inference_sampler.py"
    target.write_bytes(patch_source(target.read_bytes()))
    print(hashlib.sha256(target.read_bytes()).hexdigest())
