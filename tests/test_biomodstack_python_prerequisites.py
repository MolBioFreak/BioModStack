"""Stdlib unit coverage for standalone SHA-256 uv bootstrap and run_kvm uv seed path."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import biomodstack_python_prerequisites as p


def _load_run_kvm():
    spec = importlib.util.spec_from_file_location("bms_run_kvm", ROOT / "scripts/acceptance/run_kvm.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _make_uv_tarball(
    target: str,
    *,
    uv_script: bytes | None = None,
    include_uv: bool = True,
    uvx_script: bytes | None = b"#!/bin/sh\nexit 0\n",
) -> tuple[bytes, str]:
    buf = io.BytesIO()
    uv_bytes = uv_script if uv_script is not None else (
        f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'uv {p.UV_VERSION} ({target})'; exit 0; fi\nexit 0\n".encode()
    )
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        prefix = f"uv-{target}"
        if include_uv:
            info = tarfile.TarInfo(f"{prefix}/uv")
            info.size = len(uv_bytes)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(uv_bytes))
        if uvx_script is not None:
            info_x = tarfile.TarInfo(f"{prefix}/uvx")
            info_x.size = len(uvx_script)
            info_x.mode = 0o755
            tf.addfile(info_x, io.BytesIO(uvx_script))
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


class StandaloneUvBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        api = self.source / "platform/api"
        api.mkdir(parents=True)
        for name in p.MANIFESTS:
            (api / name).write_text(name)
        self.external = self.base / "external"
        self.env_patch = patch.dict(os.environ, {"BMS_PYTHON_ROOT": str(self.external), "BMS_HOME": str(ROOT)})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_uv_version_and_checksums_pinned(self):
        self.assertEqual(p.UV_VERSION, "0.13.9")
        expected_targets = {
            "x86_64-unknown-linux-gnu",
            "aarch64-unknown-linux-gnu",
            "x86_64-unknown-linux-musl",
            "aarch64-unknown-linux-musl",
        }
        self.assertEqual(set(p.UV_RELEASE_SHA256.keys()), expected_targets)
        for target, digest in p.UV_RELEASE_SHA256.items():
            self.assertEqual(len(digest), 64, target)
            self.assertTrue(all(c in "0123456789abcdef" for c in digest), target)

    def test_uv_release_asset_supports_x86_64_and_aarch64_gnu_and_musl(self):
        for machine, arch in (("x86_64", "x86_64"), ("AMD64", "x86_64"), ("aarch64", "aarch64"), ("arm64", "aarch64")):
            for libc in ("gnu", "musl"):
                asset = p.uv_release_asset(system="Linux", machine=machine, libc=libc)
                target = f"{arch}-unknown-linux-{libc}"
                self.assertEqual(asset["target"], target)
                self.assertEqual(asset["sha256"], p.UV_RELEASE_SHA256[target])
                self.assertEqual(
                    asset["url"],
                    f"https://github.com/astral-sh/uv/releases/download/0.13.9/uv-{target}.tar.gz",
                )
        with self.assertRaises(p.PrerequisiteError) as ctx:
            p.uv_release_asset(system="Darwin", machine="arm64")
        self.assertEqual(ctx.exception.code, "uv_platform_unsupported")
        with self.assertRaises(p.PrerequisiteError) as ctx:
            p.uv_release_asset(system="Linux", machine="riscv64")
        self.assertEqual(ctx.exception.code, "uv_platform_unsupported")

    def test_uv_version_matches_accepts_standalone_and_short_formats(self):
        self.assertTrue(p.uv_version_matches("uv 0.13.9\n", "0.13.9"))
        self.assertTrue(p.uv_version_matches("uv 0.13.9 (x86_64-unknown-linux-gnu)\n", "0.13.9"))
        self.assertTrue(p.uv_version_matches("uv 0.13.9 (873e1ab1f 2026-11-20)\n", "0.13.9"))
        self.assertFalse(p.uv_version_matches("uv 0.13.90 (x86_64-unknown-linux-gnu)\n", "0.13.9"))
        self.assertFalse(p.uv_version_matches("uv 0.8.22\n", "0.13.9"))
        self.assertFalse(p.uv_version_matches("", "0.13.9"))

    def test_install_pinned_uv_verifies_sha256_extracts_and_reuses(self):
        target = p.uv_release_asset()["target"]
        archive, digest = _make_uv_tarball(target)
        toolchain = self.external / "toolchain"
        log_path = self.external / "pinned-uv.log"
        with patch.dict(p.UV_RELEASE_SHA256, {target: digest}), \
             patch.object(p, "_download_uv_archive", return_value=archive) as dl:
            first = p.install_pinned_uv(toolchain, log_path=log_path)
        self.assertFalse(first["reused"])
        self.assertEqual(first["archive_sha256"], digest)
        self.assertTrue((toolchain / "bin/uv").is_file())
        self.assertTrue((toolchain / "bin/uvx").is_file())
        self.assertTrue((toolchain / "bin/.uv-release.json").is_file())
        self.assertIn("reused=false", log_path.read_text())
        dl.assert_called_once()

        with patch.dict(p.UV_RELEASE_SHA256, {target: digest}), \
             patch.object(p, "_download_uv_archive", side_effect=AssertionError("unexpected download")):
            second = p.install_pinned_uv(toolchain, log_path=log_path)
        self.assertTrue(second["reused"])
        self.assertEqual(second["binary_sha256"], first["binary_sha256"])
        self.assertIn("reused=true", log_path.read_text())

        (toolchain / "bin/uv").write_bytes(b"tampered")
        with patch.dict(p.UV_RELEASE_SHA256, {target: digest}), \
             patch.object(p, "_download_uv_archive", return_value=archive) as dl2:
            repaired = p.install_pinned_uv(toolchain, log_path=log_path)
        self.assertFalse(repaired["reused"])
        self.assertEqual(repaired["binary_sha256"], first["binary_sha256"])
        dl2.assert_called_once()

    def test_install_pinned_uv_rejects_sha256_mismatch_and_missing_binary(self):
        target = p.uv_release_asset()["target"]
        archive, _ = _make_uv_tarball(target)
        toolchain = self.external / "toolchain"
        log_path = self.external / "pinned-uv.log"
        with patch.object(p, "_download_uv_archive", return_value=archive):
            with self.assertRaises(p.PrerequisiteError) as ctx:
                p.install_pinned_uv(toolchain, log_path=log_path)
        self.assertEqual(ctx.exception.code, "pinned-uv_failed")
        self.assertIn("SHA-256 mismatch", str(ctx.exception))
        self.assertFalse((toolchain / "bin/uv").exists())
        self.assertIn("SHA-256 mismatch", log_path.read_text())

        bad_archive, bad_digest = _make_uv_tarball(target, include_uv=False)
        with patch.dict(p.UV_RELEASE_SHA256, {target: bad_digest}), \
             patch.object(p, "_download_uv_archive", return_value=bad_archive):
            with self.assertRaises(p.PrerequisiteError) as ctx2:
                p.install_pinned_uv(toolchain, log_path=log_path)
        self.assertEqual(ctx2.exception.code, "pinned-uv_failed")
        self.assertIn("missing uv binary", str(ctx2.exception))

    def test_python_plan_and_bootstrap_eliminate_host_pip(self):
        plan = p.prerequisite_report("python-plan", project_root=self.source)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["steps"][0]["step"], "pinned-uv")
        self.assertEqual(plan["steps"][0]["version"], "0.13.9")
        self.assertEqual(plan["steps"][0]["method"], "standalone-tarball-sha256")
        self.assertEqual(plan["steps"][0]["sha256"], p.UV_RELEASE_SHA256[plan["steps"][0]["target"]])

        target = plan["steps"][0]["target"]
        archive, digest = _make_uv_tarball(target)
        commands = []
        real_run = subprocess.run

        def intercept_run(cmd, **kwargs):
            commands.append(cmd)
            self.assertFalse(len(cmd) >= 3 and "-m" in cmd and "pip" in cmd, f"Unexpected pip invocation: {cmd}")
            if Path(cmd[0]).name == "uv" and len(cmd) >= 2 and cmd[1] == "sync":
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, **kwargs)

        with patch.dict(p.UV_RELEASE_SHA256, {target: digest}), \
             patch.object(p, "_download_uv_archive", return_value=archive), \
             patch.object(p.subprocess, "run", side_effect=intercept_run), \
             patch.object(p, "check_environment", return_value="inventory"), \
             patch.object(p, "refresh_installed_support", return_value=None), \
             patch.object(p, "publish_installed_support", return_value=None):
            report = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(report["status"], "installed", report)
        self.assertTrue(report["dependencies_installed"])
        self.assertEqual(report["steps"][0]["step"], "pinned-uv")
        self.assertEqual(report["steps"][0]["method"], "standalone-tarball-sha256")
        self.assertEqual(report["steps"][0]["status"], "complete")
        sync_cmds = [c for c in commands if Path(c[0]).name == "uv" and len(c) >= 2 and c[1] == "sync"]
        self.assertEqual(len(sync_cmds), 1)
        for flag in ("--frozen", "--no-dev", "--no-install-project", "--no-python-downloads"):
            self.assertIn(flag, sync_cmds[0])

    def test_failed_uv_bootstrap_persists_state_and_resumes(self):
        target = p.uv_release_asset()["target"]
        with patch.object(p, "_download_uv_archive", side_effect=p.PrerequisiteError("pinned-uv_failed", "network down")):
            failed = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(failed["status"], "blocked")
        self.assertEqual(failed["errors"][0]["code"], "pinned-uv_failed")
        state = json.loads((self.external / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["steps"][0]["status"], "failed")

        archive, digest = _make_uv_tarball(target)
        real_run = subprocess.run

        def intercept_run(cmd, **kwargs):
            if Path(cmd[0]).name == "uv" and len(cmd) >= 2 and cmd[1] == "sync":
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, **kwargs)

        with patch.dict(p.UV_RELEASE_SHA256, {target: digest}), \
             patch.object(p, "_download_uv_archive", return_value=archive), \
             patch.object(p.subprocess, "run", side_effect=intercept_run), \
             patch.object(p, "check_environment", return_value="inventory"), \
             patch.object(p, "refresh_installed_support", return_value=None), \
             patch.object(p, "publish_installed_support", return_value=None):
            resumed = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(resumed["status"], "installed", resumed)

    def test_install_pinned_uv_redownloads_on_receipt_digest_mismatch_or_oserror(self):
        target = p.uv_release_asset()["target"]
        archive, digest = _make_uv_tarball(target)
        toolchain = self.external / "toolchain"
        with patch.dict(p.UV_RELEASE_SHA256, {target: digest}), \
             patch.object(p, "_download_uv_archive", return_value=archive) as mock_dl:
            first = p.install_pinned_uv(toolchain, log_path=self.external / "pinned-uv.log")
            self.assertFalse(first["reused"])
            self.assertEqual(mock_dl.call_count, 1)

            # Tamper recorded archive_sha256 in receipt -> must re-download instead of reusing.
            receipt_path = toolchain / "bin/.uv-release.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["archive_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt))
            second = p.install_pinned_uv(toolchain, log_path=self.external / "pinned-uv.log")
            self.assertFalse(second["reused"])
            self.assertEqual(mock_dl.call_count, 2)

            # Existing binary raising OSError on --version -> must re-download cleanly.
            real_run = subprocess.run
            calls = {"n": 0}
            def flaky_probe(cmd, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("Exec format error")
                return real_run(cmd, **kwargs)
            with patch.object(p.subprocess, "run", side_effect=flaky_probe):
                third = p.install_pinned_uv(toolchain, log_path=self.external / "pinned-uv.log")
            self.assertFalse(third["reused"])
            self.assertEqual(mock_dl.call_count, 3)



class RunKvmUvMigrationTests(unittest.TestCase):
    def test_seed_container_uses_pinned_uv_and_no_pip(self):
        kvm = _load_run_kvm()
        self.assertEqual(kvm.UV_VERSION, "0.13.9")
        self.assertEqual(kvm.SEED_PYCDLIB, "pycdlib==1.22.0")
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            argv = kvm.build_seed_container_argv(run, container_name="bms-seed-test")
            shell_cmd = argv[-1]
            self.assertIn("/data/seed-uv/bin/uv pip install", shell_cmd)
            self.assertIn("--only-binary=:all:", shell_cmd)
            self.assertIn("--no-deps", shell_cmd)
            self.assertIn("pycdlib==1.22.0", shell_cmd)
            self.assertNotIn("-m pip", shell_cmd)

    def test_guest_root_script_drops_python3_pip_and_uses_updated_node22(self):
        kvm = _load_run_kvm()
        script = kvm.build_guest_root_script()
        self.assertNotIn("python3-pip", script)
        self.assertIn("v22.23.5", script)

    def test_bootstrap_seed_uv_stages_musl_binary_inside_run_dir(self):
        kvm = _load_run_kvm()
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            with patch.object(
                kvm,
                "install_pinned_uv",
                return_value={"uv": str(run / "seed-uv/bin/uv"), "version": "0.13.9"},
            ) as mock_inst:
                info = kvm.bootstrap_seed_uv(run)
            mock_inst.assert_called_once_with(
                run / "seed-uv", log_path=run / "seed-uv.log", timeout=180, libc="musl"
            )
            self.assertEqual(info["version"], "0.13.9")


if __name__ == "__main__":
    unittest.main()
