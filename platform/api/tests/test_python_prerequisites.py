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
        with patch.object(p.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(result["errors"][0]["code"], "pinned-uv_failed")
        state = json.loads((self.external / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["steps"][0]["exit_code"], 7)
        commands = []
        def success(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(p.subprocess, "run", side_effect=success), patch.object(p, "check_environment", return_value="inventory"):
            result = p.prerequisite_report("python-bootstrap", project_root=self.source)
        self.assertEqual(result["status"], "installed")
        self.assertIn("--only-binary=:all:", commands[0])
        self.assertIn("uv==0.8.22", commands[0])
        for flag in ("--frozen", "--no-dev", "--no-build", "--no-install-project", "--no-python-downloads"):
            self.assertIn(flag, commands[1])
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
