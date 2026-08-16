"""Install the audited Shape hook into exactly one pinned RFD3 sampler source."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


BASE_SHA256 = "e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f"
LEGACY_PATCHED_SHA256 = "5f1cf2938bbb7365ec37c83ffa01e15d85a4c0f674c11242c84cce08ba2b0b7d"
PATCHED_SHA256 = "bea19f55bc545963dd8834b6d7b22d5f7b6fd3ad9425e4cd3900cd7aa040a5ab"
DEFAULT_TARGET = Path("/usr/local/lib/python3.12/dist-packages/rfd3/model/inference_sampler.py")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one {label} seam, found {count}")
    return text.replace(old, new, 1)


def patch_sampler_source(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if digest == PATCHED_SHA256:
        return source
    if digest not in {BASE_SHA256, LEGACY_PATCHED_SHA256}:
        raise RuntimeError(
            "unsupported RFD3 sampler source: expected audited pristine or legacy Shape source, "
            f"observed {digest}"
        )
    text = source.decode("utf-8")
    native_hook = (
        "            delta_L = self.guide_coordinate_derivative(\n"
        "                X_noisy_L=X_noisy_L, X_denoised_L=X_denoised_L, delta_L=delta_L,\n"
        "                t_hat=t_hat, f=f, step_i=step_num, total_steps=len(noise_schedule) - 1,\n"
        "                fixed_atom_mask=is_motif_atom_with_fixed_coord\n"
        "            )\n\n"
    )
    if digest == BASE_SHA256:
        text = _replace_once(
            text,
            'kind: Literal["default", "symmetry"] = "default"',
            'kind: Literal["default", "symmetry", "shape"] = "default"',
            label="sampler-kind",
        )
        text = _replace_once(
            text,
            "        return t_hat\n\n    def _get_initial_structure(",
            "        return t_hat\n\n"
            "    def guide_coordinate_derivative(\n"
            "        self, *, X_noisy_L, X_denoised_L, delta_L, t_hat, f, step_i, total_steps, fixed_atom_mask\n"
            "    ):\n"
            "        return delta_L\n\n"
            "    def _get_initial_structure(",
            label="base derivative hook",
        )
        text = _replace_once(
            text,
            "\n\nclass ConditionalDiffusionSampler:\n",
            "\n\nfrom bms_shape_sampler import ShapeGuidedDiffusionSampler\n\n\n"
            "class ConditionalDiffusionSampler:\n",
            label="Shape sampler import",
        )
        text = _replace_once(
            text,
            '        "symmetry": SampleDiffusionWithSymmetry,\n',
            '        "symmetry": SampleDiffusionWithSymmetry,\n'
            '        "shape": ShapeGuidedDiffusionSampler,\n',
            label="sampler registry",
        )
    else:
        text = _replace_once(
            text,
            "    def post_step_coordinates(self, *, X_L, f, step_i, total_steps, fixed_atom_mask):\n"
            "        return X_L\n",
            "    def guide_coordinate_derivative(\n"
            "        self, *, X_noisy_L, X_denoised_L, delta_L, t_hat, f, step_i, total_steps, fixed_atom_mask\n"
            "    ):\n"
            "        return delta_L\n",
            label="legacy base coordinate hook",
        )
        text = _replace_once(
            text,
            "            X_L = self.post_step_coordinates(\n"
            "                X_L=X_L, f=f, step_i=step_num, total_steps=len(noise_schedule) - 1,\n"
            "                fixed_atom_mask=is_motif_atom_with_fixed_coord\n"
            "            )\n",
            "",
            label="legacy post-step invocation",
        )
    text = _replace_once(
        text,
        "                delta_L = delta_L + (self.cfg_scale - 1) * (delta_L - delta_L_ref)\n\n"
        "            if exists(outs.get(\"sequence_logits_I\")):\n",
        "                delta_L = delta_L + (self.cfg_scale - 1) * (delta_L - delta_L_ref)\n\n"
        + native_hook
        + "            if exists(outs.get(\"sequence_logits_I\")):\n",
        label="native EDM derivative update",
    )
    return text.encode("utf-8")


def install(target: Path = DEFAULT_TARGET) -> str:
    source = target.read_bytes()
    patched = patch_sampler_source(source)
    temporary = target.with_name(target.name + ".bms-shape.tmp")
    temporary.write_bytes(patched)
    os.chmod(temporary, target.stat().st_mode)
    os.replace(temporary, target)
    return hashlib.sha256(patched).hexdigest()


if __name__ == "__main__":
    print(install())
