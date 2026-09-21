"""Dependency-free regression tests for the tracked-file hygiene gate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_repository_hygiene.py"
SPEC = importlib.util.spec_from_file_location("repository_hygiene", SCRIPT)
assert SPEC and SPEC.loader
hygiene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hygiene)


class HygieneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-q")
        self.put(hygiene.POLICY_PATH, '{"version":1,"exceptions":[]}')

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def put(self, path: str, data: str | bytes = "source\n") -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode() if isinstance(data, str) else data)
        self.git("add", "-f", "--", path)

    def errors(self) -> list[str]:
        return hygiene.audit(self.root)[0]

    def approve(self, path: str, data: bytes) -> None:
        self.put("config/repository-hygiene.json", json.dumps({"version": 1, "exceptions": [{
            "path": path, "sha256": hashlib.sha256(data).hexdigest(), "reason": "Controlled test fixture."}]}))

    def test_normal_source_is_allowed(self) -> None:
        self.put("platform/api/services/example.py")
        self.assertEqual(self.errors(), [])

    def test_environment_examples_are_allowed(self) -> None:
        self.put(".env.core-runtime.example", "TOKEN=\n")
        self.put("platform/api/.env.sample", "TOKEN=\n")
        self.assertEqual(self.errors(), [])

    def test_private_environment_is_rejected(self) -> None:
        self.put("platform/api/.env.production")
        self.assertTrue(any("private environment" in error for error in self.errors()))

    def test_cache_dependencies_and_generated_outputs_are_rejected(self) -> None:
        for path in ("platform/api/.venv/bin/python", "node_modules/pkg/index.js",
                     "__pycache__/example.pyc", "platform/mobile-cordova/www/index.html"):
            self.put(path)
        self.assertEqual(len(self.errors()), 4)

    def test_sqlite_and_weights_are_rejected(self) -> None:
        self.put("runtime/session.sqlite3")
        self.put("models/model.safetensors")
        self.assertEqual(len(self.errors()), 2)

    def test_unapproved_archive_is_rejected(self) -> None:
        self.put("artifacts/unreviewed.zip", b"package")
        self.assertTrue(self.errors())

    def test_hash_bound_fixture_is_allowed(self) -> None:
        self.put("tests/fixtures/source.zip", b"controlled fixture")
        self.approve("tests/fixtures/source.zip", b"controlled fixture")
        self.assertEqual(self.errors(), [])

    def test_changed_fixture_is_rejected(self) -> None:
        self.put("tests/fixtures/source.zip", b"changed fixture")
        self.approve("tests/fixtures/source.zip", b"controlled fixture")
        self.assertTrue(any("hash changed" in error for error in self.errors()))

    def test_stale_exception_is_rejected(self) -> None:
        self.approve("tests/fixtures/removed.zip", b"old fixture")
        self.assertTrue(any("stale exception" in error for error in self.errors()))

    def test_unanchored_cordova_rule_masks_source(self) -> None:
        self.put("platform/mobile-cordova/.gitignore", "www/\n")
        self.put("platform/mobile-cordova/local-plugins/plugin/www/source.js")
        self.assertTrue(any("masked" in error for error in self.errors()))

    def test_anchored_cordova_rule_keeps_source_visible(self) -> None:
        self.put("platform/mobile-cordova/.gitignore", "/www/\n")
        self.put("platform/mobile-cordova/local-plugins/plugin/www/source.js")
        self.assertEqual(self.errors(), [])

    def test_private_key_detection_suppresses_value(self) -> None:
        fake = "-----BEGIN " + "PRIVATE KEY-----\n" + "A" * 40
        self.put("credentials.txt", fake)
        errors = self.errors()
        self.assertTrue(any("private-key" in error for error in errors))
        self.assertNotIn("A" * 40, "\n".join(errors))

    def test_provider_token_detection_suppresses_value(self) -> None:
        fake = "ghp_" + "A" * 36
        self.put("config.txt", fake)
        errors = self.errors()
        self.assertTrue(any("GitHub token" in error for error in errors))
        self.assertNotIn(fake, "\n".join(errors))

    def test_unreviewed_large_blob_is_rejected(self) -> None:
        self.assertIsNotNone(hygiene.violation("data/example.json", 5 * 1024 * 1024 + 1))


    def test_repo_local_excludes_do_not_mask_source(self) -> None:
        self.put("src/app.py")
        (self.root / ".git/info/exclude").write_text("*.py\n")
        self.assertEqual(self.errors(), [])

    def test_global_excludes_do_not_mask_source(self) -> None:
        self.put("src/app.py")
        rules = self.root / "local-global-excludes"
        rules.write_text("*.py\n")
        self.git("config", "core.excludesFile", str(rules))
        self.assertEqual(self.errors(), [])

    def test_untracked_ignore_cannot_hide_staged_masking(self) -> None:
        self.put(".gitignore", "*.py\n")
        self.put("src/app.py")
        (self.root / "src/.gitignore").write_text("!app.py\n")
        self.assertTrue(any("masked" in error for error in self.errors()))

    def test_untracked_ignore_cannot_create_false_positive(self) -> None:
        self.put("src/app.py")
        (self.root / "src/.gitignore").write_text("*.py\n")
        self.assertEqual(self.errors(), [])

    def test_unstaged_ignore_edit_cannot_change_staged_result(self) -> None:
        self.put(".gitignore", "*.py\n")
        self.put("src/app.py")
        (self.root / ".gitignore").write_text("# unstaged removal\n")
        self.assertTrue(any("masked" in error for error in self.errors()))

    def test_deleted_worktree_ignore_still_uses_indexed_rules(self) -> None:
        self.put(".gitignore", "*.py\n")
        self.put("src/app.py")
        (self.root / ".gitignore").unlink()
        self.assertTrue(any("masked" in error for error in self.errors()))

    def test_only_indexed_secret_content_is_scanned(self) -> None:
        fake = "ghp_" + "A" * 36
        self.put("config.txt", fake)
        (self.root / "config.txt").write_text("clean unstaged replacement\n")
        self.assertTrue(any("GitHub token" in error for error in self.errors()))

    def test_unstaged_policy_cannot_approve_indexed_archive(self) -> None:
        self.put("tests/fixtures/archive.zip", b"fixture")
        (self.root / hygiene.POLICY_PATH).write_text(json.dumps({"version": 1, "exceptions": [{
            "path": "tests/fixtures/archive.zip", "sha256": hashlib.sha256(b"fixture").hexdigest(),
            "reason": "Not staged."}]}))
        self.assertTrue(self.errors())

    def test_missing_policy_fails_closed(self) -> None:
        self.git("rm", "-f", hygiene.POLICY_PATH)
        with self.assertRaisesRegex(ValueError, "policy is missing"):
            self.errors()

    def test_duplicate_json_key_is_rejected(self) -> None:
        self.put(hygiene.POLICY_PATH, '{"version":1,"exceptions":[],"exceptions":[]}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.errors()

    def test_extra_policy_keys_are_rejected(self) -> None:
        self.put(hygiene.POLICY_PATH, '{"version":1,"exceptions":[],"disable":true}')
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.errors()

    def test_boolean_policy_version_is_rejected(self) -> None:
        self.put(hygiene.POLICY_PATH, '{"version":true,"exceptions":[]}')
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.errors()

    def test_duplicate_exception_is_rejected(self) -> None:
        self.approve("tests/fixtures/source.zip", b"fixture")
        value = json.loads((self.root / hygiene.POLICY_PATH).read_text())
        value["exceptions"] *= 2
        self.put(hygiene.POLICY_PATH, json.dumps(value))
        with self.assertRaisesRegex(ValueError, "duplicate exception"):
            self.errors()

    def test_noncanonical_exception_path_is_rejected(self) -> None:
        for path in ("./fixture.zip", "tests//fixture.zip", "../fixture.zip", "/fixture.zip", "fixture.zip/"):
            with self.subTest(path=path):
                self.approve(path, b"fixture")
                with self.assertRaisesRegex(ValueError, "relative path"):
                    self.errors()

    def test_unknown_exception_key_is_rejected(self) -> None:
        self.approve("fixture.zip", b"fixture")
        value = json.loads((self.root / hygiene.POLICY_PATH).read_text())
        value["exceptions"][0]["skip_secrets"] = True
        self.put(hygiene.POLICY_PATH, json.dumps(value))
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.errors()

    def test_private_paths_cannot_be_artifact_exceptions(self) -> None:
        for path in (".env", ".venv/state", "keys/release.jks", "node_modules/pkg/source.js"):
            with self.subTest(path=path):
                self.put(path, b"controlled bytes")
                self.approve(path, b"controlled bytes")
                self.assertTrue(any("cannot be approved" in error for error in self.errors()))

    def test_sqlite_rollback_journals_are_rejected(self) -> None:
        for suffix in (".db-journal", ".sqlite-journal", ".sqlite3-journal"):
            self.put("runtime/session" + suffix)
        self.assertEqual(len(self.errors()), 3)

    def test_synthetic_checkpoint_fixture_is_allowed(self) -> None:
        self.put("tests/fixtures/checkpoint.pt", b"synthetic test text")
        self.approve("tests/fixtures/checkpoint.pt", b"synthetic test text")
        self.assertEqual(self.errors(), [])

    def test_secret_patterns_are_not_waived_by_hash_exception(self) -> None:
        fake = ("ghp_" + "A" * 36).encode()
        self.put("tests/fixtures/source.zip", fake)
        self.approve("tests/fixtures/source.zip", fake)
        self.assertTrue(any("GitHub token" in error for error in self.errors()))

    def test_encrypted_private_key_is_rejected(self) -> None:
        self.put("config.txt", "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----\n" + "A" * 40)
        self.assertTrue(any("private-key" in error for error in self.errors()))

    def test_symlinked_ignore_is_rejected_without_dereference(self) -> None:
        self.put("rules.txt", "*.py\n")
        (self.root / ".gitignore").symlink_to("rules.txt")
        self.git("add", "-f", ".gitignore")
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.errors()

    def test_regular_symlink_is_not_dereferenced(self) -> None:
        (self.root / "external-reference").symlink_to("/does/not/exist")
        self.git("add", "external-reference")
        self.assertEqual(self.errors(), [])

    def test_oversized_blob_fails_before_blob_contents_are_loaded(self) -> None:
        oid = "a" * 40
        index = f"100644 {oid} 0\tlarge.bin\0".encode()
        info = f"{oid} blob {hygiene.MAX_SCANNABLE_BLOB_BYTES + 1}\n".encode()
        with patch.object(hygiene, "git", side_effect=[index, info]) as run:
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                hygiene.read_index(self.root)
            self.assertEqual(run.call_count, 2)

    def test_total_blob_budget_fails_before_contents_are_loaded(self) -> None:
        with patch.object(hygiene, "MAX_SCANNABLE_TOTAL_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "512 MiB"):
                hygiene.read_index(self.root)

    def test_unmerged_index_is_rejected(self) -> None:
        self.put("source.py")
        oid = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", ":source.py"], text=True).strip()
        self.git("update-index", "--force-remove", "source.py")
        subprocess.run(["git", "-C", str(self.root), "update-index", "--index-info"],
                       input=f"100644 {oid} 1\tsource.py\n100644 {oid} 2\tsource.py\n",
                       text=True, capture_output=True, check=True)
        with self.assertRaisesRegex(ValueError, "unmerged"):
            self.errors()

    def test_audit_does_not_mutate_index_or_local_rules(self) -> None:
        self.put(".gitignore", "/generated/\n")
        self.put("source.py")
        index = self.root / ".git/index"
        before = index.read_bytes()
        self.errors()
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual((self.root / ".gitignore").read_text(), "/generated/\n")

    def test_cli_exit_codes_are_explicit(self) -> None:
        def run() -> subprocess.CompletedProcess[str]:
            import sys
            return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.root)],
                                  capture_output=True, text=True)
        self.assertEqual(run().returncode, 0)
        self.put(".env", "CUSTOM_PASSWORD=test\n")
        failure = run()
        self.assertEqual(failure.returncode, 1)
        self.assertNotIn("CUSTOM_PASSWORD", failure.stderr)
        self.git("rm", "-f", hygiene.POLICY_PATH)
        self.assertEqual(run().returncode, 2)


if __name__ == "__main__":
    unittest.main()
