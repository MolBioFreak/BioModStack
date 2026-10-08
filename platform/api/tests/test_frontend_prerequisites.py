"""Offline installer contract tests: no host Node install, cache, or services."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_frontend_prerequisites as frontend


class FrontendPrerequisitesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.root = Path(self.temp.name) / "managed"
        for name, value in {"pnpm-lock.yaml": "lockfileVersion: '9.0'", "pnpm-workspace.yaml": "packages: [packages/*, platform/frontend]", "platform/frontend/package.json": '{"name":"frontend"}', "packages/shared/package.json": '{"name":"shared"}', "patches/example.patch": "patch", "docker/web.Dockerfile": "pnpm@10.11.0"}.items():
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)
        self.env = patch.dict(os.environ, {"BMS_FRONTEND_ROOT": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def state(self, status="complete"):
        self.root.mkdir()
        frontend.save(self.root, {"schema_version": frontend.SCHEMA, "identity": frontend.identity(self.source), "status": status, "inventory_sha256": "inventory"})

    def test_unknown_lock_format_blocked_before_network(self):
        (self.source / "pnpm-lock.yaml").write_text("lockfileVersion: '10.0'\n")
        with patch.object(frontend, "node_runtime", return_value="/node"), patch.object(frontend.shutil, "which", return_value="/npm"), patch.object(frontend.subprocess, "run") as run:
            report = frontend.prerequisite_report("frontend-bootstrap", project_root=self.source)
        self.assertEqual(report["errors"][0]["code"], "pnpm_lock_incompatible")
        self.assertFalse(self.root.exists())
        self.assertFalse((self.source / "node_modules").exists())
        run.assert_not_called()

    def test_pnpm_authority_mismatch_blocked(self):
        (self.source / "docker/web.Dockerfile").write_text("pnpm@9.15.4")
        with self.assertRaises(frontend.PrerequisiteError) as exc:
            frontend.validate_lock_compatibility(self.source)
        self.assertEqual(exc.exception.code, "pnpm_authority_mismatch")

    def test_existing_sha256_workspace_patch_authority_supported(self):
        root = Path(__file__).resolve().parents[3]
        before = frontend.identity(root)
        frontend.validate_lock_compatibility(root)
        self.assertEqual(frontend.identity(root), before)

    def test_node_engines(self):
        for version, compatible in [("v20.18.3", False), ("v20.19.0", True), ("v21.7.0", False), ("v22.11.0", False), ("v22.12.0", True), ("v24.0.0", True)]:
            with self.subTest(version=version), patch.object(frontend.os, "access", return_value=True), patch.object(frontend.shutil, "which", return_value="/usr/bin/node"), patch.object(frontend.subprocess, "check_output", return_value=version):
                if compatible:
                    self.assertEqual(frontend.node_runtime(), str(Path('/usr/bin/node').resolve()))
                else:
                    with self.assertRaisesRegex(frontend.PrerequisiteError, "require"):
                        frontend.node_runtime()

    def test_recorded_node_survives_service_path_without_node(self):
        node = Path(self.temp.name) / "recorded-node"
        node.write_text("#!/bin/sh\nprintf 'v22.16.0\\n'\n")
        node.chmod(0o755)
        with patch.object(frontend.shutil, "which", return_value=None):
            self.assertEqual(frontend.node_runtime(str(node)), str(node))
        other = Path(self.temp.name) / "other-node"
        other.write_text(node.read_text())
        other.chmod(0o755)
        with patch.dict(os.environ, {"BMS_FRONTEND_NODE": str(other)}):
            with self.assertRaises(frontend.PrerequisiteError) as exc:
                frontend.node_runtime(str(node))
        self.assertEqual(exc.exception.code, "node_identity_mismatch")

    def test_plan_no_writes(self):
        with patch.object(frontend, "node_runtime", return_value="/node"), patch.object(frontend.shutil, "which", return_value="/npm"):
            report = frontend.prerequisite_report("frontend-plan", project_root=self.source)
        self.assertEqual(report["status"], "planned")
        self.assertFalse(self.root.exists())
        self.assertFalse((self.source / "node_modules").exists())

    def test_absent_and_incomplete_never_install(self):
        with patch.object(frontend.subprocess, "run") as run:
            self.assertIsNone(frontend.resolve_frontend_environment(self.source))
            self.state("failed")
            with self.assertRaisesRegex(frontend.PrerequisiteError, "resume"):
                frontend.resolve_frontend_environment(self.source)
            run.assert_not_called()

    def test_manifest_workspace_patch_identity(self):
        for name in ["pnpm-lock.yaml", "pnpm-workspace.yaml", "packages/shared/package.json", "patches/example.patch"]:
            before = frontend.identity(self.source)
            p = self.source / name
            p.write_text(p.read_text() + "\nchanged")
            self.assertNotEqual(before, frontend.identity(self.source))

    def test_changed_identity_no_fallback(self):
        self.state()
        (self.source / "patches/example.patch").write_text("changed")
        with self.assertRaises(frontend.PrerequisiteError) as exc:
            frontend.resolve_frontend_environment(self.source)
        self.assertEqual(exc.exception.code, "identity_mismatch")

    def test_readonly_named_blocker(self):
        self.source.chmod(0o555)
        self.addCleanup(self.source.chmod, 0o755)
        with self.assertRaises(frontend.PrerequisiteError) as exc:
            frontend.source_guard(self.source, writable=True)
        self.assertEqual(exc.exception.code, "source_not_writable")

    def test_redirected_source_and_receipt(self):
        (self.source / "node_modules").symlink_to(self.temp.name)
        with self.assertRaises(frontend.PrerequisiteError):
            frontend.source_guard(self.source, writable=True)
        self.root.mkdir()
        (self.root / "state.json").symlink_to(self.source / "platform/frontend/package.json")
        with self.assertRaises(frontend.PrerequisiteError):
            frontend.read_state(self.root, frontend.identity(self.source))

    def test_verified_and_inventory_tamper(self):
        self.state()
        with patch.object(frontend, "check_environment", return_value={"inventory_sha256": "inventory", "node": "/node", "vite": "/vite"}):
            self.assertEqual(frontend.resolve_frontend_environment(self.source)["node"], "/node")
        with patch.object(frontend, "check_environment", return_value={"inventory_sha256": "changed"}):
            with self.assertRaises(frontend.PrerequisiteError) as exc:
                frontend.resolve_frontend_environment(self.source)
            self.assertEqual(exc.exception.code, "environment_changed")

    def test_explicit_bootstrap_flags_and_idempotence(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(frontend, "node_runtime", return_value="/node"), patch.object(frontend.shutil, "which", return_value="/npm"), patch.object(frontend.subprocess, "run", side_effect=run), patch.object(frontend, "check_environment", return_value={"inventory_sha256": "inventory"}):
            report = frontend.prerequisite_report("frontend-bootstrap", project_root=self.source)
            self.assertEqual(report["status"], "installed", report)
            report = frontend.prerequisite_report("frontend-bootstrap", project_root=self.source)
            self.assertEqual(report["status"], "already-installed", report)
        self.assertEqual(len(commands), 2)
        self.assertIn("pnpm@10.11.0", commands[0])
        self.assertIn("--frozen-lockfile", commands[1])
        self.assertIn("frontend...", commands[1])
        self.assertTrue(all("--ignore-scripts" in c for c in commands))

    def test_source_operation_lock_blocks_different_external_root(self):
        import fcntl
        frontend.source_guard(self.source, writable=True)
        with (self.source / "node_modules/.bms-frontend-bootstrap.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(frontend, "node_runtime", return_value="/node"), patch.object(frontend.shutil, "which", return_value="/npm"), patch.object(frontend.subprocess, "run") as run:
                report = frontend.prerequisite_report("frontend-bootstrap", project_root=self.source)
            self.assertEqual(report["errors"][0]["code"], "source_operation_busy", report)
            self.assertFalse((self.root / "state.json").exists())
            run.assert_not_called()

    def test_failure_receipt_is_resumable(self):
        with patch.object(frontend, "node_runtime", return_value="/node"), patch.object(frontend.shutil, "which", return_value="/npm"), patch.object(frontend.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            report = frontend.prerequisite_report("frontend-bootstrap", project_root=self.source)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(json.loads((self.root / "state.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
