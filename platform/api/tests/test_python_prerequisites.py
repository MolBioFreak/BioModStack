"""Stdlib unit coverage; real acquisition is exercised in an isolated guest."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_python_prerequisites as p


class PrerequisitesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        api = self.source / "platform/api"
        api.mkdir(parents=True)
        for name in p.MANIFESTS:
            (api / name).write_text(name)
        self.external = self.base / "external"
        self.env = patch.dict(os.environ, {"BMS_PYTHON_ROOT": str(self.external), "BMS_HOME": str(ROOT)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def receipt(self, status="complete"):
        self.external.mkdir(exist_ok=True)
        state = {"schema_version": p.SCHEMA, "identity": p.identity(self.source),
                 "status": status, "inventory_sha256": "inventory", "steps": []}
        p.save(self.external, state)
        return state

    def source_only_lock(self):
        approved = p.DOCOPT_SOURCE
        text = ('version = 1\n\n[[package]]\nname = "docopt"\n'
                f'version = "{approved["version"]}"\n'
                'source = { registry = "https://pypi.org/simple" }\n'
                f'sdist = {{ url = "{approved["url"]}", hash = "{approved["hash"]}", size = 25901 }}\n'
                '\n[[package]]\nname = "future-source-package"\nversion = "1.0"\n'
                'source = { registry = "https://pypi.org/simple" }\n'
                'sdist = { url = "https://example.invalid/package.tar.gz", hash = "sha256:other" }\n')
        lock = self.source / "platform/api/uv.lock"
        lock.write_text(text)
        return lock, text

    def test_only_exact_reviewed_docopt_source_can_build(self):
        lock, original = self.source_only_lock()
        flags, approved = p.locked_build_policy(lock)
        self.assertEqual(flags, ["--no-build-package", "future-source-package"])
        self.assertEqual(approved, [p.DOCOPT_SOURCE])
        for old, new in ((p.DOCOPT_SOURCE["hash"], "sha256:wrong"),
                         (p.DOCOPT_SOURCE["url"], "https://example.invalid/docopt.tar.gz"),
                         ('version = "0.6.2"', 'version = "0.6.3"'),
                         ('registry = "https://pypi.org/simple"', 'registry = "https://example.invalid"'),
                         ('name = "future-source-package"', 'name = "unsupported\\escape"')):
            with self.subTest(change=old):
                lock.write_text(original.replace(old, new))
                self.assertEqual(p.locked_build_policy(lock), (["--no-build"], []))

    def test_first_party_alignment_build_keeps_other_sources_restricted(self):
        lock, original = self.source_only_lock()
        local = ('\n[[package]]\nname = "bms-alignment-core"\nversion = "0.1.0"\n'
                 'source = { directory = "../../packages/bms-alignment-core" }\n')
        lock.write_text(original + local)
        flags, approved = p.locked_build_policy(lock)
        self.assertNotIn("bms-alignment-core", flags)
        self.assertIn("future-source-package", flags)
        lock.write_text(original + local.replace('directory = "../../packages/bms-alignment-core"',
                                                'registry = "https://pypi.org/simple"'))
        self.assertIn("bms-alignment-core", p.locked_build_policy(lock)[0])

    def _fake_uv_info(self):
        return {
            "uv": str(self.external / "toolchain/bin/uv"),
            "version": p.UV_VERSION,
            "target": "x86_64-unknown-linux-gnu",
            "url": p.uv_release_asset(system="Linux", machine="x86_64", libc="gnu")["url"],
            "archive_sha256": p.UV_RELEASE_SHA256["x86_64-unknown-linux-gnu"],
            "binary_sha256": "0" * 64,
            "reused": False,
            "stdout": f"uv {p.UV_VERSION} (x86_64-unknown-linux-gnu)",
        }

    def test_source_build_exception_is_recorded_and_scoped_to_locked_sync(self):
        self.source_only_lock()
        commands = []
        def success(command, **kwargs):
            commands.append((command, kwargs["env"]["UV_NO_BUILD"]))
            if "sync" in command:
                self.assertEqual(kwargs["cwd"], self.source / "platform/api")
            return subprocess.CompletedProcess(command, 0)
        with patch.object(p, "install_pinned_uv", return_value=self._fake_uv_info()), \
             patch.object(p.subprocess, "run", side_effect=success), \
             patch.object(p, "check_environment", return_value="inventory"), \
             patch.object(p, "refresh_installed_support", return_value=None), \
             patch.object(p, "publish_installed_support", return_value=None):
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["approved_source_builds"], [p.DOCOPT_SOURCE])
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][1], "false")
        for flag in ("--frozen", "--no-dev", "--no-install-project", "--no-python-downloads"):
            self.assertIn(flag, commands[0][0])
        self.assertIn("--no-build-package", commands[0][0])
        self.assertIn("future-source-package", commands[0][0])
        self.assertEqual(p.subprocess_env(self.external)["UV_NO_BUILD"], "1")

    def test_launcher_prefers_managed_interpreter_over_unrelated_path_python(self):
        managed = self.external / "environment/bin/python"
        managed.parent.mkdir(parents=True)
        managed.write_text('#!/bin/sh\nprintf "%s\\n" "$0" "managed-root" ""\n')
        managed.chmod(0o755)
        shadow = self.base / "shadow"
        shadow.mkdir()
        (shadow / "python3").write_text('#!/bin/sh\necho "wrong interpreter" >&2\nexit 42\n')
        (shadow / "python3").chmod(0o755)
        result = subprocess.run([
            "/bin/bash", "-c",
            'source "$1"; bms_python_runtime_resolve || exit $?; printf "%s\\n" "$BMS_MANAGED_PYTHON"',
            "test", str(ROOT / "scripts/python_runtime_guard.sh"),
        ], env={**os.environ, "PATH": str(shadow) + ":/usr/bin:/bin"},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(managed))

    def test_dispatch_validates_identity_under_retained_managed_python(self):
        # A private venv retains this interpreter, while the launching shell's
        # version identity changes. No packages are installed and no uv runs.
        import venv
        venv.EnvBuilder(with_pip=False).create(self.external / "environment")
        state = self.receipt()
        before = (self.external / "state.json").read_bytes()
        probe = self.base / "dispatch.py"
        probe.write_text(
            "import sys, json, os\nfrom pathlib import Path\nfrom types import SimpleNamespace\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import biomodstack_python_prerequisites as p\n"
            f"managed = Path({str(self.external / 'environment')!r})\n"
            "if Path(sys.prefix) != managed:\n"
            "    p.sys = SimpleNamespace(version_info=SimpleNamespace(major=99, minor=0), "
            "prefix=sys.prefix, base_prefix=sys.base_prefix, argv=sys.argv)\n"
            "p.check_environment = lambda root: 'inventory'\n"
            "os.environ['PIP_INDEX_URL'] = 'https://unused.invalid/simple'\n"
            f"p.dispatch_setup(Path({str(self.source)!r}))\n"
            "assert 'PIP_INDEX_URL' not in os.environ\n"
            "assert os.environ.get('VIRTUAL_ENV') == sys.prefix\n"
            "assert os.environ['PATH'].split(os.pathsep)[0] == str(Path(sys.prefix) / 'bin')\n"
            "if Path(sys.prefix) != managed:\n"
            "    assert os.environ.get('BMS_SUPPORT_PAYLOAD_RECEIPT') == str(Path(sys.prefix) / 'support-payload.json')\n"
            "print(json.dumps(dict(prefix=sys.prefix, version=p.identity("
            f"Path({str(self.source)!r}))['python'])))\n"
        )
        result = subprocess.run([sys.executable, "-B", str(probe)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "prefix": str(self.external / "environment"), "version": p.identity(self.source)["python"]})
        self.assertEqual((self.external / "state.json").read_bytes(), before)
        # The optional published representation must not bounce back to the
        # mutable environment on its next dispatch.
        published = self.base / "published"
        venv.EnvBuilder(with_pip=False).create(published)
        state["support_payload"] = {"python": str(published / "bin/python"), "payload_root": str(published)}
        p.save(self.external, state)
        probe.write_text(probe.read_text().replace(
            "if Path(sys.prefix) != managed:",
            f"if Path(sys.prefix) not in (managed, Path({str(published)!r})):", 1))
        result = subprocess.run([sys.executable, "-B", str(probe)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["prefix"], str(published))
        before = (self.external / "state.json").read_bytes()
        valid_probe = probe.read_text()
        probe.write_text(valid_probe.replace("lambda root: 'inventory'", "lambda root: 'changed'"))
        result = subprocess.run([sys.executable, "-B", str(probe)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Installed distribution inventory changed", result.stderr)
        self.assertEqual((self.external / "state.json").read_bytes(), before)
        probe.write_text(valid_probe)
        (self.source / "platform/api/uv.lock").write_text("changed")
        result = subprocess.run([sys.executable, "-B", str(probe)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Source, manifests, Python or policy changed", result.stderr)
        self.assertEqual((self.external / "state.json").read_bytes(), before)

    def test_plan_no_writes_no_processes(self):
        with patch.object(p.subprocess, "run", side_effect=AssertionError("process")):
            report = p.prerequisite_report("python-plan", project_root=self.source)
        self.assertEqual(report["status"], "planned")
        self.assertFalse(report["ready"])
        self.assertFalse(self.external.exists())

    def test_absent_preserves_legacy(self):
        self.assertIsNone(p.resolve_python_environment(self.source))

    def test_verify_never_installs(self):
        with patch.object(p.subprocess, "run", side_effect=AssertionError("process")):
            report = p.prerequisite_report("python-verify", project_root=self.source)
        self.assertEqual(report["errors"][0]["code"], "bootstrap_incomplete")
        self.assertFalse(self.external.exists())

    def test_source_storage_rejected(self):
        for root in (self.source, self.source / "env", self.base):
            with patch.dict(os.environ, {"BMS_PYTHON_ROOT": str(root)}):
                report = p.prerequisite_report("python-bootstrap", project_root=self.source)
                self.assertEqual(report["errors"][0]["code"], "external_root_invalid")

    def test_relative_storage_rejected(self):
        with patch.dict(os.environ, {"BMS_PYTHON_ROOT": "relative"}):
            self.assertEqual(p.prerequisite_report("python-plan", project_root=self.source)["errors"][0]["code"], "external_root_invalid")

    def test_lock_change_never_reuses_or_rewrites_receipt(self):
        self.receipt()
        before = (self.external / "state.json").read_bytes()
        (self.source / "platform/api/uv.lock").write_text("changed")
        report = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(report["errors"][0]["code"], "identity_mismatch")
        self.assertEqual((self.external / "state.json").read_bytes(), before)
        with self.assertRaises(p.PrerequisiteError):
            p.resolve_python_environment(self.source)

    def test_interrupted_does_not_fallback(self):
        self.receipt("running")
        with self.assertRaisesRegex(p.PrerequisiteError, "interrupted"):
            p.resolve_python_environment(self.source)

    def test_complete_idempotent_without_install(self):
        self.receipt()
        with patch.object(p, "check_environment", return_value="inventory"), patch.object(p.subprocess, "run", side_effect=AssertionError("network")):
            report = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(report["status"], "already-installed")

    def test_optional_support_publication_does_not_block_cold_or_warm_bootstrap(self):
        for previous, expected in [("running", "installed"), ("complete", "already-installed")]:
            with self.subTest(previous=previous):
                self.receipt(previous)
                with patch.object(p, "install_pinned_uv", return_value=self._fake_uv_info()), \
                     patch.object(p, "check_environment", return_value="inventory"), \
                     patch.object(p.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                     patch.object(p, "refresh_installed_support", return_value=None), \
                     patch.object(p, "publish_installed_support", side_effect=OSError("inert publication failure")):
                    report = p.prerequisite_report("python-bootstrap", project_root=self.source)
                self.assertEqual(report["status"], expected)
                self.assertTrue(report["dependencies_installed"])
                self.assertEqual(report["errors"], [])
                self.assertEqual(json.loads((self.external / "state.json").read_text())["status"], "complete")

    def test_consumer_contract(self):
        self.receipt()
        with patch.object(p, "check_environment", return_value="inventory"):
            result = p.resolve_python_environment(self.source)
        self.assertEqual(result["python"], str(self.external / "environment/bin/python"))
        self.assertTrue(result["env"]["PATH"].startswith(str(self.external)))
        self.assertEqual(result["env"]["UV_PYTHON_DOWNLOADS"], "never")

    def test_inventory_tamper_blocks(self):
        self.receipt()
        with patch.object(p, "check_environment", return_value="changed"):
            with self.assertRaisesRegex(p.PrerequisiteError, "inventory changed"):
                p.resolve_python_environment(self.source)

    def test_failed_command_persisted_and_resumable(self):
        self.receipt("running")
        with patch.object(p, "install_pinned_uv", return_value=self._fake_uv_info()), \
             patch.object(p.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(result["errors"][0]["code"], "locked-sync_failed")
        state = json.loads((self.external / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["steps"][0]["step"], "pinned-uv")
        self.assertEqual(state["steps"][0]["method"], "standalone-tarball-sha256")
        self.assertEqual(state["steps"][0]["version"], p.UV_VERSION)
        self.assertEqual(state["steps"][1]["step"], "locked-sync")
        self.assertEqual(state["steps"][1]["exit_code"], 7)
        commands = []
        def success(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(p, "install_pinned_uv", return_value=self._fake_uv_info()), \
             patch.object(p.subprocess, "run", side_effect=success), \
             patch.object(p, "check_environment", return_value="inventory"):
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(len(commands), 1)
        for cmd in commands:
            self.assertNotIn("pip", cmd)
        for flag in ("--frozen", "--no-dev", "--no-build", "--no-install-project", "--no-python-downloads"):
            self.assertIn(flag, commands[0])
        for name in p.MANIFESTS:
            self.assertEqual((self.external / "manifests" / name).read_bytes(), (self.source / "platform/api" / name).read_bytes())

    def test_concurrent_operation_blocks(self):
        self.receipt("running")
        with (self.external / "operation.lock").open("a") as lock:
            p.fcntl.flock(lock, p.fcntl.LOCK_EX | p.fcntl.LOCK_NB)
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(result["errors"][0]["code"], "operation_busy")
        self.assertEqual(json.loads((self.external / "state.json").read_text())["status"], "running")

    def test_environment_isolation(self):
        with patch.dict(os.environ, {"PIP_INDEX_URL": "https://invalid", "UV_INDEX": "bad", "PYTHONPATH": "bad", "VIRTUAL_ENV": "bad"}):
            env = p.subprocess_env(self.external)
        for key in ("PIP_INDEX_URL", "UV_INDEX", "PYTHONPATH", "VIRTUAL_ENV"):
            self.assertNotIn(key, env)

    def test_legacy_without_manifests(self):
        synthetic = self.base / "synthetic-config"
        synthetic.mkdir()
        self.assertIsNone(p.resolve_python_environment(synthetic))
        self.external.mkdir()
        self.assertIsNone(p.resolve_python_environment(synthetic))

    def test_malformed_state_object(self):
        self.external.mkdir()
        (self.external / "state.json").write_text("[]")
        report = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(report["errors"][0]["code"], "state_invalid")
        self.assertEqual((self.external / "state.json").read_text(), "[]")

    def test_state_and_child_symlinks_never_clobber(self):
        self.external.mkdir()
        victim = self.source / "important"
        victim.write_text("preserve")
        for name in ("state.json", "operation.lock", "pinned-uv.log", "toolchain"):
            link = self.external / name
            link.symlink_to(victim)
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
            self.assertEqual(result["errors"][0]["code"], "unsafe_path")
            self.assertEqual(victim.read_text(), "preserve")
            link.unlink()
        nested = self.external / "cache"
        nested.mkdir()
        (nested / "escape").symlink_to(self.source, target_is_directory=True)
        self.assertEqual(p.prerequisite_report("python-bootstrap", project_root=self.source)["errors"][0]["code"], "unsafe_path")

    def test_shell_and_direct_cli_plan(self):
        for command in (["bash", str(ROOT / "start_ui.sh"), "python-plan", "--json"],
                        [sys.executable, "-B", str(ROOT / "scripts/manage_desktop_services.py"), "--json", "python-plan"]):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "planned")
        self.assertFalse(self.external.exists())


if __name__ == "__main__":
    unittest.main()
