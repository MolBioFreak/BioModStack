"""ONT signal tool images are found on this machine and accepted on their own id."""
from __future__ import annotations

import subprocess

import pytest

from services import ont_local_images

LOCAL = "sha256:" + "e" * 64
TAG = ont_local_images.DEFAULT_TAGS["BMS_ONT_SQUIGULATOR_IMAGE"]


def _images(monkeypatch: pytest.MonkeyPatch, present: dict[str, str]) -> list[str]:
    asked: list[str] = []

    def inspect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        asked.append(command[-1])
        found = present.get(command[-1])
        return subprocess.CompletedProcess(command, 0 if found else 1, stdout=found or "", stderr="")

    monkeypatch.setattr(ont_local_images.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ont_local_images.subprocess, "run", inspect)
    return asked


def test_image_built_under_the_tool_tag_is_accepted_on_its_own_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_ONT_SQUIGULATOR_IMAGE", "sha256:" + "1" * 64)
    asked = _images(monkeypatch, {TAG: LOCAL})
    assert ont_local_images.local_image_identity("BMS_ONT_SQUIGULATOR_IMAGE", "docker") == (LOCAL, "e" * 64)
    assert asked == ["sha256:" + "1" * 64, TAG]


def test_configured_image_wins_when_it_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = "sha256:" + "1" * 64
    monkeypatch.setenv("BMS_ONT_SQUIGULATOR_IMAGE", configured)
    asked = _images(monkeypatch, {configured: configured, TAG: LOCAL})
    assert ont_local_images.local_image_identity("BMS_ONT_SQUIGULATOR_IMAGE", "docker")[0] == configured
    assert asked == [configured]


def test_inspection_uses_the_operator_container_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/operator/home")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("BMS_SUDO_PASSWORD", "do-not-forward")
    monkeypatch.delenv("BMS_ONT_SQUIGULATOR_IMAGE", raising=False)
    seen: list[dict[str, str]] = []

    def inspect(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(dict(kwargs["env"]))  # type: ignore[call-overload]
        return subprocess.CompletedProcess(command, 0, stdout=LOCAL, stderr="")

    monkeypatch.setattr(ont_local_images.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ont_local_images.subprocess, "run", inspect)
    ont_local_images.local_image_identity("BMS_ONT_SQUIGULATOR_IMAGE", "docker")
    assert seen[0]["HOME"] == "/operator/home"
    assert seen[0]["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert "BMS_SUDO_PASSWORD" not in seen[0]


@pytest.mark.parametrize("observed", ["a" * 63, "mutable:tag", ""])
def test_malformed_inspect_output_is_not_an_image(monkeypatch: pytest.MonkeyPatch, observed: str) -> None:
    monkeypatch.delenv("BMS_ONT_SQUIGULATOR_IMAGE", raising=False)
    _images(monkeypatch, {TAG: observed})
    with pytest.raises(RuntimeError, match="no local image"):
        ont_local_images.local_image_identity("BMS_ONT_SQUIGULATOR_IMAGE", "podman")


def test_podman_bare_id_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_ONT_SQUIGULATOR_IMAGE", raising=False)
    _images(monkeypatch, {TAG: "e" * 64})
    assert ont_local_images.local_image_identity("BMS_ONT_SQUIGULATOR_IMAGE", "podman") == (LOCAL, "e" * 64)


def test_missing_image_names_the_tag_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_ONT_SQUIGULATOR_IMAGE", raising=False)
    _images(monkeypatch, {})
    with pytest.raises(RuntimeError, match="build biomodstack/ont-squigulator:0.5.0"):
        ont_local_images.local_image_identity("BMS_ONT_SQUIGULATOR_IMAGE", "docker")


def test_default_tags_match_the_installer_recipe() -> None:
    import json
    from pathlib import Path

    recipe = json.loads(
        (Path(__file__).resolve().parents[3] / "tools/recipes/ont-signal-tools.json").read_text(encoding="utf-8")
    )
    built = {image["docker"]["tag"] if "docker" in image else image.get("tag") for image in recipe["images"]}
    assert set(ont_local_images.DEFAULT_TAGS.values()) <= built
